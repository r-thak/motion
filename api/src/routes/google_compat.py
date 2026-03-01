"""Google-compatible synchronous endpoint."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from ulid import ULID

from src.config import settings
from src.middleware.errors import ValidationError
from src.models.request import ComputeRoutesRequest
from src.models.response import GoogleComputeRoutesResponse, RouteResource
from src.models.vehicle import resolve_vehicle_spec
from src.services.cache import route_cache_key, telemetry_cache_key, compute_request_hash, request_cache_key
from src.services.enrichment import enrich_routes
from src.services.translator import translate_request, translate_response
from src.services.valhalla import compute_route
from src.storage import postgres as db

router = APIRouter()


def _validate_waypoints(request_body: ComputeRoutesRequest) -> None:
    """Validate that all waypoints use location (not placeId/address)."""
    for label, wp in [("origin", request_body.origin), ("destination", request_body.destination)]:
        if wp.placeId is not None or wp.address is not None:
            raise ValidationError(
                code="unsupported_waypoint_type",
                message="Address and placeId waypoints are not supported; use latLng coordinates.",
                param=label,
            )
        if wp.location is None:
            raise ValidationError(
                code="missing_location",
                message=f"Waypoint {label} must have a location with latLng coordinates.",
                param=label,
            )

    for i, wp in enumerate(request_body.intermediates):
        if wp.placeId is not None or wp.address is not None:
            raise ValidationError(
                code="unsupported_waypoint_type",
                message="Address and placeId waypoints are not supported; use latLng coordinates.",
                param=f"intermediates[{i}]",
            )
        if wp.location is None:
            raise ValidationError(
                code="missing_location",
                message=f"Intermediate waypoint {i} must have a location with latLng coordinates.",
                param=f"intermediates[{i}]",
            )


@router.post("/proxy/google/directions/v2:computeRoutes")
async def proxy_google_routes(request: Request):
    """Proxy directly to Google's real Routes API to hide API key."""
    body = await request.json()
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_api_key,
        "X-Goog-FieldMask": request.headers.get("X-Goog-FieldMask", "routes.polyline.encodedPolyline,routes.distanceMeters,routes.duration")
    }
    
    # We must use httpx client
    http_client = request.app.state.http_client
    resp = await http_client.post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        json=body,
        headers=headers
    )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@router.post("/directions/v2:computeRoutes")
async def google_compute_routes(
    request_body: ComputeRoutesRequest,
    request: Request,
    x_goog_fieldmask: str | None = Header(None, alias="X-Goog-FieldMask"),
):
    """Google-compatible synchronous route computation endpoint."""
    # 1. Validate waypoints
    _validate_waypoints(request_body)

    # 2. Generate route ID
    route_id = f"route_{ULID()}"

    # 3. Resolve vehicle spec
    resolved_vehicle = resolve_vehicle_spec(request_body.vehicleSpec)

    # 4. Translate request
    locations, costing_options, date_time, alternates = translate_request(
        request_body, resolved_vehicle
    )

    # 5. Call Valhalla
    http_client = request.app.state.http_client
    valhalla_response = await compute_route(
        http_client,
        settings.valhalla_url,
        locations,
        costing_options,
        request_body.languageCode,
        date_time,
        alternates,
    )

    # 6. Translate response
    routes, all_step_points = translate_response(valhalla_response, route_id)

    # 7. Run enrichment pipeline
    redis_client = request.app.state.redis
    zone_index = request.app.state.zone_index
    telemetry_list = await enrich_routes(
        routes,
        all_step_points,
        resolved_vehicle,
        request_body.departureTime,
        request_body.routingProfile,
        request_body.profileOverrides,
        zone_index,
        http_client,
        redis_client,
        settings,
    )

    for t in telemetry_list:
        t.routeId = route_id

    # 8. Store results in Postgres and Redis
    pg_pool = request.app.state.pg_pool
    response_data = {
        "routes": [r.model_dump() for r in routes],
        "fallbackInfo": None,
        "geocodingResults": None,
    }
    telemetry_data = telemetry_list[0].model_dump() if telemetry_list else {}
    request_hash = compute_request_hash(request_body.model_dump())

    await db.create_route(
        pg_pool,
        route_id=route_id,
        status="complete",
        request_body=request_body.model_dump(),
        request_hash=request_hash,
        response_body=response_data,
        telemetry=telemetry_data,
        vehicle_spec=resolved_vehicle,
        departure_time=request_body.departureTime,
        webhook_url=None,
    )

    # Cache in Redis
    route_resource = RouteResource(
        id=route_id,
        object="route",
        status="complete",
        createdAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        routes=routes,
        fallbackInfo=None,
        geocodingResults=None,
        warnings=[],
        error=None,
    )
    await redis_client.setex(
        route_cache_key(route_id),
        settings.cache_ttl,
        json.dumps(route_resource.model_dump()),
    )
    await redis_client.setex(
        telemetry_cache_key(route_id),
        settings.cache_ttl,
        json.dumps(telemetry_data),
    )

    # Cache by request hash
    await redis_client.setex(
        request_cache_key(request_hash),
        settings.cache_ttl,
        route_id,
    )

    # 9. Build Google-compatible response
    google_response = GoogleComputeRoutesResponse(
        routes=routes,
        fallbackInfo=None,
        geocodingResults=None,
    )

    # 10. Return with X-Route-Id header
    return JSONResponse(
        status_code=200,
        content=google_response.model_dump(),
        headers={"X-Route-Id": route_id},
    )

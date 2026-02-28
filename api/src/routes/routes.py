"""Stripe-style stateful route endpoints."""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from ulid import ULID

from src.config import settings
from src.middleware.errors import RouteNotFoundError
from src.middleware.idempotency import check_idempotency, save_idempotency_response
from src.models.driver import DriverState
from src.models.request import ComputeRoutesRequest
from src.models.response import RouteResource
from src.models.vehicle import resolve_vehicle_spec
from src.services.cache import (
    compute_request_hash,
    request_cache_key,
    route_cache_key,
    telemetry_cache_key,
)
from src.services.enrichment import enrich_routes
from src.services.translator import translate_request, translate_response
from src.services.valhalla import compute_route
from src.services.webhooks import deliver_webhook
from src.services.worker import compute_route_background
from src.storage import postgres as db

router = APIRouter(prefix="/v1/routes")


def _validate_waypoints(request_body: ComputeRoutesRequest) -> None:
    """Validate that all waypoints use location (not placeId/address)."""
    from src.middleware.errors import ValidationError

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


@router.post("", status_code=202)
async def create_route_endpoint(
    request_body: ComputeRoutesRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Create a route resource (Stripe-style hybrid sync/async)."""
    pg_pool = request.app.state.pg_pool
    redis_client = request.app.state.redis
    http_client = request.app.state.http_client
    zone_index = request.app.state.zone_index

    # 1. Idempotency check
    cached = await check_idempotency(request, request_body.model_dump())
    if cached is not None:
        return cached

    # 2. Request-level cache check
    request_hash = compute_request_hash(request_body.model_dump())
    cached_route_id = await redis_client.get(request_cache_key(request_hash))
    if cached_route_id:
        cached_route = await redis_client.get(route_cache_key(cached_route_id))
        if cached_route:
            return JSONResponse(status_code=200, content=json.loads(cached_route))

    # 3. Generate route ID
    route_id = f"route_{ULID()}"

    # 4. Validate waypoints
    _validate_waypoints(request_body)

    # 5. Resolve vehicle spec and driver state
    resolved_vehicle = resolve_vehicle_spec(request_body.vehicleSpec)
    driver_state = request_body.driverState or DriverState()

    # 6. Create route record with status: "processing"
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    await db.create_route(
        pg_pool,
        route_id=route_id,
        status="processing",
        request_body=request_body.model_dump(),
        request_hash=request_hash,
        response_body=None,
        telemetry=None,
        driver_state=driver_state.model_dump(),
        vehicle_spec=resolved_vehicle,
        departure_time=request_body.departureTime,
        webhook_url=request_body.webhookUrl,
    )

    # 7. Attempt fast-path computation
    async def _full_computation():
        locations, costing_options, date_time, alternates = translate_request(
            request_body, resolved_vehicle
        )
        valhalla_response = await compute_route(
            http_client, settings.valhalla_url, locations, costing_options,
            request_body.languageCode, date_time, alternates,
        )
        routes, all_step_points = translate_response(valhalla_response, route_id)
        telemetry_list = await enrich_routes(
            routes, all_step_points, resolved_vehicle, driver_state,
            request_body.departureTime, request_body.routingProfile,
            request_body.profileOverrides, zone_index, http_client,
            redis_client, settings,
        )
        for t in telemetry_list:
            t.routeId = route_id
        return routes, telemetry_list

    try:
        routes, telemetry_list = await asyncio.wait_for(
            _full_computation(),
            timeout=settings.async_timeout_seconds,
        )

        # Fast-path succeeded
        response_data = {
            "routes": [r.model_dump() for r in routes],
            "fallbackInfo": None,
            "geocodingResults": None,
        }
        telemetry_data = telemetry_list[0].model_dump() if telemetry_list else {}

        await db.update_route_complete(pg_pool, route_id, response_data, telemetry_data)

        route_resource = RouteResource(
            id=route_id,
            object="route",
            status="complete",
            createdAt=created_at,
            routes=routes,
            fallbackInfo=None,
            geocodingResults=None,
            warnings=[],
            error=None,
        )

        resource_dict = route_resource.model_dump()
        await redis_client.setex(
            route_cache_key(route_id), settings.cache_ttl, json.dumps(resource_dict),
        )
        await redis_client.setex(
            telemetry_cache_key(route_id), settings.cache_ttl, json.dumps(telemetry_data),
        )
        await redis_client.setex(
            request_cache_key(request_hash), settings.cache_ttl, route_id,
        )

        # Create event
        event_id = f"evt_{ULID()}"
        await db.create_event(pg_pool, event_id, "route.complete", route_id, {
            "routeId": route_id, "status": "complete",
        })

        # Webhook
        if request_body.webhookUrl:
            await deliver_webhook(request_body.webhookUrl, {
                "id": event_id, "type": "route.complete",
                "createdAt": created_at,
                "data": {"routeId": route_id, "status": "complete"},
            }, http_client, settings)

        # Save idempotency
        await save_idempotency_response(request, resource_dict, 200)

        return JSONResponse(status_code=200, content=resource_dict)

    except asyncio.TimeoutError:
        # Fast-path timed out — run computation in background
        background_tasks.add_task(
            compute_route_background,
            route_id, request_body, resolved_vehicle, driver_state,
            pg_pool, redis_client, http_client, zone_index, settings,
        )

        route_resource = RouteResource(
            id=route_id,
            object="route",
            status="processing",
            createdAt=created_at,
            routes=[],
            fallbackInfo=None,
            geocodingResults=None,
            warnings=[],
            error=None,
        )

        resource_dict = route_resource.model_dump()
        await save_idempotency_response(request, resource_dict, 202)

        return JSONResponse(status_code=202, content=resource_dict)


@router.get("/{route_id}")
async def get_route_endpoint(route_id: str, request: Request):
    """Get a route resource by ID."""
    redis_client = request.app.state.redis
    pg_pool = request.app.state.pg_pool

    # 1. Check Redis cache
    cached = await redis_client.get(route_cache_key(route_id))
    if cached:
        return JSONResponse(status_code=200, content=json.loads(cached))

    # 2. Check Postgres
    row = await db.get_route(pg_pool, route_id)
    if row is None:
        raise RouteNotFoundError(route_id)

    # Build RouteResource from DB row
    route_resource = RouteResource(
        id=row["id"],
        object=row["object"],
        status=row["status"],
        createdAt=row["created_at"],
        routes=row["response_body"]["routes"] if row["response_body"] and "routes" in row["response_body"] else [],
        fallbackInfo=row["response_body"].get("fallbackInfo") if row["response_body"] else None,
        geocodingResults=row["response_body"].get("geocodingResults") if row["response_body"] else None,
        warnings=[],
        error=row["error"],
    )

    resource_dict = route_resource.model_dump()

    # Cache in Redis if complete
    if row["status"] == "complete":
        await redis_client.setex(
            route_cache_key(route_id), settings.cache_ttl, json.dumps(resource_dict),
        )

    return JSONResponse(status_code=200, content=resource_dict)


@router.delete("/{route_id}", status_code=200)
async def delete_route_endpoint(route_id: str, request: Request):
    """Delete a route resource."""
    pg_pool = request.app.state.pg_pool
    redis_client = request.app.state.redis

    deleted = await db.delete_route(pg_pool, route_id)
    if not deleted:
        raise RouteNotFoundError(route_id)

    # Remove from Redis cache
    await redis_client.delete(route_cache_key(route_id))
    await redis_client.delete(telemetry_cache_key(route_id))

    return {"id": route_id, "object": "route", "deleted": True}

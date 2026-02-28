"""Driver state update endpoint with re-scoring."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.middleware.errors import RouteNotFoundError
from src.models.driver import DriverState, DriverStateUpdate
from src.models.response import Route
from src.services.cache import route_cache_key, telemetry_cache_key
from src.services.enrichment import enrich_routes
from src.storage import postgres as db

router = APIRouter()


@router.patch("/v1/routes/{route_id}/driver-state")
async def update_driver_state_endpoint(
    route_id: str,
    update: DriverStateUpdate,
    request: Request,
):
    """Update driver state and re-score enrichment for a route."""
    pg_pool = request.app.state.pg_pool
    redis_client = request.app.state.redis
    http_client = request.app.state.http_client
    zone_index = request.app.state.zone_index

    # Get existing route
    row = await db.get_route(pg_pool, route_id)
    if row is None:
        raise RouteNotFoundError(route_id)

    if row["status"] == "processing":
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "code": "route_processing",
                    "message": "Cannot update driver state while route is processing.",
                    "param": "route_id",
                }
            },
        )

    if row["status"] == "failed":
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "code": "route_failed",
                    "message": "Cannot update driver state for a failed route.",
                    "param": "route_id",
                }
            },
        )

    # Merge driver state update
    existing_driver = row.get("driver_state") or {}
    new_driver_dict = dict(existing_driver)
    if update.fatigueScore is not None:
        new_driver_dict["fatigueScore"] = update.fatigueScore
    if update.attentionScore is not None:
        new_driver_dict["attentionScore"] = update.attentionScore
    if update.stressLevel is not None:
        new_driver_dict["stressLevel"] = update.stressLevel

    new_driver_state = DriverState(**new_driver_dict)

    # Re-enrich the route with updated driver state
    response_body = row["response_body"]
    if not response_body or "routes" not in response_body:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "api_error",
                    "code": "no_route_data",
                    "message": "No route data available for re-scoring.",
                }
            },
        )

    # Rebuild routes from stored data
    routes = [Route(**r) for r in response_body["routes"]]

    # Get request body for enrichment params
    request_body = row["request_body"]
    vehicle_spec = row["vehicle_spec"]

    # Re-extract step points from route polylines (needed for enrichment)
    import polyline as polyline_lib
    all_step_points = []
    for route_obj in routes:
        route_step_points = []
        for leg in route_obj.legs:
            for step in leg.steps:
                points = polyline_lib.decode(step.polyline.encodedPolyline, 5)
                route_step_points.append(points)
        all_step_points.append(route_step_points)

    # Re-run enrichment with new driver state
    telemetry_list = await enrich_routes(
        routes,
        all_step_points,
        vehicle_spec,
        new_driver_state,
        request_body.get("departureTime"),
        request_body.get("routingProfile", "balanced"),
        request_body.get("profileOverrides", {}),
        zone_index,
        http_client,
        redis_client,
        settings,
    )

    for t in telemetry_list:
        t.routeId = route_id

    # Update stored data
    new_response_body = {
        "routes": [r.model_dump() for r in routes],
        "fallbackInfo": response_body.get("fallbackInfo"),
        "geocodingResults": response_body.get("geocodingResults"),
    }
    telemetry_data = telemetry_list[0].model_dump() if telemetry_list else {}

    await db.update_driver_state(
        pg_pool, route_id, new_driver_dict, telemetry_data, new_response_body,
    )

    # Invalidate Redis cache
    await redis_client.delete(route_cache_key(route_id))
    await redis_client.delete(telemetry_cache_key(route_id))

    return JSONResponse(
        status_code=200,
        content={
            "routeId": route_id,
            "driverState": new_driver_dict,
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )

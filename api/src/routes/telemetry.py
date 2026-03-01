"""Telemetry endpoint for route segment data."""

import json

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from src.config import settings
from src.middleware.errors import RouteNotFoundError
from src.services.cache import telemetry_cache_key
from src.storage import postgres as db

router = APIRouter()


@router.get("/v1/routes/{route_id}/telemetry")
async def get_route_telemetry(
    route_id: str,
    request: Request,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Get telemetry data for a route with pagination."""
    redis_client = request.app.state.redis
    pg_pool = request.app.state.pg_pool

    # Try Redis cache first
    cached = await redis_client.get(telemetry_cache_key(route_id))
    telemetry_data = None
    if cached:
        telemetry_data = json.loads(cached)
    else:
        # Check Postgres
        row = await db.get_route(pg_pool, route_id)
        if row is None:
            raise RouteNotFoundError(route_id)

        if row["status"] == "processing":
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "route_processing",
                        "message": "Route is still processing. Poll GET /v1/routes/{route_id} until status is 'complete'.",
                        "param": "route_id",
                        "doc_url": None,
                    }
                },
            )

        if row["status"] == "failed":
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "type": "routing_error",
                        "code": "route_failed",
                        "message": "Route computation failed.",
                        "param": "route_id",
                        "doc_url": None,
                    }
                },
            )

        telemetry_data = row["telemetry"]
        if telemetry_data is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "no_telemetry",
                        "message": "No telemetry data available for this route.",
                        "param": None,
                        "doc_url": None,
                    }
                },
            )

        # Cache for future requests
        await redis_client.setex(
            telemetry_cache_key(route_id), settings.cache_ttl, json.dumps(telemetry_data),
        )

    # Apply pagination to segments
    segments = telemetry_data.get("segments", [])
    total = len(segments)
    paginated = segments[cursor : cursor + limit]
    has_more = (cursor + limit) < total
    next_cursor = str(cursor + limit) if has_more else None

    response = dict(telemetry_data)
    response["segments"] = paginated
    response["hasMore"] = has_more
    response["nextCursor"] = next_cursor

    return JSONResponse(status_code=200, content=response)

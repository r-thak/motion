"""Background computation worker for the Stripe-style async path."""

import logging
from datetime import datetime, timezone

import asyncpg
import httpx
import redis.asyncio as redis_lib
from ulid import ULID

from src.config import Settings
from src.models.driver import DriverState
from src.models.request import ComputeRoutesRequest
from src.services.cache import route_cache_key, telemetry_cache_key
from src.services.enrichment import enrich_routes
from src.services.translator import translate_request, translate_response
from src.services.valhalla import compute_route
from src.services.webhooks import deliver_webhook
from src.services.zones import ZoneIndex
from src.storage import postgres as db

import json

logger = logging.getLogger(__name__)


async def compute_route_background(
    route_id: str,
    request_body: ComputeRoutesRequest,
    resolved_vehicle: dict,
    driver_state: DriverState,
    pg_pool: asyncpg.Pool,
    redis_client: redis_lib.Redis,
    http_client: httpx.AsyncClient,
    zone_index: ZoneIndex,
    settings: Settings,
) -> None:
    """
    Perform the full route computation pipeline in the background.
    Updates the route record in Postgres and Redis on completion or failure.
    Sends webhook if configured.
    """
    try:
        # 1. Translate request
        locations, costing_options, date_time, alternates = translate_request(
            request_body, resolved_vehicle
        )

        # 2. Call Valhalla
        valhalla_response = await compute_route(
            http_client,
            settings.valhalla_url,
            locations,
            costing_options,
            request_body.languageCode,
            date_time,
            alternates,
        )

        # 3. Translate response
        routes, all_step_points = translate_response(valhalla_response, route_id)

        # 4. Run enrichment
        telemetry_list = await enrich_routes(
            routes,
            all_step_points,
            resolved_vehicle,
            driver_state,
            request_body.departureTime,
            request_body.routingProfile,
            request_body.profileOverrides,
            zone_index,
            http_client,
            redis_client,
            settings,
        )

        # Set route ID on telemetry
        for t in telemetry_list:
            t.routeId = route_id

        # 5. Build response body
        response_body = {
            "routes": [r.model_dump() for r in routes],
            "fallbackInfo": None,
            "geocodingResults": None,
        }
        telemetry_data = telemetry_list[0].model_dump() if telemetry_list else {}

        # 6. Update Postgres
        await db.update_route_complete(pg_pool, route_id, response_body, telemetry_data)

        # 7. Update Redis cache
        from src.models.response import RouteResource
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

        # 8. Create event
        event_id = f"evt_{ULID()}"
        event_data = {
            "routeId": route_id,
            "status": "complete",
        }
        await db.create_event(pg_pool, event_id, "route.complete", route_id, event_data)

        # 9. Webhook
        if request_body.webhookUrl:
            event_payload = {
                "id": event_id,
                "type": "route.complete",
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "data": event_data,
            }
            await deliver_webhook(request_body.webhookUrl, event_payload, http_client, settings)

    except Exception as exc:
        logger.exception("Background route computation failed for %s: %s", route_id, exc)
        error = {"code": "computation_failed", "message": str(exc)}

        try:
            await db.update_route_failed(pg_pool, route_id, error)

            event_id = f"evt_{ULID()}"
            event_data = {
                "routeId": route_id,
                "status": "failed",
                "error": error,
            }
            await db.create_event(pg_pool, event_id, "route.failed", route_id, event_data)

            if request_body.webhookUrl:
                event_payload = {
                    "id": event_id,
                    "type": "route.failed",
                    "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "data": event_data,
                }
                await deliver_webhook(request_body.webhookUrl, event_payload, http_client, settings)
        except Exception as inner_exc:
            logger.exception("Failed to update route status for %s: %s", route_id, inner_exc)

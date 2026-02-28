"""Elevation lookups with Redis caching via USGS 3DEP EPQS."""

import asyncio
import logging

import httpx
import redis.asyncio as redis_lib

from src.config import Settings

logger = logging.getLogger(__name__)


async def get_elevation(
    lat: float,
    lng: float,
    http_client: httpx.AsyncClient,
    redis_client: redis_lib.Redis,
    settings: Settings,
) -> float | None:
    """
    Get elevation in meters for a coordinate.
    Returns None if data is unavailable (over water, API error).
    """
    # Round to 5 decimal places
    lat_r = round(lat, 5)
    lng_r = round(lng, 5)
    cache_key = f"elev:{lat_r:.5f}:{lng_r:.5f}"

    # Check Redis cache
    cached = await redis_client.get(cache_key)
    if cached is not None:
        value = float(cached)
        if value < settings.elevation_water_floor:
            return None
        return value

    # Call USGS EPQS
    try:
        response = await http_client.get(
            settings.epqs_base_url,
            params={
                "x": lng_r,
                "y": lat_r,
                "wkid": 4326,
                "units": "Meters",
                "includeDate": "false",
            },
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
        value = float(data["value"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Elevation lookup failed for (%s, %s): %s", lat_r, lng_r, exc)
        return None

    # Cache the value
    if value < settings.elevation_water_floor:
        # Cache the raw value so we don't re-query, but return None
        await redis_client.setex(cache_key, settings.cache_ttl, str(value))
        return None

    await redis_client.setex(cache_key, settings.cache_ttl, str(value))
    return value


async def get_elevations_batch(
    points: list[tuple[float, float]],
    http_client: httpx.AsyncClient,
    redis_client: redis_lib.Redis,
    settings: Settings,
) -> list[float | None]:
    """
    Get elevations for multiple points concurrently.
    Uses asyncio.gather with up to 30 concurrent requests.
    """
    semaphore = asyncio.Semaphore(30)

    async def _get_with_semaphore(lat: float, lng: float) -> float | None:
        async with semaphore:
            return await get_elevation(lat, lng, http_client, redis_client, settings)

    tasks = [_get_with_semaphore(lat, lng) for lat, lng in points]
    return await asyncio.gather(*tasks)

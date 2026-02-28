"""Pre-warm Redis elevation cache for Chicago metro area."""

import asyncio
import logging
import sys

import httpx

from src.config import settings
from src.services.elevation import get_elevation
from src.storage.redis import create_redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Chicago metro bounding box (approximate)
CHICAGO_BOUNDS = {
    "min_lat": 41.60,
    "max_lat": 42.10,
    "min_lng": -88.00,
    "max_lng": -87.50,
}

# Grid spacing in degrees (approximately 1km spacing at Chicago latitude)
GRID_STEP = 0.01


async def prewarm_elevation() -> None:
    """Pre-warm the elevation cache for the Chicago metro area."""
    redis_client = await create_redis(settings.redis_url)
    async with httpx.AsyncClient() as http_client:
        lat = CHICAGO_BOUNDS["min_lat"]
        points = []
        while lat <= CHICAGO_BOUNDS["max_lat"]:
            lng = CHICAGO_BOUNDS["min_lng"]
            while lng <= CHICAGO_BOUNDS["max_lng"]:
                points.append((lat, lng))
                lng += GRID_STEP
            lat += GRID_STEP

        logger.info("Pre-warming %d elevation points...", len(points))

        semaphore = asyncio.Semaphore(20)
        completed = 0
        errors = 0

        async def fetch_one(pt_lat: float, pt_lng: float) -> None:
            nonlocal completed, errors
            async with semaphore:
                try:
                    await get_elevation(pt_lat, pt_lng, http_client, redis_client, settings)
                    completed += 1
                except Exception:
                    errors += 1

                if (completed + errors) % 100 == 0:
                    logger.info("Progress: %d/%d (errors: %d)", completed + errors, len(points), errors)

        tasks = [fetch_one(lat, lng) for lat, lng in points]
        await asyncio.gather(*tasks)

        logger.info("Pre-warming complete: %d succeeded, %d errors out of %d total",
                     completed, errors, len(points))

    await redis_client.close()


if __name__ == "__main__":
    asyncio.run(prewarm_elevation())

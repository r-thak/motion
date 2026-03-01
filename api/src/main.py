"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.middleware.errors import register_error_handlers
from src.middleware.rate_limit import RateLimitMiddleware
from src.routes import google_compat, routes, telemetry
from src.services.zones import ZoneIndex
from src.storage.redis import create_redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    logger.info("Starting up Motion API...")

    # Create asyncpg connection pool
    app.state.pg_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
    )
    logger.info("PostgreSQL connection pool created")

    # Connect to Redis
    app.state.redis = await create_redis(settings.redis_url)
    logger.info("Redis connected")

    # Create HTTP client
    app.state.http_client = httpx.AsyncClient()
    logger.info("HTTP client created")

    # Load zone polygons
    zone_index = ZoneIndex()
    try:
        await zone_index.load(app.state.pg_pool)
        logger.info(
            "Zone index loaded: %d school zones, %d residential zones",
            len(zone_index.school_geoms),
            len(zone_index.residential_geoms),
        )
    except Exception as exc:
        logger.warning("Could not load zone polygons: %s", exc)
    app.state.zone_index = zone_index

    # Run Alembic migrations programmatically in a separate thread
    try:
        from alembic.config import Config
        from alembic import command
        import os
        import asyncio

        alembic_cfg = Config()
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        migrations_dir = os.path.join(base_dir, "migrations")
        alembic_cfg.set_main_option("script_location", migrations_dir)
        
        # Ensure sync URL for migrations (psycopg2)
        sync_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        alembic_cfg.set_main_option("sqlalchemy.url", sync_url)
        
        # Run synchronous command in current event loop's worker thread
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        logger.info("Alembic migrations applied")
    except Exception as exc:
        logger.warning("Could not run Alembic migrations (tables may already exist): %s", exc)

    yield

    # Shutdown
    logger.info("Shutting down Motion API...")
    await app.state.pg_pool.close()
    await app.state.redis.close()
    await app.state.http_client.aclose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Freight Router API",
    version="1.0.0",
    description="Drop-in replacement for Google Routes API with segment-level physics enrichment for heavy freight.",
    lifespan=lifespan,
)

# Register CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://demo.rthak.com", 
        "https://demo1.rthak.com",
        "https://demo2.rthak.com",
        "http://localhost:8000", 
        "http://localhost:8001", 
        "http://localhost:8002",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8001",
        "http://127.0.0.1:8002"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Route-Id"],
)

# Register error handlers
register_error_handlers(app)

# Register rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# Include routers
app.include_router(google_compat.router)
app.include_router(routes.router)
app.include_router(telemetry.router)



@app.get("/health")
async def health():
    return {"status": "ok"}

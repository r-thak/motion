"""
Motion API — Dev Server (Real Mode + Mock Mode)
=================================================
Starts the full API on http://localhost:8000.

Usage:
    python dev_server.py          # Mock mode (synthetic everything)
    python dev_server.py --real   # Real mode (real Valhalla + Redis + USGS elevation)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import polyline as polyline_lib
import uvicorn
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dev_server")


# ─────────────────────────────────────────────────────────────────────────────
# MOCK BACKENDS (used in mock mode)
# ─────────────────────────────────────────────────────────────────────────────

def _build_valhalla_response(locations, alternates=0):
    origin = (locations[0]["lat"], locations[0]["lon"])
    dest = (locations[-1]["lat"], locations[-1]["lon"])
    mid1 = ((origin[0]*2+dest[0])/3, (origin[1]*2+dest[1])/3)
    mid2 = ((origin[0]+dest[0]*2)/3, (origin[1]+dest[1]*2)/3)
    points = [origin, mid1, mid2, dest]
    shape_encoded = polyline_lib.encode(points, 6)
    dlat, dlng = abs(dest[0]-origin[0]), abs(dest[1]-origin[1])
    dist_km = max(((dlat*111)**2 + (dlng*85)**2)**0.5, 0.5)
    time_s = int(dist_km / 40 * 3600)
    maneuvers = [
        {"type":1,"instruction":"Head north.","length":dist_km*0.3,"time":int(time_s*0.3),"begin_shape_index":0,"end_shape_index":1},
        {"type":10,"instruction":"Turn right.","length":dist_km*0.4,"time":int(time_s*0.4),"begin_shape_index":1,"end_shape_index":2},
        {"type":15,"instruction":"Turn left onto destination.","length":dist_km*0.3,"time":int(time_s*0.3),"begin_shape_index":2,"end_shape_index":3},
    ]
    trip = {"locations":locations,"legs":[{"shape":shape_encoded,"summary":{"length":dist_km,"time":time_s},"maneuvers":maneuvers}],"summary":{"length":dist_km,"time":time_s},"status_message":"Found route","status":0,"units":"kilometers","language":"en-US"}
    response = {"trip": trip}
    if alternates > 0:
        alt_mid1 = (mid1[0]+0.002, mid1[1]-0.003)
        alt_mid2 = (mid2[0]-0.002, mid2[1]+0.003)
        alt_points = [origin, alt_mid1, alt_mid2, dest]
        alt_shape = polyline_lib.encode(alt_points, 6)
        alt_dist, alt_time = dist_km*1.15, int(time_s*1.1)
        alt_maneuvers = [
            {"type":1,"instruction":"Head east (alt).","length":alt_dist*0.5,"time":int(alt_time*0.5),"begin_shape_index":0,"end_shape_index":1},
            {"type":9,"instruction":"Slight right.","length":alt_dist*0.25,"time":int(alt_time*0.25),"begin_shape_index":1,"end_shape_index":2},
            {"type":8,"instruction":"Continue.","length":alt_dist*0.25,"time":int(alt_time*0.25),"begin_shape_index":2,"end_shape_index":3},
        ]
        alt_trip = {"locations":locations,"legs":[{"shape":alt_shape,"summary":{"length":alt_dist,"time":alt_time},"maneuvers":alt_maneuvers}],"summary":{"length":alt_dist,"time":alt_time},"status_message":"Found route","status":0,"units":"kilometers","language":"en-US"}
        response["alternates"] = [{"trip": alt_trip}]
    return response


class FakeRedis:
    def __init__(self):
        self._store = {}
    async def get(self, key): return self._store.get(key)
    async def set(self, key, value): self._store[key] = value
    async def setex(self, key, ttl, value): self._store[key] = value
    async def delete(self, *keys):
        for k in keys: self._store.pop(k, None)
    async def close(self): pass
    def pipeline(self): return FakePipeline()


class FakePipeline:
    def __init__(self): self._results = []
    def zremrangebyscore(self, *a): self._results.append(0); return self
    def zadd(self, *a, **kw): self._results.append(1); return self
    def zcard(self, *a): self._results.append(1); return self
    def expire(self, *a): self._results.append(True); return self
    async def execute(self): return self._results


class FakeRecord(dict):
    def __getitem__(self, key): return dict.__getitem__(self, key)
    def get(self, key, default=None): return dict.get(self, key, default)


class FakePgPool:
    def __init__(self):
        self._routes, self._events, self._idempotency = {}, [], {}

    async def execute(self, query, *args):
        q = query.strip().upper()
        if q.startswith("INSERT INTO ROUTES"):
            self._routes[args[0]] = {"id":args[0],"object":"route","status":args[1],"request_body":args[2],"request_hash":args[3],"response_body":args[4],"telemetry":args[5],"vehicle_spec":args[6],"departure_time":args[7],"webhook_url":args[8],"error":None}
            return "INSERT 0 1"
        elif q.startswith("UPDATE ROUTES SET STATUS='COMPLETE'"):
            if args[0] in self._routes: self._routes[args[0]].update(status="complete",response_body=args[1],telemetry=args[2])
            return "UPDATE 1"
        elif q.startswith("UPDATE ROUTES SET STATUS='FAILED'"):
            if args[0] in self._routes: self._routes[args[0]].update(status="failed",error=args[1])
            return "UPDATE 1"

        elif q.startswith("DELETE FROM ROUTES"):
            if args[0] in self._routes: del self._routes[args[0]]; return "DELETE 1"
            return "DELETE 0"
        elif q.startswith("INSERT INTO EVENTS"):
            self._events.append({"id":args[0],"type":args[1],"route_id":args[2],"data":args[3]})
            return "INSERT 0 1"
        elif q.startswith("INSERT INTO IDEMPOTENCY_KEYS"):
            if args[0] in self._idempotency: return "INSERT 0 0"
            self._idempotency[args[0]] = {"key_hash":args[0],"request_hash":args[1],"response_body":None,"status_code":None}
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, query, *args):
        q = query.strip().upper()
        if "FROM ROUTES" in q:
            row = self._routes.get(args[0])
            if not row: return None
            return FakeRecord({"id":row["id"],"object":row.get("object","route"),"status":row["status"],"request_body":row.get("request_body"),"response_body":row.get("response_body"),"telemetry":row.get("telemetry"),"vehicle_spec":row.get("vehicle_spec"),"error":row.get("error"),"created_at":datetime.now(timezone.utc),"expires_at":datetime.now(timezone.utc)+timedelta(hours=6),"webhook_url":row.get("webhook_url")})
        elif "FROM IDEMPOTENCY_KEYS" in q:
            entry = self._idempotency.get(args[0])
            if entry and entry.get("response_body") is not None: return FakeRecord(entry)
            return None
        return None

    async def fetch(self, query, *args): return []
    async def close(self): pass


async def _fake_elevations_batch(points, http_client, redis_client, settings):
    import random
    return [180.0 + random.uniform(-15, 30) + i*2.0 for i in range(len(points))]


# ─────────────────────────────────────────────────────────────────────────────
# REAL VALHALLA (via pyvalhalla in-process)
# ─────────────────────────────────────────────────────────────────────────────

def create_valhalla_actor():
    """Create a pyvalhalla Actor from built tiles."""
    import valhalla as pv

    data_dir = Path(__file__).parent.parent / "valhalla_data"
    config_file = data_dir / "valhalla.json"
    tile_dir = data_dir / "valhalla_tiles"

    if config_file.exists():
        config_path = config_file
    elif tile_dir.exists():
        config = pv.get_config(tile_dir=str(tile_dir))
        config["mjolnir"]["tile_dir"] = str(tile_dir)
        config_path = config
    else:
        raise FileNotFoundError(
            f"No Valhalla tiles found at {tile_dir}. "
            f"Run: python build_tiles.py"
        )

    actor = pv.Actor(config_path)
    logger.info("Valhalla Actor loaded from %s", tile_dir)
    return actor


# ─────────────────────────────────────────────────────────────────────────────
# Build and run the app
# ─────────────────────────────────────────────────────────────────────────────

def build_app(real_mode: bool = False) -> FastAPI:
    from src.middleware.errors import register_error_handlers
    from src.middleware.rate_limit import RateLimitMiddleware
    from src.routes import google_compat, routes, telemetry
    from src.services.zones import ZoneIndex

    fake_pg = FakePgPool()

    # Pre-create the Valhalla actor if in real mode (so it loads once at startup)
    valhalla_actor = None
    if real_mode:
        valhalla_actor = create_valhalla_actor()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pg_pool = fake_pg
        app.state.http_client = httpx.AsyncClient()
        app.state.zone_index = ZoneIndex()

        if real_mode:
            import redis.asyncio as aioredis
            app.state.redis = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
            try:
                await app.state.redis.ping()
                logger.info("✅ Redis connected (real)")
            except Exception as e:
                logger.warning("⚠️  Redis connection failed, falling back to in-memory: %s", e)
                app.state.redis = FakeRedis()

            app.state.valhalla_actor = valhalla_actor
            logger.info("🚀 Dev server started in REAL mode")
            logger.info("   Valhalla: real (pyvalhalla in-process)")
            logger.info("   Redis:    real")
            logger.info("   Postgres: in-memory (ditched per request)")
            logger.info("   Elevation: real USGS 3DEP API")
        else:
            app.state.redis = FakeRedis()
            logger.info("🚀 Dev server started in MOCK mode")
            logger.info("   Valhalla: mocked (synthetic routes)")
            logger.info("   Redis:    in-memory dict")
            logger.info("   Postgres: in-memory dict")
            logger.info("   Elevation: synthetic data")

        yield
        await app.state.http_client.aclose()
        if hasattr(app.state.redis, 'close'):
            await app.state.redis.close()

    mode_label = "Real" if real_mode else "Mock"
    app = FastAPI(
        title=f"Motion Freight Router API ({mode_label} Mode)",
        version="1.0.0-dev",
        description="Drop-in replacement for Google Routes API with segment-level physics enrichment for heavy freight.",
        lifespan=lifespan,
    )
    register_error_handlers(app)

    # CORS — allow the browser demo to call the API
    from fastapi.middleware.cors import CORSMiddleware
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

    app.add_middleware(RateLimitMiddleware)
    app.include_router(google_compat.router)
    app.include_router(routes.router)
    app.include_router(telemetry.router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "real" if real_mode else "mock"}

    return app


def run_real_mode():
    """Run with real Valhalla (pyvalhalla in-process), real Redis, real Elevation."""
    # The real Valhalla integration: replace the HTTP-based compute_route
    # with a direct in-process pyvalhalla call.
    actor = create_valhalla_actor()

    async def real_compute_route(http_client, valhalla_url, locations, costing_options, language, date_time, alternates=0):
        """Call Valhalla directly in-process via pyvalhalla Actor."""
        body = {
            "locations": locations,
            "costing": "truck",
            "costing_options": {"truck": costing_options},
            "directions_options": {"units": "kilometers", "language": language},
            "alternates": alternates,
        }
        if date_time is not None:
            body["date_time"] = date_time

        # pyvalhalla Actor.route() accepts a dict or JSON string and returns a dict
        import json
        with open("/tmp/valhalla_body.log", "a") as f:
            f.write(json.dumps(body) + "\n")
            
        try:
            result = actor.route(body)
            if isinstance(result, str):
                return json.loads(result)
            return result
        except Exception as exc:
            from src.middleware.errors import RoutingError
            err_msg = str(exc)
            if "No path could be found" in err_msg or "170" in err_msg:
                raise RoutingError(code="no_route_found", message="No route could be found between the specified locations.")
            raise RoutingError(code="routing_error", message=f"Routing error: {err_msg}")

    with patch("src.routes.google_compat.compute_route", side_effect=real_compute_route), \
         patch("src.routes.routes.compute_route", side_effect=real_compute_route), \
         patch("src.services.valhalla.compute_route", side_effect=real_compute_route):
        app = build_app(real_mode=True)
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


def run_mock_mode():
    """Run with all backends mocked."""
    async def _fake_valhalla(http_client, valhalla_url, locations, costing_options, language, date_time, alternates=0):
        return _build_valhalla_response(locations, alternates)

    with patch("src.routes.google_compat.compute_route", side_effect=_fake_valhalla), \
         patch("src.routes.routes.compute_route", side_effect=_fake_valhalla), \
         patch("src.services.valhalla.compute_route", side_effect=_fake_valhalla), \
         patch("src.services.enrichment.get_elevations_batch", side_effect=_fake_elevations_batch), \
         patch("src.services.elevation.get_elevations_batch", side_effect=_fake_elevations_batch):
        app = build_app(real_mode=False)
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Motion API Dev Server")
    parser.add_argument("--real", action="store_true", help="Run with real Valhalla + Redis + USGS elevation (requires built tiles and running Redis)")
    args = parser.parse_args()

    mode = "REAL" if args.real else "MOCK"
    logger.info("=" * 60)
    logger.info("  Motion API Dev Server (%s MODE)", mode)
    logger.info("  http://localhost:8000")
    logger.info("  http://localhost:8000/docs  (Swagger UI)")
    logger.info("  http://localhost:8000/redoc (ReDoc)")
    logger.info("=" * 60)

    if args.real:
        run_real_mode()
    else:
        run_mock_mode()

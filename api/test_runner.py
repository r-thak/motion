"""
Motion Freight Router API — End-to-End Test Runner
===================================================
Exercises every endpoint with mocked external services (Valhalla, Postgres, Redis).
Uses FastAPI TestClient so no live containers are needed.

Run:  python -m pytest test_runner.py -v --tb=short
"""

import asyncio
import json
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import polyline as polyline_lib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── Fake Valhalla response ──────────────────────────────────────────────────
# A small but realistic Valhalla /route JSON response for a ~2-step trip
# from downtown Chicago (41.8781, -87.6298) to a point ~2 km north.

ORIGIN = (41.8781, -87.6298)
DESTINATION = (41.8950, -87.6240)
MIDPOINT = (41.8866, -87.6269)

_shape_points = [ORIGIN, MIDPOINT, DESTINATION]
_shape_encoded = polyline_lib.encode(_shape_points, 6)

FAKE_VALHALLA_RESPONSE = {
    "trip": {
        "locations": [
            {"lat": ORIGIN[0], "lon": ORIGIN[1], "type": "break"},
            {"lat": DESTINATION[0], "lon": DESTINATION[1], "type": "break"},
        ],
        "legs": [
            {
                "shape": _shape_encoded,
                "summary": {"length": 2.0, "time": 180},
                "maneuvers": [
                    {
                        "type": 1,
                        "instruction": "Head north on Michigan Avenue.",
                        "length": 1.0,
                        "time": 90,
                        "begin_shape_index": 0,
                        "end_shape_index": 1,
                    },
                    {
                        "type": 10,
                        "instruction": "Turn right on Division Street.",
                        "length": 1.0,
                        "time": 90,
                        "begin_shape_index": 1,
                        "end_shape_index": 2,
                    },
                ],
            }
        ],
        "summary": {"length": 2.0, "time": 180},
        "status_message": "Found route between points",
        "status": 0,
        "units": "kilometers",
        "language": "en-US",
    }
}


# ── Fake Redis (in-memory dict) ────────────────────────────────────────────

class FakeRedis:
    """Minimal async Redis stand-in backed by a plain dict."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str):
        self._store[key] = value

    async def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    async def delete(self, *keys: str):
        for k in keys:
            self._store.pop(k, None)

    async def close(self):
        pass

    def pipeline(self):
        return FakePipeline(self)

    async def zremrangebyscore(self, key, lo, hi):
        return 0

    async def zadd(self, key, mapping):
        return 1

    async def zcard(self, key):
        return 1

    async def expire(self, key, ttl):
        return True


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._calls: list = []

    def zremrangebyscore(self, key, lo, hi):
        self._calls.append(0)
        return self

    def zadd(self, key, mapping):
        self._calls.append(1)
        return self

    def zcard(self, key):
        self._calls.append(1)  # 1 request in window
        return self

    def expire(self, key, ttl):
        self._calls.append(True)
        return self

    async def execute(self):
        return self._calls


# ── Fake Postgres Pool ──────────────────────────────────────────────────────

class FakePgPool:
    """Minimal async pool stand-in that stores routes in a dict."""

    def __init__(self):
        self._routes: dict[str, dict] = {}
        self._events: list[dict] = []
        self._idempotency: dict[bytes, dict] = {}

    async def execute(self, query: str, *args):
        q = query.strip().upper()
        if q.startswith("INSERT INTO ROUTES"):
            route_id = args[0]
            self._routes[route_id] = {
                "id": route_id,
                "object": "route",
                "status": args[1],
                "request_body": args[2],
                "request_hash": args[3],
                "response_body": args[4],
                "telemetry": args[5],
                "vehicle_spec": args[6],
                "departure_time": args[7],
                "webhook_url": args[8],
                "error": None,
                "created_at": "2026-03-01T00:00:00Z",
                "expires_at": "2026-04-01T00:00:00Z",
            }
            return "INSERT 0 1"
        elif q.startswith("UPDATE ROUTES SET STATUS='COMPLETE'"):
            route_id = args[0]
            if route_id in self._routes:
                self._routes[route_id]["status"] = "complete"
                self._routes[route_id]["response_body"] = args[1]
                self._routes[route_id]["telemetry"] = args[2]
            return "UPDATE 1"
        elif q.startswith("UPDATE ROUTES SET STATUS='FAILED'"):
            route_id = args[0]
            if route_id in self._routes:
                self._routes[route_id]["status"] = "failed"
                self._routes[route_id]["error"] = args[1]
            return "UPDATE 1"
        elif q.startswith("UPDATE ROUTES SET TELEMETRY"):
            route_id = args[0]
            if route_id in self._routes:
                self._routes[route_id]["telemetry"] = args[1]
                self._routes[route_id]["response_body"] = args[2]
            return "UPDATE 1"
        elif q.startswith("DELETE FROM ROUTES"):
            route_id = args[0]
            if route_id in self._routes:
                del self._routes[route_id]
                return "DELETE 1"
            return "DELETE 0"
        elif q.startswith("INSERT INTO EVENTS"):
            self._events.append({"id": args[0], "type": args[1], "route_id": args[2], "data": args[3]})
            return "INSERT 0 1"
        elif q.startswith("INSERT INTO IDEMPOTENCY_KEYS"):
            key_hash = args[0]
            if key_hash in self._idempotency:
                return "INSERT 0 0"
            self._idempotency[key_hash] = {"key_hash": key_hash, "request_hash": args[1], "response_body": None, "status_code": None}
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, query: str, *args):
        q = query.strip().upper()
        if "FROM ROUTES" in q:
            route_id = args[0]
            row = self._routes.get(route_id)
            if row is None:
                return None
            # Return as a dict-like asyncpg Record mock
            from datetime import datetime, timezone, timedelta
            return FakeRecord({
                "id": row["id"],
                "object": row.get("object", "route"),
                "status": row["status"],
                "request_body": row.get("request_body"),
                "response_body": row.get("response_body"),
                "telemetry": row.get("telemetry"),
                "vehicle_spec": row.get("vehicle_spec"),
                "error": row.get("error"),
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=6),
                "webhook_url": row.get("webhook_url"),
            })
        elif "FROM IDEMPOTENCY_KEYS" in q:
            key_hash = args[0]
            entry = self._idempotency.get(key_hash)
            if entry and entry.get("response_body") is not None:
                return FakeRecord(entry)
            return None
        return None

    async def fetch(self, query: str, *args):
        if "zone_polygons" in query.lower():
            return []
        return []

    async def close(self):
        pass


class FakeRecord(dict):
    """Dict-like object that also supports attribute-style access like asyncpg Record."""

    def __init__(self, data: dict):
        super().__init__(data)

    def __getitem__(self, key):
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        return dict.get(self, key, default)


# ── Build the test app ──────────────────────────────────────────────────────

def build_test_app() -> FastAPI:
    """Build a FastAPI app with all external I/O mocked out."""
    from src.middleware.errors import register_error_handlers
    from src.middleware.rate_limit import RateLimitMiddleware
    from src.routes import google_compat, routes, telemetry

    fake_redis = FakeRedis()
    fake_pg = FakePgPool()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pg_pool = fake_pg
        app.state.redis = fake_redis
        app.state.http_client = httpx.AsyncClient()

        from src.services.zones import ZoneIndex
        zone_index = ZoneIndex()
        app.state.zone_index = zone_index

        yield

        await app.state.http_client.aclose()

    app = FastAPI(title="Motion Test Runner", lifespan=lifespan)
    register_error_handlers(app)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(google_compat.router)
    app.include_router(routes.router)
    app.include_router(telemetry.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    return build_test_app()


@pytest.fixture(scope="module")
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Payloads ────────────────────────────────────────────────────────────────

COMPUTE_ROUTES_PAYLOAD = {
    "origin": {
        "location": {
            "latLng": {"latitude": ORIGIN[0], "longitude": ORIGIN[1]}
        }
    },
    "destination": {
        "location": {
            "latLng": {"latitude": DESTINATION[0], "longitude": DESTINATION[1]}
        }
    },
    "travelMode": "DRIVE",
    "routingPreference": "TRAFFIC_UNAWARE",
    "vehicleSpec": {"type": "SEMI_TRAILER"},
    "routingProfile": "balanced",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fake_compute_route(http_client, valhalla_url, locations, costing_options, language, date_time, alternates=0):
    """Fake Valhalla compute_route that returns our canned response."""
    async def _inner(*a, **kw):
        return FAKE_VALHALLA_RESPONSE
    return _inner(*[http_client, valhalla_url, locations, costing_options, language, date_time], **{"alternates": alternates})


async def _fake_elevations_batch(points, http_client, redis_client, settings):
    """Return varying elevations to test grade computation."""
    return [200.0 + i * 5.0 for i in range(len(points))]


from contextlib import contextmanager

@contextmanager
def _patch_valhalla(fake_response=None):
    """Patch compute_route at every import site."""
    response = fake_response or FAKE_VALHALLA_RESPONSE
    async def _fake(http_client, valhalla_url, locations, costing_options, language, date_time, alternates=0):
        return response
    with patch("src.routes.google_compat.compute_route", side_effect=_fake), \
         patch("src.routes.routes.compute_route", side_effect=_fake), \
         patch("src.services.valhalla.compute_route", side_effect=_fake):
        yield


@contextmanager
def _patch_elevation():
    """Patch elevation lookups at every import site."""
    with patch("src.services.enrichment.get_elevations_batch", side_effect=_fake_elevations_batch), \
         patch("src.services.elevation.get_elevations_batch", side_effect=_fake_elevations_batch):
        yield


# ═══════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestGoogleCompatEndpoint:
    """Tests for POST /directions/v2:computeRoutes (Google-compatible sync endpoint)."""

    def test_compute_routes_success(self, client):
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=COMPUTE_ROUTES_PAYLOAD)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()

        # Must have routes
        assert "routes" in body
        assert len(body["routes"]) >= 1

        route = body["routes"][0]
        assert route["distanceMeters"] == 2000
        assert route["duration"] == "180s"
        assert len(route["legs"]) == 1

        leg = route["legs"][0]
        assert len(leg["steps"]) == 2

        # Verify enrichment data is present on steps
        for step in leg["steps"]:
            enrichment = step["enrichment"]
            assert enrichment is not None
            assert "gradePercent" in enrichment
            assert "curvatureDegreesPerKm" in enrichment
            assert "fuelBurnLiters" in enrichment
            assert "zoneFlags" in enrichment

        # Check X-Route-Id header
        assert "x-route-id" in resp.headers
        route_id = resp.headers["x-route-id"]
        assert route_id.startswith("route_")

        print(f"\n✅ Google-compat route computed successfully!")
        print(f"   Route ID:        {route_id}")
        print(f"   Distance:        {route['distanceMeters']}m")
        print(f"   Duration:        {route['duration']}")
        print(f"   Steps:           {len(leg['steps'])}")
        for i, step in enumerate(leg["steps"]):
            e = step["enrichment"]
            print(f"   Step {i}: grade={e['gradePercent']}%, fuel={e['fuelBurnLiters']}L")

    def test_compute_routes_missing_location(self, client):
        payload = {
            "origin": {"placeId": "ChIJ7cv00DwsDogRAMDACa2m4K8"},
            "destination": {
                "location": {
                    "latLng": {"latitude": DESTINATION[0], "longitude": DESTINATION[1]}
                }
            },
        }
        resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "unsupported_waypoint_type"
        print(f"\n✅ Validation correctly rejects placeId waypoints: {body['error']['message']}")

    def test_compute_routes_no_origin_location(self, client):
        payload = {
            "origin": {},
            "destination": {
                "location": {
                    "latLng": {"latitude": DESTINATION[0], "longitude": DESTINATION[1]}
                }
            },
        }
        resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "missing_location"
        print(f"\n✅ Validation correctly rejects missing location: {body['error']['message']}")


class TestStripeStyleRouteEndpoints:
    """Tests for POST/GET/DELETE /v1/routes (Stripe-style stateful endpoint)."""

    def test_create_route_sync_fast_path(self, client):
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/v1/routes", json=COMPUTE_ROUTES_PAYLOAD)

        # Fast-path should return 200 (completed inline)
        assert resp.status_code in (200, 202), f"Expected 200|202, got {resp.status_code}: {resp.text}"
        body = resp.json()

        assert "id" in body
        assert body["object"] == "route"
        assert body["status"] in ("complete", "processing")
        route_id = body["id"]

        print(f"\n✅ Stripe-style route created!")
        print(f"   Route ID: {route_id}")
        print(f"   Status:   {body['status']}")
        if body["status"] == "complete" and body.get("routes"):
            print(f"   Routes:   {len(body['routes'])} route(s)")

        return route_id

    def test_get_route_by_id(self, client):
        # First create a route
        with _patch_valhalla(), _patch_elevation():
            create_resp = client.post("/v1/routes", json=COMPUTE_ROUTES_PAYLOAD)
        assert create_resp.status_code in (200, 202)
        route_id = create_resp.json()["id"]

        # Now retrieve it
        resp = client.get(f"/v1/routes/{route_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == route_id
        print(f"\n✅ Route retrieved by ID: {route_id}, status={body['status']}")

    def test_get_nonexistent_route(self, client):
        resp = client.get("/v1/routes/route_NONEXISTENT123")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "route_not_found"
        print(f"\n✅ Non-existent route correctly returns 404")

    def test_delete_route(self, client):
        # Create
        with _patch_valhalla(), _patch_elevation():
            create_resp = client.post("/v1/routes", json=COMPUTE_ROUTES_PAYLOAD)
        route_id = create_resp.json()["id"]

        # Delete
        resp = client.delete(f"/v1/routes/{route_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] is True
        print(f"\n✅ Route deleted: {route_id}")

        # Confirm it's gone
        resp2 = client.get(f"/v1/routes/{route_id}")
        assert resp2.status_code == 404
        print(f"   Confirmed route is gone after deletion")

    def test_delete_nonexistent_route(self, client):
        resp = client.delete("/v1/routes/route_GHOST999")
        assert resp.status_code == 404
        print(f"\n✅ Deleting non-existent route correctly returns 404")


class TestTelemetryEndpoint:
    """Tests for GET /v1/routes/{route_id}/telemetry."""

    def test_telemetry_for_complete_route(self, client):
        # Create route first
        with _patch_valhalla(), _patch_elevation():
            create_resp = client.post("/directions/v2:computeRoutes", json=COMPUTE_ROUTES_PAYLOAD)
        route_id = create_resp.headers["x-route-id"]

        # Get telemetry
        resp = client.get(f"/v1/routes/{route_id}/telemetry")
        assert resp.status_code == 200
        body = resp.json()

        assert "segments" in body
        assert "summary" in body
        assert "routeId" in body

        summary = body["summary"]
        print(f"\n✅ Telemetry retrieved for route {route_id}")
        print(f"   Total fuel burn:  {summary.get('totalFuelBurnLiters')}L")
        print(f"   Grade gain:       {summary.get('totalGradeGainMeters')}m")
        print(f"   Grade loss:       {summary.get('totalGradeLossMeters')}m")
        print(f"   Max grade:        {summary.get('maxGradePercent')}%")
        print(f"   Avg curvature:    {summary.get('averageCurvatureDegreesPerKm')}°/km")
        print(f"   Segments:         {len(body['segments'])}")

    def test_telemetry_pagination(self, client):
        with _patch_valhalla(), _patch_elevation():
            create_resp = client.post("/directions/v2:computeRoutes", json=COMPUTE_ROUTES_PAYLOAD)
        route_id = create_resp.headers["x-route-id"]

        # Request with limit=1 to test pagination
        resp = client.get(f"/v1/routes/{route_id}/telemetry?limit=1&cursor=0")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["segments"]) <= 1
        print(f"\n✅ Telemetry pagination works: hasMore={body.get('hasMore')}, nextCursor={body.get('nextCursor')}")

    def test_telemetry_nonexistent_route(self, client):
        resp = client.get("/v1/routes/route_NOPE/telemetry")
        assert resp.status_code == 404
        print(f"\n✅ Telemetry for non-existent route correctly returns 404")


class TestVehiclePresets:
    """Tests for vehicle spec resolution."""

    def test_semi_trailer_preset(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["vehicleSpec"] = {"type": "SEMI_TRAILER"}
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ SEMI_TRAILER preset accepted")

    def test_box_truck_preset(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["vehicleSpec"] = {"type": "BOX_TRUCK"}
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ BOX_TRUCK preset accepted")

    def test_custom_overrides(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["vehicleSpec"] = {"type": "SEMI_TRAILER", "grossWeightKg": 44000.0}
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ Custom vehicle weight override accepted")


class TestRoutingProfiles:
    """Tests for different routing profiles and profile overrides."""

    @pytest.mark.parametrize("profile", ["balanced", "fuel_optimal", "time_optimal"])
    def test_routing_profile(self, client, profile):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["routingProfile"] = profile
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ Routing profile '{profile}' accepted")

    def test_profile_overrides(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["profileOverrides"] = {"fuelWeight": 3.0, "zoneWeight": 0.1}
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ Profile overrides accepted")


class TestRouteModifiers:
    """Tests for Google-compatible route modifiers."""

    def test_avoid_tolls(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["routeModifiers"] = {"avoidTolls": True}
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ avoidTolls modifier accepted")

    def test_avoid_highways(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["routeModifiers"] = {"avoidHighways": True}
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)
        assert resp.status_code == 200
        print(f"\n✅ avoidHighways modifier accepted")


class TestAlternativeRoutes:
    """Tests for alternative route computation."""

    def test_compute_alternative_routes(self, client):
        payload = dict(COMPUTE_ROUTES_PAYLOAD)
        payload["computeAlternativeRoutes"] = True

        # Fake Valhalla response with alternates
        alt_response = dict(FAKE_VALHALLA_RESPONSE)
        alt_response["alternates"] = [{"trip": FAKE_VALHALLA_RESPONSE["trip"]}]

        with _patch_valhalla(fake_response=alt_response), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["routes"]) == 2, f"Expected 2 routes, got {len(body['routes'])}"
        print(f"\n✅ Alternative routes computed: {len(body['routes'])} routes")


class TestEnrichmentData:
    """Tests verifying specific enrichment data quality."""

    def test_fuel_burn_positive(self, client):
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=COMPUTE_ROUTES_PAYLOAD)
        assert resp.status_code == 200

        for step in resp.json()["routes"][0]["legs"][0]["steps"]:
            fb = step["enrichment"]["fuelBurnLiters"]
            assert fb is not None and fb > 0, f"Fuel burn should be positive, got {fb}"
        print(f"\n✅ All steps have positive fuel burn values")

    def test_curvature_non_negative(self, client):
        with _patch_valhalla(), _patch_elevation():
            resp = client.post("/directions/v2:computeRoutes", json=COMPUTE_ROUTES_PAYLOAD)
        assert resp.status_code == 200

        for step in resp.json()["routes"][0]["legs"][0]["steps"]:
            curv = step["enrichment"]["curvatureDegreesPerKm"]
            assert curv is not None and curv >= 0, f"Curvature should be non-negative, got {curv}"
        print(f"\n✅ All steps have non-negative curvature values")


# ═══════════════════════════════════════════════════════════════════════════
# Run directly
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])

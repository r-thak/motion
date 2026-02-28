"""Integration tests for the Google-compatible endpoint."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def valhalla_response():
    with open(FIXTURES_DIR / "valhalla_response.json") as f:
        return json.load(f)


@pytest.fixture
def simple_request():
    with open(FIXTURES_DIR / "sample_requests.json") as f:
        requests = json.load(f)
    return requests[0]["body"]


@pytest.fixture
def client(valhalla_response):
    """Create a test client with all external services mocked."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_redis.delete = AsyncMock()
    pipe = AsyncMock()
    pipe.execute = AsyncMock(return_value=[None, None, 1, None])
    mock_redis.pipeline = MagicMock(return_value=pipe)

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value="INSERT 0 1")
    mock_pool.fetchrow = AsyncMock(return_value=None)

    from src.services.zones import ZoneIndex

    with patch("src.services.valhalla.compute_route", new_callable=AsyncMock) as mock_valhalla, \
         patch("src.services.elevation.get_elevation", new_callable=AsyncMock) as mock_elev:

        mock_valhalla.return_value = valhalla_response
        mock_elev.return_value = 180.0

        from src.main import app
        app.state.pg_pool = mock_pool
        app.state.redis = mock_redis
        app.state.http_client = AsyncMock()
        app.state.zone_index = ZoneIndex()

        yield TestClient(app, raise_server_exceptions=False)


class TestGoogleEndpoint:
    def test_google_endpoint_returns_google_shape(self, client, simple_request):
        """Top-level keys are exactly routes, fallbackInfo, geocodingResults."""
        response = client.post("/directions/v2:computeRoutes", json=simple_request)
        assert response.status_code == 200
        data = response.json()
        assert "routes" in data
        assert "fallbackInfo" in data
        assert "geocodingResults" in data
        # Must NOT have Stripe-style fields
        assert "id" not in data
        assert "status" not in data
        assert "object" not in data

    def test_google_endpoint_route_has_required_fields(self, client, simple_request):
        """routes[0] has distanceMeters, duration, staticDuration, polyline, legs, routeLabels, viewport."""
        response = client.post("/directions/v2:computeRoutes", json=simple_request)
        data = response.json()
        route = data["routes"][0]

        assert "distanceMeters" in route
        assert "duration" in route
        assert "staticDuration" in route
        assert "polyline" in route
        assert "legs" in route
        assert "routeLabels" in route
        assert "viewport" in route

    def test_google_endpoint_leg_has_required_fields(self, client, simple_request):
        """legs[0] has distanceMeters, duration, staticDuration, polyline, startLocation, endLocation, steps."""
        response = client.post("/directions/v2:computeRoutes", json=simple_request)
        data = response.json()
        leg = data["routes"][0]["legs"][0]

        assert "distanceMeters" in leg
        assert "duration" in leg
        assert "staticDuration" in leg
        assert "polyline" in leg
        assert "startLocation" in leg
        assert "endLocation" in leg
        assert "steps" in leg

    def test_google_endpoint_step_has_enrichment(self, client, simple_request):
        """steps[0] has enrichment object."""
        response = client.post("/directions/v2:computeRoutes", json=simple_request)
        data = response.json()
        step = data["routes"][0]["legs"][0]["steps"][0]

        assert "enrichment" in step
        assert isinstance(step["enrichment"], dict)

    def test_google_endpoint_returns_route_id_header(self, client, simple_request):
        """X-Route-Id response header is present and starts with 'route_'."""
        response = client.post("/directions/v2:computeRoutes", json=simple_request)
        assert response.status_code == 200
        route_id = response.headers.get("X-Route-Id")
        assert route_id is not None
        assert route_id.startswith("route_")

    def test_google_endpoint_route_accessible_via_v1(self, client, simple_request):
        """POST to google endpoint, GET from /v1/routes/{id} using X-Route-Id."""
        # POST to Google endpoint
        response = client.post("/directions/v2:computeRoutes", json=simple_request)
        assert response.status_code == 200
        route_id = response.headers["X-Route-Id"]

        # Mock Redis to return cached route for GET
        # The GET will check Redis first, then Postgres
        # Since we just created it, let's mock the Redis response
        from src.main import app
        old_get = app.state.redis.get

        async def mock_get(key):
            if key.startswith("route:"):
                return json.dumps({
                    "id": route_id,
                    "object": "route",
                    "status": "complete",
                    "createdAt": "2026-02-28T22:00:00Z",
                    "routes": response.json()["routes"],
                    "fallbackInfo": None,
                    "geocodingResults": None,
                    "warnings": [],
                    "error": None,
                })
            return None

        app.state.redis.get = mock_get

        # GET from Stripe endpoint
        get_response = client.get(f"/v1/routes/{route_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == route_id

        # Restore
        app.state.redis.get = old_get

    def test_google_endpoint_accepts_field_mask_header(self, client, simple_request):
        """POST with X-Goog-FieldMask header is not rejected."""
        response = client.post(
            "/directions/v2:computeRoutes",
            json=simple_request,
            headers={"X-Goog-FieldMask": "routes.duration,routes.distanceMeters"},
        )
        assert response.status_code == 200

    def test_google_endpoint_rejects_placeid_waypoint(self, client):
        """POST with origin.placeId returns error with code: unsupported_waypoint_type."""
        request = {
            "origin": {"placeId": "ChIJrTLr-GyuEmsRBfy61i59si0"},
            "destination": {
                "location": {
                    "latLng": {"latitude": 41.882702, "longitude": -87.623520}
                }
            },
        }
        response = client.post("/directions/v2:computeRoutes", json=request)
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "unsupported_waypoint_type"

    def test_google_endpoint_alternative_routes(self, client, valhalla_response):
        """POST with computeAlternativeRoutes: true returns multiple routes."""
        # Mock Valhalla to return alternates
        valhalla_with_alt = dict(valhalla_response)
        valhalla_with_alt["alternates"] = [{"trip": valhalla_response["trip"]}]

        with patch("src.routes.google_compat.compute_route", new_callable=AsyncMock) as mock_val, \
             patch("src.services.elevation.get_elevation", new_callable=AsyncMock) as mock_elev:
            mock_val.return_value = valhalla_with_alt
            mock_elev.return_value = 180.0

            request = {
                "origin": {
                    "location": {"latLng": {"latitude": 41.878876, "longitude": -87.635918}}
                },
                "destination": {
                    "location": {"latLng": {"latitude": 41.882702, "longitude": -87.623520}}
                },
                "computeAlternativeRoutes": True,
            }
            response = client.post("/directions/v2:computeRoutes", json=request)
            assert response.status_code == 200
            data = response.json()
            assert len(data["routes"]) >= 2
            labels = [r["routeLabels"][0] for r in data["routes"]]
            assert "DEFAULT_ROUTE" in labels
            assert "DEFAULT_ROUTE_ALTERNATE" in labels

    def test_google_endpoint_accepts_extra_google_fields(self, client, simple_request):
        """POST with polylineQuality, regionCode, trafficModel is not rejected."""
        request = dict(simple_request)
        request["polylineQuality"] = "HIGH_QUALITY"
        request["regionCode"] = "US"
        request["trafficModel"] = "BEST_GUESS"

        response = client.post("/directions/v2:computeRoutes", json=request)
        assert response.status_code == 200

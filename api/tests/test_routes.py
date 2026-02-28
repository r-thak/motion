"""Integration tests for the Stripe-style endpoints."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.services.cache import compute_request_hash
from src.models.request import ComputeRoutesRequest

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

        yield TestClient(app, raise_server_exceptions=False), mock_pool, mock_redis


class TestPostRoutes:
    def test_post_routes_returns_resource(self, client, simple_request):
        """POST returns a resource with id, object, status, createdAt."""
        test_client, _, _ = client
        response = test_client.post("/v1/routes", json=simple_request)
        assert response.status_code in (200, 202)
        data = response.json()
        assert "id" in data
        assert data["object"] == "route"
        assert data["status"] in ("complete", "processing")
        assert "createdAt" in data

    def test_post_routes_fast_path_returns_200(self, client, simple_request):
        """POST a short route returns 200 with status: complete."""
        test_client, _, _ = client
        response = test_client.post("/v1/routes", json=simple_request)
        # With mocked Valhalla (instant response), should complete fast
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "complete"
        assert len(data["routes"]) > 0

    def test_get_nonexistent_route_returns_404(self, client):
        """GET for nonexistent route returns 404 with Stripe-style error."""
        test_client, _, _ = client
        response = test_client.get("/v1/routes/route_nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "route_not_found"


class TestGetRoute:
    def test_get_route_after_post(self, client, simple_request):
        """POST, GET by id, verify match."""
        test_client, mock_pool, mock_redis = client

        # POST
        response = test_client.post("/v1/routes", json=simple_request)
        assert response.status_code in (200, 202)
        route_id = response.json()["id"]

        # Mock Redis to return the cached route
        mock_redis.get = AsyncMock(return_value=json.dumps(response.json()))

        # GET
        get_response = test_client.get(f"/v1/routes/{route_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == route_id


class TestDeleteRoute:
    def test_delete_route(self, client, simple_request):
        """POST, DELETE, GET → 404."""
        test_client, mock_pool, mock_redis = client

        # POST
        response = test_client.post("/v1/routes", json=simple_request)
        assert response.status_code in (200, 202)
        route_id = response.json()["id"]

        # DELETE
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        del_response = test_client.delete(f"/v1/routes/{route_id}")
        assert del_response.status_code == 200
        assert del_response.json()["deleted"] is True

        # GET after delete should 404
        mock_redis.get = AsyncMock(return_value=None)
        mock_pool.fetchrow = AsyncMock(return_value=None)
        get_response = test_client.get(f"/v1/routes/{route_id}")
        assert get_response.status_code == 404


class TestIdempotency:
    def test_idempotency_returns_same_response(self, client, simple_request):
        """POST twice with same Idempotency-Key returns same response."""
        test_client, mock_pool, _ = client

        # First request (no idempotency key — baseline)
        response1 = test_client.post("/v1/routes", json=simple_request)
        assert response1.status_code in (200, 202)

        # Now mock the idempotency check to return cached response for second request
        with patch("src.middleware.idempotency.db.get_idempotency_key", new_callable=AsyncMock) as mock_get_key:
            mock_get_key.return_value = {
                "key_hash": b"test",
                "request_hash": compute_request_hash(
                    ComputeRoutesRequest(**simple_request).model_dump()
                ),
                "response_body": response1.json(),
                "status_code": response1.status_code,
            }

            headers = {"Idempotency-Key": "test-key-123"}
            response2 = test_client.post("/v1/routes", json=simple_request, headers=headers)
            assert response2.status_code == response1.status_code

    def test_idempotency_reuse_different_body_returns_422(self, client, simple_request):
        """POST with same key but different body returns 422."""
        test_client, mock_pool, _ = client

        with patch("src.middleware.idempotency.db.get_idempotency_key", new_callable=AsyncMock) as mock_get_key:
            mock_get_key.return_value = {
                "key_hash": b"test",
                "request_hash": b"completely_different_hash_value__",
                "response_body": {},
                "status_code": 200,
            }

            headers = {"Idempotency-Key": "test-key-reuse"}
            response = test_client.post("/v1/routes", json=simple_request, headers=headers)
            assert response.status_code == 422

"""Test configuration and fixtures."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def valhalla_response():
    """Load the Valhalla response fixture."""
    with open(FIXTURES_DIR / "valhalla_response.json") as f:
        return json.load(f)


@pytest.fixture
def sample_requests():
    """Load the sample requests fixture."""
    with open(FIXTURES_DIR / "sample_requests.json") as f:
        return json.load(f)


@pytest.fixture
def simple_request(sample_requests):
    """Get the simple A→B request."""
    return sample_requests[0]["body"]


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    redis.pipeline = MagicMock()
    pipe = AsyncMock()
    pipe.zremrangebyscore = AsyncMock()
    pipe.zadd = AsyncMock()
    pipe.zcard = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(return_value=[None, None, 1, None])
    redis.pipeline.return_value = pipe
    return redis


@pytest.fixture
def mock_pg_pool():
    """Create a mock PostgreSQL pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    return pool


@pytest.fixture
def mock_http_client():
    """Create a mock HTTP client."""
    return AsyncMock()


@pytest.fixture
def mock_zone_index():
    """Create a mock ZoneIndex."""
    from src.services.zones import ZoneIndex
    zone = ZoneIndex()
    return zone


def _make_valhalla_mock_response(valhalla_response):
    """Create a mock httpx response for Valhalla."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = valhalla_response
    return mock_resp


def _make_elevation_mock_response(elevation: float = 180.0):
    """Create a mock httpx response for elevation."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"value": elevation}
    return mock_resp


@pytest.fixture
def app_client(mock_redis, mock_pg_pool, mock_http_client, mock_zone_index, valhalla_response):
    """Create a test client with mocked dependencies."""
    # Patch dependencies before importing app
    with patch("src.services.valhalla.compute_route") as mock_valhalla, \
         patch("src.services.elevation.get_elevation") as mock_elevation:

        mock_valhalla.return_value = valhalla_response
        mock_elevation.return_value = 180.0

        from src.main import app

        # Override app state
        app.state.pg_pool = mock_pg_pool
        app.state.redis = mock_redis
        app.state.http_client = mock_http_client
        app.state.zone_index = mock_zone_index

        client = TestClient(app, raise_server_exceptions=False)
        yield client, mock_valhalla, mock_elevation

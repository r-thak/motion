"""Tests for the enrichment module."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.models.vehicle import resolve_vehicle_spec
from src.services.enrichment import enrich_routes, ROUTING_PROFILES
from src.services.translator import translate_response
from src.services.zones import ZoneIndex
from src.config import settings as app_settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def valhalla_response():
    with open(FIXTURES_DIR / "valhalla_response.json") as f:
        return json.load(f)


@pytest.fixture
def routes_and_points(valhalla_response):
    return translate_response(valhalla_response, "route_test")


@pytest.fixture
def vehicle():
    return resolve_vehicle_spec(None)


@pytest.fixture
def zone_index():
    return ZoneIndex()


@pytest.mark.asyncio
async def test_enrichment_fills_grade(routes_and_points, vehicle, zone_index):
    """Enrichment should fill in gradePercent or mark as degraded."""
    routes, all_step_points = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0, 182.0, 181.0, 183.0, 182.0, 184.0,
                                  183.0, 185.0, 184.0, 186.0, 185.0, 187.0,
                                  186.0]
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        telemetry = await enrich_routes(
            routes, all_step_points, vehicle,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

        # Check that steps have enrichment data
        for leg in routes[0].legs:
            for step in leg.steps:
                if step.distanceMeters > 0:
                    assert step.enrichment.gradePercent is not None or "gradePercent" in step.enrichment.degradedFields


@pytest.mark.asyncio
async def test_enrichment_fills_curvature(routes_and_points, vehicle, zone_index):
    """Enrichment should fill in curvatureDegreesPerKm."""
    routes, all_step_points = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0] * 20
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        await enrich_routes(
            routes, all_step_points, vehicle,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

        for leg in routes[0].legs:
            for step in leg.steps:
                assert step.enrichment.curvatureDegreesPerKm is not None


@pytest.mark.asyncio
async def test_enrichment_fills_fuel_burn(routes_and_points, vehicle, zone_index):
    """Enrichment should fill in fuelBurnLiters."""
    routes, all_step_points = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0] * 20
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        telemetry = await enrich_routes(
            routes, all_step_points, vehicle,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

        assert len(telemetry) == 1
        assert telemetry[0].summary.totalFuelBurnLiters is not None or telemetry[0].summary.totalFuelBurnLiters == 0



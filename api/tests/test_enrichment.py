"""Tests for the enrichment module."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.models.driver import DriverState
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
def driver_state():
    return DriverState()


@pytest.fixture
def zone_index():
    return ZoneIndex()


@pytest.mark.asyncio
async def test_enrichment_fills_grade(routes_and_points, vehicle, driver_state, zone_index):
    """Enrichment should fill in gradePercent or mark as degraded."""
    routes, all_step_points = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0, 182.0, 181.0, 183.0, 182.0, 184.0,
                                  183.0, 185.0, 184.0, 186.0, 185.0, 187.0,
                                  186.0]
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        telemetry = await enrich_routes(
            routes, all_step_points, vehicle, driver_state,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

        # Check that steps have enrichment data
        for leg in routes[0].legs:
            for step in leg.steps:
                if step.distanceMeters > 0:
                    assert step.enrichment.gradePercent is not None or "gradePercent" in step.enrichment.degradedFields


@pytest.mark.asyncio
async def test_enrichment_fills_curvature(routes_and_points, vehicle, driver_state, zone_index):
    """Enrichment should fill in curvatureDegreesPerKm."""
    routes, all_step_points = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0] * 20
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        await enrich_routes(
            routes, all_step_points, vehicle, driver_state,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

        for leg in routes[0].legs:
            for step in leg.steps:
                assert step.enrichment.curvatureDegreesPerKm is not None


@pytest.mark.asyncio
async def test_enrichment_fills_fuel_burn(routes_and_points, vehicle, driver_state, zone_index):
    """Enrichment should fill in fuelBurnLiters."""
    routes, all_step_points = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0] * 20
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        telemetry = await enrich_routes(
            routes, all_step_points, vehicle, driver_state,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

        assert len(telemetry) == 1
        assert telemetry[0].summary.totalFuelBurnLiters is not None or telemetry[0].summary.totalFuelBurnLiters == 0


@pytest.mark.asyncio
async def test_enrichment_respects_profile(routes_and_points, vehicle, driver_state, zone_index):
    """Different routing profiles should produce different stress scores."""
    routes_1, all_step_points_1 = routes_and_points

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0] * 20
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        # Run with balanced profile
        await enrich_routes(
            routes_1, all_step_points_1, vehicle, driver_state,
            None, "balanced", {}, zone_index, mock_http, mock_redis, app_settings,
        )

    # Get stress values from balanced
    balanced_stress = [
        step.enrichment.stressFactor
        for leg in routes_1[0].legs
        for step in leg.steps
        if step.enrichment.stressFactor is not None
    ]

    # Re-translate for clean data
    with open(FIXTURES_DIR / "valhalla_response.json") as f:
        valhalla_resp = json.load(f)
    routes_2, all_step_points_2 = translate_response(valhalla_resp, "route_test2")

    with patch("src.services.enrichment.get_elevations_batch") as mock_elev:
        mock_elev.return_value = [180.0] * 20
        mock_redis = AsyncMock()
        mock_http = AsyncMock()

        # Run with fatigue_aware profile (higher stress weight)
        await enrich_routes(
            routes_2, all_step_points_2, vehicle, driver_state,
            None, "fatigue_aware", {}, zone_index, mock_http, mock_redis, app_settings,
        )

    fatigue_stress = [
        step.enrichment.stressFactor
        for leg in routes_2[0].legs
        for step in leg.steps
        if step.enrichment.stressFactor is not None
    ]

    # fatigue_aware has stressWeight: 2.0 vs balanced: 1.0
    # So fatigue_aware stress should generally be higher
    assert sum(fatigue_stress) >= sum(balanced_stress)

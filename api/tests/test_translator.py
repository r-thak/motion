"""Tests for the translator module."""

import json
from pathlib import Path

import pytest

from src.models.request import ComputeRoutesRequest
from src.models.vehicle import resolve_vehicle_spec
from src.services.translator import (
    VALHALLA_MANEUVER_TO_GOOGLE,
    translate_request,
    translate_response,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def valhalla_response():
    with open(FIXTURES_DIR / "valhalla_response.json") as f:
        return json.load(f)


@pytest.fixture
def sample_requests():
    with open(FIXTURES_DIR / "sample_requests.json") as f:
        return json.load(f)


def _make_request(body: dict) -> ComputeRoutesRequest:
    return ComputeRoutesRequest(**body)


class TestTranslateRequest:
    def test_translate_request_basic(self, sample_requests):
        """Verify Valhalla locations and costing options are correct."""
        body = sample_requests[0]["body"]
        req = _make_request(body)
        vehicle = resolve_vehicle_spec(None)
        locations, costing, date_time, alternates = translate_request(req, vehicle)

        assert len(locations) == 2
        assert locations[0]["type"] == "break"
        assert locations[1]["type"] == "break"
        assert abs(locations[0]["lat"] - 41.878876) < 0.0001
        assert abs(locations[0]["lon"] - (-87.635918)) < 0.0001
        assert costing["weight"] == vehicle["grossWeightKg"] / 1000
        assert date_time is None
        assert alternates == 0

    def test_translate_request_avoid_modifiers(self, sample_requests):
        """avoidTolls/avoidHighways/avoidFerries → use_tolls=0/etc."""
        body = sample_requests[2]["body"]  # has avoidTolls: true
        req = _make_request(body)
        vehicle = resolve_vehicle_spec(None)
        _, costing, _, _ = translate_request(req, vehicle)

        assert costing["use_tolls"] == 0.0
        # Highways and ferries not avoided in this fixture
        assert costing["use_highways"] == 1.0
        assert costing["use_ferry"] == 1.0

    def test_translate_request_via_waypoint(self, sample_requests):
        """Verify intermediates with via=True get type: 'through'."""
        body = dict(sample_requests[2]["body"])
        body["intermediates"] = [
            {"location": {"latLng": {"latitude": 41.88, "longitude": -87.63}}, "via": True}
        ]
        req = _make_request(body)
        vehicle = resolve_vehicle_spec(None)
        locations, _, _, _ = translate_request(req, vehicle)

        assert len(locations) == 3
        assert locations[1]["type"] == "through"

    def test_translate_request_alternates(self, sample_requests):
        """Verify computeAlternativeRoutes=True produces alternates=2."""
        body = sample_requests[3]["body"]  # has computeAlternativeRoutes: true
        req = _make_request(body)
        vehicle = resolve_vehicle_spec(None)
        _, _, _, alternates = translate_request(req, vehicle)

        assert alternates == 2


class TestTranslateResponse:
    def test_translate_response_step_count(self, valhalla_response):
        """Maneuver count matches step count."""
        routes, _ = translate_response(valhalla_response, "route_test")
        assert len(routes) == 1

        total_maneuvers = sum(
            len(leg["maneuvers"])
            for leg in valhalla_response["trip"]["legs"]
        )
        total_steps = sum(len(leg.steps) for leg in routes[0].legs)
        assert total_steps == total_maneuvers

    def test_translate_response_distance_conversion(self, valhalla_response):
        """km → meters."""
        routes, _ = translate_response(valhalla_response, "route_test")
        expected_meters = int(valhalla_response["trip"]["summary"]["length"] * 1000)
        assert routes[0].distanceMeters == expected_meters

    def test_translate_response_duration_format(self, valhalla_response):
        """'{int}s' format."""
        routes, _ = translate_response(valhalla_response, "route_test")
        expected = f'{int(valhalla_response["trip"]["summary"]["time"])}s'
        assert routes[0].duration == expected
        assert routes[0].staticDuration == expected

    def test_translate_response_maneuver_mapping(self, valhalla_response):
        """Known types map to correct Google strings."""
        routes, _ = translate_response(valhalla_response, "route_test")
        steps = routes[0].legs[0].steps

        # Type 1 = DEPART
        assert steps[0].navigationInstruction.maneuver == "DEPART"
        # Type 15 = TURN_LEFT
        assert steps[1].navigationInstruction.maneuver == "TURN_LEFT"
        # Type 8 = STRAIGHT
        assert steps[2].navigationInstruction.maneuver == "STRAIGHT"
        # Type 10 = TURN_RIGHT
        assert steps[3].navigationInstruction.maneuver == "TURN_RIGHT"
        # Type 4 = MANEUVER_UNSPECIFIED
        assert steps[4].navigationInstruction.maneuver == "MANEUVER_UNSPECIFIED"

    def test_translate_response_polyline_precision(self, valhalla_response):
        """Decode Valhalla polyline at precision 6, verify coordinates in Chicago range."""
        import polyline as polyline_lib

        valhalla_shape = valhalla_response["trip"]["legs"][0]["shape"]
        points = polyline_lib.decode(valhalla_shape, 6)

        # Chicago range check
        for lat, lng in points:
            assert 41.0 < lat < 42.5, f"Latitude {lat} not in Chicago range"
            assert -88.5 < lng < -87.0, f"Longitude {lng} not in Chicago range"

        # Re-encode at precision 5
        encoded_5 = polyline_lib.encode(points, 5)
        assert encoded_5 != valhalla_shape  # Different precision must produce different encoding

    def test_translate_response_google_fields_present(self, valhalla_response):
        """Verify Route, RouteLeg, RouteLegStep have Google fields."""
        routes, _ = translate_response(valhalla_response, "route_test")
        route = routes[0]

        assert route.staticDuration is not None
        assert route.viewport is not None
        assert route.description is not None
        assert route.warnings is not None

        leg = route.legs[0]
        assert leg.staticDuration is not None
        assert leg.polyline is not None

        step = leg.steps[0]
        assert step.travelAdvisory is None

    def test_translate_response_alternates(self, valhalla_response):
        """Verify alternates are labeled DEFAULT_ROUTE_ALTERNATE."""
        # Add an alternate
        response_with_alt = dict(valhalla_response)
        response_with_alt["alternates"] = [{"trip": valhalla_response["trip"]}]

        routes, _ = translate_response(response_with_alt, "route_test")
        assert len(routes) == 2
        assert routes[0].routeLabels == ["DEFAULT_ROUTE"]
        assert routes[1].routeLabels == ["DEFAULT_ROUTE_ALTERNATE"]


class TestManeuverMapping:
    def test_all_expected_mappings(self):
        """Verify specific important mappings exist."""
        assert VALHALLA_MANEUVER_TO_GOOGLE[25] == "MERGE"
        assert VALHALLA_MANEUVER_TO_GOOGLE[28] == "FERRY"
        assert VALHALLA_MANEUVER_TO_GOOGLE[24] == "FORK_LEFT"
        assert VALHALLA_MANEUVER_TO_GOOGLE[7] == "NAME_CHANGE"
        assert VALHALLA_MANEUVER_TO_GOOGLE[4] == "MANEUVER_UNSPECIFIED"

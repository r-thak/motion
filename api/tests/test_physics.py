"""Tests for the physics module."""

import numpy as np
import pytest

from src.models.vehicle import VEHICLE_PRESETS
from src.services.physics import estimate_fuel_burn


@pytest.fixture
def semi_trailer():
    return dict(VEHICLE_PRESETS["SEMI_TRAILER"])


class TestEstimateFuelBurn:
    def test_flat_road_positive_fuel(self, semi_trailer):
        """Flat road at constant speed should produce positive fuel burn."""
        distances = np.array([1000.0, 2000.0, 500.0])
        grades = np.array([0.0, 0.0, 0.0])
        speeds = np.array([25.0, 25.0, 25.0])

        fuel = estimate_fuel_burn(distances, grades, speeds, semi_trailer)
        assert fuel.shape == (3,)
        assert all(f > 0 for f in fuel)

    def test_uphill_more_fuel_than_flat(self, semi_trailer):
        """Uphill grade should consume more fuel than flat."""
        distances = np.array([1000.0, 1000.0])
        grades_flat = np.array([0.0, 0.0])
        grades_up = np.array([5.0, 5.0])
        speeds = np.array([20.0, 20.0])

        fuel_flat = estimate_fuel_burn(distances, grades_flat, speeds, semi_trailer)
        fuel_up = estimate_fuel_burn(distances, grades_up, speeds, semi_trailer)

        assert fuel_up.sum() > fuel_flat.sum()

    def test_zero_speed_zero_fuel(self, semi_trailer):
        """Zero speed should produce zero fuel burn."""
        distances = np.array([0.0, 0.0])
        grades = np.array([0.0, 0.0])
        speeds = np.array([0.0, 0.0])

        fuel = estimate_fuel_burn(distances, grades, speeds, semi_trailer)
        assert all(f == 0 for f in fuel)

    def test_nan_grade_treated_as_zero(self, semi_trailer):
        """NaN grade should be treated as 0 (flat)."""
        distances = np.array([1000.0])
        grades_nan = np.array([float("nan")])
        grades_zero = np.array([0.0])
        speeds = np.array([25.0])

        fuel_nan = estimate_fuel_burn(distances, grades_nan, speeds, semi_trailer)
        fuel_zero = estimate_fuel_burn(distances, grades_zero, speeds, semi_trailer)

        np.testing.assert_allclose(fuel_nan, fuel_zero)

    def test_heavier_vehicle_more_fuel(self):
        """Heavier vehicle should consume more fuel on the same route."""
        light = dict(VEHICLE_PRESETS["BOX_TRUCK"])
        heavy = dict(VEHICLE_PRESETS["SEMI_TRAILER"])

        distances = np.array([1000.0, 2000.0])
        grades = np.array([0.0, 0.0])
        speeds = np.array([25.0, 25.0])

        fuel_light = estimate_fuel_burn(distances, grades, speeds, light)
        fuel_heavy = estimate_fuel_burn(distances, grades, speeds, heavy)

        assert fuel_heavy.sum() > fuel_light.sum()

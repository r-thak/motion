"""Pure computation module for fuel burn estimation. No I/O."""

import numpy as np

# Constants
GRAVITY = 9.81           # m/s²
AIR_DENSITY = 1.225      # kg/m³ at sea level
DIESEL_ENERGY_MJ_PER_L = 35.8


def estimate_fuel_burn(
    distances_m: np.ndarray,
    grades_percent: np.ndarray,
    speeds_mps: np.ndarray,
    vehicle: dict,
) -> np.ndarray:
    """
    Estimate fuel burn in liters for each step using the road load equation.

    Returns: np.ndarray of shape (N,) — fuel burn in liters per step.
    """
    # Replace NaN grades with 0
    grades = np.where(np.isnan(grades_percent), 0.0, grades_percent)

    # Vehicle parameters
    m = vehicle["grossWeightKg"]
    crr = vehicle["rollingResistance"]
    cd = vehicle["dragCoefficient"]
    a = vehicle["frontalAreaM2"]
    eta = vehicle["engineEfficiency"]
    idle_rate = vehicle["idleFuelLitersPerHour"] / 3600.0  # liters per second

    # Compute grade angle
    theta = np.arctan(grades / 100.0)

    # Identify valid steps (positive speed and distance)
    valid = (speeds_mps > 0) & (distances_m > 0)

    # Initialize output
    fuel_burn = np.zeros_like(distances_m, dtype=np.float64)

    # Compute for valid steps
    v = np.where(valid, speeds_mps, 1.0)  # avoid division by zero
    d = np.where(valid, distances_m, 1.0)

    # Road load power (W)
    P = v * (
        m * GRAVITY * (crr * np.cos(theta) + np.sin(theta))
        + 0.5 * AIR_DENSITY * cd * a * v ** 2
    )

    # Traversal time (seconds)
    t = d / v

    # Energy in joules
    energy_joules = P * t

    # Fuel from engine work (liters)
    fuel_liters = energy_joules / (eta * DIESEL_ENERGY_MJ_PER_L * 1e6)

    # Idle fuel
    idle_fuel = idle_rate * t

    # Take the max of computed fuel and idle fuel
    computed = np.maximum(fuel_liters, idle_fuel)

    # Only assign to valid steps
    fuel_burn = np.where(valid, computed, 0.0)

    return fuel_burn

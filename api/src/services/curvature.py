"""Pure geometry module for curvature computation. No I/O."""

import math


def compute_bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Compute initial bearing from point 1 to point 2 in degrees [0, 360).
    Uses the standard spherical formula.
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlng = math.radians(lng2 - lng1)

    x = math.sin(dlng) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlng)

    bearing = math.degrees(math.atan2(x, y))
    return bearing % 360.0


def compute_curvature(points: list[tuple[float, float]], distance_meters: float) -> float:
    """
    Compute curvature in degrees per kilometer for a sequence of (lat, lng) points.

    Logic:
    1. If fewer than 3 points or distance_meters <= 0, return 0.0.
    2. For each consecutive triple (p[i-1], p[i], p[i+1]):
       a. Compute bearing from p[i-1] to p[i].
       b. Compute bearing from p[i] to p[i+1].
       c. Compute the absolute bearing change: |b2 - b1|, normalized to [0, 180].
    3. Sum all absolute bearing changes.
    4. Divide by (distance_meters / 1000) to get degrees per km.
    """
    if len(points) < 3 or distance_meters <= 0:
        return 0.0

    total_change = 0.0
    for i in range(1, len(points) - 1):
        b1 = compute_bearing(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        b2 = compute_bearing(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        diff = abs(b2 - b1)
        if diff > 180:
            diff = 360 - diff
        total_change += diff

    return total_change / (distance_meters / 1000)

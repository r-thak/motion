"""Enrichment orchestrator — runs all enrichment steps on translated routes."""

import logging
from datetime import datetime, timezone

import httpx
import numpy as np
import redis.asyncio as redis_lib

from src.config import Settings
from src.models.driver import DriverState
from src.models.enrichment import StepEnrichment
from src.models.response import Route, make_location
from src.models.telemetry import TelemetryResponse, TelemetrySegment, TelemetrySummary
from src.services.curvature import compute_curvature
from src.services.elevation import get_elevations_batch
from src.services.physics import estimate_fuel_burn
from src.services.translator import MANEUVER_STRESS, VALHALLA_ROAD_CLASS
from src.services.zones import ZoneIndex

logger = logging.getLogger(__name__)

ROUTING_PROFILES: dict[str, dict[str, float]] = {
    "balanced":      {"fuelWeight": 1.0, "stressWeight": 1.0, "gradeWeight": 1.0, "zoneWeight": 1.0},
    "fuel_optimal":  {"fuelWeight": 2.0, "stressWeight": 0.5, "gradeWeight": 1.5, "zoneWeight": 0.5},
    "time_optimal":  {"fuelWeight": 0.3, "stressWeight": 0.3, "gradeWeight": 0.3, "zoneWeight": 0.3},
    "fatigue_aware": {"fuelWeight": 0.8, "stressWeight": 2.0, "gradeWeight": 1.0, "zoneWeight": 1.5},
}


async def enrich_routes(
    routes: list[Route],
    all_step_points: list[list[list[tuple[float, float]]]],
    vehicle: dict,
    driver_state: DriverState,
    departure_time: str | None,
    routing_profile: str,
    profile_overrides: dict[str, float],
    zone_index: ZoneIndex,
    http_client: httpx.AsyncClient,
    redis_client: redis_lib.Redis,
    settings: Settings,
) -> list[TelemetryResponse]:
    """
    Enrich all steps in all routes with physics data.
    Returns a TelemetryResponse per route.
    Mutates routes in place (fills in enrichment objects on each step).
    """
    # Resolve routing profile
    profile = dict(ROUTING_PROFILES.get(routing_profile, ROUTING_PROFILES["balanced"]))
    profile.update(profile_overrides)

    telemetry_responses = []

    for route_idx, route in enumerate(routes):
        step_points_list = all_step_points[route_idx]

        # Collect all points needing elevation lookups (first and last of each step)
        elevation_points: list[tuple[float, float]] = []
        point_set: set[tuple[float, float]] = set()

        step_index = 0
        for leg in route.legs:
            for step in leg.steps:
                sp = step_points_list[step_index]
                first = (round(sp[0][0], 5), round(sp[0][1], 5))
                last = (round(sp[-1][0], 5), round(sp[-1][1], 5))
                for pt in [first, last]:
                    if pt not in point_set:
                        point_set.add(pt)
                        elevation_points.append(pt)
                step_index += 1

        # Batch-fetch elevations
        elevations = await get_elevations_batch(elevation_points, http_client, redis_client, settings)
        elev_lookup: dict[tuple[float, float], float | None] = dict(zip(elevation_points, elevations))

        # Collect per-step data for vectorized fuel burn
        distances_list: list[float] = []
        grades_list: list[float] = []
        speeds_list: list[float] = []
        step_enrichments: list[StepEnrichment] = []
        segments: list[TelemetrySegment] = []

        step_index = 0
        cumulative_time = 0
        global_step_idx = 0

        for leg in route.legs:
            for step in leg.steps:
                sp = step_points_list[step_index]
                first_pt = (round(sp[0][0], 5), round(sp[0][1], 5))
                last_pt = (round(sp[-1][0], 5), round(sp[-1][1], 5))

                start_elev = elev_lookup.get(first_pt)
                end_elev = elev_lookup.get(last_pt)

                # Grade
                grade = None
                degraded_fields = []
                degraded_reason = None
                if start_elev is not None and end_elev is not None and step.distanceMeters > 0:
                    grade = (end_elev - start_elev) / step.distanceMeters * 100
                else:
                    degraded_fields.append("gradePercent")
                    degraded_reason = "Elevation data unavailable"

                # Curvature
                curvature = compute_curvature(sp, step.distanceMeters)

                # Speed from duration and distance
                duration_s = int(step.staticDuration.rstrip("s")) if step.staticDuration else 0
                speed_mps = step.distanceMeters / duration_s if duration_s > 0 else 0.0

                # Zone flags
                mid_lat = sp[len(sp) // 2][0]
                mid_lng = sp[len(sp) // 2][1]
                zone_flags = zone_index.check_zones(mid_lat, mid_lng)

                # Filter school zones by time
                if "SCHOOL_ZONE" in zone_flags:
                    if not zone_index.is_school_zone_active(departure_time, cumulative_time):
                        zone_flags.remove("SCHOOL_ZONE")

                # Stress factor
                maneuver_str = step.navigationInstruction.maneuver
                base_stress, fatigue_mult = MANEUVER_STRESS.get(maneuver_str, (0.05, 0.5))
                stress = base_stress + fatigue_mult * driver_state.fatigueScore
                # Apply zone stress
                if "SCHOOL_ZONE" in zone_flags:
                    stress += 0.3 * profile.get("zoneWeight", 1.0)
                if "RESIDENTIAL" in zone_flags:
                    stress += 0.1 * profile.get("zoneWeight", 1.0)
                stress *= profile.get("stressWeight", 1.0)
                stress = min(stress, 1.0)

                # Road class from maneuver (not available directly; use step info if available)
                road_class = None

                enrichment = StepEnrichment(
                    gradePercent=round(grade, 2) if grade is not None else None,
                    curvatureDegreesPerKm=round(curvature, 2),
                    fuelBurnLiters=None,  # filled after vectorized computation
                    stressFactor=round(stress, 3),
                    zoneFlags=zone_flags,
                    speedLimitKmh=None,
                    roadClass=road_class,
                    degradedFields=degraded_fields,
                    degradedReason=degraded_reason,
                )

                step.enrichment = enrichment
                step_enrichments.append(enrichment)

                distances_list.append(float(step.distanceMeters))
                grades_list.append(grade if grade is not None else float("nan"))
                speeds_list.append(speed_mps)

                # Build telemetry segment
                segments.append(TelemetrySegment(
                    index=global_step_idx,
                    startLocation=step.startLocation,
                    endLocation=step.endLocation,
                    distanceMeters=step.distanceMeters,
                    durationSeconds=duration_s,
                    gradePercent=round(grade, 2) if grade is not None else None,
                    curvatureDegreesPerKm=round(curvature, 2),
                    fuelBurnLiters=None,  # filled below
                    stressFactor=round(stress, 3),
                    zoneFlags=zone_flags,
                    speedLimitKmh=None,
                    roadClass=road_class,
                ))

                cumulative_time += duration_s
                step_index += 1
                global_step_idx += 1

        # Vectorized fuel burn
        if distances_list:
            distances_arr = np.array(distances_list, dtype=np.float64)
            grades_arr = np.array(grades_list, dtype=np.float64)
            speeds_arr = np.array(speeds_list, dtype=np.float64)

            fuel_burns = estimate_fuel_burn(distances_arr, grades_arr, speeds_arr, vehicle)
            fuel_burns *= profile.get("fuelWeight", 1.0)

            for i, fb in enumerate(fuel_burns):
                step_enrichments[i].fuelBurnLiters = round(float(fb), 4)
                segments[i].fuelBurnLiters = round(float(fb), 4)

        # Build telemetry summary
        total_fuel = sum(s.fuelBurnLiters for s in segments if s.fuelBurnLiters is not None)
        valid_grades = [s.gradePercent for s in segments if s.gradePercent is not None]
        grade_gain = 0.0
        grade_loss = 0.0
        for i, seg in enumerate(segments):
            if seg.gradePercent is not None and seg.distanceMeters > 0:
                elev_change = seg.gradePercent / 100 * seg.distanceMeters
                if elev_change > 0:
                    grade_gain += elev_change
                else:
                    grade_loss += abs(elev_change)

        curvatures = [s.curvatureDegreesPerKm for s in segments if s.curvatureDegreesPerKm is not None]
        avg_curvature = sum(curvatures) / len(curvatures) if curvatures else None

        school_count = sum(1 for s in segments if "SCHOOL_ZONE" in s.zoneFlags)
        residential_count = sum(1 for s in segments if "RESIDENTIAL" in s.zoneFlags)

        stress_scores = [s.stressFactor for s in segments if s.stressFactor is not None]
        overall_stress = sum(stress_scores) / len(stress_scores) if stress_scores else None

        summary = TelemetrySummary(
            totalFuelBurnLiters=round(total_fuel, 4) if total_fuel else None,
            totalGradeGainMeters=round(grade_gain, 2),
            totalGradeLossMeters=round(grade_loss, 2),
            maxGradePercent=round(max(valid_grades), 2) if valid_grades else None,
            averageCurvatureDegreesPerKm=round(avg_curvature, 2) if avg_curvature is not None else None,
            schoolZoneCount=school_count,
            residentialSegmentCount=residential_count,
            overallStressScore=round(overall_stress, 3) if overall_stress is not None else None,
        )

        telemetry_responses.append(TelemetryResponse(
            routeId="",  # filled by caller
            computedAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            driverState=driver_state.model_dump(),
            vehicleSpec=vehicle,
            summary=summary,
            segments=segments,
        ))

    return telemetry_responses

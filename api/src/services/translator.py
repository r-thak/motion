"""Translate between Google Routes API format and Valhalla format."""

from datetime import datetime, timezone

import polyline as polyline_lib

from src.models.request import ComputeRoutesRequest
from src.models.response import (
    Route,
    RouteLeg,
    RouteLegStep,
    Polyline,
    NavigationInstruction,
    make_location,
    compute_viewport,
)
from src.models.enrichment import StepEnrichment


# --- Constants ---

VALHALLA_MANEUVER_TO_GOOGLE: dict[int, str] = {
    0: "DEPART",
    1: "DEPART",
    2: "DEPART",
    3: "DEPART",
    4: "MANEUVER_UNSPECIFIED",  # kDestination
    5: "MANEUVER_UNSPECIFIED",  # kDestinationRight
    6: "MANEUVER_UNSPECIFIED",  # kDestinationLeft
    7: "NAME_CHANGE",           # kBecomes
    8: "STRAIGHT",              # kContinue
    9: "TURN_SLIGHT_RIGHT",
    10: "TURN_RIGHT",
    11: "TURN_SHARP_RIGHT",
    12: "UTURN_RIGHT",
    13: "UTURN_LEFT",
    14: "TURN_SHARP_LEFT",
    15: "TURN_LEFT",
    16: "TURN_SLIGHT_LEFT",
    17: "STRAIGHT",             # kRampStraight
    18: "RAMP_RIGHT",
    19: "RAMP_LEFT",
    20: "RAMP_RIGHT",           # kExitRight
    21: "RAMP_LEFT",            # kExitLeft
    22: "STRAIGHT",             # kStayStraight
    23: "FORK_RIGHT",           # kStayRight
    24: "FORK_LEFT",            # kStayLeft
    25: "MERGE",                # kMerge
    26: "ROUNDABOUT_RIGHT",     # kRoundaboutEnter
    27: "ROUNDABOUT_RIGHT",     # kRoundaboutExit
    28: "FERRY",                # kFerryEnter
    29: "STRAIGHT",             # kFerryExit
    30: "STRAIGHT",
    31: "STRAIGHT",
    32: "FERRY_TRAIN",          # kTransitConnectionStart
    33: "STRAIGHT",
    34: "STRAIGHT",
    35: "STRAIGHT",
    36: "STRAIGHT",
    37: "MERGE",                # kMergeRight
    38: "MERGE",                # kMergeLeft
}


VALHALLA_ROAD_CLASS: dict[str, str] = {
    "motorway": "MOTORWAY",
    "trunk": "TRUNK",
    "primary": "PRIMARY",
    "secondary": "SECONDARY",
    "tertiary": "TERTIARY",
    "unclassified": "UNCLASSIFIED",
    "residential": "RESIDENTIAL",
    "service": "SERVICE_ROAD",
}


# --- Functions ---

def translate_request(
    request: ComputeRoutesRequest,
    resolved_vehicle: dict,
) -> tuple[list[dict], dict, dict | None, int]:
    """
    Translate a Google-compatible request into Valhalla parameters.

    Returns:
        locations: list of Valhalla location dicts
        costing_options: dict for costing_options.truck
        date_time: dict or None
        alternates: int (number of alternate routes, 0 or 2)
    """
    # 1. Build locations list
    locations = []

    # Origin
    origin_loc = request.origin.location
    locations.append({
        "lat": origin_loc.latLng.latitude,
        "lon": origin_loc.latLng.longitude,
        "type": "break",
    })

    # Intermediates
    for wp in request.intermediates:
        loc = wp.location
        locations.append({
            "lat": loc.latLng.latitude,
            "lon": loc.latLng.longitude,
            "type": "through" if wp.via else "break",
        })

    # Destination
    dest_loc = request.destination.location
    locations.append({
        "lat": dest_loc.latLng.latitude,
        "lon": dest_loc.latLng.longitude,
        "type": "break",
    })

    # 2. Build costing options — base vehicle parameters
    weight_tonnes = resolved_vehicle["grossWeightKg"] / 1000
    costing_options = {
        "weight": weight_tonnes,  # Valhalla wants tonnes
        "height": resolved_vehicle["heightM"],
        "width": resolved_vehicle["widthM"],
        "length": resolved_vehicle["lengthM"],
        "axle_load": resolved_vehicle["axleLoadTonnes"],
        "use_tolls": 0.0 if request.routeModifiers.avoidTolls else 1.0,
        "use_highways": 0.0 if request.routeModifiers.avoidHighways else 1.0,
        "use_ferry": 0.0 if request.routeModifiers.avoidFerries else 1.0,
    }

    # ─── Grade & Turn Avoidance ───────────────────────────────────────
    # use_hills:  0.0 = max hill avoidance, 1.0 = ignore hills (default 0.5)
    # maneuver_penalty: seconds added per turn/maneuver (default 5)
    # service_penalty:  seconds added for using service roads (default 0)
    #
    # Map routing profiles → Valhalla costing parameters so the route
    # itself (not just post-hoc enrichment scores) favors lower grade
    # and fewer turns.
    #
    # Heavy vehicles (>20t) get stronger penalties because turns and
    # hills cost them disproportionately more fuel.

    profile = request.routingProfile
    heavy = weight_tonnes >= 20  # semi-trailers, heavy rigs

    PROFILE_COSTING = {
        #                  use_hills  maneuver_penalty    service_penalty  use_living_streets
        "fuel_optimal":   (0.0,       80 if heavy else 50, 100,          0.1),
        "balanced":       (0.5,       15 if heavy else  8,  15,          0.5),
        "time_optimal":   (1.0,        0,                   0,           1.0),
    }

    use_hills, maneuver_penalty, service_penalty, use_living = PROFILE_COSTING.get(
        profile, PROFILE_COSTING["balanced"]
    )

    costing_options["use_hills"] = use_hills
    costing_options["maneuver_penalty"] = maneuver_penalty
    costing_options["service_penalty"] = service_penalty
    costing_options["use_living_streets"] = use_living

    # Also apply profile overrides if the user explicitly set them
    overrides = request.profileOverrides
    if "hillAvoidance" in overrides:
        # User override: 0.0 = ignore hills, 1.0 = max avoidance
        costing_options["use_hills"] = max(0.0, min(1.0, 1.0 - overrides["hillAvoidance"]))
    if "turnPenalty" in overrides:
        costing_options["maneuver_penalty"] = max(0, min(60, overrides["turnPenalty"]))

    # 3. Build date_time
    date_time = None
    if request.departureTime is not None:
        try:
            dt = datetime.fromisoformat(request.departureTime.replace("Z", "+00:00"))
            date_time = {
                "type": 1,
                "value": dt.strftime("%Y-%m-%dT%H:%M"),
            }
        except (ValueError, AttributeError):
            pass

    # 4. Alternates
    alternates = 0
    if request.computeAlternativeRoutes and len(request.intermediates) == 0:
        alternates = 2

    return locations, costing_options, date_time, alternates


def _translate_trip(
    trip_data: dict,
    label: str,
) -> tuple[Route, list[list[tuple[float, float]]]]:
    """
    Translate a single Valhalla trip into a Route object.

    Returns:
        route: Route object
        step_points: list of (lat, lng) tuples per step for enrichment
    """
    legs = []
    all_route_points: list[tuple[float, float]] = []
    route_step_points: list[list[tuple[float, float]]] = []

    for leg_data in trip_data["legs"]:
        # Decode leg shape at precision 6 (Valhalla)
        shape_points = polyline_lib.decode(leg_data["shape"], 6)

        leg_distance_m = int(leg_data["summary"]["length"] * 1000)
        leg_duration_s = f'{int(leg_data["summary"]["time"])}s'

        first_point = shape_points[0]
        last_point = shape_points[-1]

        # Re-encode at precision 5 (Google)
        leg_encoded = polyline_lib.encode(shape_points, 5)

        steps = []
        for maneuver in leg_data["maneuvers"]:
            begin_idx = maneuver["begin_shape_index"]
            end_idx = maneuver["end_shape_index"]
            step_points = shape_points[begin_idx:end_idx + 1]

            if not step_points:
                step_points = [shape_points[begin_idx]]

            step_encoded = polyline_lib.encode(step_points, 5)
            step_start = step_points[0]
            step_end = step_points[-1]

            maneuver_type = maneuver.get("type", 0)
            google_maneuver = VALHALLA_MANEUVER_TO_GOOGLE.get(maneuver_type, "STRAIGHT")

            step = RouteLegStep(
                distanceMeters=int(maneuver["length"] * 1000),
                staticDuration=f'{int(maneuver["time"])}s',
                polyline=Polyline(encodedPolyline=step_encoded),
                startLocation=make_location(step_start[0], step_start[1]),
                endLocation=make_location(step_end[0], step_end[1]),
                navigationInstruction=NavigationInstruction(
                    maneuver=google_maneuver,
                    instructions=maneuver.get("instruction", ""),
                ),
                travelAdvisory=None,
                localizedValues=None,
                transitDetails=None,
                travelMode="DRIVE",
                enrichment=StepEnrichment(),
            )
            steps.append(step)
            route_step_points.append(step_points)

        leg = RouteLeg(
            distanceMeters=leg_distance_m,
            duration=leg_duration_s,
            staticDuration=leg_duration_s,
            polyline=Polyline(encodedPolyline=leg_encoded),
            startLocation=make_location(first_point[0], first_point[1]),
            endLocation=make_location(last_point[0], last_point[1]),
            steps=steps,
            travelAdvisory=None,
            localizedValues=None,
            stepsOverview=None,
        )
        legs.append(leg)

        # Accumulate route-level points, deduplicating shared endpoints
        if all_route_points and all_route_points[-1] == shape_points[0]:
            all_route_points.extend(shape_points[1:])
        else:
            all_route_points.extend(shape_points)

    # Route-level fields
    route_distance_m = int(trip_data["summary"]["length"] * 1000)
    route_duration_s = f'{int(trip_data["summary"]["time"])}s'
    route_encoded = polyline_lib.encode(all_route_points, 5)

    route = Route(
        routeLabels=[label],
        legs=legs,
        distanceMeters=route_distance_m,
        duration=route_duration_s,
        staticDuration=route_duration_s,
        polyline=Polyline(encodedPolyline=route_encoded),
        description="",
        warnings=[],
        viewport=compute_viewport(all_route_points) if all_route_points else None,
        travelAdvisory=None,
        optimizedIntermediateWaypointIndex=[],
        localizedValues=None,
        routeToken=None,
        polylineDetails=None,
    )

    return route, route_step_points


def translate_response(
    valhalla_response: dict,
    route_id: str,
) -> tuple[list[Route], list[list[list[tuple[float, float]]]]]:
    """
    Translate Valhalla's response into Google-compatible Route objects.

    Returns:
        routes: list of Route objects (main route + any alternates)
        all_step_points: list (per route) of lists (per step) of (lat, lng) tuples.
                         Used by the enrichment pipeline.
    """
    routes = []
    all_step_points = []

    # Primary route
    trip = valhalla_response["trip"]
    route, step_points = _translate_trip(trip, "DEFAULT_ROUTE")
    routes.append(route)
    all_step_points.append(step_points)

    # Alternates
    if "alternates" in valhalla_response:
        for alt in valhalla_response["alternates"]:
            alt_trip = alt["trip"]
            alt_route, alt_step_points = _translate_trip(alt_trip, "DEFAULT_ROUTE_ALTERNATE")
            routes.append(alt_route)
            all_step_points.append(alt_step_points)

    return routes, all_step_points

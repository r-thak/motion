from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.vehicle import VehicleSpec


class LatLng(BaseModel):
    latitude: float
    longitude: float


class Location(BaseModel):
    latLng: LatLng
    heading: int | None = None  # Google: compass heading 0-360


class Waypoint(BaseModel):
    # Union field: exactly one of location, placeId, address must be set.
    location: Location | None = None
    placeId: str | None = None
    address: str | None = None
    # Google waypoint flags:
    via: bool = False  # True = pass-through (Valhalla "through"), False = stop ("break")
    vehicleStopover: bool = False  # Accepted, ignored
    sideOfRoad: bool = False  # Accepted, ignored


class RouteModifiers(BaseModel):
    avoidTolls: bool = False
    avoidHighways: bool = False
    avoidFerries: bool = False
    avoidIndoor: bool = False  # Accepted, ignored
    vehicleInfo: dict | None = None  # Accepted, ignored
    tollPasses: list[str] = Field(default_factory=list)  # Accepted, ignored


class ComputeRoutesRequest(BaseModel):
    model_config = {"extra": "ignore"}

    # Required Google fields
    origin: Waypoint
    destination: Waypoint

    # Optional Google fields — all supported or gracefully ignored
    intermediates: list[Waypoint] = Field(default_factory=list)
    travelMode: str = "DRIVE"
    routingPreference: str = "TRAFFIC_UNAWARE"
    polylineQuality: str = "OVERVIEW"  # Accepted, ignored
    polylineEncoding: str = "ENCODED_POLYLINE"  # Accepted, ignored
    departureTime: str | None = None  # ISO 8601 UTC, supported
    arrivalTime: str | None = None  # Accepted, ignored
    computeAlternativeRoutes: bool = False  # SUPPORTED — maps to Valhalla alternates
    routeModifiers: RouteModifiers = Field(default_factory=RouteModifiers)
    languageCode: str = "en-US"
    regionCode: str | None = None  # Accepted, ignored
    units: str = "METRIC"  # Accepted, ignored (we always return meters)
    optimizeWaypointOrder: bool = False  # Accepted, ignored
    requestedReferenceRoutes: list[str] = Field(default_factory=list)  # Accepted, ignored
    extraComputations: list[str] = Field(default_factory=list)  # Accepted, ignored
    trafficModel: str | None = None  # Accepted, ignored
    transitPreferences: dict | None = None  # Accepted, ignored

    # Our extension fields (ignored by Google-migrating clients)
    vehicleSpec: VehicleSpec | None = None
    routingProfile: str = "balanced"
    profileOverrides: dict[str, float] = Field(default_factory=dict)
    webhookUrl: str | None = None  # Only used by POST /v1/routes (Stripe-style endpoint)

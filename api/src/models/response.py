from pydantic import BaseModel, Field

from src.models.enrichment import StepEnrichment


# --- Shared building blocks (used by both response shapes) ---


class Polyline(BaseModel):
    encodedPolyline: str


class NavigationInstruction(BaseModel):
    maneuver: str
    instructions: str


class RouteLegStep(BaseModel):
    """Matches Google's RouteLegStep exactly, plus our enrichment namespace."""
    distanceMeters: int
    staticDuration: str  # e.g. "28s" or "28.1s"
    polyline: Polyline
    startLocation: dict  # {"latLng": {"latitude": ..., "longitude": ...}}
    endLocation: dict  # same shape
    navigationInstruction: NavigationInstruction
    travelAdvisory: dict | None = None  # Google field — we return null
    localizedValues: dict | None = None  # Google field — we return null
    transitDetails: dict | None = None  # Google field — we return null
    travelMode: str = "DRIVE"
    # Our extension:
    enrichment: StepEnrichment = Field(default_factory=StepEnrichment)


class RouteLeg(BaseModel):
    """Matches Google's RouteLeg exactly."""
    distanceMeters: int
    duration: str  # e.g. "1820s"
    staticDuration: str  # same as duration (we don't model traffic)
    polyline: Polyline  # leg-level polyline
    startLocation: dict
    endLocation: dict
    steps: list[RouteLegStep]
    travelAdvisory: dict | None = None  # Google field — we return null
    localizedValues: dict | None = None  # Google field — we return null
    stepsOverview: dict | None = None  # Google field — we return null


class Viewport(BaseModel):
    low: dict  # {"latitude": ..., "longitude": ...}
    high: dict  # {"latitude": ..., "longitude": ...}


class Route(BaseModel):
    """Matches Google's Route exactly, plus enrichment on steps."""
    routeLabels: list[str] = Field(default_factory=lambda: ["DEFAULT_ROUTE"])
    legs: list[RouteLeg]
    distanceMeters: int
    duration: str
    staticDuration: str  # same as duration
    polyline: Polyline
    description: str = ""
    warnings: list[str] = Field(default_factory=list)
    viewport: Viewport | None = None
    travelAdvisory: dict | None = None
    optimizedIntermediateWaypointIndex: list[int] = Field(default_factory=list)
    localizedValues: dict | None = None
    routeToken: str | None = None
    polylineDetails: dict | None = None


# --- Google-compatible response (Door 1) ---


class GoogleComputeRoutesResponse(BaseModel):
    """
    Exact match for Google's computeRoutes response body.
    No id, no status, no extra top-level fields.
    """
    routes: list[Route]
    fallbackInfo: dict | None = None
    geocodingResults: dict | None = None


# --- Stripe-style route resource (Door 2) ---


class RouteResource(BaseModel):
    """
    Stateful route resource following Stripe's pattern.
    Contains the Google-compatible route data embedded in the 'routes' field.
    """
    id: str  # route_01HYX3...
    object: str = "route"  # Stripe convention: object type as a field
    status: str = "processing"  # "processing" | "complete" | "failed"
    createdAt: str  # ISO 8601 UTC
    routes: list[Route] = Field(default_factory=list)  # empty while processing
    fallbackInfo: dict | None = None
    geocodingResults: dict | None = None
    warnings: list[str] = Field(default_factory=list)
    error: dict | None = None  # populated on failure


# --- Helpers ---


def make_location(lat: float, lng: float) -> dict:
    return {"latLng": {"latitude": lat, "longitude": lng}}


def compute_viewport(points: list[tuple[float, float]]) -> Viewport:
    """Compute bounding box from a list of (lat, lng) tuples."""
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    return Viewport(
        low={"latitude": min(lats), "longitude": min(lngs)},
        high={"latitude": max(lats), "longitude": max(lngs)},
    )

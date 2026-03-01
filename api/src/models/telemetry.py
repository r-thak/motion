from pydantic import BaseModel, Field


class TelemetrySegment(BaseModel):
    index: int
    startLocation: dict
    endLocation: dict
    distanceMeters: int
    durationSeconds: int
    gradePercent: float | None = None
    curvatureDegreesPerKm: float | None = None
    fuelBurnLiters: float | None = None
    zoneFlags: list[str] = Field(default_factory=list)
    speedLimitKmh: float | None = None
    roadClass: str | None = None


class TelemetrySummary(BaseModel):
    totalFuelBurnLiters: float | None = None
    totalGradeGainMeters: float | None = None
    totalGradeLossMeters: float | None = None
    maxGradePercent: float | None = None
    averageCurvatureDegreesPerKm: float | None = None
    schoolZoneCount: int = 0
    residentialSegmentCount: int = 0


class TelemetryResponse(BaseModel):
    routeId: str
    computedAt: str
    vehicleSpec: dict
    summary: TelemetrySummary
    segments: list[TelemetrySegment]
    hasMore: bool = False
    nextCursor: str | None = None

from pydantic import BaseModel, Field


class StepEnrichment(BaseModel):
    gradePercent: float | None = None
    curvatureDegreesPerKm: float | None = None
    fuelBurnLiters: float | None = None
    stressFactor: float | None = None
    zoneFlags: list[str] = Field(default_factory=list)
    speedLimitKmh: float | None = None
    roadClass: str | None = None
    degradedFields: list[str] = Field(default_factory=list)
    degradedReason: str | None = None

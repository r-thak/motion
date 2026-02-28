from pydantic import BaseModel, Field


class DriverState(BaseModel):
    fatigueScore: float = Field(0.0, ge=0.0, le=1.0)
    attentionScore: float = Field(1.0, ge=0.0, le=1.0)
    stressLevel: float = Field(0.0, ge=0.0, le=1.0)


class DriverStateUpdate(BaseModel):
    fatigueScore: float | None = Field(None, ge=0.0, le=1.0)
    attentionScore: float | None = Field(None, ge=0.0, le=1.0)
    stressLevel: float | None = Field(None, ge=0.0, le=1.0)

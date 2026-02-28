from pydantic import BaseModel


VEHICLE_PRESETS: dict[str, dict] = {
    "SEMI_TRAILER": {
        "grossWeightKg": 36000.0,
        "dragCoefficient": 0.65,
        "frontalAreaM2": 9.0,
        "rollingResistance": 0.007,
        "heightM": 4.11,
        "widthM": 2.6,
        "lengthM": 21.64,
        "axleLoadTonnes": 9.0,
        "fuelType": "DIESEL",
        "idleFuelLitersPerHour": 2.5,
        "engineEfficiency": 0.40,
    },
    "BOX_TRUCK": {
        "grossWeightKg": 12000.0,
        "dragCoefficient": 0.7,
        "frontalAreaM2": 7.5,
        "rollingResistance": 0.007,
        "heightM": 3.5,
        "widthM": 2.44,
        "lengthM": 10.0,
        "axleLoadTonnes": 5.0,
        "fuelType": "DIESEL",
        "idleFuelLitersPerHour": 1.8,
        "engineEfficiency": 0.38,
    },
}


class VehicleSpec(BaseModel):
    type: str = "SEMI_TRAILER"
    grossWeightKg: float | None = None
    dragCoefficient: float | None = None
    frontalAreaM2: float | None = None
    rollingResistance: float | None = None
    heightM: float | None = None
    widthM: float | None = None
    lengthM: float | None = None
    axleLoadTonnes: float | None = None
    fuelType: str = "DIESEL"
    idleFuelLitersPerHour: float | None = None
    engineEfficiency: float | None = None


def resolve_vehicle_spec(spec: VehicleSpec | None) -> dict:
    """Resolve a VehicleSpec against presets, returning a flat dict of all values."""
    if spec is None:
        return dict(VEHICLE_PRESETS["SEMI_TRAILER"])

    preset_name = spec.type if spec.type in VEHICLE_PRESETS else "SEMI_TRAILER"
    resolved = dict(VEHICLE_PRESETS[preset_name])

    for field_name in [
        "grossWeightKg", "dragCoefficient", "frontalAreaM2", "rollingResistance",
        "heightM", "widthM", "lengthM", "axleLoadTonnes", "fuelType",
        "idleFuelLitersPerHour", "engineEfficiency",
    ]:
        value = getattr(spec, field_name)
        if value is not None:
            resolved[field_name] = value

    return resolved

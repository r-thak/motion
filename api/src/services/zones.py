"""Zone polygon spatial index and intersection checking."""

from datetime import datetime, timezone, timedelta

import asyncpg
from shapely import STRtree
from shapely.geometry import Point, shape


class ZoneIndex:
    """In-memory spatial index of school and residential zone polygons."""

    def __init__(self):
        self.school_geoms: list = []
        self.school_names: list[str] = []
        self.residential_geoms: list = []
        self.tree_school: STRtree | None = None
        self.tree_residential: STRtree | None = None

    async def load(self, pg_pool: asyncpg.Pool) -> None:
        """Load all zone polygons from the zone_polygons table. Build STRtree indices."""
        try:
            rows = await pg_pool.fetch("SELECT zone_type, name, geometry FROM zone_polygons")
        except asyncpg.UndefinedTableError:
            # Table doesn't exist yet — no zones to load
            return

        for row in rows:
            geom = shape(row["geometry"])
            if row["zone_type"] == "school":
                self.school_geoms.append(geom)
                self.school_names.append(row["name"] or "")
            elif row["zone_type"] == "residential":
                self.residential_geoms.append(geom)

        if self.school_geoms:
            self.tree_school = STRtree(self.school_geoms)
        if self.residential_geoms:
            self.tree_residential = STRtree(self.residential_geoms)

    def check_zones(self, lat: float, lng: float, buffer_deg: float = 0.002) -> list[str]:
        """Check which zones a point falls within. Returns ["SCHOOL_ZONE", "RESIDENTIAL"] as applicable."""
        flags = []
        point = Point(lng, lat)  # Shapely uses (x, y) = (lng, lat)
        buffered = point.buffer(buffer_deg)

        if self.tree_school is not None:
            hits = self.tree_school.query(buffered)
            for idx in hits:
                if self.school_geoms[idx].intersects(buffered):
                    flags.append("SCHOOL_ZONE")
                    break

        if self.tree_residential is not None:
            hits = self.tree_residential.query(buffered)
            for idx in hits:
                if self.residential_geoms[idx].intersects(buffered):
                    flags.append("RESIDENTIAL")
                    break

        return flags

    def is_school_zone_active(self, departure_time_iso: str | None, step_offset_seconds: int) -> bool:
        """
        Determine if school zones are active at the time a step would be traversed.
        1. If departure_time_iso is None, assume active (conservative).
        2. Parse as UTC datetime. Add step_offset_seconds.
        3. Convert to Chicago local time (hardcoded UTC-6 for hackathon).
        4. Active if weekday AND (07:00–09:00 OR 14:00–16:00).
        """
        if departure_time_iso is None:
            return True

        try:
            dt = datetime.fromisoformat(departure_time_iso.replace("Z", "+00:00"))
            dt = dt + timedelta(seconds=step_offset_seconds)
            # Convert to Chicago time (UTC-6)
            chicago_offset = timedelta(hours=-6)
            chicago_time = dt + chicago_offset
            # Check weekday (0=Monday, 6=Sunday)
            if chicago_time.weekday() >= 5:  # Weekend
                return False
            hour = chicago_time.hour
            if (7 <= hour < 9) or (14 <= hour < 16):
                return True
            return False
        except (ValueError, AttributeError):
            return True  # Conservative: assume active on parse errors

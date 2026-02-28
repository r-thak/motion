"""Load OSM school and residential zone polygons into the zone_polygons table."""

import asyncio
import json
import sys

import asyncpg


async def load_zones(database_url: str, geojson_path: str) -> None:
    """
    Load zone polygons from a GeoJSON file into the zone_polygons table.

    The GeoJSON file should have features with properties:
    - zone_type: "school" or "residential"
    - name: optional name string

    Usage:
        python -m src.scripts.load_zones <geojson_path>
    """
    pool = await asyncpg.create_pool(database_url)

    with open(geojson_path, "r") as f:
        data = json.load(f)

    features = data.get("features", [])
    count = 0

    for feature in features:
        props = feature.get("properties", {})
        zone_type = props.get("zone_type", "")
        if zone_type not in ("school", "residential"):
            continue

        name = props.get("name", "")
        geometry = feature.get("geometry", {})
        geom_json = json.dumps(geometry)

        # Compute bounding box from coordinates
        coords = _extract_coords(geometry)
        if not coords:
            continue

        lats = [c[1] for c in coords]
        lngs = [c[0] for c in coords]

        await pool.execute(
            """
            INSERT INTO zone_polygons (zone_type, name, geometry, bbox_min_lat, bbox_max_lat, bbox_min_lng, bbox_max_lng)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            zone_type,
            name,
            geom_json,
            min(lats),
            max(lats),
            min(lngs),
            max(lngs),
        )
        count += 1

    await pool.close()
    print(f"Loaded {count} zone polygons from {geojson_path}")


def _extract_coords(geometry: dict) -> list[list[float]]:
    """Extract all coordinate pairs from a GeoJSON geometry."""
    coords = []
    geom_type = geometry.get("type", "")
    raw_coords = geometry.get("coordinates", [])

    if geom_type == "Point":
        coords.append(raw_coords)
    elif geom_type in ("LineString", "MultiPoint"):
        coords.extend(raw_coords)
    elif geom_type in ("Polygon", "MultiLineString"):
        for ring in raw_coords:
            coords.extend(ring)
    elif geom_type in ("MultiPolygon",):
        for polygon in raw_coords:
            for ring in polygon:
                coords.extend(ring)
    return coords


if __name__ == "__main__":
    from src.config import settings

    if len(sys.argv) < 2:
        print("Usage: python -m src.scripts.load_zones <geojson_path>")
        sys.exit(1)

    asyncio.run(load_zones(settings.database_url, sys.argv[1]))

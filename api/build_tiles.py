#!/usr/bin/env python3
"""
Build Valhalla routing tiles from OSM PBF data.

Usage:
    python build_tiles.py

This script:
  1. Generates a Valhalla config pointing at ./valhalla_data/valhalla_tiles
  2. Runs valhalla_build_tiles against the downloaded .osm.pbf file
  3. Verifies the tile output

Prerequisites:
  - pip install pyvalhalla
  - Download an OSM PBF extract to ./valhalla_data/sf.osm.pbf
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import valhalla

# Paths
BASE_DIR = Path(__file__).parent.parent  # workspace root
DATA_DIR = BASE_DIR / "valhalla_data"
TILE_DIR = DATA_DIR / "valhalla_tiles"
CONFIG_FILE = DATA_DIR / "valhalla.json"
PBF_FILE = DATA_DIR / "sf.osm.pbf"

# Find the bundled valhalla_build_tiles binary
VALHALLA_PKG_DIR = Path(valhalla.__file__).parent
BUILD_TILES_BIN = VALHALLA_PKG_DIR / "bin" / "valhalla_build_tiles"
BUILD_ADMINS_BIN = VALHALLA_PKG_DIR / "bin" / "valhalla_build_admins"


def main():
    if not PBF_FILE.exists():
        print(f"❌ OSM PBF file not found at {PBF_FILE}")
        print("   Download it with:")
        print("   curl -L -o valhalla_data/sf.osm.pbf https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf")
        sys.exit(1)

    print(f"📦 OSM PBF: {PBF_FILE} ({PBF_FILE.stat().st_size / 1e6:.0f} MB)")

    # Create tile directory
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📂 Tile dir: {TILE_DIR}")

    # Generate default config — pass empty strings to avoid strict path resolution
    # (tile_dir doesn't exist yet since we're about to build it)
    config = valhalla.get_config(tile_dir="", tile_extract="")
    # Set paths manually
    config["mjolnir"]["tile_dir"] = str(TILE_DIR)
    config["mjolnir"]["tile_extract"] = ""
    config["mjolnir"]["logging"] = {"type": "std_out", "color": True}
    config["mjolnir"]["concurrency"] = max(1, os.cpu_count() - 1)

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"⚙️  Config: {CONFIG_FILE}")

    # Build tiles
    print(f"\n🔨 Building Valhalla routing tiles (this may take several minutes)...")
    print(f"   Using: {BUILD_TILES_BIN}")
    print(f"   Input: {PBF_FILE}\n")

    cmd = [str(BUILD_TILES_BIN), "--config", str(CONFIG_FILE), str(PBF_FILE)]
    result = subprocess.run(cmd, cwd=str(DATA_DIR))

    if result.returncode != 0:
        print(f"\n❌ Tile build failed with exit code {result.returncode}")
        sys.exit(1)

    # Count tiles
    tile_count = sum(1 for _ in TILE_DIR.rglob("*.gph"))
    total_size = sum(f.stat().st_size for f in TILE_DIR.rglob("*") if f.is_file())

    print(f"\n✅ Tile build complete!")
    print(f"   Tiles: {tile_count} graph files")
    print(f"   Size:  {total_size / 1e6:.0f} MB")
    print(f"   Dir:   {TILE_DIR}")
    print(f"\n   You can now run: python dev_server.py --real")


if __name__ == "__main__":
    main()

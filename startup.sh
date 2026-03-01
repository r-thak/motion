#!/bin/bash
set -e

echo "============================================================"
echo " Starting First-Time Setup: Motion Freight Router (Real Mode)"
echo "============================================================"

# Navigate to the project root
if [ -d "/workspaces/motion" ]; then
    cd /workspaces/motion
else
    echo "Error: /workspaces/motion directory not found."
    exit 1
fi

echo "[1/4] Installing pyvalhalla..."
pip install pyvalhalla

echo "[2/4] Setting up Valhalla Grid for San Francisco / NorCal..."
mkdir -p valhalla_data

if [ ! -f "valhalla_data/sf.osm.pbf" ]; then
    echo "Downloading San Francisco / NorCal OSM PBF data..."
    curl -L -o valhalla_data/sf.osm.pbf https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf
else
    echo "OSM PBF data already exists. Skipping download."
fi

if [ ! -d "valhalla_data/valhalla_tiles" ] || [ -z "$(ls -A valhalla_data/valhalla_tiles 2>/dev/null)" ]; then
    echo "Building Valhalla routing tiles (this may take a while)..."
    cd api
    python build_tiles.py
    cd ..
else
    echo "Valhalla routing tiles already built. Skipping build."
fi

echo "[3/4] Starting Redis Server..."
# Avoid starting a new Redis instance if one is already listening on the default port
if ! pgrep -x "redis-server" > /dev/null; then
    redis-server --daemonize yes
else
    echo "Redis is already running."
fi

echo "[4/4] Starting Dev Server in Real Mode..."
cd api
python dev_server.py --real

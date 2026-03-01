#!/bin/bash
set -e

# Resolve the absolute path of the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo " Starting First-Time Setup: Motion Freight Router"
echo "============================================================"

if [ "$1" == "--local" ]; then
    echo "Mode: Local Python Setup (--local)"
    echo "------------------------------------------------------------"
    
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
    if ! command -v redis-server &> /dev/null; then
        echo "Warning: redis-server not found. You may need to install redis."
    elif ! pgrep -x "redis-server" > /dev/null; then
        redis-server --daemonize yes
    else
        echo "Redis is already running."
    fi

    echo "[4/4] Starting Demos and Dev Server..."
    echo "Starting Demo 1 (MotionDemo) on port 8001..."
    (cd MotionDemo && python3 -m http.server 8001 &)
    echo "Starting Demo 2 (secondDemo) on port 8002..."
    (cd secondDemo && python3 -m http.server 8002 &)

    cd api
    python dev_server.py --real

else
    echo "Mode: Docker Compose (Default - Recommended)"
    echo "Tip: To run the local python version instead, use: ./startup.sh --local"
    echo "------------------------------------------------------------"

    if ! command -v docker &> /dev/null; then
        echo "Error: docker is not installed. Please install Docker or run with: ./startup.sh --local"
        exit 1
    fi

    echo "Building and starting Docker containers..."
    echo "Note: The Valhalla container will automatically download OSM PBF data"
    echo "and build routing tiles on the first run (this may take 15-30 mins)."
    echo ""

    if docker compose version &> /dev/null; then
        docker compose up -d --build
    elif command -v docker-compose &> /dev/null; then
        docker-compose up -d --build
    else
        echo "Error: docker compose is not available."
        exit 1
    fi

    echo ""
    echo "✅ Success! Containers are starting up."
    echo ""
    echo "Starting Demo 1 (MotionDemo) on port 8001..."
    (cd MotionDemo && python3 -m http.server 8001 &)
    echo "Starting Demo 2 (secondDemo) on port 8002..."
    (cd secondDemo && python3 -m http.server 8002 &)
    echo ""
    echo "To monitor the Valhalla tile building progress, run:"
    echo "  docker compose logs -f valhalla"
    echo ""
    echo "Once complete, the API will be available at: http://localhost:8000"
    echo "Interactive API Docs: http://localhost:8000/docs"
fi

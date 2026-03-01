#!/bin/bash

# Resolve the absolute path of the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo " Stopping Motion Freight Router Services..."
echo "============================================================"

# 1. Stop Python Dev Server if running in the background
echo "[1/4] Stopping Python Dev Server (dev_server.py)..."
if pkill -f "python dev_server.py" &> /dev/null; then
    echo "  - API Dev Server stopped."
else
    echo "  - API Dev Server was not running."
fi

# 2. Stop background HTTP servers for Demo 1 and Demo 2
echo "[2/4] Stopping background Demo HTTP servers (ports 8001, 8002)..."
if pkill -f "python3.*serve_with_cors.py 8001" &> /dev/null || pkill -f "python3 -m http.server 8001" &> /dev/null; then
    echo "  - Demo 1 (port 8001) stopped."
else
    echo "  - Demo 1 (port 8001) was not running."
fi

if pkill -f "python3.*serve_with_cors.py 8002" &> /dev/null || pkill -f "python3 -m http.server 8002" &> /dev/null; then
    echo "  - Demo 2 (port 8002) stopped."
else
    echo "  - Demo 2 (port 8002) was not running."
fi

# 3. Stop Docker Compose services
echo "[3/4] Stopping Docker containers (if any)..."
if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    docker compose down
elif command -v docker-compose &> /dev/null; then
    docker-compose down
else
    echo "  - Docker / Docker Compose not found, skipping container shutdown."
fi

# 4. Stop local Redis instance (useful for --local mode)
echo "[4/4] Stopping local Redis server (if any)..."
if command -v redis-cli &> /dev/null; then
    if redis-cli ping &> /dev/null; then
        redis-cli shutdown
        echo "  - Local Redis server stopped."
    else
        echo "  - Local Redis server was not running."
    fi
else
    echo "  - redis-cli not found, skipping Redis shutdown."
fi

echo "============================================================"
echo " All services stopped successfully."
echo "============================================================"

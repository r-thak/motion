# Motion Freight Router API Context

This document provides a comprehensive overview of the `motion` workspace to help Large Language Models (LLMs) understand the project's architecture, tech stack, and core business logic.

## Overview
The application is **Freight Router API**, a drop-in API replacement for the Google Routes API specifically designed to support **heavy freight** operations. It enriches standard mapping routes (turn-by-turn navigation) with segment-level physics data such as elevation grades, curvature, fuel burn estimations, and driver stress/fatigue factors. 

## Technology Stack
- **Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12+)
- **Routing Engine**: [Valhalla](https://github.com/valhalla/valhalla) (open-source routing engine running locally via Docker)
- **Database**: PostgreSQL 17 accessed asynchronously using `asyncpg` and managed with `alembic` for migrations.
- **Cache**: Redis 7 for caching route requests and telemetry responses.
- **Data Validation & Parsing**: Pydantic v2
- **Orchestration**: Docker Compose (`docker-compose.yml` configures API, Postgres, Redis, and Valhalla services).

## Core Architecture and Data Flow
When a client requests a route (e.g., via the `/directions/v2:computeRoutes` endpoint):
1. **Request Translation**: The API translates the Google Routes compatible request into a format understood by the underlying Valhalla routing engine (handling locations, truck-specific costing options, languages, etc.).
2. **Base Routing**: A call is made to the local Valhalla container (`http://valhalla:8002/route`) to calculate the geometric and geographical route path.
3. **Valhalla Response Translation**: The system translates Valhalla’s proprietary JSON response format back into the unified response models.
4. **Segment-Level Enrichment (`src/services/enrichment.py`)**: The application orchestrates several specific data enrichment modules across all segments/steps in the route:
   - **Elevation & Grade (`src/services/elevation.py`)**: Fetches elevation profiles and calculates the road grade percentage.
   - **Curvature (`src/services/curvature.py`)**: Calculates the turn severity and road curvature.
   - **Physics & Fuel (`src/services/physics.py`)**: Estimates fuel burn based on a combination of physical parameters (vehicle weight, road grade, speed, and distance).
   - **Zoning (`src/services/zones.py`)**: Through geometric point-in-polygon tests (e.g., Shapely), it checks if passing through specific areas like school zones or residential areas. Filtering is applied (e.g., whether the school zone is currently active based on departure time).
   - **Driver Stress / Fatigue**: Computes a driving stress factor that combines maneuver complexity (e.g., turns vs. straight lines), known fatigue multipliers, and environmental zoning.
5. **Persistence & Caching**: The enriched route and computed telemetry metrics are cached into Redis and permanently stored in PostgreSQL.
6. **Response Generation**: The final payload generated aligns with standard Google Compute Routes objects alongside attached segment-by-segment freight physics arrays (`telemetry`).

## Project Layout (`api/src/`)
- `main.py`: The entry point for FastAPI. It establishes connection pools (`asyncpg` and Redis), initializes HTTP clients, loads routing zones into memory (`ZoneIndex`), and runs DB migrations programmatically.
- `routes/`: Contains different FastAPI routers (endpoints).
  - `google_compat.py`: The drop-in synchronous endpoint substituting Google's spec.
  - `routes.py`: Native or custom routing endpoints.
  - `telemetry.py` & `driver_state.py`: Supporting data collection and driver endpoints.
- `models/`: Pydantic schema declarations dividing into `driver.py`, `enrichment.py`, `request.py`, `response.py`, `telemetry.py`, and `vehicle.py`.
- `services/`: Business logic, calculations, and external system integrations.
  - Core modules: `enrichment.py`, `valhalla.py`, `physics.py`, `curvature.py`, `elevation.py`, `zones.py`.
- `storage/`: Persistence layer abstractions.
  - `postgres.py`: SQL execution and persistence.
  - `redis.py`: Memory-store functions.
- `middleware/`: HTTP middlewares enforcing structured error handling (`errors.py`) and throttling (`rate_limit.py`).
- `scripts/`: Standalone utility scripts like `prewarm_elevation.py`.

## Running and Development
- A `docker-compose.yml` is provided at the workspace root to orchestrate dependencies (Postgres, Valhalla, Redis) mirroring a production-like environment.
- The `valhalla` container depends on downloaded map tiles (configured using the scriptable image `ghcr.io/valhalla/valhalla-scripted`).

## Contextual Understanding for LLMs
- This project acts essentially as a smart "proxy" to an open-source routing engine, layering heavy vehicle operational constraints and telemetry on top.
- Pay attention to `src.models.vehicle` and `src.models.driver` models when working with logic adjustments, as they dictate the heavy-freight and human metrics altering the navigation.
- Modifications to route-building metrics usually require touching `src/services/enrichment.py` and the respective localized service files (e.g. `physics.py`).

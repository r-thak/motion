# Motion Freight Router API - Quick Reference

> **Drop-in replacement for the Google Routes API**, enriched with segment-level physics data for heavy freight: fuel burn, road grade, curvature, driver stress, and zone-aware routing.

---

## 🚀 Starting the Server

### Real Mode (real Valhalla + Redis + USGS elevation)

```bash
redis-server --daemonize yes
cd api && python dev_server.py --real
```

### Mock Mode (no dependencies needed)

```bash
cd api && python dev_server.py
```

| URL | Description |
|-----|-------------|
| http://localhost:8000/health | Health check |
| http://localhost:8000/docs | Swagger UI (interactive) |
| http://localhost:8000/redoc | ReDoc documentation |

### What's Real vs Mocked

| Component | `--real` mode | default mode |
|-----------|--------------|--------------|
| **Valhalla routing** | ✅ Real (pyvalhalla, SF/NorCal tiles) | Synthetic geometry |
| **Redis** | ✅ Real (localhost:6379) | In-memory dict |
| **Elevation** | ✅ Real (USGS 3DEP API) | Synthetic values |
| **Postgres** | In-memory dict | In-memory dict |
| **Physics engine** | ✅ Real (NumPy) | ✅ Real (NumPy) |

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/directions/v2:computeRoutes` | Google-compatible sync route computation |
| `POST` | `/v1/routes` | Stripe-style async/sync route creation |
| `GET` | `/v1/routes/{route_id}` | Retrieve a route by ID |
| `DELETE` | `/v1/routes/{route_id}` | Delete a route |
| `GET` | `/v1/routes/{route_id}/telemetry` | Get physics telemetry for a route |
| `PATCH` | `/v1/routes/{route_id}/driver-state` | Update driver fatigue and re-score |
| `GET` | `/health` | Health check |

---

## 🔧 curl Examples (Every Endpoint)

> **One-liner format** - works on Linux, macOS, and Windows. For readable versions, see the JSON blocks below each command.

---

### `GET /health` - Health Check

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok", "mode": "real"}
```

---

### `POST /directions/v2:computeRoutes` - Compute Routes (Google-compatible)

**Minimal request** (defaults to SEMI_TRAILER, balanced profile):

```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7694,\"longitude\":-122.4862}}}}"
```

**Full request** - Semi Trailer, fuel-optimal:

```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7694,\"longitude\":-122.4862}}},\"vehicleSpec\":{\"type\":\"SEMI_TRAILER\",\"grossWeightKg\":36000},\"routingProfile\":\"fuel_optimal\"}"
```

<details>
<summary>Readable JSON body</summary>

```json
{
  "origin": {
    "location": { "latLng": { "latitude": 37.7749, "longitude": -122.4194 } }
  },
  "destination": {
    "location": { "latLng": { "latitude": 37.7694, "longitude": -122.4862 } }
  },
  "vehicleSpec": { "type": "SEMI_TRAILER", "grossWeightKg": 36000 },
  "routingProfile": "fuel_optimal"
}
```
</details>

**Box Truck to SFO, fuel-optimal, avoid tolls:**

```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7955,\"longitude\":-122.3937}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.6213,\"longitude\":-122.3790}}},\"vehicleSpec\":{\"type\":\"BOX_TRUCK\",\"grossWeightKg\":10000},\"routingProfile\":\"fuel_optimal\",\"routeModifiers\":{\"avoidTolls\":true}}"
```

**Alternative routes:**

```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7850,\"longitude\":-122.4094}}},\"computeAlternativeRoutes\":true,\"vehicleSpec\":{\"type\":\"SEMI_TRAILER\"}}"
```

**With X-Goog-FieldMask header** (accepted for Google compatibility, ignored):

```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -H "X-Goog-FieldMask: routes.duration,routes.distanceMeters" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7694,\"longitude\":-122.4862}}}}"
```

Example response (abbreviated):

```json
{
  "routes": [{
    "distanceMeters": 8323,
    "duration": "801s",
    "polyline": { "encodedPolyline": "..." },
    "legs": [{
      "steps": [{
        "distanceMeters": 1204,
        "staticDuration": "142s",
        "navigationInstruction": {
          "maneuver": "DEPART",
          "instructions": "Drive northeast on Market Street."
        },
        "enrichment": {
          "gradePercent": 1.04,
          "curvatureDegreesPerKm": 15.3,
          "fuelBurnLiters": 0.0812,
          "stressFactor": 0.2,
          "zoneFlags": [],
          "degradedFields": [],
          "degradedReason": null
        }
      }]
    }]
  }]
}
```

---

### `POST /v1/routes` - Create Route (Stripe-style)

```bash
curl -X POST http://localhost:8000/v1/routes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7694,\"longitude\":-122.4862}}},\"vehicleSpec\":{\"type\":\"SEMI_TRAILER\"}}"
```

**With idempotency key** (safe retries):

```bash
curl -X POST http://localhost:8000/v1/routes -H "Content-Type: application/json" -H "Idempotency-Key: my-unique-key-123" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7694,\"longitude\":-122.4862}}},\"vehicleSpec\":{\"type\":\"SEMI_TRAILER\"}}"
```

Example response:
```json
{
  "id": "route_01HYX3MPK5XJQJG0NB0PRSDVWC",
  "object": "route",
  "status": "complete",
  "createdAt": "2026-03-01T04:30:00Z",
  "routes": [ "..." ],
  "error": null
}
```

---

### `GET /v1/routes/{route_id}` - Retrieve Route

```bash
curl http://localhost:8000/v1/routes/ROUTE_ID_HERE
```

---

### `DELETE /v1/routes/{route_id}` - Delete Route

```bash
curl -X DELETE http://localhost:8000/v1/routes/ROUTE_ID_HERE
```

Example response:
```json
{
  "id": "route_01HYX3MPK5XJQJG0NB0PRSDVWC",
  "object": "route",
  "deleted": true
}
```

---

### `GET /v1/routes/{route_id}/telemetry` - Get Telemetry

**Default (first 50 segments):**

```bash
curl "http://localhost:8000/v1/routes/ROUTE_ID_HERE/telemetry"
```

**With pagination:**

```bash
curl "http://localhost:8000/v1/routes/ROUTE_ID_HERE/telemetry?cursor=0&limit=10"
```

Example response (abbreviated):
```json
{
  "routeId": "route_01HYX3MPK5XJQJG0NB0PRSDVWC",
  "computedAt": "2026-03-01T04:30:00Z",
  "summary": {
    "totalFuelBurnLiters": 3.456,
    "totalGradeGainMeters": 85.2,
    "totalGradeLossMeters": 62.1
  },
  "segments": [{
    "index": 0,
    "distanceMeters": 1204,
    "gradePercent": 1.04,
    "fuelBurnLiters": 0.0812
  }],
  "hasMore": true,
  "nextCursor": 10
}
```

---

### Full Lifecycle Example (Create → Get → Telemetry → Update → Delete)

```bash
# 1. Create a route
ROUTE=$(curl -s -X POST http://localhost:8000/v1/routes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":37.7749,\"longitude\":-122.4194}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":37.7694,\"longitude\":-122.4862}}},\"vehicleSpec\":{\"type\":\"SEMI_TRAILER\"}}")
echo "$ROUTE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"

# 2. Get route (paste the ID from step 1)
curl -s http://localhost:8000/v1/routes/ROUTE_ID_HERE | python3 -m json.tool

# 3. Get telemetry
curl -s "http://localhost:8000/v1/routes/ROUTE_ID_HERE/telemetry?limit=5" | python3 -m json.tool

# 4. Delete route
curl -s -X DELETE http://localhost:8000/v1/routes/ROUTE_ID_HERE
```

---

### Error Examples

**Missing required fields (400):**
```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -d "{}"
```

**Route not found (404):**
```bash
curl http://localhost:8000/v1/routes/route_nonexistent
```

**Invalid coordinates (422):**
```bash
curl -X POST http://localhost:8000/directions/v2:computeRoutes -H "Content-Type: application/json" -d "{\"origin\":{\"location\":{\"latLng\":{\"latitude\":0,\"longitude\":0}}},\"destination\":{\"location\":{\"latLng\":{\"latitude\":0.001,\"longitude\":0.001}}}}"
```

---

## 📦 Vehicle Presets

| Preset | Weight | Drag | Frontal Area | Engine Eff. |
|--------|--------|------|--------------|-------------|
| `SEMI_TRAILER` | 36,000 kg | 0.65 | 9.0 m² | 40% |
| `BOX_TRUCK` | 12,000 kg | 0.70 | 7.5 m² | 38% |

Override any field:
```json
{"vehicleSpec": {"type": "SEMI_TRAILER", "grossWeightKg": 44000, "dragCoefficient": 0.60}}
```

---

## 🎯 Routing Profiles

| Profile | Fuel | Grade | Zone |
|---------|------|-------|------|
| `balanced` | 1.0 | 1.0 | 1.0 |
| `fuel_optimal` | 2.0 | 1.5 | 0.5 |
| `time_optimal` | 0.3 | 0.3 | 0.3 |

Override specific weights:
```json
{"routingProfile": "balanced", "profileOverrides": {"fuelWeight": 3.0, "zoneWeight": 0.1}}
```

---

## 🔑 Enrichment Fields (per step)

| Field | Type | Description |
|-------|------|-------------|
| `gradePercent` | float | Road grade (positive = uphill). Real USGS elevation data in `--real` mode |
| `curvatureDegreesPerKm` | float | Turn severity per kilometer |
| `fuelBurnLiters` | float | Estimated diesel consumption for this segment |
| `zoneFlags` | string[] | `"SCHOOL_ZONE"`, `"RESIDENTIAL"` if applicable |
| `speedLimitKmh` | float | Speed limit (when available) |
| `roadClass` | string | Road classification |
| `degradedFields` | string[] | Fields with degraded accuracy |
| `degradedReason` | string | Why fields are degraded |

---

## 🔨 Building Tiles (First-Time Setup)

If you need to rebuild tiles from scratch:

```bash
pip install pyvalhalla
curl -L -o valhalla_data/sf.osm.pbf https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf
cd api && python build_tiles.py
```

This downloads ~605MB of NorCal map data and builds ~736MB of routing tiles (~35 min).

---

## 🏃 Running Tests

```bash
cd api && python -m pytest test_runner.py -v -s
```

27 end-to-end tests covering all endpoints, vehicle presets, routing profiles, and enrichment validation.

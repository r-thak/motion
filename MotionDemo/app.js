/**
 * Motion Route Demo — Fleet simulation comparing our fuel-efficient routing
 * vs Google Routes API. Uses coordinates only (no geocoding). Speed-based animation; live trails.
 */

(function () {
  'use strict';

  const SF_CENTER = [37.7749, -122.4194];
  const SF_BOUNDS = { north: 37.78, south: 37.712, west: -122.48, east: -122.39 };
  const NUM_VEHICLES = 4;
  const MAX_WAYPOINTS_PER_VEHICLE = 8;
  const WAYPOINTS_TOTAL = 20;
  const DEPOT = { lat: 37.7849, lng: -122.4094 };
  const PAUSE_BEFORE_RESTART_MS = 4500;
  const SIMULATION_SPEED_KMH = 120;
  const USGS_BASE = 'https://epqs.nationalmap.gov/v1/json';
  const GOOGLE_MAPS_CALLBACK = '__motionMapsCallback';

  const OUR_TRAIL_COLOR = '#0ea5e9';
  const OTHER_TRAIL_COLOR = '#f97316';

  const VEHICLE_SIZES = ['small', 'medium', 'large'];
  const TURN_PENALTY_BY_SIZE = { small: 1, medium: 1.8, large: 2.8 };
  const BASE_FUEL_PER_KM = 0.08;
  const FUEL_PER_METER_ELEVATION_GAIN = 0.002;

  let cycleCount = 0;
  let elevationCache = new Map();
  let running = false;
  let RouteClass = null;

  function getConfig() {
    const key = document.getElementById('api-key').value.trim();
    const ourUrl = document.getElementById('our-api-url').value.trim();
    return { apiKey: key, ourRouteApiBase: ourUrl || null };
  }

  function saveConfig(apiKey, ourApiBase) {
    try {
      localStorage.setItem('motion_google_api_key', apiKey);
      localStorage.setItem('motion_our_api_base', ourApiBase || '');
    } catch (_) { }
  }

  function loadSavedConfig() {
    try {
      const key = localStorage.getItem('motion_google_api_key') || '';
      const our = localStorage.getItem('motion_our_api_base') || '';
      document.getElementById('api-key').value = key;
      document.getElementById('our-api-url').value = our;
    } catch (_) { }
  }

  function cacheKey(lat, lng) {
    return `${lat.toFixed(5)},${lng.toFixed(5)}`;
  }

  function loadGoogleMaps(apiKey) {
    return new Promise((resolve, reject) => {
      if (window.google && window.google.maps) {
        ensureRouteClass().then(resolve).catch(reject);
        return;
      }
      window[GOOGLE_MAPS_CALLBACK] = function () {
        window[GOOGLE_MAPS_CALLBACK] = null;
        ensureRouteClass().then(resolve).catch(reject);
      };
      const script = document.createElement('script');
      script.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(apiKey) + '&callback=' + GOOGLE_MAPS_CALLBACK + '&v=beta';
      script.async = true;
      script.onerror = () => reject(new Error('Failed to load Google Maps'));
      document.head.appendChild(script);
    });
  }

  function ensureRouteClass() {
    if (RouteClass) return Promise.resolve();
    return google.maps.importLibrary('routes').then(function (lib) {
      RouteClass = lib.Route;
      return RouteClass;
    });
  }

  // ——— Google Routes API (accepts lat/lng — no geocoding needed) ———
  function fetchDirectionsGoogle(origin, dest, intermediates) {
    const request = {
      origin: origin,
      destination: dest,
      travelMode: 'DRIVING',
      fields: ['path', 'distanceMeters', 'durationMillis'],
    };
    if (intermediates && intermediates.length) request.intermediates = intermediates;
    return RouteClass.computeRoutes(request).then((out) => {
      if (!out.routes || !out.routes[0]) throw new Error('No route returned');
      return out;
    });
  }

  function pathFromRoutesResult(result) {
    if (!result || !result.routes || !result.routes[0]) return null;
    const route = result.routes[0];
    if (!route.path || !route.path.length) return null;
    return route.path.map((p) => ({
      lat: typeof p.lat === 'function' ? p.lat() : p.lat,
      lng: typeof p.lng === 'function' ? p.lng() : p.lng,
    }));
  }

  function metricsFromRoutesResult(result) {
    if (!result || !result.routes || !result.routes[0]) return null;
    const route = result.routes[0];
    const distanceM = route.distanceMeters != null ? route.distanceMeters : 0;
    const durationS = route.durationMillis != null ? route.durationMillis / 1000 : 0;
    return {
      distanceKm: distanceM / 1000,
      durationSec: durationS,
      fuelL: null,
      elevGainM: null,
      turnPenalty: null,
    };
  }

  // ——— Legacy Directions result helpers (for Our API REST response) ———
  function pathFromDirectionsResult(result) {
    if (!result || !result.routes || !result.routes[0]) return null;
    const route = result.routes[0];
    const path = [];
    if (route.legs) {
      for (const leg of route.legs) {
        if (leg.steps) {
          for (const step of leg.steps) {
            if (step.path) {
              if (step.path.getLength && step.path.getAt) {
                for (let i = 0; i < step.path.getLength(); i++) {
                  const ll = step.path.getAt(i);
                  path.push({ lat: ll.lat(), lng: ll.lng() });
                }
              } else if (Array.isArray(step.path)) {
                for (const ll of step.path) path.push({ lat: ll.lat(), lng: ll.lng() });
              }
            }
          }
        }
      }
    }
    return path.length ? path : null;
  }

  function metricsFromDirectionsResult(result) {
    if (!result || !result.routes || !result.routes[0]) return null;
    const route = result.routes[0];
    let distanceM = 0, durationS = 0;
    if (route.legs) {
      for (const leg of route.legs) {
        if (leg.distance && leg.distance.value != null) distanceM += leg.distance.value;
        if (leg.duration && leg.duration.value != null) durationS += leg.duration.value;
      }
    }
    return {
      distanceKm: distanceM / 1000,
      durationSec: durationS,
      fuelL: null,
      elevGainM: null,
      turnPenalty: null,
    };
  }

  // ——— Our API (REST fetch — your server must allow CORS) ———
  const GOOGLE_DIRECTIONS_REST = 'https://maps.googleapis.com/maps/api/directions/json';
  function buildDirectionsUrl(origin, dest, waypoints, apiKey, baseUrl) {
    const base = baseUrl || GOOGLE_DIRECTIONS_REST;
    const o = typeof origin === 'string' ? origin : origin.lat + ',' + origin.lng;
    const d = typeof dest === 'string' ? dest : dest.lat + ',' + dest.lng;
    const params = new URLSearchParams({
      origin: o,
      destination: d,
      mode: 'driving',
      units: 'metric',
    });
    if (apiKey) params.set('key', apiKey);
    if (waypoints.length) {
      const wpStr = waypoints.map((p) => (typeof p === 'string' ? p : p.lat + ',' + p.lng)).join('|');
      params.set('waypoints', wpStr);
    }
    return `${base}?${params.toString()}`;
  }

  function fetchDirectionsRest(origin, dest, waypoints, apiKey, baseUrl) {
    const url = buildDirectionsUrl(origin, dest, waypoints, apiKey, baseUrl);
    return fetch(url).then((r) => r.json());
  }

  // ——— Decode Google polyline ———
  function decodePolyline(encoded) {
    const points = [];
    let index = 0,
      lat = 0,
      lng = 0;
    while (index < encoded.length) {
      let b, shift = 0, result = 0;
      do {
        b = encoded.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      const dlat = (result & 1) ? ~(result >> 1) : result >> 1;
      lat += dlat;
      shift = 0;
      result = 0;
      do {
        b = encoded.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      const dlng = (result & 1) ? ~(result >> 1) : result >> 1;
      lng += dlng;
      points.push({ lat: lat * 1e-5, lng: lng * 1e-5 });
    }
    return points;
  }

  function pathFromDirectionsResponse(data) {
    if (!data || data.status !== 'OK' || !data.routes || !data.routes[0]) return null;
    const route = data.routes[0];
    const path = [];
    if (route.legs) {
      for (const leg of route.legs) {
        if (leg.steps) {
          for (const step of leg.steps) {
            if (step.polyline && step.polyline.points) {
              const decoded = decodePolyline(step.polyline.points);
              path.push(...decoded);
            }
          }
        }
      }
    }
    return path.length ? path : null;
  }

  function metricsFromDirectionsResponse(data) {
    if (!data || data.status !== 'OK' || !data.routes || !data.routes[0]) return null;
    const route = data.routes[0];
    let distanceM = 0;
    let durationS = 0;
    if (route.legs) {
      for (const leg of route.legs) {
        if (leg.distance && leg.distance.value) distanceM += leg.distance.value;
        if (leg.duration && leg.duration.value) durationS += leg.duration.value;
      }
    }
    return {
      distanceKm: distanceM / 1000,
      durationSec: durationS,
      fuelL: null,
      elevGainM: null,
      turnPenalty: null,
    };
  }

  // ——— USGS Elevation ———
  function getElevation(lat, lng) {
    const key = cacheKey(lat, lng);
    if (elevationCache.has(key)) return Promise.resolve(elevationCache.get(key));
    const url = `${USGS_BASE}?x=${lng}&y=${lat}&units=Meters`;
    return fetch(url)
      .then((r) => r.json())
      .then((data) => {
        const v = data.value != null ? data.value : 0;
        elevationCache.set(key, v);
        return v;
      })
      .catch(() => {
        elevationCache.set(key, 0);
        return 0;
      });
  }

  function getElevationsBatch(points) {
    return Promise.all(points.map((p) => getElevation(p.lat, p.lng)));
  }

  // ——— Seeded random ———
  function mulberry32(seed) {
    return function () {
      let t = (seed += 0x6d2b79f5);
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function generateWaypoints(seed) {
    const rng = mulberry32(seed);
    const out = [];
    for (let i = 0; i < WAYPOINTS_TOTAL; i++) {
      out.push({
        lat: SF_BOUNDS.south + rng() * (SF_BOUNDS.north - SF_BOUNDS.south),
        lng: SF_BOUNDS.west + rng() * (SF_BOUNDS.east - SF_BOUNDS.west),
      });
    }
    return out;
  }

  /** Use 75 saved waypoints when available; otherwise generate random. Shuffle so each cycle gets a different subset. */
  function getWaypointsForCycle(seed, rng) {
    const pool = window.FIXED_WAYPOINTS_SF;
    if (pool && Array.isArray(pool) && pool.length >= WAYPOINTS_TOTAL) {
      const shuffled = [...pool].sort(() => rng() - 0.5);
      return shuffled.slice(0, WAYPOINTS_TOTAL);
    }
    return generateWaypoints(seed);
  }

  function haversineDist(a, b) {
    const R = 6371000;
    const dLat = ((b.lat - a.lat) * Math.PI) / 180;
    const dLon = ((b.lng - a.lng) * Math.PI) / 180;
    const x =
      Math.sin(dLat / 2) ** 2 +
      Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  }

  function nearestNeighborOrder(waypoints, start) {
    const order = [];
    let remaining = waypoints.map((w) => ({ ...w }));
    let current = start;
    while (remaining.length) {
      let best = 0;
      let bestD = Infinity;
      for (let j = 0; j < remaining.length; j++) {
        const d = haversineDist(current, remaining[j]);
        if (d < bestD) { bestD = d; best = j; }
      }
      const next = remaining[best];
      order.push(next);
      remaining = remaining.filter((_, j) => j !== best);
      current = next;
    }
    return order;
  }

  function ourMethodOrder(waypoints, start, elevations) {
    const getElev = (p) => elevations.get(cacheKey(p.lat, p.lng)) ?? 0;
    const order = [];
    let remaining = waypoints.map((w) => ({ ...w }));
    let current = { ...start };
    while (remaining.length) {
      let bestIdx = 0;
      let bestScore = Infinity;
      for (let j = 0; j < remaining.length; j++) {
        const p = remaining[j];
        const d = haversineDist(current, p) / 1000;
        const elevGain = Math.max(0, getElev(p) - getElev(current));
        const score = d * 0.5 + elevGain * 2;
        if (score < bestScore) { bestScore = score; bestIdx = j; }
      }
      const next = remaining[bestIdx];
      order.push(next);
      remaining = remaining.filter((_, j) => j !== bestIdx);
      current = next;
    }
    return order;
  }

  function turnAngleDeg(a, b, c) {
    const v1 = { x: b.lng - a.lng, y: b.lat - a.lat };
    const v2 = { x: c.lng - b.lng, y: c.lat - b.lat };
    const dot = v1.x * v2.x + v1.y * v2.y;
    const cross = v1.x * v2.y - v1.y * v2.x;
    return Math.abs((Math.atan2(cross, dot) * 180) / Math.PI);
  }

  function computeRouteMetricsFromPath(path, elevations, vehicleSize) {
    if (!path || path.length < 2) return { distanceKm: 0, fuelL: 0, elevGainM: 0, turnPenalty: 0, path };
    const getElev = (p) => elevations.get(cacheKey(p.lat, p.lng)) ?? 0;
    const mult = TURN_PENALTY_BY_SIZE[vehicleSize] ?? 1;
    let totalDist = 0, totalElevGain = 0, turnPenalty = 0;
    for (let i = 1; i < path.length; i++) {
      const prev = path[i - 1];
      const curr = path[i];
      totalDist += haversineDist(prev, curr);
      totalElevGain += Math.max(0, getElev(curr) - getElev(prev));
      if (i >= 2) turnPenalty += (turnAngleDeg(path[i - 2], prev, curr) / 90) * mult;
    }
    const fuel =
      (totalDist / 1000) * BASE_FUEL_PER_KM +
      totalElevGain * FUEL_PER_METER_ELEVATION_GAIN +
      turnPenalty * 0.05;
    return {
      distanceKm: totalDist / 1000,
      fuelL: Math.max(0, fuel),
      elevGainM: totalElevGain,
      turnPenalty,
      path,
    };
  }

  function assignWaypointsToVehicles(waypoints, nVehicles, rng) {
    const shuffled = [...waypoints].sort(() => rng() - 0.5);
    const per = Math.min(MAX_WAYPOINTS_PER_VEHICLE, Math.ceil(shuffled.length / nVehicles));
    const out = [];
    for (let v = 0; v < nVehicles; v++) {
      const slice = shuffled.slice(v * per, (v + 1) * per).filter(Boolean);
      if (slice.length) out.push(slice);
    }
    return out;
  }

  let mapOur, mapOther;
  let fleetOur = [];
  let fleetOther = [];

  function createMap(id, center, zoom) {
    const map = L.map(id, { center, zoom, zoomControl: false });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OSM, CARTO',
    }).addTo(map);
    return map;
  }

  function computePathSegmentLengths(path) {
    if (!path || path.length < 2) return { segmentLengths: [], totalLengthM: 0 };
    const segmentLengths = [];
    let total = 0;
    for (let i = 0; i < path.length - 1; i++) {
      const d = haversineDist(path[i], path[i + 1]);
      segmentLengths.push(d);
      total += d;
    }
    return { segmentLengths, totalLengthM: total };
  }

  function distanceToSegmentT(segmentLengths, distanceM) {
    let acc = 0;
    for (let i = 0; i < segmentLengths.length; i++) {
      const len = segmentLengths[i];
      if (distanceM <= acc + len) {
        const t = len > 0 ? (distanceM - acc) / len : 1;
        return { segmentIndex: i, t: Math.min(1, t) };
      }
      acc += len;
    }
    return { segmentIndex: segmentLengths.length - 1, t: 1 };
  }

  function buildFleetState(routes, color) {
    return routes.map((r, i) => {
      const path = r.path || [];
      const { segmentLengths, totalLengthM } = computePathSegmentLengths(path);
      return {
        path,
        metrics: r,
        color,
        vehicleIndex: i,
        segmentLengths,
        totalLengthM,
        distanceTraveledM: 0,
        currentSegment: 0,
        t: 0,
        trail: [],
        marker: null,
        trailLayer: null,
      };
    });
  }

  function initMaps() {
    if (mapOur) mapOur.remove();
    if (mapOther) mapOther.remove();
    mapOur = createMap('map-our', SF_CENTER, 12);
    mapOther = createMap('map-other', SF_CENTER, 12);
    L.control.zoom({ position: 'bottomright' }).addTo(mapOur);
    L.control.zoom({ position: 'bottomright' }).addTo(mapOther);
  }

  function updateSidebar(ourAgg, otherAgg) {
    function safeNum(v, fmt) {
      if (v == null || !Number.isFinite(v)) return '—';
      return typeof fmt === 'function' ? fmt(v) : v.toFixed(2);
    }
    const set = (prefix, o) => {
      document.getElementById(prefix + '-distance').textContent =
        safeNum(o.distanceKm, (v) => v.toFixed(2) + ' km');
      document.getElementById(prefix + '-fuel').textContent =
        safeNum(o.fuelL, (v) => v.toFixed(2));
      document.getElementById(prefix + '-elev-gain').textContent =
        safeNum(o.elevGainM, (v) => v.toFixed(0) + ' m');
      document.getElementById(prefix + '-turns').textContent =
        safeNum(o.turnPenalty, (v) => v.toFixed(1));
      document.getElementById(prefix + '-vehicles').textContent =
        o.vehicles != null && Number.isFinite(o.vehicles) ? String(Math.round(o.vehicles)) : '—';
    };
    set('our', ourAgg);
    set('other', otherAgg);
    const cmp = document.getElementById('comparison-text');
    const ourFuel = ourAgg.fuelL;
    const otherFuel = otherAgg.fuelL;
    if (Number.isFinite(ourFuel) && Number.isFinite(otherFuel) && otherFuel > 0) {
      const pct = ((1 - ourFuel / otherFuel) * 100).toFixed(1);
      cmp.textContent = pct > 0 ? `Our method ~${pct}% less fuel.` : `Difference: ${(ourFuel - otherFuel).toFixed(2)} L.`;
    } else if (Number.isFinite(ourFuel) && Number.isFinite(otherFuel)) {
      cmp.textContent = 'Difference: ' + (ourFuel - otherFuel).toFixed(2) + ' L.';
    } else {
      cmp.textContent = 'Run simulation to compare.';
    }
  }

  function aggregateFleetMetrics(fleet) {
    let distanceKm = 0, fuelL = 0, elevGainM = 0, turnPenalty = 0;
    fleet.forEach((f) => {
      const m = f.metrics || {};
      distanceKm += Number.isFinite(m.distanceKm) ? m.distanceKm : 0;
      fuelL += Number.isFinite(m.fuelL) ? m.fuelL : 0;
      elevGainM += Number.isFinite(m.elevGainM) ? m.elevGainM : 0;
      turnPenalty += Number.isFinite(m.turnPenalty) ? m.turnPenalty : 0;
    });
    return {
      distanceKm: distanceKm || 0,
      fuelL,
      elevGainM,
      turnPenalty,
      vehicles: fleet.length,
    };
  }

  /** Live aggregate: same formula, scaled by distance traveled so far (for sidebar during animation). */
  function aggregateLiveFleetMetrics(fleet) {
    let distanceKm = 0, fuelL = 0, elevGainM = 0, turnPenalty = 0;
    fleet.forEach((f) => {
      const m = f.metrics || {};
      const totalM = f.totalLengthM > 0 ? f.totalLengthM : 1;
      const frac = Math.min(1, (f.distanceTraveledM || 0) / totalM);
      distanceKm += (Number.isFinite(m.distanceKm) ? m.distanceKm : 0) * frac;
      fuelL += (Number.isFinite(m.fuelL) ? m.fuelL : 0) * frac;
      elevGainM += (Number.isFinite(m.elevGainM) ? m.elevGainM : 0) * frac;
      turnPenalty += (Number.isFinite(m.turnPenalty) ? m.turnPenalty : 0) * frac;
    });
    return {
      distanceKm,
      fuelL,
      elevGainM,
      turnPenalty,
      vehicles: fleet.length,
    };
  }

  function clearFleetFromMap(fleet, map) {
    fleet.forEach((f) => {
      if (f.marker) map.removeLayer(f.marker);
      if (f.trailLayer) map.removeLayer(f.trailLayer);
    });
  }

  function addVehicleToMap(f, map, isOur) {
    const path = f.path;
    if (!path || path.length < 2) return;
    const icon = L.divIcon({ className: 'vehicle-marker ' + (isOur ? 'our' : 'other'), iconSize: [20, 20] });
    const start = path[0];
    const marker = L.marker([start.lat, start.lng], { icon }).addTo(map);
    const trailLayer = L.polyline([], { color: f.color, weight: 4, opacity: 0.85 }).addTo(map);
    f.marker = marker;
    f.trailLayer = trailLayer;
    f.distanceTraveledM = 0;
    f.currentSegment = 0;
    f.t = 0;
    f.trail = [[start.lat, start.lng]];
  }

  function getPointOnSegment(path, segmentIndex, t) {
    if (segmentIndex >= path.length - 1) {
      const last = path[path.length - 1];
      return [last.lat, last.lng];
    }
    const a = path[segmentIndex];
    const b = path[segmentIndex + 1];
    return [a.lat + t * (b.lat - a.lat), a.lng + t * (b.lng - a.lng)];
  }

  function animateFleet() {
    const speedMetersPerSec = (SIMULATION_SPEED_KMH * 1000) / 3600;
    let lastTs = null;

    function step(timestamp) {
      if (lastTs == null) lastTs = timestamp;
      const deltaSec = (timestamp - lastTs) / 1000;
      lastTs = timestamp;

      let anyMoving = false;
      [fleetOur, fleetOther].forEach((fleet) => {
        fleet.forEach((f) => {
          const path = f.path;
          const segLens = f.segmentLengths || [];
          if (!path || path.length < 2 || segLens.length === 0) return;
          if (f.distanceTraveledM >= f.totalLengthM) return;

          anyMoving = true;
          f.distanceTraveledM = Math.min(f.totalLengthM, f.distanceTraveledM + speedMetersPerSec * deltaSec);
          const { segmentIndex, t } = distanceToSegmentT(segLens, f.distanceTraveledM);
          f.currentSegment = segmentIndex;
          f.t = t;

          const pos = getPointOnSegment(path, segmentIndex, t);
          f.marker.setLatLng(pos);
          if (f.trail.length === 0 || haversineDist(
            { lat: f.trail[f.trail.length - 1][0], lng: f.trail[f.trail.length - 1][1] },
            { lat: pos[0], lng: pos[1] }
          ) > 0.3) {
            f.trail.push([pos[0], pos[1]]);
          }
          f.trailLayer.setLatLngs(f.trail);
        });
      });

      updateSidebar(aggregateLiveFleetMetrics(fleetOur), aggregateLiveFleetMetrics(fleetOther));

      if (anyMoving) return requestAnimationFrame(step);
      document.getElementById('status').textContent = 'Cycle complete. Restarting shortly…';
      setTimeout(runCycle, PAUSE_BEFORE_RESTART_MS);
    }
    requestAnimationFrame(step);
  }

  function runCycle() {
    if (!running) return;
    const { apiKey, ourRouteApiBase } = getConfig();
    if (!apiKey) {
      document.getElementById('status').textContent = 'Enter Google API key and click Start.';
      return;
    }
    cycleCount++;
    document.getElementById('cycle').textContent = 'Cycle ' + cycleCount;
    document.getElementById('status').textContent = 'Loading Google Maps…';

    const seed = Math.floor(Date.now() / 1000) + cycleCount * 1000;
    const rng = mulberry32(seed);
    const waypoints = getWaypointsForCycle(seed, rng);
    const vehicleWaypoints = assignWaypointsToVehicles(waypoints, NUM_VEHICLES, rng);
    const depotPoint = DEPOT;

    loadGoogleMaps(apiKey)
      .then(() => {
        document.getElementById('status').textContent = 'Fetching elevations…';
        const allPathPoints = [depotPoint];
        vehicleWaypoints.forEach((wpList) => wpList.forEach((p) => allPathPoints.push(p)));
        return getElevationsBatch(allPathPoints).then((elevs) => {
          const elevMap = new Map();
          allPathPoints.forEach((p, i) => elevMap.set(cacheKey(p.lat, p.lng), elevs[i]));
          return { depotPoint, vehicleWaypoints, rng, elevMap };
        });
      })
      .then(({ depotPoint, vehicleWaypoints, rng, elevMap }) => {
        document.getElementById('status').textContent = 'Requesting routes (Google & Our)…';

        const routesOur = [];
        const routesOther = [];
        const ourPromises = [];
        const otherPromises = [];

        vehicleWaypoints.forEach((wpList) => {
          if (wpList.length === 0) return;
          const size = VEHICLE_SIZES[Math.floor(rng() * VEHICLE_SIZES.length)];
          // Same visit order for both methods so e.g. vehicle 1 goes A→E→R on both maps
          const order = ourMethodOrder(wpList, depotPoint, elevMap);

          otherPromises.push(
            fetchDirectionsGoogle(depotPoint, depotPoint, order).then((result) => {
              const path = pathFromRoutesResult(result);
              const metrics = metricsFromRoutesResult(result);
              const pathForMetrics = path && path.length ? path : [depotPoint, ...order];
              const enriched = path && metrics
                ? { ...metrics, path: pathForMetrics }
                : { distanceKm: 0, fuelL: 0, elevGainM: 0, turnPenalty: 0, path: pathForMetrics };
              if (pathForMetrics && pathForMetrics.length >= 2) {
                const fromElev = computeRouteMetricsFromPath(pathForMetrics, elevMap, size);
                enriched.distanceKm = fromElev.distanceKm;
                enriched.fuelL = fromElev.fuelL;
                enriched.elevGainM = fromElev.elevGainM;
                enriched.turnPenalty = fromElev.turnPenalty;
              }
              routesOther.push(enriched);
            })
          );

          const ourDirectionsPromise = ourRouteApiBase
            ? fetchDirectionsRest(depotPoint, depotPoint, order, null, ourRouteApiBase)
              .then((data) => {
                if (data && data.status === 'OK' && data.routes && data.routes[0]) {
                  return { data, useResult: false };
                }
                return fetchDirectionsGoogle(depotPoint, depotPoint, order).then((result) => ({ result, useResult: true }));
              })
              .catch(() => fetchDirectionsGoogle(depotPoint, depotPoint, order).then((result) => ({ result, useResult: true })))
            : fetchDirectionsGoogle(depotPoint, depotPoint, order).then((result) => ({ result, useResult: true }));

          ourPromises.push(
            ourDirectionsPromise.then((out) => {
              const path = out.useResult
                ? pathFromRoutesResult(out.result)
                : pathFromDirectionsResponse(out.data);
              const metrics = out.useResult
                ? metricsFromRoutesResult(out.result)
                : metricsFromDirectionsResponse(out.data);
              const pathForMetrics = path && path.length ? path : [depotPoint, ...order];
              const enriched = path && metrics
                ? { ...metrics, path: pathForMetrics }
                : { distanceKm: 0, fuelL: 0, elevGainM: 0, turnPenalty: 0, path: pathForMetrics };
              if (pathForMetrics && pathForMetrics.length >= 2) {
                const fromElev = computeRouteMetricsFromPath(pathForMetrics, elevMap, size);
                enriched.distanceKm = fromElev.distanceKm;
                enriched.fuelL = fromElev.fuelL;
                enriched.elevGainM = fromElev.elevGainM;
                enriched.turnPenalty = fromElev.turnPenalty;
              }
              routesOur.push(enriched);
            })
          );
        });

        return Promise.all([Promise.all(ourPromises), Promise.all(otherPromises)]).then(() => ({
          routesOur,
          routesOther,
        }));
      })
      .then(({ routesOur, routesOther }) => {
        clearFleetFromMap(fleetOur, mapOur);
        clearFleetFromMap(fleetOther, mapOther);
        fleetOur = buildFleetState(routesOur, OUR_TRAIL_COLOR);
        fleetOther = buildFleetState(routesOther, OTHER_TRAIL_COLOR);
        fleetOur.forEach((f) => addVehicleToMap(f, mapOur, true));
        fleetOther.forEach((f) => addVehicleToMap(f, mapOther, false));
        updateSidebar(aggregateLiveFleetMetrics(fleetOur), aggregateLiveFleetMetrics(fleetOther));
        document.getElementById('status').textContent = 'Simulating…';
        animateFleet();
      })
      .catch((err) => {
        document.getElementById('status').textContent = 'Error: ' + (err.message || String(err));
        if (running) setTimeout(runCycle, 5000);
      });
  }

  function start() {
    loadSavedConfig();
    initMaps();
    document.getElementById('status').textContent = 'Enter Google API key and click Start.';
    fetch('waypoints-sf.json')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) window.FIXED_WAYPOINTS_SF = data;
      })
      .catch(() => { });

    document.getElementById('btn-start').addEventListener('click', () => {
      const { apiKey, ourRouteApiBase } = getConfig();
      saveConfig(apiKey, ourRouteApiBase);
      if (!apiKey) {
        document.getElementById('status').textContent = 'Please enter a Google API key.';
        return;
      }
      running = true;
      document.getElementById('btn-start').disabled = true;
      document.getElementById('status').textContent = 'Starting…';
      runCycle();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();

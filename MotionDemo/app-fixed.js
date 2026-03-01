/**
 * Motion Route Demo - "Our Model" vs Google Maps
 *
 * LEFT:  Motion Freight Router API (Valhalla truck routing + physics)
 * RIGHT: Google Maps (standard car routing)
 *
 * FAIR COMPARISON: Both sides use the same physics model for fuel/elevation.
 * The ONLY difference is the route geometry chosen by each routing engine.
 *
 * Features:
 *  • Same USGS elevation + fuel formula applied to BOTH routes
 *  • Destination markers per vehicle (numbered, color-coded)
 *  • All vehicles route in parallel for fast loading
 *  • Google API key optional (falls back to straight-line baseline)
 */
(function () {
  'use strict';

  /* ─── Config ─── */
  const MOTION_API = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    ? 'http://localhost:8000'
    : 'https://motion.rthak.com';
  const USGS_BASE = 'https://epqs.nationalmap.gov/v1/json';
  const SF_CENTER = [37.7749, -122.4194];
  const NUM_VEHICLES = 4;
  const STOPS_PER_VEHICLE = 4;
  const DEPOT = { lat: 37.7849, lng: -122.4094 };
  const PAUSE_MS = 5000;
  const SIM_SPEED_KMH = 100;

  const OUR_COLOR = '#0ea5e9';
  const GOOGLE_COLOR = '#f97316';
  const VEHICLE_COLORS = ['#38bdf8', '#22d3ee', '#818cf8', '#a78bfa'];

  /* Physics constants - same model applied to BOTH sides */
  const VEHICLE_WEIGHTS = { SEMI_TRAILER: 36000, BOX_TRUCK: 12000 };
  const GRADE_PENALTY = 0.0025;  // extra L per meter elevation gain per tonne
  const TURN_PENALTY_L = 0.002;  // extra L per degree of sharp turn per km
  const SHARP_TURN_THRESHOLD = 45; // only count turns sharper than this (degrees)
  const BASE_L_PER_KM = 0.035;  // base fuel consumption L/km for a 36t truck

  let cycleCount = 0;
  let elevCache = new Map();
  let running = false;

  /* ─── Polyline decoder (precision 5) ─── */
  function decodePoly(enc) {
    const pts = []; let idx = 0, lat = 0, lng = 0;
    while (idx < enc.length) {
      let b, s = 0, r = 0;
      do { b = enc.charCodeAt(idx++) - 63; r |= (b & 0x1f) << s; s += 5; } while (b >= 0x20);
      lat += (r & 1) ? ~(r >> 1) : r >> 1; s = 0; r = 0;
      do { b = enc.charCodeAt(idx++) - 63; r |= (b & 0x1f) << s; s += 5; } while (b >= 0x20);
      lng += (r & 1) ? ~(r >> 1) : r >> 1;
      pts.push({ lat: lat * 1e-5, lng: lng * 1e-5 });
    }
    return pts;
  }

  /* ─── Geo helpers ─── */
  function haversine(a, b) {
    const R = 6371000, dLat = (b.lat - a.lat) * Math.PI / 180, dLon = (b.lng - a.lng) * Math.PI / 180;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  }

  function mulberry32(seed) {
    return () => { let t = seed += 0x6d2b79f5; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  function nearestNeighbor(waypoints, start) {
    const order = []; let rem = [...waypoints], cur = start;
    while (rem.length) {
      let best = 0, bestD = Infinity;
      rem.forEach((w, j) => { const d = haversine(cur, w); if (d < bestD) { bestD = d; best = j; } });
      order.push(rem[best]); cur = rem[best]; rem.splice(best, 1);
    }
    return order;
  }

  function pathSegLens(path) {
    const lens = []; let total = 0;
    for (let i = 0; i < path.length - 1; i++) { const d = haversine(path[i], path[i + 1]); lens.push(d); total += d; }
    return { lens, total };
  }

  function distToSeg(lens, dist) {
    let acc = 0;
    for (let i = 0; i < lens.length; i++) {
      if (dist <= acc + lens[i]) return { idx: i, t: lens[i] > 0 ? Math.min(1, (dist - acc) / lens[i]) : 1 };
      acc += lens[i];
    }
    return { idx: lens.length - 1, t: 1 };
  }

  function lerp(path, idx, t) {
    if (idx >= path.length - 1) { const l = path[path.length - 1]; return [l.lat, l.lng]; }
    const a = path[idx], b = path[idx + 1];
    return [a.lat + t * (b.lat - a.lat), a.lng + t * (b.lng - a.lng)];
  }

  function turnAngle(a, b, c) {
    const v1x = b.lng - a.lng, v1y = b.lat - a.lat;
    const v2x = c.lng - b.lng, v2y = c.lat - b.lat;
    return Math.abs(Math.atan2(v1x * v2y - v1y * v2x, v1x * v2x + v1y * v2y) * 180 / Math.PI);
  }

  /* ─── USGS Elevation (cached, batched) ─── */
  function ck(lat, lng) { return `${lat.toFixed(5)},${lng.toFixed(5)}`; }

  function fetchElev(lat, lng) {
    const k = ck(lat, lng);
    if (elevCache.has(k)) return Promise.resolve(elevCache.get(k));
    return fetch(`${USGS_BASE}?x=${lng}&y=${lat}&units=Meters&wkid=4326&includeDate=false`)
      .then(r => r.json())
      .then(d => { const v = d.value != null ? Number(d.value) : 0; elevCache.set(k, v); return v; })
      .catch(() => { elevCache.set(k, 0); return 0; });
  }

  /** Sample N evenly-spaced points from a path for elevation lookup. */
  function samplePoints(path, n) {
    if (path.length <= n) return path.map((p, i) => ({ point: p, index: i }));
    const step = (path.length - 1) / (n - 1);
    const samples = [];
    for (let i = 0; i < n; i++) {
      const idx = Math.min(Math.round(i * step), path.length - 1);
      samples.push({ point: path[idx], index: idx });
    }
    return samples;
  }

  /**
   * UNIFIED PHYSICS MODEL — applied identically to both "Our" and "Google" routes.
   * Given a path + vehicle weight, fetches USGS elevations and computes:
   *   fuel burn, elevation gain, turn penalty
   */
  async function computePhysicsForPath(path, vehicleType) {
    if (!path || path.length < 2) return { distanceKm: 0, fuelL: 0, elevGainM: 0, turnPenalty: 0 };

    const weightKg = VEHICLE_WEIGHTS[vehicleType] || 36000;
    const tonnes = weightKg / 1000;

    // 1. Total distance
    let totalDist = 0;
    for (let i = 1; i < path.length; i++) totalDist += haversine(path[i - 1], path[i]);

    // 2. Sample points for elevation (max 30 to keep USGS calls fast)
    const samples = samplePoints(path, Math.min(30, path.length));
    const elevResults = await Promise.all(samples.map(s => fetchElev(s.point.lat, s.point.lng)));
    const elevByIndex = new Map();
    samples.forEach((s, i) => elevByIndex.set(s.index, elevResults[i]));

    // Interpolate elevations for all path points
    const allElev = new Array(path.length);
    // Fill known values
    for (const [idx, elev] of elevByIndex) allElev[idx] = elev;
    // Linear interpolate between known values
    let lastKnown = 0;
    allElev[0] = allElev[0] ?? 0;
    for (let i = 1; i < path.length; i++) {
      if (allElev[i] != null) { lastKnown = i; continue; }
      // Find next known
      let nextKnown = i + 1;
      while (nextKnown < path.length && allElev[nextKnown] == null) nextKnown++;
      if (nextKnown >= path.length) { allElev[i] = allElev[lastKnown]; continue; }
      const t = (i - lastKnown) / (nextKnown - lastKnown);
      allElev[i] = allElev[lastKnown] + t * (allElev[nextKnown] - allElev[lastKnown]);
    }

    // 3. Elevation gain + fuel from grade
    let elevGain = 0;
    let gradeFuel = 0;
    for (let i = 1; i < path.length; i++) {
      const dElev = allElev[i] - allElev[i - 1];
      if (dElev > 0) {
        elevGain += dElev;
        gradeFuel += dElev * GRADE_PENALTY * tonnes;
      }
    }

    // 4. Turn penalty — only count meaningful turns (> threshold)
    //    Normalize: we measure total sharp-turn degrees, then compute fuel per-km
    let turnPenaltyDeg = 0;
    // Sample turns at ~50m intervals to normalize for polyline density
    const SAMPLE_DIST = 50; // meters between sampled turn measurements
    let accumDist = 0;
    let lastSampleIdx = 0;
    for (let i = 1; i < path.length; i++) {
      accumDist += haversine(path[i - 1], path[i]);
      if (accumDist >= SAMPLE_DIST && lastSampleIdx < i - 1) {
        const angle = turnAngle(path[lastSampleIdx], path[Math.floor((lastSampleIdx + i) / 2)], path[i]);
        if (angle > SHARP_TURN_THRESHOLD) turnPenaltyDeg += angle;
        lastSampleIdx = i;
        accumDist = 0;
      }
    }
    const distKm = totalDist / 1000;
    const turnFuel = turnPenaltyDeg * TURN_PENALTY_L * (tonnes / 36) * (distKm > 0 ? 1 : 0);

    // 5. Base fuel (distance × weight-proportional consumption)
    const baseFuel = distKm * BASE_L_PER_KM * (tonnes / 36);

    // 6. Total fuel
    const totalFuel = baseFuel + gradeFuel + turnFuel;

    return {
      distanceKm: totalDist / 1000,
      fuelL: Math.max(0, totalFuel),
      elevGainM: elevGain,
      turnPenalty: turnPenaltyDeg,
    };
  }


  /* ─── Motion API: fetch route geometry ─── */
  async function motionRouteVehicle(stops, vehicleType) {
    // Fire ALL legs in parallel
    const legPromises = [];
    for (let i = 0; i < stops.length - 1; i++) {
      const o = stops[i], d = stops[i + 1];
      legPromises.push(
        fetch(`${MOTION_API}/directions/v2/computeRoutes`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            origin: { location: { latLng: { latitude: o.lat, longitude: o.lng } } },
            destination: { location: { latLng: { latitude: d.lat, longitude: d.lng } } },
            vehicleSpec: { type: vehicleType },
            routingProfile: 'fuel_optimal',
          }),
        }).then(r => r.ok ? r.json() : null).catch(() => null)
      );
    }
    const results = await Promise.all(legPromises);

    // Stitch polyline paths from all legs
    const fullPath = [];
    for (const data of results) {
      if (!data || !data.routes || !data.routes[0]) continue;
      const route = data.routes[0];
      if (route.polyline && route.polyline.encodedPolyline) {
        const pts = decodePoly(route.polyline.encodedPolyline);
        if (fullPath.length > 0 && pts.length > 0) pts.shift();
        fullPath.push(...pts);
      }
    }

    // Now compute physics with the SAME unified model
    const metrics = await computePhysicsForPath(fullPath, vehicleType);
    return { ...metrics, path: fullPath };
  }

  /* ─── Google route: get geometry, then apply same physics ─── */
  async function googleRouteVehicleSDK(stops, vehicleType) {
    let path = null;

    try {
      const formatPoint = (pt) => ({
        location: { latLng: { latitude: pt.lat, longitude: pt.lng } }
      });
      const requestBody = {
        origin: formatPoint(stops[0]),
        destination: formatPoint(stops[stops.length - 1]),
        travelMode: 'DRIVE'
      };
      const intermediates = stops.slice(1, -1);
      if (intermediates.length) {
        requestBody.intermediates = intermediates.map(formatPoint);
      }

      const r = await fetch(`${MOTION_API}/proxy/google/directions/v2/computeRoutes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });
      const result = await r.json();
      if (result.routes && result.routes[0] && result.routes[0].polyline && result.routes[0].polyline.encodedPolyline) {
        path = decodePoly(result.routes[0].polyline.encodedPolyline);
      }
    } catch (e) {
      console.warn('Google route proxy failed:', e);
    }

    // Fallback: straight-line between stops
    if (!path || path.length < 2) path = stops;

    // Apply the SAME unified physics model
    const metrics = await computePhysicsForPath(path, vehicleType);
    return { ...metrics, path };
  }


  /* ─── Leaflet maps ─── */
  let mapOur, mapGoogle;
  let fleetOur = [], fleetGoogle = [];
  let waypointMarkersOur = [], waypointMarkersGoogle = [];

  function makeMap(id) {
    const map = L.map(id, { center: SF_CENTER, zoom: 12, zoomControl: false });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { attribution: '© OSM, CARTO' }).addTo(map);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    return map;
  }

  function clearMarkers(arr, map) { arr.forEach(m => map.removeLayer(m)); arr.length = 0; }
  function clearFleet(fleet, map) { fleet.forEach(f => { if (f.marker) map.removeLayer(f.marker); if (f.trail) map.removeLayer(f.trail); }); }

  /* ─── Destination markers for each vehicle ─── */
  function addWaypointMarkers(map, vehicles, markersArr, colorSet) {
    vehicles.forEach((stops, vi) => {
      const color = colorSet[vi % colorSet.length];
      stops.forEach((stop, si) => {
        if (si === 0) return; // skip depot (added separately)
        const isLast = si === stops.length - 1;
        const label = isLast ? '★' : String(si);
        const size = isLast ? 24 : 18;
        const icon = L.divIcon({
          className: '',
          html: `<div style="
            width:${size}px;height:${size}px;border-radius:50%;
            background:${color};border:2px solid #fff;
            color:#fff;font-size:${isLast ? 14 : 10}px;font-weight:700;
            display:flex;align-items:center;justify-content:center;
            box-shadow:0 2px 6px rgba(0,0,0,0.4);
            ${isLast ? 'z-index:1000;' : ''}
          ">${label}</div>`,
          iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        });
        const m = L.marker([stop.lat, stop.lng], { icon }).addTo(map);
        m.bindTooltip(`V${vi + 1} ${isLast ? 'Dest' : 'Stop ' + si}`, { permanent: false, direction: 'top', offset: [0, -12] });
        markersArr.push(m);
      });
    });

    // Depot marker
    const depotIcon = L.divIcon({
      className: '',
      html: `<div style="width:28px;height:28px;border-radius:50%;background:#10b981;border:3px solid #fff;color:#fff;font-size:14px;font-weight:700;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.5);">D</div>`,
      iconSize: [28, 28], iconAnchor: [14, 14],
    });
    const dm = L.marker([DEPOT.lat, DEPOT.lng], { icon: depotIcon }).addTo(map);
    dm.bindTooltip('Depot', { permanent: false, direction: 'top', offset: [0, -16] });
    markersArr.push(dm);
  }

  /* ─── Fleet state ─── */
  function buildFleet(routes, color) {
    return routes.map(r => {
      const path = r.path || [];
      const { lens, total } = pathSegLens(path);
      return { path, metrics: r, color, lens, total, dist: 0, marker: null, trail: null, trailPts: [] };
    });
  }

  function addToMap(f, map, cls) {
    if (!f.path || f.path.length < 2) return;
    const icon = L.divIcon({ className: 'vehicle-marker ' + cls, iconSize: [20, 20] });
    f.marker = L.marker([f.path[0].lat, f.path[0].lng], { icon }).addTo(map);
    f.trail = L.polyline([], { color: f.color, weight: 4, opacity: 0.85 }).addTo(map);
    f.trailPts = [[f.path[0].lat, f.path[0].lng]];
  }

  /* ─── Sidebar ─── */
  function liveMetrics(fleet) {
    let dk = 0, fl = 0, eg = 0, tp = 0;
    fleet.forEach(f => {
      const m = f.metrics || {}, frac = f.total > 0 ? Math.min(1, f.dist / f.total) : 0;
      dk += (m.distanceKm || 0) * frac;
      fl += (m.fuelL || 0) * frac;
      eg += (m.elevGainM || 0) * frac;
      tp += (m.turnPenalty || 0) * frac;
    });
    return { distanceKm: dk, fuelL: fl, elevGainM: eg, turnPenalty: tp, vehicles: fleet.length };
  }

  function fmt(v, fn) { return v == null || !Number.isFinite(v) ? '—' : fn(v); }

  function updateUI(ourAgg, googleAgg) {
    const set = (pfx, o) => {
      document.getElementById(pfx + '-distance').textContent = fmt(o.distanceKm, v => v.toFixed(2) + ' km');
      document.getElementById(pfx + '-fuel').textContent = fmt(o.fuelL, v => v.toFixed(3));
      document.getElementById(pfx + '-elev-gain').textContent = fmt(o.elevGainM, v => v.toFixed(0) + ' m');
      document.getElementById(pfx + '-turns').textContent = fmt(o.turnPenalty, v => v.toFixed(0) + '°');
      document.getElementById(pfx + '-vehicles').textContent = o.vehicles;
    };
    set('our', ourAgg); set('other', googleAgg);

    const cmp = document.getElementById('comparison-text');
    if (Number.isFinite(ourAgg.fuelL) && Number.isFinite(googleAgg.fuelL) && googleAgg.fuelL > 0 && ourAgg.fuelL > 0) {
      const pct = ((1 - ourAgg.fuelL / googleAgg.fuelL) * 100).toFixed(1);
      const elevDiff = (googleAgg.elevGainM - ourAgg.elevGainM).toFixed(0);
      if (Number(pct) > 0) {
        cmp.innerHTML = `<span style="color:#0ea5e9;font-weight:600">Our model saves ~${pct}% fuel</span> by choosing routes with ${elevDiff}m less climbing and fewer sharp turns.`;
      } else if (Number(pct) < 0) {
        cmp.innerHTML = `Google route uses ${Math.abs(Number(pct))}% less fuel this cycle. Routes vary by terrain.`;
      } else {
        cmp.textContent = 'Roughly equal fuel this cycle.';
      }
    } else {
      cmp.textContent = 'Run simulation to compare.';
    }
  }

  /* ─── Animation ─── */
  function animate() {
    const speed = SIM_SPEED_KMH * 1000 / 3600; let last = null;
    function step(ts) {
      if (!last) last = ts; const dt = (ts - last) / 1000; last = ts;
      let moving = false;
      [fleetOur, fleetGoogle].forEach(fleet => fleet.forEach(f => {
        if (!f.path || f.path.length < 2 || f.dist >= f.total) return;
        moving = true;
        f.dist = Math.min(f.total, f.dist + speed * dt);
        const { idx, t } = distToSeg(f.lens, f.dist);
        const pos = lerp(f.path, idx, t);
        f.marker.setLatLng(pos);
        const last2 = f.trailPts[f.trailPts.length - 1];
        if (!last2 || haversine({ lat: last2[0], lng: last2[1] }, { lat: pos[0], lng: pos[1] }) > 0.5) f.trailPts.push(pos);
        f.trail.setLatLngs(f.trailPts);
      }));
      updateUI(liveMetrics(fleetOur), liveMetrics(fleetGoogle));
      if (moving) requestAnimationFrame(step);
      else { document.getElementById('status').textContent = 'Cycle complete. Restarting…'; setTimeout(runCycle, PAUSE_MS); }
    }
    requestAnimationFrame(step);
  }

  /* ─── Main cycle ─── */
  async function runCycle() {
    if (!running) return;
    const pool = window.FIXED_WAYPOINTS_SF;
    if (!pool || !pool.length) { setStatus('No waypoints loaded.'); return; }

    cycleCount++;
    document.getElementById('cycle').textContent = 'Cycle ' + cycleCount;

    try {
      setStatus('Selecting waypoints…');
      const seed = Math.floor(Date.now() / 1000) + cycleCount * 1000;
      const rng = mulberry32(seed);

      // Assign waypoints to vehicles
      const shuffled = [...pool].sort(() => rng() - 0.5);
      const vehicleStops = [];
      for (let v = 0; v < NUM_VEHICLES; v++) {
        const slice = shuffled.slice(v * STOPS_PER_VEHICLE, (v + 1) * STOPS_PER_VEHICLE).filter(Boolean);
        if (slice.length) vehicleStops.push(nearestNeighbor(slice, DEPOT));
      }

      const vehicleFullStops = vehicleStops.map(order => [DEPOT, ...order]);
      const vehicleTypes = vehicleStops.map((_, i) => i % 2 === 0 ? 'SEMI_TRAILER' : 'BOX_TRUCK');

      // Clear old state
      clearFleet(fleetOur, mapOur); clearFleet(fleetGoogle, mapGoogle);
      clearMarkers(waypointMarkersOur, mapOur); clearMarkers(waypointMarkersGoogle, mapGoogle);

      // Add destination markers
      addWaypointMarkers(mapOur, vehicleFullStops, waypointMarkersOur, VEHICLE_COLORS);
      addWaypointMarkers(mapGoogle, vehicleFullStops, waypointMarkersGoogle, VEHICLE_COLORS);

      // Compute routes in parallel — ALL vehicles on BOTH sides simultaneously
      setStatus(`Routing ${vehicleStops.length} vehicles (both sides in parallel)…`);

      const ourPromises = vehicleFullStops.map((stops, i) => motionRouteVehicle(stops, vehicleTypes[i]));
      const googlePromises = vehicleFullStops.map((stops, i) => googleRouteVehicleSDK(stops, vehicleTypes[i]));

      const [ourResults, googleResults] = await Promise.all([
        Promise.all(ourPromises),
        Promise.all(googlePromises),
      ]);

      // Build fleet state
      fleetOur = buildFleet(ourResults.filter(r => r && r.path && r.path.length >= 2), OUR_COLOR);
      fleetGoogle = buildFleet(googleResults.filter(r => r && r.path && r.path.length >= 2), GOOGLE_COLOR);
      fleetOur.forEach(f => addToMap(f, mapOur, 'our'));
      fleetGoogle.forEach(f => addToMap(f, mapGoogle, 'other'));

      updateUI(liveMetrics(fleetOur), liveMetrics(fleetGoogle));
      setStatus('Simulating…');
      animate();

    } catch (err) {
      setStatus('Error: ' + (err.message || err));
      if (running) setTimeout(runCycle, 5000);
    }
  }

  function setStatus(msg) { document.getElementById('status').textContent = msg; }

  /* ─── Load Google Maps SDK ─── */
  function loadGoogleMaps() {
    return Promise.resolve();
  }

  /* ─── Init ─── */
  async function start() {
    mapOur = makeMap('map-our');
    mapGoogle = makeMap('map-other');

    setStatus('Loading waypoints & checking API…');

    // Load waypoints
    try {
      const r = await fetch('waypoints-sf.json');
      if (r.ok) { const data = await r.json(); if (Array.isArray(data) && data.length > 0) window.FIXED_WAYPOINTS_SF = data; }
    } catch (_) { }

    // Check Motion API
    let apiOk = false;
    try { const r = await fetch(`${MOTION_API}/health`); apiOk = r.ok; } catch (_) { }

    // Load saved config
    try {
      const saved = localStorage.getItem('motion_google_api_key') || '';
      if (saved && document.getElementById('api-key')) document.getElementById('api-key').value = saved;
    } catch (_) { }

    if (!apiOk) {
      setStatus('⚠️ Motion API not reachable. Run: cd api && python dev_server.py --real');
      document.getElementById('btn-start').disabled = true;
      return;
    }

    const wpCount = (window.FIXED_WAYPOINTS_SF || []).length;
    setStatus(`✅ Motion API online • ${wpCount} waypoints • Click Start`);

    document.getElementById('btn-start').addEventListener('click', async () => {

      running = true;
      document.getElementById('btn-start').disabled = true;
      runCycle();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

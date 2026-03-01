const API_BASE = "https://motion.rthak.com";

let map;
let originMarker, destMarker;
let routePath;
let currentMode = 'sync'; // 'sync' or 'async'
let currentRouteId = null;

let originLatLng = { lat: 37.7956, lng: -122.3937 };
let destLatLng = { lat: 37.7544, lng: -122.4477 };

window.onload = () => {
    initMap();
    updateWaypointDisplays();
};

function initMap() {
    map = L.map('map', { zoomControl: false }).setView([37.7749, -122.4194], 12);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
    }).addTo(map);

    originMarker = L.marker([originLatLng.lat, originLatLng.lng], {
        icon: L.divIcon({ className: 'custom-div-icon', html: '<div style="background:#2c68ff;width:12px;height:12px;border-radius:50%;border:2px solid white;"></div>' })
    }).addTo(map);

    destMarker = L.marker([destLatLng.lat, destLatLng.lng], {
        icon: L.divIcon({ className: 'custom-div-icon', html: '<div style="background:#ef4444;width:12px;height:12px;border-radius:50%;border:2px solid white;"></div>' })
    }).addTo(map);

    let settingOrigin = false;
    map.on('click', (e) => {
        if (!settingOrigin) {
            originLatLng = { lat: e.latlng.lat, lng: e.latlng.lng };
            originMarker.setLatLng(e.latlng);
            settingOrigin = true;
        } else {
            destLatLng = { lat: e.latlng.lat, lng: e.latlng.lng };
            destMarker.setLatLng(e.latlng);
            settingOrigin = false;
        }
        updateWaypointDisplays();
    });
}

function updateWaypointDisplays() {
    document.getElementById('origin-coords').innerText = `${originLatLng.lat.toFixed(4)}, ${originLatLng.lng.toFixed(4)}`;
    document.getElementById('dest-coords').innerText = `${destLatLng.lat.toFixed(4)}, ${destLatLng.lng.toFixed(4)}`;
}

function setApiMode(mode) {
    currentMode = mode;
    document.getElementById('mode-sync').classList.toggle('active', mode === 'sync');
    document.getElementById('mode-async').classList.toggle('active', mode === 'async');

    const asyncActions = document.getElementById('async-actions');
    if (mode === 'async') {
        asyncActions.classList.remove('hidden');
        document.getElementById('compute-btn').innerText = "Create Route Job (Async)";
    } else {
        asyncActions.classList.add('hidden');
        document.getElementById('compute-btn').innerText = "Compute Route (Sync)";
    }
}

function logRequest(endpointStr, reqBody) {
    const parts = endpointStr.split(' ');
    const method = parts[0];
    const path = parts.slice(1).join(' ');

    const el = document.getElementById('log-container');

    // Generator logic: produce a workable cURL command
    const targetUrl = `https://motion.rthak.com${path}`;
    let curlCmd = `curl -X ${method} ${targetUrl}`;
    if (reqBody) {
        curlCmd += ` \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(reqBody, null, 2)}'`;
    }

    const curlId = 'curl-' + Date.now() + Math.floor(Math.random() * 1000);

    el.innerHTML = `
        <div class="log-entry generator-entry" style="border-left: 2px solid var(--accent); margin-bottom: 12px; position: relative;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span class="endpoint" style="margin-bottom: 0;">${method} ${path}</span>
                <button class="secondary-btn" style="width: auto; margin-bottom: 0; padding: 4px 8px; font-size: 11px; z-index: 10;" onclick="copyToClipboard('${curlId}', this)">Copy cURL</button>
            </div>
            <pre id="${curlId}" style="white-space: pre-wrap; word-break: break-all; color: #a5d6ff; margin: 0; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 4px;">${curlCmd}</pre>
        </div>` + el.innerHTML;
}

function copyToClipboard(id, btn) {
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text).then(() => {
        const originalText = btn.innerText;
        btn.innerText = 'Copied!';
        setTimeout(() => { btn.innerText = originalText; }, 2000);
    });
}

function logResponse(endpoint, resBody) {
    // Disabled intentionally: this is now a request generator, not a logger.
}

async function computeRoute() {
    const profile = document.getElementById('profile-select').value;
    const vehicle = document.getElementById('vehicle-type').value;
    const body = {
        origin: { location: { latLng: { latitude: originLatLng.lat, longitude: originLatLng.lng } } },
        destination: { location: { latLng: { latitude: destLatLng.lat, longitude: destLatLng.lng } } },
        vehicleSpec: { type: vehicle },
        routingProfile: profile
    };

    if (currentMode === 'sync') {
        logRequest("POST /directions/v2/computeRoutes", body);
        const res = await fetch(`${API_BASE}/directions/v2/computeRoutes`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        logResponse("POST /directions/v2/computeRoutes", data);

        if (data.routes && data.routes.length > 0) {
            renderRoute(data.routes[0]);
        }
    } else {
        logRequest("POST /v1/routes", body);
        const res = await fetch(`${API_BASE}/v1/routes`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        logResponse("POST /v1/routes", data);

        currentRouteId = data.id;

        if (data.status === 'complete' && data.routes && data.routes.length > 0) {
            renderRoute(data.routes[0]);
        } else {
            document.getElementById('metrics-card').classList.remove('hidden');
            document.getElementById('res-distance').innerText = "Processing...";
            // Poll for completion
            setTimeout(pollRouteStatus, 1000);
        }
    }
}

async function pollRouteStatus() {
    if (!currentRouteId) return;
    logRequest(`GET /v1/routes/${currentRouteId}`, null);
    const res = await fetch(`${API_BASE}/v1/routes/${currentRouteId}`);
    const data = await res.json();
    logResponse(`GET /v1/routes/${currentRouteId}`, data);

    if (data.status === 'complete' && data.routes && data.routes.length > 0) {
        renderRoute(data.routes[0]);
    } else if (data.status === 'processing') {
        setTimeout(pollRouteStatus, 1500);
    } else {
        alert("Route calculation failed or not found.");
    }
}

async function fetchTelemetry() {
    if (!currentRouteId) return alert("Run an async route first.");
    logRequest(`GET /v1/routes/${currentRouteId}/telemetry?limit=5`, null);

    const res = await fetch(`${API_BASE}/v1/routes/${currentRouteId}/telemetry?limit=5`);
    const data = await res.json();
    logResponse(`GET /v1/routes/${currentRouteId}/telemetry?limit=5`, data);

    if (data.summary) {
        document.getElementById('res-fuel').innerText = `${data.summary.totalFuelBurnLiters.toFixed(2)} L`;
        document.getElementById('res-elev').innerText = `${data.summary.totalGradeGainMeters.toFixed(1)} m`;
    }
}


async function deleteRoute() {
    if (!currentRouteId) return alert("Run an async route first.");

    logRequest(`DELETE /v1/routes/${currentRouteId}`, null);
    const res = await fetch(`${API_BASE}/v1/routes/${currentRouteId}`, { method: "DELETE" });
    const data = await res.json();
    logResponse(`DELETE /v1/routes/${currentRouteId}`, data);

    if (routePath) {
        map.removeLayer(routePath);
        routePath = null;
    }
    document.getElementById('metrics-card').classList.add('hidden');
    currentRouteId = null;
}

function renderRoute(route) {
    if (routePath) {
        map.removeLayer(routePath);
    }

    let pathCoordinates = [];
    let totalFuel = 0;
    let maxGrade = 0;
    let turnCount = 0;
    let elevationGain = 0;

    // We can extract metrics from the sync response directly
    route.legs.forEach(leg => {
        leg.steps.forEach(step => {
            if (step.enrichment) {
                totalFuel += step.enrichment.fuelBurnLiters || 0;
                if (step.enrichment.gradePercent > maxGrade) maxGrade = step.enrichment.gradePercent;
                if (step.enrichment.gradePercent > 0) {
                    elevationGain += (step.enrichment.gradePercent / 100) * step.distanceMeters;
                }
            }
            if (step.navigationInstruction) {
                const m = step.navigationInstruction.maneuver;
                if (!['DEPART', 'STRAIGHT', 'NAME_CHANGE', 'MANEUVER_UNSPECIFIED'].includes(m)) {
                    turnCount++;
                }
            }
        });
    });

    try {
        const decoded = polyline.decode(route.polyline.encodedPolyline);
        pathCoordinates = decoded.map(p => [p[0], p[1]]);
    } catch (e) {
        console.error("Polyline decode error", e);
    }

    routePath = L.polyline(pathCoordinates, { color: '#2c68ff', weight: 5, opacity: 0.8 }).addTo(map);
    map.fitBounds(routePath.getBounds(), { padding: [50, 50] });

    document.getElementById('metrics-card').classList.remove('hidden');
    document.getElementById('res-distance').innerText = `${(route.distanceMeters / 1000).toFixed(1)} km`;
    document.getElementById('res-duration').innerText = Math.round(parseInt(route.duration) / 60) + " mins";
    document.getElementById('res-fuel').innerText = `${totalFuel.toFixed(2)} L`;
    document.getElementById('res-elev').innerText = `${elevationGain.toFixed(1)} m`;
    document.getElementById('res-turns').innerText = turnCount.toString();
}

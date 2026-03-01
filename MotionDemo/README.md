# Motion Route — Fleet Demo

Split-screen demo comparing **our** fuel-efficient routing (slope + curvature + vehicle size) with **Google Directions API** (real routes). Uses **real addresses** via Google Geocoding; "Our" side uses the same API shape so you can point it at your own route API.

## Setup

1. **Google Cloud**: Enable **Directions API** and **Geocoding API**, create an API key.
2. Run a local server (e.g. `npx serve . -p 8001` or `python3 -m http.server 8001`) and open the app.
3. Paste your **Google API key** in the sidebar; optionally set **Our route API URL**.
4. Click **Start simulation**.

## What it does

- **Geocoding**: Random delivery waypoints in San Francisco are reverse-geocoded to real addresses.
- **Google (right)**: For each vehicle, waypoints are ordered by nearest-neighbor; the app calls **Google Directions API** with origin/destination = depot and waypoints = those addresses. The returned route (polyline) is decoded and used for the animation and distance.
- **Our method (left)**: Same request shape (origin, destination, waypoints as addresses). If **Our route API URL** is set, the app calls that URL with the same query parameters (`origin`, `destination`, `waypoints`, `mode`, `units`). Your API should return the **same JSON shape** as Google Directions (e.g. `routes[0].legs[].steps[].polyline.points`, `distance.value`, `duration.value`). If the URL is empty or the request fails, the app falls back to Google Directions with *our* waypoint order (elevation-aware).
- **Fuel stats**: USGS elevation along the path is used to compute fuel (slope + turn penalty by vehicle size). Sidebar shows totals and a short comparison.

## Our API contract

- **Request**: GET with the same query string as Google Directions:
  - `origin`, `destination`: address strings (depot).
  - `waypoints`: pipe-separated addresses.
  - `mode=driving`, `units=metric`.
- **Response**: Same as [Directions API (Legacy) JSON](https://developers.google.com/maps/documentation/directions/get-directions): `status`, `routes[].legs[].steps[].polyline.points`, `distance.value`, `duration.value`.
- The app does **not** send your Google API key to the Our API URL.

## Files

- `index.html` — Layout: config inputs, sidebar stats, two map panels.
- `styles.css` — Split screen, config, labels, dark theme.
- `app.js` — Geocoding, Directions (Google + Our), polyline decode, USGS elevation, fuel model, animation.

## Tech

- **Maps**: Leaflet + CARTO dark basemap.
- **Google**: Geocoding API (reverse), Directions API (Legacy).
- **Elevation**: USGS EPQS (`epqs.nationalmap.gov`).

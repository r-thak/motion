# Motion Freight Router & API Demos

This repository contains the backend **Motion Routing API** (which powers heavy-duty truck routing through Valhalla with custom physics models and USGS elevation data) along with **two frontend demos** that showcase its capabilities.

---

## 🏗️ 1. Start the Main API (Backend)

The backend API must be running for either of the demos to work. It handles route generation, physics calculations, driver fatigue patching, and database interactions.

1. Open a terminal.
2. Navigate to the `api` folder:
   ```bash
   cd api
   ```
3. Start the development server in **real mode** (uses actual Valhalla routing and USGS elevation):
   ```bash
   python dev_server.py --real
   ```
   *The API will start at `http://localhost:8000`.*

> Note: To check if it's running successfully, you can visit `http://localhost:8000/health`.

---

## 🚚 2. Start Demo 1: Fleet Comparison (Our Model vs Google Maps)

This demo simulates an entire fleet of vehicles navigating San Francisco. It compares the **Motion Physics-Aware Routing** directly against **Google Maps Routes**, highlighting fuel savings, elevation differences, and turn penalties.

1. Open a **new** terminal.
2. Navigate to the first demo folder:
   ```bash
   cd MotionDemo
   ```
3. Start a simple HTTP server on port 3001:
   ```bash
   python3 -m http.server 3001
   ```
4. Open your browser and go to:
   **[http://localhost:3001](http://localhost:3001)**
5. Click **"Our Model vs Baseline"**. *(Optional: Provide a Google Maps API Key in the UI to see the live Google baseline comparison).*

---

## 🛠️ 3. Start Demo 2: API Explorer & Lifecycle Showcase

This is a developer-focused, interactive UI. It allows you to:
* Click on the map to place custom origin and destination waypoints.
* Try different vehicle specs (Semi Trailer vs. Box Truck).
* Test different Valhalla routing profiles (Fuel Optimal vs Time Optimal) and instantly see the physical route change.
* Interact with the Stripe-style Async API (`/v1/routes`).
* Update driver state (fatigue sliders) live and poll paginated telemetry data.

1. Open a **new** terminal.
2. Navigate to the second demo folder:
   ```bash
   cd secondDemo
   ```
3. Start a simple HTTP server on port 3002:
   ```bash
   python3 -m http.server 3002
   ```
4. Open your browser and go to:
   **[http://localhost:3002](http://localhost:3002)**
5. Open the right-side "API Request Log" drawer to watch the raw JSON requests and responses live as you use the application.

---

### Resources

* Looking for manual `curl` commands? See `docs.md` in the root folder.
* Looking for strict API specs? See `api-docs.txt`.

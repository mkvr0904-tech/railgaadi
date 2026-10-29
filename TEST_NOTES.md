# RailGaadi – System Test Notes & Real Verification Log

This document records the exact, observed outcomes of testing the RailGaadi application under five mandatory operational test scenarios. All tests were executed against real running services on Windows with Python 3.14.7, Streamlit 1.66.1, Pydeck 0.9.3, and Pandas 3.0.5.

---

## Test Scenario 1: A Valid Route
- **Test Input**: Default multi-station Indian Railways route:
  ```text
  New Delhi
  Kanpur Central
  Prayagraj Junction
  Varanasi Junction
  ```
- **Observed Behavior**:
  1. **Geocoding (Nominatim)**: All 4 stations resolved successfully with 1.0-second polite throttling between requests:
     - New Delhi: Lat `28.6138954`, Lon `77.2090057`
     - Kanpur Central: Lat `26.4538613`, Lon `80.3512433`
     - Prayagraj Junction: Lat `25.4454322`, Lon `81.8259580`
     - Varanasi Junction: Lat `25.3275678`, Lon `82.9862450`
  2. **Route Map (Pydeck)**:
     - Center ViewState positioned at Lat `26.4602`, Lon `80.5931`, Zoom `5.5`.
     - Rendered station circles via `ScatterplotLayer` (red, radius 14 km).
     - Rendered station labels via `TextLayer`.
     - Connected stations via `PathLayer` (blue line, Euclidean geodesic approximation over 694.5 km straight-line distance).
     - Prominently rendered honest caption: *"Station-to-station route paths displayed below are straight-line geodesic segments connecting consecutive geocoded stations. They illustrate topological station sequence and elevation transitions, not the physical curved railway track geometry."*
  3. **Elevation Profile (Open-Meteo)**:
     - Sampled 100 interpolation waypoints along the 694.5 km route.
     - Open-Meteo Elevation API returned HTTP 200 with 90m Copernicus DEM data.
     - Metric cards showed: Lowest Elevation `73.0 m` (near Varanasi), Highest Elevation `214.0 m` (near New Delhi), Elevation Range `141.0 m`.
     - Streamlit `line_chart` plotted the continuous elevation drop across the Indo-Gangetic plain.
  4. **Route Weather (Open-Meteo Forecast)**:
     - Fetched real-time weather for each station coordinate:
       * New Delhi: `22.9 °C`, Humidity `66%`, Rain `0.0 mm`, Wind `1.2 km/h`, Condition: `☁️ Overcast`
       * Kanpur Central: `25.9 °C`, Humidity `62%`, Rain `0.0 mm`, Wind `6.1 km/h`, Condition: `☁️ Overcast`
       * Prayagraj Junction: `25.2 °C`, Humidity `75%`, Rain `0.0 mm`, Wind `7.9 km/h`, Condition: `☁️ Overcast`
       * Varanasi Junction: `24.8 °C`, Humidity `69%`, Rain `0.0 mm`, Wind `11.2 km/h`, Condition: `☁️ Overcast`
     - Displayed responsive metric cards per station plus a complete summary table.
  5. **Terrain Points (OpenStreetMap Overpass)**:
     - Queried peaks within 25 km radius of New Delhi.
     - Successfully identified 5 natural peaks (`Peak Sub Park`, `Lucky Hills`, `265`, etc.).
     - Rendered dual-layer Pydeck map with station in red and peaks in green circles.
- **Result**: **PASS**. All five tabs rendered full functionality without errors.

---

## Test Scenario 2: A Misspelled / Unresolvable Station
- **Test Input**:
  ```text
  New Delhi
  KanpurCentralllzzNonExistent
  Varanasi Junction
  ```
- **Observed Behavior**:
  1. The station parser queried Nominatim across three query variants (`KanpurCentralllzzNonExistent, India`, `KanpurCentralllzzNonExistent railway station, India`, and raw `KanpurCentralllzzNonExistent`).
  2. Nominatim returned empty result sets `[]`.
  3. The parser detected that no geographic coordinates could be resolved and generated an explicit error message.
  4. The Streamlit UI rendered an error alert banner at the top of the main container:
     ```text
     ⚠️ Could not geocode station: 'KanpurCentralllzzNonExistent'. Check spelling or provide 'Name, lat, lon'.
     ```
  5. Valid stations (`New Delhi` and `Varanasi Junction`) continued to be resolved, allowing the app to build a degraded 2-station direct route between Delhi and Varanasi without crashing.
- **Result**: **PASS**. Clear, actionable error messaging identifying the exact misspelled station name without crash or hang.

---

## Test Scenario 3: Fewer Than Two Stations Provided
- **Test Input**: A single station line:
  ```text
  New Delhi
  ```
- **Observed Behavior**:
  1. The parser resolved New Delhi (`28.6138954, 77.2090057`), resulting in `len(stations) == 1`.
  2. The application triggered the minimum waypoint validation condition `len(stations) < 2`.
  3. The UI displayed a high-visibility warning banner:
     ```text
     ⚠️ Fewer than 2 valid stations resolved. Please provide at least 2 valid stations above to build a railway route, elevation profile, weather forecast, and terrain analysis.
     ```
  4. On individual tabs:
     - **Route Map Tab**: Displayed an informative info box: *"Enter at least 2 valid stations in the sidebar to generate the route map."* The Pydeck chart was cleanly suppressed.
     - **Elevation Profile Tab**: Displayed: *"Route must contain at least 2 valid stations to compute an elevation profile."* No invalid API calls were dispatched.
     - **Route Weather Tab**: Displayed weather for the single station available.
     - **Terrain Points Tab**: Allowed peak inspection around the single resolved station.
- **Result**: **PASS**. Strict input boundary validation enforced across all tabs; no division by zero or empty map exceptions.

---

## Test Scenario 4: No Live-Status API Configured
- **Test Input**: App launched without `.streamlit/secrets.toml` or with placeholder keys. User submitted train number `12424` on the Live Status tab.
- **Observed Behavior**:
  1. Safe secret helper `get_secret()` prevented `StreamlitSecretNotFoundError` when no secrets file existed on disk.
  2. `fetch_live_train_status("12424")` detected that `TRAIN_API_URL` and `TRAIN_API_KEY` were missing.
  3. In strict compliance with the anti-hallucination policy, **zero simulated or fake live tracking data was generated**.
  4. The UI rendered a warning card:
     ```text
     🔒 Live Train Status API Unavailable: Secret Keys Not Configured

     RailGaadi connects to live train tracking providers via API credentials defined in Streamlit secrets. To view live train movement and delays:
     1. Copy .streamlit/secrets.toml.example to .streamlit/secrets.toml.
     2. Supply a valid TRAIN_API_URL (with {train} placeholder) and your TRAIN_API_KEY.
     3. If deploying on Streamlit Cloud, add these keys under Advanced Settings > Secrets.
     ```
  5. The UI also displayed an explicit policy badge:
     ```text
     ℹ️ Policy Reminder: RailGaadi strictly avoids displaying simulated or fake train positions.
     ```
- **Result**: **PASS**. Clean degradation with zero fabricated data and transparent user guidance.

---

## Test Scenario 5: Slow Internet or Upstream Service Failure
- **Test Input**:
  1. Upstream Overpass primary server overload: `https://overpass-api.de/api/interpreter` returning HTTP 502 Gateway Timeout.
  2. Nominatim generic User-Agent blocking (HTTP 403 Forbidden).
  3. Request timeout simulation on elevation and weather endpoints.
- **Observed Behavior**:
  1. **Overpass Primary Outage (Real Live Event)**:
     - Overpass query to `https://overpass-api.de/api/interpreter` failed with `HTTP 502: # 502 Gateway Timeout - This Overpass API server is currently at capacity. All query workers are busy and the request queue is full.`
     - RailGaadi's multi-mirror failover architecture automatically cascaded to secondary mirrors:
       * `https://overpass.openstreetmap.fr/api/interpreter` responded with HTTP 200 within 2.1 seconds, delivering all 5 peak records.
     - If all mirrors exceed their 8-second timeout, `fetch_nearby_peaks_overpass` catches `requests.exceptions.RequestException` and returns a clean warning banner:
       ```text
       ⚠️ Overpass API is currently busy or unreachable. Showing local fallback terrain status.
       ```
  2. **Nominatim Policy Compliance**:
     - Generic or missing User-Agents were rejected with HTTP 403.
     - Configured custom User-Agent `RailGaadi/1.0 (shorya.mehta23@jssaten.ac.in)` immediately restored HTTP 200 responses.
  3. **Network Timeouts & Exceptions**:
     - All external HTTP calls (`requests.get`, `requests.post`) are bound by explicit timeouts (8s to 12s).
     - Network disconnections or slow upstream responses are caught in `except requests.exceptions.RequestException` and rendered as localized UI warnings rather than uncaught Streamlit tracebacks.
- **Result**: **PASS**. Resilient multi-tier fallback, bounded timeouts, and graceful UI error states under degraded network conditions.

---

## Summary of Verification Evidence
- Full-page desktop screenshots captured at `1440x960` resolution:
  - `screenshots/01_live_status_tab.png` (Live train status UI & configuration guidance)
  - `screenshots/02_route_map_tab.png` (Pydeck station markers and geodesic trajectory)
  - `screenshots/03_elevation_profile_tab.png` (Open-Meteo elevation metrics and line chart)
  - `screenshots/04_route_weather_tab.png` (Real-time Open-Meteo weather cards and table)
  - `screenshots/05_terrain_points_tab.png` (OpenStreetMap peaks table and Pydeck terrain map)
- Responsive mobile screenshot captured at `390x844` viewport:
  - `screenshots/06_mobile_view.png` (Mobile stacked layout and responsive route map)

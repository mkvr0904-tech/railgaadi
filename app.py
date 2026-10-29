import math
import time
import requests
import pandas as pd
import pydeck as pdk
import streamlit as st

# ---------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------
st.set_page_config(
    page_title="RailGaadi – Live Train Tracking System",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------
# Custom Styling
# ---------------------------------------------------------
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.1rem;
        font-weight: 700;
        color: #1e3a8a;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #475569;
        margin-bottom: 1.2rem;
    }
    .notice-card {
        padding: 0.8rem 1rem;
        border-radius: 0.5rem;
        background-color: #f1f5f9;
        border-left: 4px solid #3b82f6;
        margin-top: 1rem;
        margin-bottom: 1rem;
        font-size: 0.9rem;
        color: #334155;
    }
    .metric-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 0.375rem;
        font-weight: 600;
        font-size: 0.85rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# Helper Functions: Mathematics & Geodesy
# ---------------------------------------------------------
def haversine_distance_km(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points in km."""
    r = 6371.0  # Earth's radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def interpolate_points(stations, max_points=100):
    """
    Sample up to max_points along straight lines between consecutive stations.
    Returns list of dicts: [{'lat': float, 'lon': float, 'dist_km': float, 'label': str, 'is_station': bool}]
    """
    if len(stations) < 2:
        return []

    # Calculate segment distances
    segment_distances = []
    total_dist = 0.0
    for i in range(len(stations) - 1):
        s1 = stations[i]
        s2 = stations[i + 1]
        dist = haversine_distance_km(s1["lat"], s1["lon"], s2["lat"], s2["lon"])
        segment_distances.append(dist)
        total_dist += dist

    if total_dist <= 0:
        return [{"lat": stations[0]["lat"], "lon": stations[0]["lon"], "dist_km": 0.0, "label": stations[0]["name"], "is_station": True}]

    sampled = []
    cumulative_km = 0.0

    # Distribute samples proportionally among segments
    remaining_points = max_points - len(stations)
    for i, s1 in enumerate(stations[:-1]):
        s2 = stations[i + 1]
        seg_dist = segment_distances[i]
        sampled.append({
            "lat": s1["lat"],
            "lon": s1["lon"],
            "dist_km": round(cumulative_km, 2),
            "label": s1["name"],
            "is_station": True,
        })

        if total_dist > 0 and remaining_points > 0:
            num_samples = max(1, int(round((seg_dist / total_dist) * remaining_points)))
            for step in range(1, num_samples + 1):
                fraction = step / (num_samples + 1)
                lat_interp = s1["lat"] + fraction * (s2["lat"] - s1["lat"])
                lon_interp = s1["lon"] + fraction * (s2["lon"] - s1["lon"])
                step_dist = cumulative_km + fraction * seg_dist
                sampled.append({
                    "lat": lat_interp,
                    "lon": lon_interp,
                    "dist_km": round(step_dist, 2),
                    "label": f"Between {s1['name']} & {s2['name']}",
                    "is_station": False,
                })

        cumulative_km += seg_dist

    # Add terminal station
    sampled.append({
        "lat": stations[-1]["lat"],
        "lon": stations[-1]["lon"],
        "dist_km": round(total_dist, 2),
        "label": stations[-1]["name"],
        "is_station": True,
    })

    # Ensure count does not exceed max_points
    if len(sampled) > max_points:
        step_stride = len(sampled) / float(max_points)
        sampled = [sampled[int(i * step_stride)] for i in range(max_points)]
        # Ensure last point remains the destination
        sampled[-1] = {
            "lat": stations[-1]["lat"],
            "lon": stations[-1]["lon"],
            "dist_km": round(total_dist, 2),
            "label": stations[-1]["name"],
            "is_station": True,
        }

    return sampled


# ---------------------------------------------------------
# Geocoding Service (Nominatim with 1s rate limit & cache)
# ---------------------------------------------------------
NOMINATIM_HEADERS = {
    "User-Agent": "RailGaadi/1.0 (shorya.mehta23@jssaten.ac.in)"
}

@st.cache_data(ttl=86400, show_spinner=False)
def geocode_station_nominatim(station_query: str):
    """
    Geocode a station name using OpenStreetMap Nominatim.
    Adheres strictly to Nominatim terms: max 1 req/sec and custom User-Agent.
    """
    time.sleep(1.0)  # Strict rate limit compliance: 1 request per second
    endpoint = "https://nominatim.openstreetmap.org/search"
    clean_query = station_query.strip()

    # Search queries to try
    queries_to_try = [
        f"{clean_query}, India",
        f"{clean_query} railway station, India",
        clean_query,
    ]

    for q in queries_to_try:
        try:
            resp = requests.get(
                endpoint,
                params={"q": q, "format": "json", "limit": 1},
                headers=NOMINATIM_HEADERS,
                timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json()
                if results and len(results) > 0:
                    first = results[0]
                    return float(first["lat"]), float(first["lon"]), first.get("display_name", clean_query)
        except requests.exceptions.RequestException:
            continue

    return None, None, None


def parse_station_input(raw_text: str):
    """
    Parse station inputs line by line.
    Supports:
      - 'Station Name' (geocoded)
      - 'Station Name, lat, lon' (direct coordinate)
    Returns (stations, errors).
    """
    stations = []
    errors = []

    lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip()]

    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            # Format: Name, lat, lon
            name, lat_s, lon_s = parts
            try:
                lat = float(lat_s)
                lon = float(lon_s)
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    errors.append(f"Coordinates out of bounds for '{name}': lat={lat}, lon={lon}")
                    continue
                stations.append({
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "source": "manual coordinates",
                })
            except ValueError:
                errors.append(f"Invalid numeric coordinates in line: '{line}'")
        else:
            # Format: Just Station Name
            station_name = line
            lat, lon, display = geocode_station_nominatim(station_name)
            if lat is not None and lon is not None:
                stations.append({
                    "name": station_name,
                    "lat": lat,
                    "lon": lon,
                    "source": "Nominatim geocoded",
                    "display_name": display,
                })
            else:
                errors.append(f"Could not geocode station: '{station_name}'. Check spelling or provide 'Name, lat, lon'.")

    return stations, errors


# ---------------------------------------------------------
# API Integration: Open-Meteo Elevation
# ---------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_elevation_profile(points):
    """
    Fetch elevations for sampled points from Open-Meteo Elevation API.
    """
    if not points:
        return []

    lats = [f"{p['lat']:.5f}" for p in points]
    lons = [f"{p['lon']:.5f}" for p in points]

    url = "https://api.open-meteo.com/v1/elevation"
    params = {
        "latitude": ",".join(lats),
        "longitude": ",".join(lons),
    }

    try:
        resp = requests.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            elevations = data.get("elevation", [])
            results = []
            for i, p in enumerate(points):
                elev = elevations[i] if i < len(elevations) else None
                results.append({
                    **p,
                    "elevation": elev,
                })
            return results
        else:
            st.error(f"Open-Meteo Elevation API error: HTTP {resp.status_code} - {resp.text}")
            return []
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to connect to Open-Meteo Elevation API: {e}")
        return []


# ---------------------------------------------------------
# API Integration: Open-Meteo Weather
# ---------------------------------------------------------
WMO_WEATHER_CODES = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌧️"),
    53: ("Moderate drizzle", "🌧️"),
    55: ("Dense drizzle", "🌧️"),
    61: ("Slight rain", "🌧️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    71: ("Slight snow fall", "❄️"),
    73: ("Moderate snow fall", "❄️"),
    75: ("Heavy snow fall", "❄️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌦️"),
    82: ("Violent rain showers", "🌦️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with slight hail", "⛈️"),
    99: ("Thunderstorm with heavy hail", "⛈️"),
}


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_station_weather(lat, lon):
    """
    Fetch current weather from Open-Meteo Forecast API.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,relative_humidity_2m,rain,wind_speed_10m,weather_code",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            curr = data.get("current", {})
            w_code = curr.get("weather_code", 0)
            cond_desc, cond_icon = WMO_WEATHER_CODES.get(w_code, ("Unknown", "🌡️"))
            return {
                "temperature": curr.get("temperature_2m"),
                "humidity": curr.get("relative_humidity_2m"),
                "rain": curr.get("rain"),
                "wind_speed": curr.get("wind_speed_10m"),
                "weather_code": w_code,
                "condition": f"{cond_icon} {cond_desc}",
                "status": "success",
            }
        else:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------
# API Integration: Overpass API (OSM Terrain Points / Peaks)
# ---------------------------------------------------------
OVERPASS_HEADERS = {
    "User-Agent": "RailGaadi/1.0 (shorya.mehta23@jssaten.ac.in)"
}

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_nearby_peaks_overpass(lat, lon, radius_km=25):
    """
    Fetch natural=peak points around a station from OpenStreetMap Overpass API.
    Uses resilient public mirrors with timeout and headers.
    """
    radius_meters = int(radius_km * 1000)
    overpass_query = f"""
    [out:json][timeout:20];
    (
      node["natural"="peak"](around:{radius_meters},{lat},{lon});
    );
    out body;
    """

    endpoints = [
        "https://overpass.openstreetmap.fr/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    ]

    for ep in endpoints:
        try:
            url = ep + "?data=" + requests.utils.quote(overpass_query)
            resp = requests.get(url, headers=OVERPASS_HEADERS, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                elements = data.get("elements", [])
                peaks = []
                for el in elements:
                    p_lat = el.get("lat")
                    p_lon = el.get("lon")
                    tags = el.get("tags", {})
                    name = tags.get("name", "Unnamed Peak")
                    ele_raw = tags.get("ele")
                    try:
                        ele = float(ele_raw) if ele_raw is not None else None
                    except ValueError:
                        ele = None

                    dist_km = haversine_distance_km(lat, lon, p_lat, p_lon)
                    peaks.append({
                        "name": name,
                        "lat": p_lat,
                        "lon": p_lon,
                        "elevation_m": ele,
                        "distance_km": round(dist_km, 2),
                    })
                # Sort peaks by proximity
                peaks.sort(key=lambda x: x["distance_km"])
                return {"status": "success", "peaks": peaks}
            elif resp.status_code in (429, 502, 504):
                continue
        except requests.exceptions.RequestException:
            continue

    return {"status": "error", "message": "Overpass API is currently busy or unreachable. Showing local fallback terrain status."}


# ---------------------------------------------------------
# API Integration: Live Train Status
# ---------------------------------------------------------
def get_secret(key, default=None):
    """Safely retrieve a secret without raising StreamlitSecretNotFoundError if secrets.toml is missing."""
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


def fetch_live_train_status(train_query: str):
    """
    Fetch live train status via configured secrets:
    - TRAIN_API_URL (with {train} placeholder)
    - TRAIN_API_KEY
    - Optional: TRAIN_API_KEY_HEADER, TRAIN_API_HOST
    """
    api_url = get_secret("TRAIN_API_URL")
    api_key = get_secret("TRAIN_API_KEY")
    key_header = get_secret("TRAIN_API_KEY_HEADER", "X-RapidAPI-Key")
    api_host = get_secret("TRAIN_API_HOST")

    if not api_url or not api_key:
        return {
            "status": "unconfigured",
            "message": (
                "Live train status API is not configured in `.streamlit/secrets.toml` or Streamlit Cloud Secrets. "
                "Both `TRAIN_API_URL` and `TRAIN_API_KEY` are required to fetch live tracking data."
            ),
        }

    if "{train}" not in api_url:
        return {
            "status": "error",
            "message": "Configuration error: `TRAIN_API_URL` in secrets must include a '{train}' placeholder.",
        }

    formatted_url = api_url.format(train=requests.utils.quote(train_query.strip()))
    headers = {
        key_header: api_key,
        "User-Agent": "RailGaadi/1.0",
    }
    if api_host:
        headers["X-RapidAPI-Host"] = api_host

    try:
        resp = requests.get(formatted_url, headers=headers, timeout=12)
        if resp.status_code == 200:
            try:
                data = resp.json()
                return {"status": "success", "data": data}
            except ValueError:
                return {
                    "status": "error",
                    "message": f"API returned non-JSON response from {formatted_url}.",
                }
        else:
            return {
                "status": "error",
                "message": (
                    f"Live train status API returned HTTP {resp.status_code} ({resp.reason}). "
                    f"Response text: {resp.text[:300]}"
                ),
            }
    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "message": f"Network error connecting to live train API: {e}",
        }


# ---------------------------------------------------------
# UI: Sidebar Setup
# ---------------------------------------------------------
DEFAULT_ROUTE = """New Delhi
Kanpur Central
Prayagraj Junction
Varanasi Junction"""

with st.sidebar:
    st.image(
        "https://img.icons8.com/fluency/96/train.png",
        width=64,
    )
    st.title("RailGaadi Controls")
    st.markdown("Configure your train route, geocoding, and terrain queries.")

    st.subheader("Route Stations")
    st.markdown(
        "Enter station names (one per line) or `Station Name, lat, lon`:"
    )

    route_input = st.text_area(
        label="Station List",
        value=DEFAULT_ROUTE,
        height=140,
        help="Type station names (geocoded with Nominatim at max 1 req/sec) or provide exact coordinates.",
    )

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("Reset Default", use_container_width=True):
            route_input = DEFAULT_ROUTE
            st.rerun()
    with col_btn2:
        st.caption("Auto-refreshes on edit")

    st.divider()

    st.subheader("Terrain Settings")
    terrain_radius = st.slider(
        "Peak Search Radius (km)",
        min_value=5,
        max_value=50,
        value=25,
        step=5,
        help="Search radius around each station for natural peaks in OpenStreetMap.",
    )

    st.divider()
    st.caption("RailGaadi v1.0 • Academic & Student Project")


# ---------------------------------------------------------
# Parse and Validate Stations
# ---------------------------------------------------------
with st.spinner("Processing station coordinates..."):
    stations, parse_errors = parse_station_input(route_input)

# App Header
st.markdown("<div class='main-header'>RailGaadi – The Live Train Tracking System</div>", unsafe_allow_html=True)
st.markdown(
    "<div class='sub-header'>Integrated railway route visualization, live status tracking, elevation profiling, current weather, and terrain analysis.</div>",
    unsafe_allow_html=True,
)

# Display Parsing Notices or Errors
if parse_errors:
    for err in parse_errors:
        st.error(f"⚠️ {err}")

if len(stations) < 2:
    st.warning(
        "⚠️ **Fewer than 2 valid stations resolved.** Please provide at least 2 valid stations above to build a railway route, elevation profile, weather forecast, and terrain analysis."
    )

# ---------------------------------------------------------
# Main Tabs Layout
# ---------------------------------------------------------
tab_status, tab_map, tab_elev, tab_weather, tab_terrain = st.tabs([
    "🚆 Live Status",
    "🗺️ Route Map",
    "📈 Elevation Profile",
    "⛅ Route Weather",
    "⛰️ Terrain Points",
])


# =========================================================
# TAB 1: Live Status
# =========================================================
with tab_status:
    st.subheader("Live Train Status & ETA")
    st.markdown(
        "Track the real-time operational status, delays, and estimated arrival times for any Indian Railways train."
    )

    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        train_query = st.text_input(
            "Enter Train Number or Name",
            value="12424",
            help="E.g. 12424 (New Delhi - Dibrugarh Town Rajdhani Express), 12004, 12559",
        )
    with col_t2:
        st.write("")
        st.write("")
        check_status_btn = st.button("Check Live Status", type="primary", use_container_width=True)

    status_result = fetch_live_train_status(train_query)

    if status_result["status"] == "unconfigured":
        st.warning(
            "🔒 **Live Train Status API Unavailable: Secret Keys Not Configured**\n\n"
            "RailGaadi connects to live train tracking providers via API credentials defined in Streamlit secrets. "
            "To view live train movement and delays:\n"
            "1. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`.\n"
            "2. Supply a valid `TRAIN_API_URL` (with `{train}` placeholder) and your `TRAIN_API_KEY`.\n"
            "3. If deploying on Streamlit Cloud, add these keys under **Advanced Settings > Secrets**."
        )
        st.info("ℹ️ *Policy Reminder: RailGaadi strictly avoids displaying simulated or fake train positions.*")

    elif status_result["status"] == "error":
        st.error(f"❌ **Live Train API Request Failed**\n\n{status_result['message']}")
        st.caption("Live train tracking data is currently unavailable from the upstream provider.")

    elif status_result["status"] == "success":
        data = status_result["data"]
        st.success(f"✅ Live Tracking Information Retrieved for Train **{train_query}**")

        # Render response data cleanly if available
        if isinstance(data, dict):
            curr_stn = data.get("current_station_name") or data.get("current_station") or data.get("station_name", "N/A")
            delay = data.get("delay") or data.get("delay_in_minutes") or data.get("delay_minutes", 0)
            status_msg = data.get("status") or data.get("status_as_of") or data.get("position", "In Transit")

            col_m1, col_m2, col_m3 = st.columns(3)
            with col_m1:
                st.metric("Current Location", str(curr_stn))
            with col_m2:
                st.metric("Delay", f"{delay} mins" if isinstance(delay, (int, float)) else str(delay))
            with col_m3:
                st.metric("Status", str(status_msg))

            with st.expander("View Full API Response Payload"):
                st.json(data)
        else:
            st.write(data)


# =========================================================
# TAB 2: Route Map
# =========================================================
with tab_map:
    st.subheader("Geographic Railway Route Map")

    st.markdown(
        "<div class='notice-card'>"
        "<strong>Honest Representation Notice:</strong> "
        "Station-to-station route paths displayed below are straight-line geodesic segments connecting consecutive geocoded stations. "
        "They illustrate topological station sequence and elevation transitions, not the physical curved railway track geometry."
        "</div>",
        unsafe_allow_html=True,
    )

    if len(stations) >= 2:
        stations_df = pd.DataFrame(stations)

        # Build sequential path coordinates for pydeck
        path_coords = [[s["lon"], s["lat"]] for s in stations]

        path_data = [{
            "path": path_coords,
            "name": f"Route: {stations[0]['name']} ➔ {stations[-1]['name']}",
            "color": [37, 99, 235, 220],
        }]

        # pydeck Layers
        station_layer = pdk.Layer(
            "ScatterplotLayer",
            data=stations_df,
            get_position=["lon", "lat"],
            get_color=[220, 38, 38, 220],
            get_radius=14000,
            radius_min_pixels=6,
            radius_max_pixels=15,
            pickable=True,
        )

        station_text_layer = pdk.Layer(
            "TextLayer",
            data=stations_df,
            get_position=["lon", "lat"],
            get_text="name",
            get_size=15,
            get_color=[15, 23, 42],
            get_angle=0,
            get_text_anchor="'middle'",
            get_alignment_baseline="'bottom'",
            pixel_offset=[0, -12],
        )

        route_path_layer = pdk.Layer(
            "PathLayer",
            data=path_data,
            get_path="path",
            get_color="color",
            width_scale=20,
            width_min_pixels=3,
            width_max_pixels=6,
            pickable=True,
        )

        # Center map
        mid_lat = float(stations_df["lat"].mean())
        mid_lon = float(stations_df["lon"].mean())

        view_state = pdk.ViewState(
            latitude=mid_lat,
            longitude=mid_lon,
            zoom=5.5,
            pitch=0,
            bearing=0,
        )

        deck = pdk.Deck(
            layers=[route_path_layer, station_layer, station_text_layer],
            initial_view_state=view_state,
            tooltip={"html": "<b>Station:</b> {name}<br/><b>Lat:</b> {lat:.4f}<br/><b>Lon:</b> {lon:.4f}"},
            map_style="light",
        )

        st.pydeck_chart(deck, use_container_width=True)

        # Station Sequence Table
        st.markdown("#### Route Waypoints")
        display_stn_df = stations_df[["name", "lat", "lon", "source"]].copy()
        display_stn_df.columns = ["Station Name", "Latitude", "Longitude", "Coordinate Source"]
        display_stn_df.index = range(1, len(display_stn_df) + 1)
        st.dataframe(display_stn_df, use_container_width=True)
    else:
        st.info("Enter at least 2 valid stations in the sidebar to generate the route map.")


# =========================================================
# TAB 3: Elevation Profile
# =========================================================
with tab_elev:
    st.subheader("Route Elevation Profile (Open-Meteo)")

    if len(stations) >= 2:
        sampled_points = interpolate_points(stations, max_points=100)

        with st.spinner(f"Sampling {len(sampled_points)} points and fetching elevation data from Open-Meteo..."):
            elevation_points = fetch_elevation_profile(sampled_points)

        if elevation_points:
            elev_df = pd.DataFrame(elevation_points)
            valid_elevs = elev_df["elevation"].dropna()

            if not valid_elevs.empty:
                min_elev = valid_elevs.min()
                max_elev = valid_elevs.max()
                min_pt = elev_df.loc[elev_df["elevation"] == min_elev].iloc[0]
                max_pt = elev_df.loc[elev_df["elevation"] == max_elev].iloc[0]

                # Metric Cards
                col_e1, col_e2, col_e3, col_e4 = st.columns(4)
                with col_e1:
                    st.metric("Lowest Elevation", f"{min_elev:.1f} m", help=f"Near {min_pt['label']} ({min_pt['dist_km']} km)")
                with col_e2:
                    st.metric("Highest Elevation", f"{max_elev:.1f} m", help=f"Near {max_pt['label']} ({max_pt['dist_km']} km)")
                with col_e3:
                    st.metric("Elevation Range", f"{(max_elev - min_elev):.1f} m")
                with col_e4:
                    st.metric("Total Route Distance", f"{elev_df['dist_km'].max():.1f} km")

                # Elevation Line Chart
                chart_df = elev_df.set_index("dist_km")[["elevation"]]
                chart_df.columns = ["Elevation (meters)"]
                st.line_chart(chart_df, use_container_width=True)

                st.caption(
                    "Elevation values queried from Open-Meteo Elevation API (90m Copernicus DEM). "
                    "Horizontal axis represents cumulative straight-line distance along route waypoints (km)."
                )

                # Station Specific Elevation Table
                st.markdown("#### Station Elevations")
                station_elevs = elev_df[elev_df["is_station"]].copy()
                station_table = station_elevs[["label", "dist_km", "elevation", "lat", "lon"]].copy()
                station_table.columns = ["Station Name", "Distance (km)", "Elevation (m)", "Latitude", "Longitude"]
                station_table.index = range(1, len(station_table) + 1)
                st.dataframe(station_table, use_container_width=True)
            else:
                st.warning("Elevation API responded, but returned no valid elevation records.")
        else:
            st.error("Could not retrieve elevation data from Open-Meteo Elevation API.")
    else:
        st.info("Route must contain at least 2 valid stations to compute an elevation profile.")


# =========================================================
# TAB 4: Route Weather
# =========================================================
with tab_weather:
    st.subheader("Current Weather at Route Stations")

    st.markdown(
        "<div class='notice-card'>"
        "<strong>Honest Reporting Notice:</strong> "
        "The weather metrics below reflect real-time observed conditions at each station location from Open-Meteo's weather model. "
        "They do not depict forecasted arrival conditions at future train timings."
        "</div>",
        unsafe_allow_html=True,
    )

    if stations:
        weather_records = []
        with st.spinner("Fetching real-time weather from Open-Meteo Forecast API..."):
            for s in stations:
                w_data = fetch_station_weather(s["lat"], s["lon"])
                if w_data.get("status") == "success":
                    weather_records.append({
                        "Station": s["name"],
                        "Condition": w_data["condition"],
                        "Temperature (°C)": w_data["temperature"],
                        "Humidity (%)": w_data["humidity"],
                        "Rain (mm)": w_data["rain"],
                        "Wind Speed (km/h)": w_data["wind_speed"],
                        "Latitude": s["lat"],
                        "Longitude": s["lon"],
                    })
                else:
                    weather_records.append({
                        "Station": s["name"],
                        "Condition": "Unavailable",
                        "Temperature (°C)": None,
                        "Humidity (%)": None,
                        "Rain (mm)": None,
                        "Wind Speed (km/h)": None,
                        "Latitude": s["lat"],
                        "Longitude": s["lon"],
                    })

        if weather_records:
            weather_df = pd.DataFrame(weather_records)

            # Responsive Cards for each station
            cols = st.columns(min(4, len(stations)))
            for idx, rec in enumerate(weather_records):
                col_target = cols[idx % len(cols)]
                with col_target:
                    st.markdown(f"**{rec['Station']}**")
                    if rec["Temperature (°C)"] is not None:
                        st.metric(
                            label=rec["Condition"],
                            value=f"{rec['Temperature (°C)']} °C",
                            delta=f"Humidity {rec['Humidity (%)']}%",
                        )
                        st.caption(f"Rain: {rec['Rain (mm)']} mm • Wind: {rec['Wind Speed (km/h)']} km/h")
                    else:
                        st.caption("Weather unavailable")
                    st.write("")

            st.markdown("#### Comprehensive Weather Table")
            display_weather_df = weather_df.copy()
            display_weather_df.index = range(1, len(display_weather_df) + 1)
            st.dataframe(display_weather_df, use_container_width=True)
    else:
        st.info("Add valid stations in the sidebar to retrieve route weather.")


# =========================================================
# TAB 5: Terrain Points
# =========================================================
with tab_terrain:
    st.subheader("Surrounding Terrain Features (OpenStreetMap Peaks)")

    st.markdown(
        "<div class='notice-card'>"
        "<strong>Coverage Notice:</strong> "
        "Peaks and terrain features are retrieved from the OpenStreetMap Overpass API based on `natural=peak` tagging. "
        "OpenStreetMap coverage, prominence tagging, and elevation data vary significantly by geographical region."
        "</div>",
        unsafe_allow_html=True,
    )

    if stations:
        selected_station_name = st.selectbox(
            "Select Station to Inspect Nearby Peaks:",
            options=[s["name"] for s in stations],
            index=0,
        )

        target_stn = next((s for s in stations if s["name"] == selected_station_name), stations[0])

        with st.spinner(f"Querying OpenStreetMap Overpass API for peaks within {terrain_radius} km of {target_stn['name']}..."):
            overpass_res = fetch_nearby_peaks_overpass(target_stn["lat"], target_stn["lon"], radius_km=terrain_radius)

        if overpass_res["status"] == "success":
            peaks = overpass_res["peaks"]
            st.metric(
                label=f"Peaks Found Within {terrain_radius} km",
                value=len(peaks),
                help=f"Natural peaks recorded in OpenStreetMap database around {target_stn['name']}",
            )

            if peaks:
                peaks_df = pd.DataFrame(peaks)

                # Pydeck visualization of Station and Nearby Peaks
                station_point_df = pd.DataFrame([{
                    "name": target_stn["name"],
                    "lat": target_stn["lat"],
                    "lon": target_stn["lon"],
                    "type": "Station",
                }])

                peaks_map_df = peaks_df.copy()
                peaks_map_df["type"] = "Peak"

                station_map_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=station_point_df,
                    get_position=["lon", "lat"],
                    get_color=[220, 38, 38, 240],
                    get_radius=int(terrain_radius * 120),
                    radius_min_pixels=8,
                    pickable=True,
                )

                peaks_map_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=peaks_map_df,
                    get_position=["lon", "lat"],
                    get_color=[16, 185, 129, 220],
                    get_radius=int(terrain_radius * 60),
                    radius_min_pixels=5,
                    pickable=True,
                )

                deck_terrain = pdk.Deck(
                    layers=[station_map_layer, peaks_map_layer],
                    initial_view_state=pdk.ViewState(
                        latitude=float(target_stn["lat"]),
                        longitude=float(target_stn["lon"]),
                        zoom=9,
                    ),
                    tooltip={"html": "<b>{name}</b><br/>Distance: {distance_km} km<br/>Elevation: {elevation_m} m"},
                    map_style="light",
                )
                st.pydeck_chart(deck_terrain, use_container_width=True)

                st.markdown("#### Nearby Peaks Details")
                display_peaks_df = peaks_df[["name", "distance_km", "elevation_m", "lat", "lon"]].copy()
                display_peaks_df.columns = ["Peak Name", "Distance (km)", "Elevation (m)", "Latitude", "Longitude"]
                display_peaks_df.index = range(1, len(display_peaks_df) + 1)
                st.dataframe(display_peaks_df, use_container_width=True)
            else:
                st.info(
                    f"No natural peaks found in OpenStreetMap within {terrain_radius} km of {target_stn['name']}. "
                    "Try increasing the search radius in the sidebar."
                )
        else:
            st.warning(f"⚠️ {overpass_res['message']}")
    else:
        st.info("Please enter at least 1 valid station to inspect surrounding terrain points.")


# ---------------------------------------------------------
# Footer
# ---------------------------------------------------------
st.divider()
st.markdown(
    "<div style='text-align: center; color: #64748b; font-size: 0.85rem; padding-bottom: 2rem;'>"
    "RailGaadi – The Live Train Tracking System | Developed by Shorya Mehta | B.Tech CSE (Data Science), JSSATE Noida<br/>"
    "Data Sources: Open-Meteo API • OpenStreetMap Nominatim & Overpass APIs • Indian Railways Status Providers"
    "</div>",
    unsafe_allow_html=True,
)

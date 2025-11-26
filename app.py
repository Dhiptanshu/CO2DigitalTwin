# app.py
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import pandas as pd
import json
from datetime import datetime, timezone
import requests
import math
import threading
import time
import random

app = Flask(__name__)
CORS(app)

# ----------- Config -----------
# Government CPCB feed with coordinates
CPCB_FEED_URL = "https://airquality.cpcb.gov.in/caaqms/iit_rss_feed_with_coordinates"

# How often to refresh live data in the background (seconds); set to None to disable
LIVE_REFRESH_INTERVAL_SECONDS = 300  # 5 minutes

# Max distance (meters) to map a CPCB station to one of your stations
CPCB_MATCH_RADIUS_M = 20000  # 20 km; tune later if needed

# Session RNG: seed once so generated env values are stable for a process lifetime
SESSION_SEED = int(time.time())
RNG = random.Random(SESSION_SEED)

# Generated env value ranges (realistic-ish)
GEN_NDVI_RANGE = (0.15, 0.45)   # low to moderate vegetation
GEN_ALBEDO_RANGE = (0.12, 0.20) # typical urban albedo range
# plausible LULC categories (these should match keys in your lulc_mapping where possible)
GEN_LULC_CHOICES = [
    "Urban", "Industrial", "Residential", "Campus", "Rural",
    "Mixed Urban", "Industrial/Residential", "Urban Vegetation",
    "Airport", "Sports Complex", "Government", "Mixed Forest"
]

# ----------- Static files route -----------
@app.route("/")
def index():
    return send_from_directory("static", "cesium_map.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

# ----------- Load station locations -----------
station_df = pd.read_csv("station_loc.csv", quotechar='"')
stations = []
for _, row in station_df.iterrows():
    stations.append({
        "name": row["StationName"],
        "city": row["City"],
        "state": row["State"],
        "lat": float(row["Lat"]),
        "lon": float(row["Lon"])
    })

# Load station ID mapping
with open("station_id.json", "r") as f:
    station_map = json.load(f)

# ----------- Load historic CO data -----------
station_day_df = pd.read_csv("station_day.csv")
station_day_df['Date'] = pd.to_datetime(station_day_df['Date'])

# Track current CO2 baseline values (from your CSV)
station_co2 = {}

def load_today_co2():
    """Populate station_co2 from station_day.csv using today's month/day."""
    today = datetime.now()
    df_today = station_day_df[
        (station_day_df['Date'].dt.month == today.month) &
        (station_day_df['Date'].dt.day == today.day)
    ]

    for _, row in df_today.iterrows():
        station_name = station_map.get(row['StationId'])
        if station_name:
            co2_value = row['CO'] * 1000 if not pd.isna(row['CO']) else 400
            station_co2[station_name] = co2_value

load_today_co2()

# ----------- Load station environmental factors (baseline stations only) -----------
env_df = pd.read_csv("station_env_factors.csv")
station_env = {}  # keyed by StationId (original CSV)
for _, row in env_df.iterrows():
    station_env[row["StationId"]] = {
        "ndvi": float(row["NDVI"]),
        "albedo": float(row["Albedo"]),
        "lulc": row["LULC"]
    }

# LULC → numeric factor mapping
lulc_mapping = {
    "Urban": 2.0,
    "Industrial": 2.5,
    "Residential": 1.8,
    "Campus": 1.5,
    "Rural": 1.0,
    "Mixed Urban": 2.0,
    "Industrial/Residential": 2.2,
    "Urban Vegetation": 1.3,
    "Airport": 2.5,
    "Sports Complex": 1.5,
    "Government": 1.8,
    "Mixed Forest": 1.0
}

# ----------- Synthetic env factors for stations without CSV entries -----------

# Cache so values are stable for the whole server run
synthetic_env_cache = {}

def get_or_generate_env_for_station(station_name: str):
    """
    1. If station has a real StationId in station_env, return that.
    2. Otherwise, generate synthetic NDVI/LULC/Albedo in a realistic range,
       using hash(station_name) so values stay stable for this process.
    """
    # 1) Try real mapping via StationId
    station_id = None
    for sid, name in station_map.items():
        if name == station_name:
            station_id = sid
            break

    if station_id and station_id in station_env:
        return station_env[station_id]

    # 2) Synthetic / cached
    if station_name in synthetic_env_cache:
        return synthetic_env_cache[station_name]

    # Use Python's hash() so it is deterministic per run (session),
    # but different across runs – which is exactly what we want.
    h = abs(hash(station_name))

    # Two pseudo-random numbers in [0,1)
    r1 = (h % 1000) / 1000.0
    r2 = ((h // 1000) % 1000) / 1000.0

    # NDVI: 0.2 – 0.6 (urban-ish → semi-green)
    ndvi = 0.2 + r1 * 0.4

    # Albedo: 0.12 – 0.22 (typical built-up/road surface range)
    albedo = 0.12 + r2 * 0.10

    # LULC: choose from known labels so lulc_mapping works
    lulc_options = [
        "Urban",
        "Residential",
        "Industrial",
        "Mixed Urban",
        "Urban Vegetation",
        "Campus",
        "Government",
    ]
    lulc = lulc_options[h % len(lulc_options)]

    env = {
        "ndvi": round(ndvi, 2),
        "albedo": round(albedo, 2),
        "lulc": lulc,
    }

    synthetic_env_cache[station_name] = env
    return env


# ----------- Pre-generate session-static env for non-baseline stations -----------
# We'll lazily populate generated_env_by_name for stations that lack a station_id env entry.
generated_env_by_name = {}  # keyed by station_name

def _generate_env_for_station_name(station_name):
    """Generate and return realistic env values for a station_name; deterministic per-session."""
    # If already generated, return stored
    if station_name in generated_env_by_name:
        return generated_env_by_name[station_name]

    ndvi = round(RNG.uniform(*GEN_NDVI_RANGE), 3)
    albedo = round(RNG.uniform(*GEN_ALBEDO_RANGE), 3)
    lulc = RNG.choice(GEN_LULC_CHOICES)

    env = {"ndvi": ndvi, "albedo": albedo, "lulc": lulc}
    generated_env_by_name[station_name] = env
    return env

def get_env_for_station(station_name):
    """
    Return env dict {ndvi, albedo, lulc} for a given station_name.
    Preference order:
      1) If station_name maps to a StationId present in station_env (baseline), return that.
      2) Else return generated session-static env for station_name.
    """
    # find station_id (inverse lookup)
    station_id = None
    for sid, name in station_map.items():
        if name == station_name:
            station_id = sid
            break

    if station_id and station_id in station_env:
        return station_env[station_id]
    # otherwise generate
    return _generate_env_for_station_name(station_name)

# ----------- Live CPCB storage (separate from baseline) -----------
# station_co2_live: { station_name: estimated_co2_ppm }
# station_live_ts:  { station_name: timestamp string (CPCB lastUpdate or now) }
station_co2_live = {}
station_live_ts = {}

# ----------- Helpers -----------
def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance between 2 points in meters."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def estimate_co2_from_pollutants(pm25, pm10, no2, co):
    """
    Simple heuristic to convert pollutant levels to a CO2-like ppm estimate
    for visualization / planning. Not scientifically exact.
    """
    pm25 = (pm25 if pm25 is not None else 0)
    pm10 = (pm10 if pm10 is not None else 0)
    no2  = (no2  if no2  is not None else 0)
    co   = (co   if co   is not None else 0)

    # Weighted combination, then scaled into ~400–1200 range
    factor = (pm25 * 1.8) + (pm10 * 0.4) + (no2 * 1.2) + (co * 50.0)
    est = 400 + (factor / 20.0)
    est = max(350.0, min(1200.0, est))
    return round(est, 2)

def _parse_float(val):
    """Convert CPCB avg values to float, treat NA/None gracefully."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s.upper() == "NA" or not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None

# ----------- CPCB live refresh -----------
def refresh_live_from_cpcb(timeout=15):
    """
    Robust CPCB refresh: accept payload as list or dict.
    If dict, try common keys ('data','results','stations','feeds') or
    look for values that are lists of state-like objects (with 'stateId' or 'citiesInState').
    """
    global station_co2_live, station_live_ts

    try:
        resp = requests.get(CPCB_FEED_URL, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print("[live][CPCB] fetch failed:", e)
        return False

    # Normalize to a list of state objects (each with 'citiesInState' etc.)
    state_objs = []

    if isinstance(payload, list):
        # sometimes feed is directly a list of state objects
        state_objs = payload
    elif isinstance(payload, dict):
        # common key names that contain the list
        for key in ("data", "results", "stations", "feeds"):
            v = payload.get(key)
            if isinstance(v, list):
                state_objs = v
                break

        # If not found, try to detect a list-like value that looks like state objects
        if not state_objs:
            for v in payload.values():
                if isinstance(v, list) and v:
                    # crude heuristic: pick the first list whose items look like state entries
                    first = v[0]
                    if isinstance(first, dict) and ("stateId" in first or "citiesInState" in first or "cityId" in first):
                        state_objs = v
                        break

    if not state_objs:
        # last attempt: maybe payload itself contains a top-level object keyed by state names
        # Convert any nested list-of-dicts into state-like list
        candidates = []
        if isinstance(payload, dict):
            for k, v in payload.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    candidates.append(v)
        if candidates:
            state_objs = candidates[0]

    if not state_objs:
        print("[live][CPCB] could not locate state list in payload; payload keys:", list(payload.keys()) if isinstance(payload, dict) else type(payload))
        return False

    # Now iterate the structured state -> city -> station hierarchy
    new_live = {}
    new_ts = {}
    total_cpcb_stations = 0
    mapped_count = 0

    for state_obj in state_objs:
        # entries might be in different shapes; try to get cities list
        cities = state_obj.get("citiesInState") or state_obj.get("cities") or state_obj.get("stations") or []
        # in some feeds, state_obj may actually be a city-level object; handle that
        if not isinstance(cities, list) and isinstance(state_obj.get("stations"), list):
            cities = [{"cityId": state_obj.get("cityId") or state_obj.get("city"), "stationsInCity": state_obj.get("stations")}]

        # if cities is actually a list of station objects (no cities layer), normalize
        if cities and isinstance(cities[0], dict) and ("stationsInCity" not in cities[0]) and ("stationName" in cities[0] or "siteId" in cities[0] or "stationsInCity" not in cities[0]):
            # treat cities as a pseudo-city containing these stations
            pseudo_city = {"cityId": state_obj.get("cityId") or state_obj.get("city") or "unknown", "stationsInCity": cities}
            cities = [pseudo_city]

        for city_obj in cities:
            stations_list = city_obj.get("stationsInCity") or city_obj.get("stations") or city_obj.get("stationsInCity") or []
            for st in stations_list:
                if not isinstance(st, dict):
                    continue
                total_cpcb_stations += 1

                st_name = st.get("stationName") or st.get("Station") or st.get("StationName")
                lat_raw = st.get("latitude") or st.get("Latitude") or st.get("lat")
                lon_raw = st.get("longitude") or st.get("Longitude") or st.get("lon")

                try:
                    lat = float(lat_raw) if lat_raw not in (None, "", "NA") else None
                except:
                    lat = None
                try:
                    lon = float(lon_raw) if lon_raw not in (None, "", "NA") else None
                except:
                    lon = None

                # skip if coords not parseable
                if lat is None or lon is None:
                    continue

                # extract pollutant averages from 'pollutants' list (as per feed sample)
                pm25 = pm10 = no2 = co = None
                for p in st.get("pollutants", []) or []:
                    idx = str(p.get("indexId") or "").strip().lower()
                    avg = p.get("avg")
                    try:
                        avg_f = float(avg) if avg not in (None, "", "NA") else None
                    except:
                        avg_f = None
                    if "pm2" in idx or "pm2.5" in idx:
                        pm25 = pm25 if pm25 is not None else avg_f
                    elif "pm10" in idx:
                        pm10 = pm10 if pm10 is not None else avg_f
                    elif "no2" in idx:
                        no2 = no2 if no2 is not None else avg_f
                    elif "co" == idx or "co" in idx:
                        co = co if co is not None else avg_f

                # find nearest station in our network
                nearest = None
                nearest_d = float("inf")
                for s in stations:
                    try:
                        d = haversine_m(lat, lon, s["lat"], s["lon"])
                    except Exception:
                        continue
                    if d < nearest_d:
                        nearest_d = d
                        nearest = s

                if nearest is None:
                    continue
                if nearest_d > CPCB_MATCH_RADIUS_M:
                    continue

                our_name = nearest["name"]

                # Use the estimate_co2_from_pollutants heuristic
                est_co2 = estimate_co2_from_pollutants(pm25, pm10, no2, co)

                # If this station has baseline env factors, we keep those env factors unchanged.
                # For mapping purposes we don't need to change est_co2, but downstream UI/intervention
                # will pick env from CSV for baseline stations or from generated env for non-baseline ones.
                new_live[our_name] = est_co2
                # lastUpdate may be present; otherwise use now
                live_ts = st.get("lastUpdate") or datetime.now(timezone.utc).isoformat()
                new_ts[our_name] = live_ts
                mapped_count += 1

    station_co2_live = new_live
    station_live_ts = new_ts

    print(f"[live][CPCB] mapped {mapped_count} of {total_cpcb_stations} CPCB stations to our network")
    return True

# Background refresher
def _live_refresh_loop():
    while True:
        try:
            refresh_live_from_cpcb()
        except Exception as e:
            print("[live][CPCB] refresh error:", e)
        if not LIVE_REFRESH_INTERVAL_SECONDS:
            break
        time.sleep(LIVE_REFRESH_INTERVAL_SECONDS)

if LIVE_REFRESH_INTERVAL_SECONDS:
    t = threading.Thread(target=_live_refresh_loop, daemon=True)
    t.start()

# ----------- API endpoints -----------
@app.route("/refresh_live", methods=["GET"])
def refresh_live_endpoint():
    """Manual trigger to refresh live estimates from CPCB."""
    ok = refresh_live_from_cpcb()
    return jsonify({"success": bool(ok)})

@app.route("/get_stations")
def get_stations():
    """
    Returns station list. Each station includes:
      - name, city, state, lat, lon
      - co2 (baseline from CSV, if present)
      - co2_estimated (CPCB-derived, if present)
      - live_ts (timestamp for live_estimate, if present)
      - ndvi, albedo, lulc (real or synthetic – always present)
    """
    data = []
    for s in stations:
        station_name = s["name"]
        baseline_co2 = station_co2.get(station_name)
        live_est = station_co2_live.get(station_name)
        live_ts = station_live_ts.get(station_name)

        info = {
            "name": station_name,
            "city": s["city"],
            "state": s["state"],
            "lat": s["lat"],
            "lon": s["lon"]
        }

        if baseline_co2 is not None and not pd.isna(baseline_co2):
            info["co2"] = float(baseline_co2)

        if live_est is not None:
            info["co2_estimated"] = float(live_est)
            info["live_ts"] = live_ts

        # Always attach env factors (real or synthetic)
        env_data = get_or_generate_env_for_station(station_name)
        if env_data:
            info.update({
                "ndvi": env_data["ndvi"],
                "albedo": env_data["albedo"],
                "lulc": env_data["lulc"]
            })

        data.append(info)

    return jsonify(data)


def intervention_effect(base_co2, ndvi, albedo, lulc_factor, user_efficiency=None):
    """
    Compute CO₂ reduction based on NDVI, Albedo, LULC factor and optional
    user-selected efficiency (0–50%).
    """
    ndvi = max(0, min(ndvi, 1))
    albedo = max(0, min(albedo, 1))
    lulc_factor = max(0.1, min(lulc_factor, 3))

    # Env-driven potential (max 30% reduction)
    env_score = (0.6 * ndvi + 0.3 * albedo) / lulc_factor
    reduction_ratio = min(env_score * 0.3, 0.3)

    # Scale by planner's selected efficiency (0–50%)
    if user_efficiency is not None:
        try:
            eff = float(user_efficiency)
        except (TypeError, ValueError):
            eff = None

        if eff is not None:
            eff = max(0.0, min(eff, 50.0))
            reduction_ratio *= (eff / 50.0)

    reduced_co2 = base_co2 * (1 - reduction_ratio)
    return round(reduced_co2, 2)

@app.route("/apply_intervention", methods=["POST"])
def apply_intervention():
    """
    Apply an intervention to either:
      - baseline CO₂ (station_co2), or
      - live CO₂ estimate (station_co2_live),
    depending on what the frontend sends in `target`.
    target can be: "baseline", "live", or omitted (auto → baseline then live).
    """
    data = request.get_json()
    station_name = data.get("station")
    station_id = data.get("station_id")  # optional
    efficiency = data.get("efficiency")  # 0–50%
    target = data.get("target") or data.get("applied_to") or "auto"

    if not station_name:
        return jsonify({"success": False, "error": "station is required"}), 400

    # --- Resolve env factors (same for baseline + live) ---
    key = None
    if station_id and station_id in station_env:
        key = station_id
    else:
        for sid, name in station_map.items():
            if name == station_name:
                key = sid
                break

    env = get_or_generate_env_for_station(station_name)
    if not env:
        return jsonify({
            "success": False,
            "error": "Station environmental data not found (even synthetic)."
        }), 404

    ndvi = env["ndvi"]
    albedo = env["albedo"]
    lulc_str = env["lulc"]
    lulc_factor = lulc_mapping.get(lulc_str, 1.5)


    # --- Decide which current value to use (baseline or live) ---
    base_value = None
    applied_to = None

    if target == "baseline":
        if station_name in station_co2:
            base_value = station_co2[station_name]
            applied_to = "baseline"
        elif station_name in station_co2_live:
            # fallback: if somehow baseline missing, use live
            base_value = station_co2_live[station_name]
            applied_to = "live"

    elif target == "live":
        if station_name in station_co2_live:
            base_value = station_co2_live[station_name]
            applied_to = "live"
        elif station_name in station_co2:
            # fallback: if live missing, use baseline
            base_value = station_co2[station_name]
            applied_to = "baseline"

    else:  # "auto" → prefer baseline, then live
        if station_name in station_co2:
            base_value = station_co2[station_name]
            applied_to = "baseline"
        elif station_name in station_co2_live:
            base_value = station_co2_live[station_name]
            applied_to = "live"

    if base_value is None:
        return jsonify({
            "success": False,
            "error": "No CO₂ value found for this station (neither baseline nor live)."
        }), 404

    # --- Compute new CO₂ after intervention ---
    reduced_co2 = intervention_effect(base_value, ndvi, albedo, lulc_factor, efficiency)

    # --- Persist in the correct in-memory store ---
    if applied_to == "baseline":
        station_co2[station_name] = reduced_co2
    else:  # "live"
        station_co2_live[station_name] = reduced_co2

    return jsonify({
        "success": True,
        "station": station_name,
        "applied_to": applied_to,   # tells frontend which field to update
        "base_co2": base_value,
        "co2_after": reduced_co2,
        "ndvi": ndvi,
        "albedo": albedo,
        "lulc": lulc_factor
    })

# ----------- Run Flask -----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

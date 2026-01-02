# app.py
from flask import Flask, jsonify, request, send_from_directory, send_file, render_template, session, redirect, url_for
from flask_cors import CORS
import pandas as pd
import json
from datetime import datetime, timezone
import requests
import math
import threading
import time
import random
import io
import base64
import sqlite3
import hashlib
import hmac
import os  # add this
import base64  # already present later, fine
import hashlib
import hmac
from cryptography.fernet import Fernet  # NEW if you want real encryption
from dotenv import load_dotenv 


# Load variables from .env into os.environ
# Load variables from .env or app_config.bin
# load_dotenv() <-- replaced by config_loader
from backend import config_loader
config_loader.load_config()

app = Flask(__name__)

# ---- Dataset encryption (Fernet) ----
DATA_FERNET_KEY = os.environ.get("DATA_FERNET_KEY")
data_fernet = None

if DATA_FERNET_KEY:
    data_fernet = Fernet(DATA_FERNET_KEY.encode("utf-8"))
else:
    print("[warning] DATA_FERNET_KEY not set; encrypted datasets cannot be loaded")

ENCRYPTED_DIR = "encrypted"


def load_encrypted_csv(filename_enc: str, **read_csv_kwargs):
    """
    Decrypt a .csv.enc file and return a pandas DataFrame.
    """
    if not data_fernet:
        raise RuntimeError("DATA_FERNET_KEY not configured for encrypted CSV")
    path = os.path.join(ENCRYPTED_DIR, filename_enc)
    with open(path, "rb") as f:
        enc_data = f.read()
    raw = data_fernet.decrypt(enc_data)
    buf = io.BytesIO(raw)
    return pd.read_csv(buf, **read_csv_kwargs)


def load_encrypted_json(filename_enc: str):
    """
    Decrypt a .json.enc file and return a Python object (dict).
    """
    if not data_fernet:
        raise RuntimeError("DATA_FERNET_KEY not configured for encrypted JSON")
    path = os.path.join(ENCRYPTED_DIR, filename_enc)
    with open(path, "rb") as f:
        enc_data = f.read()
    raw = data_fernet.decrypt(enc_data)
    return json.loads(raw.decode("utf-8"))


# Load Flask secret from environment (do NOT hardcode in code)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is not set in environment")

CORS(app)


# ----------------------- USER DATABASE -----------------------
def init_db():
    """Create user table if not exists"""
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        );
    """)
    conn.commit()
    conn.close()

def init_activity_db():
    conn = sqlite3.connect("activities.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            city TEXT,
            station TEXT,
            intervention TEXT,
            efficiency REAL,
            base_co2 REAL,
            after_co2 REAL
        );
    """)

    conn.commit()
    conn.close()

# ----------------------- LOGIN / REGISTER ROUTES -----------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        conn = sqlite3.connect("users.db")
        cur = conn.cursor()

        try:
            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return "Username already exists!"

        conn.close()
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        conn = sqlite3.connect("users.db")
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = cur.fetchone()
        conn.close()

        if user:
            session["user"] = username
            return redirect(url_for("index"))
        else:
            error = "Invalid username or password!"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ----------- Config -----------
# Government CPCB feed with coordinates
CPCB_FEED_URL = "https://airquality.cpcb.gov.in/caaqms/iit_rss_feed_with_coordinates"

# ----------- OpenAQ fallback config -----------
OPENAQ_BASE_URL = "https://api.openaq.org/v3"
# We’ll limit how many locations we pull per call to avoid too big responses
OPENAQ_LIMIT = 1000
# Only India
OPENAQ_COUNTRY = "IN"

# OpenAQ v3 parameter IDs – 2 = PM2.5
OPENAQ_PM25_PARAM_ID = 2


# ---- OpenAQ cache & rate limiting ----
OPENAQ_CACHE_TTL = 600          # seconds (10 minutes) - reuse latest OpenAQ mapping
OPENAQ_MAX_CALLS_PER_MIN = 20   # be polite with OpenAQ API

# in-memory cache structure:
# { "ts": unix_time, "co2_map": {...}, "ts_map": {...} }
openaq_live_cache = {}

# rolling window of timestamps of calls
recent_openaq_calls = []



# How often to refresh live data in the background (seconds); set to None to disable
LIVE_REFRESH_INTERVAL_SECONDS = 300  # 5 minutes

# Max distance (meters) to map a CPCB station to one of your stations
CPCB_MATCH_RADIUS_M = 20000  # 20 km; tune later if needed

# Session RNG: seed once so generated env values are stable for a process lifetime
SESSION_SEED = int(time.time())
RNG = random.Random(SESSION_SEED)
# ---- Weather cache & rate limiting ----
WEATHER_CACHE_TTL = 900          # 15 minutes per city
WEATHER_MAX_CALLS_PER_MIN = 60   # OpenWeather free limit (approx)
WEATHER_CACHE_FILE = "weather_cache.json"

# { city_key: { "data": {...}, "ts": unix_time } }
weather_cache = {}
# list of timestamps (seconds) of recent outbound calls to OpenWeather
recent_weather_calls = []

def load_weather_cache_from_disk():
    """Load cached weather responses from local JSON file, if present."""
    global weather_cache
    try:
        with open(WEATHER_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # ensure structure is correct
        if isinstance(data, dict):
            weather_cache = data
            print(f"[weather] loaded {len(weather_cache)} entries from disk cache")
    except FileNotFoundError:
        print("[weather] no disk cache file yet")
    except Exception as e:
        print("[weather] failed to load disk cache:", e)


def save_weather_cache_to_disk():
    """Persist current weather_cache to local JSON file."""
    try:
        with open(WEATHER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(weather_cache, f)
    except Exception as e:
        print("[weather] failed to save disk cache:", e)

init_db()
init_activity_db();
load_weather_cache_from_disk()

# ------------ Data integrity (HMAC-SHA256) ------------

# In real deployment, load this from an environment variable (os.environ[...] instead of hardcoding)
# In production, store this only in env variables / secret manager
INTEGRITY_SECRET = os.environ.get("INTEGRITY_SECRET")
if not INTEGRITY_SECRET:
    raise RuntimeError("INTEGRITY_SECRET is not set in environment")
INTEGRITY_SECRET = INTEGRITY_SECRET.encode("utf-8")


def _compute_station_integrity_token(*, name, city, co2, ndvi, albedo, lulc):
    """
    Build a stable message from key station fields and compute HMAC-SHA256.
    This gives each station snapshot a tamper-evident token.
    """
    # Normalize numeric values so both sides agree
    co2_str = f"{float(co2):.2f}"
    ndvi_str = f"{float(ndvi):.3f}"
    albedo_str = f"{float(albedo):.3f}"
    lulc_str = str(lulc)
    city_str = (city or "")

    message = "|".join([name, city_str, co2_str, ndvi_str, albedo_str, lulc_str])
    return hmac.new(INTEGRITY_SECRET, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _get_station_city(station_name: str):
    """
    Helper to fetch city for a given station name from the global 'stations' list.
    """
    for s in stations:
        if s.get("name") == station_name:
            return s.get("city")
    return None

# ----------- Weather config -----------
# Using OpenWeather (API key required) for current weather
OPENWEATHER_API_BASE = "https://api.openweathermap.org/data/2.5/weather"

# Load from environment (no plaintext key in code)
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")
if not OPENWEATHER_API_KEY:
    print("[warning] OPENWEATHER_API_KEY not set; weather features will fail")

OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY")
if not OPENAQ_API_KEY:
    print("[warning] OPENAQ_API_KEY not set; OpenAQ fallback will not work")

# Generated env value ranges (realistic-ish)
GEN_NDVI_RANGE = (0.15, 0.45)   # low to moderate vegetation
GEN_ALBEDO_RANGE = (0.12, 0.20) # typical urban albedo range
# plausible LULC categories (these should match keys in your lulc_mapping where possible)
GEN_LULC_CHOICES = [
    "Urban", "Industrial", "Residential", "Campus", "Rural",
    "Mixed Urban", "Industrial/Residential", "Urban Vegetation",
    "Airport", "Sports Complex", "Government", "Mixed Forest"
]

# ----------- Weather config -----------
# Using Open-Meteo (no API key required) for simple current weather
WEATHER_API_BASE = "https://api.open-meteo.com/v1/forecast"


# ----------- Static files route -----------
@app.route("/")
def index():
    # If logged in, go straight to your Cesium map
    if "user" in session:
        # Get token from env
        cesium_token = os.environ.get("CESIUM_ION_TOKEN", "")
        if not cesium_token:
            print("[warning] CESIUM_ION_TOKEN not set!")
            
        return render_template("cesium_map.html", cesium_token=cesium_token)
    # Otherwise show the login page
    return redirect(url_for("login"))


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

# ----------- Load station locations -----------
station_df = load_encrypted_csv("station_loc.csv.enc", quotechar='"')
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
station_map = load_encrypted_json("station_id.json.enc")

# ----------- Load historic CO data -----------
station_day_df = load_encrypted_csv("station_day.csv.enc")
station_day_df['Date'] = pd.to_datetime(station_day_df['Date'])

# Track current CO2 baseline values (from your CSV)
station_co2 = {}

def _sanitize_co2(value, default=400.0, min_val=350.0, max_val=2000.0):
    """
    Ensure CO2 value is finite and within [min_val, max_val].
    If it is invalid, return default.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default

    if math.isnan(v) or math.isinf(v):
        return default

    # clamp
    v = max(min_val, min(v, max_val))
    return v


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
            raw_co = None if pd.isna(row['CO']) else row['CO'] * 1000
            co2_value = _sanitize_co2(raw_co, default=400.0, min_val=350.0, max_val=2000.0)
            station_co2[station_name] = co2_value

load_today_co2()

# ----------- Load station environmental factors (baseline stations only) -----------
env_df = load_encrypted_csv("station_env_factors.csv.enc")
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
    pm25 = (pm25 if pm25 is not None else 0)
    pm10 = (pm10 if pm10 is not None else 0)
    no2  = (no2  if no2  is not None else 0)
    co   = (co   if co   is not None else 0)

    factor = (pm25 * 1.8) + (pm10 * 0.4) + (no2 * 1.2) + (co * 50.0)
    est_raw = 400 + (factor / 20.0)

    # clamp & sanitize (never negative / insane)
    est = _sanitize_co2(est_raw, default=400.0, min_val=350.0, max_val=1200.0)
    return round(est, 2)


def _parse_float(val):
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

# ----------- Weather helpers -----------

def get_city_coords(city_name: str):
    """
    Return (lat, lon) for a city by averaging all station coordinates in that city.
    Uses:
      1) clean exact match (strip + lower)
      2) if none, substring match (e.g. 'delhi' in 'new delhi')
    """
    if not city_name:
        return None, None

    city_clean = city_name.strip().lower()

    # 1) exact match
    city_stations = [
        s for s in stations
        if isinstance(s.get("city"), str)
        and s["city"].strip().lower() == city_clean
    ]

    # 2) fallback: substring match (handles 'Delhi' vs 'New Delhi', 'Ahmedabad' vs 'Ahmedabad City')
    if not city_stations:
        city_stations = [
            s for s in stations
            if isinstance(s.get("city"), str)
            and city_clean in s["city"].strip().lower()
        ]

    if not city_stations:
        # helpful debug print
        print(
            "[get_city_coords] No stations for city:",
            repr(city_name),
            "Available examples:",
            sorted({(s.get('city') or '').strip() for s in stations})[:20]
        )
        return None, None

    lat = sum(s["lat"] for s in city_stations) / len(city_stations)
    lon = sum(s["lon"] for s in city_stations) / len(city_stations)
    return lat, lon


def get_month_factor(dt=None):
    """
    Very simple seasonal factor to illustrate 'monthly impact':
    - Winter (Nov–Jan): higher pollution build-up  (1.25x)
    - Shoulder (Oct, Feb): moderately higher       (1.15x)
    - Pre-monsoon (Apr–Jun): better dispersion     (0.90x)
    - Monsoon (Jul–Sep): slightly better           (0.95x)
    - Other months: neutral                        (1.00x)
    """
    if dt is None:
        dt = datetime.now()
    m = dt.month

    if m in (11, 12, 1):
        return 1.25, "winter"
    if m in (10, 2):
        return 1.15, "shoulder"
    if m in (4, 5, 6):
        return 0.90, "pre-monsoon"
    if m in (7, 8, 9):
        return 0.95, "monsoon"
    return 1.00, "neutral"


def fetch_weather_for_city(city_name: str):
    """
    Query OpenWeather for current weather near the city centroid.

    - Cache per city for WEATHER_CACHE_TTL seconds.
    - Enforce ~60 calls per minute to OpenWeather.
    - If rate limit would be exceeded, return cached data (if available).
    """
    global recent_weather_calls, weather_cache

    city_name = (city_name or "").strip()
    if not city_name:
        return None

    lat, lon = get_city_coords(city_name)
    if lat is None or lon is None:
        return None

    if not OPENWEATHER_API_KEY:
        print("[weather] OPENWEATHER_API_KEY is not set")
        return None

    city_key = city_name.lower()
    now = time.time()

    # ---- 1) Check per-city cache (TTL) ----
    cached = weather_cache.get(city_key)
    if cached and isinstance(cached, dict):
        ts = cached.get("ts")
        if isinstance(ts, (int, float)) and (now - ts) < WEATHER_CACHE_TTL:
            # fresh enough: no API call
            return cached.get("data")

    # ---- 2) Enforce global 60 calls/min limit ----
    recent_weather_calls = [
        t for t in recent_weather_calls
        if now - t < 60.0
    ]
    if len(recent_weather_calls) >= WEATHER_MAX_CALLS_PER_MIN:
        # We would exceed our own limit -> use cache if any, else abort
        if cached:
            print("[weather] using cached weather for", city_name, "(rate limit guard)")
            return cached.get("data")
        print("[weather] rate limit reached and no cache for", city_name)
        return None

    # ---- 3) Make real request to OpenWeather ----
    try:
        params = {
            "lat": lat,
            "lon": lon,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",  # temperature in °C, wind in m/s
        }
        resp = requests.get(OPENWEATHER_API_BASE, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        recent_weather_calls.append(now)
    except Exception as e:
        print("[weather] fetch failed for city", city_name, ":", e)
        # fall back to cache if available
        if cached:
            return cached.get("data")
        return None

    main = data.get("main", {})
    wind = data.get("wind", {})
    weather_list = data.get("weather", [])

    temp = main.get("temp")                  # °C
    windspeed = wind.get("speed")            # m/s
    winddir = wind.get("deg")                # degrees
    weather_code = weather_list[0]["id"] if weather_list else None

    # Convert m/s to km/h for consistency with earlier logic
    if isinstance(windspeed, (int, float)):
        windspeed_kmh = windspeed * 3.6
    else:
        windspeed_kmh = None

    # Simple dispersion heuristic based on wind speed
    dispersion = "Unknown"
    if isinstance(windspeed_kmh, (int, float)):
        if windspeed_kmh < 2:
            dispersion = "Poor dispersion (very low wind)"
        elif windspeed_kmh < 5:
            dispersion = "Moderate dispersion"
        else:
            dispersion = "Good dispersion (high wind)"

    month_factor, season_label = get_month_factor()

    info = {
        "city": city_name,
        "latitude": lat,
        "longitude": lon,
        "temperature": temp,
        "windspeed": windspeed_kmh,
        "winddirection": winddir,
        "weather_code": weather_code,
        "dispersion_label": dispersion,
        "month_factor": month_factor,
        "season": season_label,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # ---- 4) Save to in-memory + disk cache ----
    weather_cache[city_key] = {
        "data": info,
        "ts": now,
    }
    save_weather_cache_to_disk()

    return info

# ----------- Gaussian plume-based dispersion (demo) -----------

def _latlon_to_local_xy_m(lat, lon, lat0, lon0):
    """
    Approximate local tangent-plane coordinates (x east, y north) in meters,
    relative to (lat0, lon0).
    """
    R = 6371000.0  # Earth radius (m)
    dlat = math.radians(lat - lat0)
    dlon = math.radians(lon - lon0)
    # use mean latitude to scale longitude
    lat_mean = math.radians((lat + lat0) / 2.0)
    x = R * dlon * math.cos(lat_mean)   # east-west
    y = R * dlat                        # north-south
    return x, y


def _gaussian_plume_2d(Q, x_downwind, y_cross, u):
    """
    Very simplified ground-level 2D Gaussian plume.

    Q: emission proxy (arbitrary units, proportional to source strength)
    x_downwind: distance along wind direction (m)
    y_cross: crosswind distance (m)
    u: wind speed (m/s)

    This is not dimensionally perfect, but good for a visual dispersion demo.
    """
    if x_downwind <= 0 or u <= 0:
        # upwind or no wind -> no contribution
        return 0.0

    # Simple stability: spreads grow with distance
    # (you can tune these multipliers to make plume fatter/thinner)
    sigma_y = max(20.0, 0.25 * x_downwind)
    sigma_z = max(15.0, 0.15 * x_downwind)

    # Basic Gaussian in y; ignore vertical term and stack height for now
    try:
        term_y = math.exp(-0.5 * (y_cross / sigma_y) ** 2)
    except OverflowError:
        term_y = 0.0

    # Classic denominator Q / (2π u σy σz), but we treat as relative
    denom = 2.0 * math.pi * u * sigma_y * sigma_z
    if denom <= 0:
        return 0.0

    return Q * term_y / denom


def compute_plume_for_city(city_name: str, use_live: bool = True, grid_size: int = 25):
    """
    Build a simple 2D Gaussian-plume-based CO2 field over the selected city.

    - Picks stations belonging to this city (exact + substring match).
    - Uses either live CO2 (station_co2_live) or baseline (station_co2) as source strength.
    - Uses wind from fetch_weather_for_city(city_name) if available.
    - Returns a list of {lat, lon, co2} grid cells.
    """
    city_name = (city_name or "").strip()
    if not city_name:
        return []

    # 1) Select stations in this city (same matching logic as get_city_coords)
    city_clean = city_name.lower()
    city_stations = [
        s for s in stations
        if isinstance(s.get("city"), str)
        and (
            s["city"].strip().lower() == city_clean
            or city_clean in s["city"].strip().lower()
        )
    ]

    if not city_stations:
        print("[plume] no stations found for city:", repr(city_name))
        return []

    # 2) Get wind info (direction and speed)
    w = fetch_weather_for_city(city_name)
    if w:
        wind_dir_deg = w.get("winddirection")  # degrees FROM which wind is blowing
        wind_speed_kmh = w.get("windspeed")
    else:
        wind_dir_deg = None
        wind_speed_kmh = None

    # Default values if missing
    if not isinstance(wind_dir_deg, (int, float)):
        wind_dir_deg = 0.0  # "from north" -> plume goes south
    if not isinstance(wind_speed_kmh, (int, float)) or wind_speed_kmh <= 0:
        wind_speed_kmh = 10.0  # 10 km/h ~ light breeze

    wind_speed_ms = wind_speed_kmh / 3.6

    # Meteorological convention: direction is where wind COMES FROM.
    # Plume travels TO direction + 180°.
    plume_dir_deg = (wind_dir_deg + 180.0) % 360.0
    plume_dir_rad = math.radians(plume_dir_deg)

    # 3) City bounding box + grid definition
    lats = [s["lat"] for s in city_stations]
    lons = [s["lon"] for s in city_stations]

    lat_min = min(lats) - 0.02
    lat_max = max(lats) + 0.02
    lon_min = min(lons) - 0.02
    lon_max = max(lons) + 0.02

    # Center for local coordinate transform
    lat0 = sum(lats) / len(lats)
    lon0 = sum(lons) / len(lons)

    # Prepare station sources: coordinates + emission proxy Q
    sources = []
    for s in city_stations:
        name = s["name"]
        if use_live and name in station_co2_live:
            co2_val = station_co2_live[name]
        else:
            co2_val = station_co2.get(name)

        if co2_val is None or pd.isna(co2_val):
            continue

        # emission proxy: only the "excess" over 400 ppm contributes
        excess = max(float(co2_val) - 400.0, 0.0)
        Q = max(excess, 10.0)  # avoid zero; tune later if needed

        x_s, y_s = _latlon_to_local_xy_m(s["lat"], s["lon"], lat0, lon0)
        sources.append({
            "name": name,
            "x": x_s,
            "y": y_s,
            "Q": Q,
        })

    if not sources:
        print("[plume] no valid CO2 sources for city:", city_name)
        return []

    # 4) Build grid and accumulate contributions
    grid = []
    raw_vals = []

    for i in range(grid_size):
        frac_lat = i / (grid_size - 1) if grid_size > 1 else 0.5
        lat_g = lat_min + frac_lat * (lat_max - lat_min)

        for j in range(grid_size):
            frac_lon = j / (grid_size - 1) if grid_size > 1 else 0.5
            lon_g = lon_min + frac_lon * (lon_max - lon_min)

            x_g, y_g = _latlon_to_local_xy_m(lat_g, lon_g, lat0, lon0)

            # Rotate to plume coordinate system: x' along plume direction, y' crosswind
            # x_downwind = x_g * sin(dir) + y_g * cos(dir)
            # y_cross    = x_g * cos(dir) - y_g * sin(dir)
            x_down = x_g * math.sin(plume_dir_rad) + y_g * math.cos(plume_dir_rad)
            y_cross = x_g * math.cos(plume_dir_rad) - y_g * math.sin(plume_dir_rad)

            C_raw = 0.0
            for src in sources:
                dx = x_down - (src["x"] * math.sin(plume_dir_rad) + src["y"] * math.cos(plume_dir_rad))
                dy = y_cross - (src["x"] * math.cos(plume_dir_rad) - src["y"] * math.sin(plume_dir_rad))
                C_raw += _gaussian_plume_2d(src["Q"], dx, dy, wind_speed_ms)

            raw_vals.append(C_raw)
            grid.append({
                "lat": lat_g,
                "lon": lon_g,
                "raw": C_raw
            })

    # 5) Normalize raw field to something like 400–1000 ppm for visualization
    if not raw_vals:
        return []

    min_raw = min(raw_vals)
    max_raw = max(raw_vals)

    # Avoid division by zero
    if max_raw <= 0:
        # fallback: flat field 400 ppm
        for cell in grid:
            cell["co2"] = 400.0
            cell.pop("raw", None)
        return grid

    norm_grid = []
    for cell in grid:
        # Normalize 0–1
        v = max(cell["raw"], 0.0)
        frac = v / max_raw
        # Map to 400–1000 ppm
        co2_val = 400.0 + frac * 600.0
        norm_grid.append({
            "lat": cell["lat"],
            "lon": cell["lon"],
            "co2": round(co2_val, 1)
        })

    return norm_grid

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

def refresh_live_from_openaq(timeout=20):
    """
    Fallback: refresh live CO2 estimates from OpenAQ v3.

    v2 `/measurements` is retired; in v3 we use the "Latest" resource:
      - GET /v3/parameters/{parameters_id}/latest

    Here we:
      1. Call /v3/parameters/2/latest (PM2.5) with a limit.
      2. For each result, map the coordinates to the nearest of *our* stations.
      3. Use `estimate_co2_from_pollutants(pm25, pm10, no2, co)` with only PM2.5.
      4. Store results in `station_co2_live` and `station_live_ts`.
      5. Cache for OPENAQ_CACHE_TTL seconds to avoid hammering the API.
    """
    global station_co2_live, station_live_ts
    global openaq_live_cache, recent_openaq_calls

    if not OPENAQ_API_KEY:
        print("[live][OpenAQ v3] OPENAQ_API_KEY is not set; cannot call v3 API")
        return False

    now = time.time()
    print("[live][OpenAQ v3] refresh_live_from_openaq() called")

    # ---- 1) Reuse cache if still fresh ----
    try:
        if openaq_live_cache and isinstance(openaq_live_cache, dict):
            ts_cached = openaq_live_cache.get("ts")
            if isinstance(ts_cached, (int, float)) and (now - ts_cached) < OPENAQ_CACHE_TTL:
                cached_co2 = openaq_live_cache.get("co2_map") or {}
                cached_ts  = openaq_live_cache.get("ts_map") or {}

                station_co2_live = dict(cached_co2)
                station_live_ts  = dict(cached_ts)

                print(f"[live][OpenAQ v3] using cached mapping (stations={len(cached_co2)})")
                return True
    except Exception as e:
        print("[live][OpenAQ v3] cache reuse error:", e)

    # ---- 2) Rate-limit guard ----
    try:
        recent_openaq_calls = [t for t in recent_openaq_calls if now - t < 60.0]
    except Exception:
        recent_openaq_calls = []

    if len(recent_openaq_calls) >= OPENAQ_MAX_CALLS_PER_MIN:
        if openaq_live_cache:
            cached_co2 = openaq_live_cache.get("co2_map") or {}
            cached_ts  = openaq_live_cache.get("ts_map") or {}

            station_co2_live = dict(cached_co2)
            station_live_ts  = dict(cached_ts)

            print("[live][OpenAQ v3] rate limit guard – reusing cached mapping")
            return True

        print("[live][OpenAQ v3] rate limit reached and no cache; skipping call")
        return False

    # ---- 3) Call v3 "latest" for PM2.5 ----
    # Docs: https://api.openaq.org/v3/parameters/2/latest
    # Example in docs uses parameter 2 (PM2.5) with ?limit=1000
    OPENAQ_V3_LATEST_PM25_URL = f"{OPENAQ_BASE_URL}/parameters/{OPENAQ_PM25_PARAM_ID}/latest"

    params = {
        "limit": OPENAQ_LIMIT,   # keep your existing limit (1000)
        # NOTE: parameters/latest doesn't document a country filter in examples,
        # so we grab global PM2.5 and then map by distance to your Indian stations.
        # If OpenAQ later documents countries_id/country filter here, you can add it.
    }

    headers = {
        "X-API-Key": OPENAQ_API_KEY
    }

    try:
        print(f"[live][OpenAQ v3] requesting {OPENAQ_V3_LATEST_PM25_URL} with params={params}")
        resp = requests.get(OPENAQ_V3_LATEST_PM25_URL, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        recent_openaq_calls.append(now)
    except Exception as e:
        print("[live][OpenAQ v3] fetch failed:", e)

        # fallback: if we have previous cache, still reuse it
        if openaq_live_cache:
            cached_co2 = openaq_live_cache.get("co2_map") or {}
            cached_ts  = openaq_live_cache.get("ts_map") or {}

            station_co2_live = dict(cached_co2)
            station_live_ts  = dict(cached_ts)

            print("[live][OpenAQ v3] using stale cache due to fetch error")
            return True

        return False

    # ---- 4) Parse Latest results ----
    results = payload.get("results", [])
    if not isinstance(results, list) or not results:
        print("[live][OpenAQ v3] no results in payload or wrong structure; keys:", list(payload.keys()))
        return False

    new_live = {}
    new_ts = {}
    mapped_count = 0
    total_points = 0

    for r in results:
        total_points += 1

        coords = r.get("coordinates") or {}
        lat = coords.get("latitude")
        lon = coords.get("longitude")

        # Some clients might represent coords as [lon, lat] – handle list/tuple too
        if lat is None or lon is None:
            if isinstance(coords, (list, tuple)) and len(coords) == 2:
                # assume [longitude, latitude] as common GeoJSON-like order
                lon, lat = coords[0], coords[1]

        if lat is None or lon is None:
            continue

        try:
            lat = float(lat)
            lon = float(lon)
        except Exception:
            continue

        val = r.get("value")
        try:
            pm25_val = float(val) if val is not None else None
        except Exception:
            pm25_val = None

        if pm25_val is None:
            continue

        # Map this latest PM2.5 point to the nearest of *our* stations
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

        # respect your CPCB_MATCH_RADIUS_M (20km) for mapping
        if nearest_d > CPCB_MATCH_RADIUS_M:
            continue

        our_name = nearest["name"]

        # We only have PM2.5 here; others are None – still fine for heuristic
        est_co2 = estimate_co2_from_pollutants(pm25_val, None, None, None)

        # If we already saw this station in this run, just overwrite with last one
        new_live[our_name] = est_co2

        # Latest resource has datetime.{utc,local}
        dt_info = r.get("datetime") or {}
        ts_val = dt_info.get("utc") or dt_info.get("local") or datetime.now(timezone.utc).isoformat()

        new_ts[our_name] = ts_val
        mapped_count += 1

    if not new_live:
        print("[live][OpenAQ v3] no stations mapped to your network (after processing)")
        return False

    # ---- 5) Save mapping + timestamp to cache ----
    openaq_live_cache = {
        "ts": now,
        "co2_map": new_live,
        "ts_map": new_ts,
    }

    station_co2_live = new_live
    station_live_ts = new_ts

    print(f"[live][OpenAQ v3] mapped {mapped_count} of {total_points} PM2.5 points to your stations")
    return True


# Background refresher
def _live_refresh_loop():
    while True:
        try:
            ok = refresh_live_from_cpcb()
            if not ok:
                print("[live] CPCB background refresh failed; trying OpenAQ fallback")
                refresh_live_from_openaq()
        except Exception as e:
            print("[live][CPCB] refresh error:", e)
            # try OpenAQ as last resort
            try:
                refresh_live_from_openaq()
            except Exception as e2:
                print("[live][OpenAQ] refresh error:", e2)

        if not LIVE_REFRESH_INTERVAL_SECONDS:
            break
        time.sleep(LIVE_REFRESH_INTERVAL_SECONDS)

if LIVE_REFRESH_INTERVAL_SECONDS:
    t = threading.Thread(target=_live_refresh_loop, daemon=True)
    t.start()

# ----------- API endpoints -----------
@app.route("/refresh_live", methods=["GET"])
def refresh_live_endpoint():
    """
    Manual trigger to refresh live estimates.

    Tries CPCB first; if that fails, falls back to OpenAQ.
    """
    ok = refresh_live_from_cpcb()
    if not ok:
        print("[live] CPCB refresh failed; trying OpenAQ fallback")
        ok = refresh_live_from_openaq()

    return jsonify({"success": bool(ok)})


@app.route("/get_weather", methods=["GET"])
def get_weather():
    city = (request.args.get("city") or "").strip()
    if not city:
        return jsonify({"success": False, "error": "city query parameter is required"}), 400

    info = fetch_weather_for_city(city)
    if not info:
        return jsonify({"success": False, "error": f"No weather data for city '{city}'"}), 404

    info["success"] = True
    return jsonify(info)

@app.route("/get_dispersion", methods=["GET"])
def get_dispersion():
    """
    Return a Gaussian-plume-based CO2 field for a given city.

    Query params:
      - city (required)
      - use_live = 1/0 (optional, default 1 → prefer live CO2 if available)
    """
    city = (request.args.get("city") or "").strip()
    if not city:
        return jsonify({"success": False, "error": "city query parameter is required"}), 400

    use_live_param = request.args.get("use_live", "1")
    use_live = use_live_param not in ("0", "false", "False")

    grid = compute_plume_for_city(city, use_live=use_live, grid_size=25)

    if not grid:
        return jsonify({"success": False, "error": f"No dispersion field for city '{city}'"}), 404

    return jsonify({
        "success": True,
        "city": city,
        "use_live": use_live,
        "points": grid
    })


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
            info["co2"] = _sanitize_co2(baseline_co2)

        if live_est is not None:
            info["co2_estimated"] = _sanitize_co2(live_est)
            info["live_ts"] = live_ts

        # env factors + integrity_token logic stays the same...


        # Always attach env factors (real or synthetic)
        env_data = get_or_generate_env_for_station(station_name)
        if env_data:
            info.update({
                "ndvi": env_data["ndvi"],
                "albedo": env_data["albedo"],
                "lulc": env_data["lulc"]
            })

        # ---- NEW: integrity_token for this station snapshot ----
        # Use the same "auto" logic as interventions: prefer baseline CO2, else live_est
        effective_co2 = None
        if baseline_co2 is not None and not pd.isna(baseline_co2):
            effective_co2 = float(baseline_co2)
        elif live_est is not None:
            effective_co2 = float(live_est)

        if effective_co2 is not None and env_data is not None:
            token = _compute_station_integrity_token(
                name=station_name,
                city=s["city"],
                co2=effective_co2,
                ndvi=env_data["ndvi"],
                albedo=env_data["albedo"],
                lulc=env_data["lulc"],
            )
            info["integrity_token"] = token
        data.append(info)

    return jsonify(data)


def intervention_effect(base_co2, ndvi, albedo, lulc_factor, user_efficiency=None, weather=None):
    """
    Compute CO2 reduction based on NDVI, Albedo, LULC factor and optional
    user-selected efficiency (0–50%) plus optional weather scenario.

    Weather can be a dict with keys like:
      - temperature (°C)
      - windspeed_ms
      - mixing_height (m)
      - stagnation_risk ("High"/"Elevated"/"Moderate"/"Low")
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

    # --- Weather modulation (play mode) ---
    if weather and isinstance(weather, dict):
        stag = (weather.get("stagnation_risk") or "").lower()
        wind = weather.get("windspeed_ms") or weather.get("windspeed")
        mixing = weather.get("mixing_height")

        # Stagnation: in high-stagnation months an intervention
        # can have more visible impact (higher local buildup).
        if "high" in stag:
            reduction_ratio *= 1.20
        elif "elevated" in stag:
            reduction_ratio *= 1.10
        elif "low" in stag:
            reduction_ratio *= 0.90

        # Very low wind => a bit more impact, high wind => slightly less
        try:
            if wind is not None:
                w = float(wind)
                if w < 1.5:
                    reduction_ratio *= 1.05
                elif w > 5.0:
                    reduction_ratio *= 0.90
        except (ValueError, TypeError):
            pass

        # Low mixing height => more accumulation => more visible reduction
        try:
            if mixing is not None:
                mh = float(mixing)
                if mh < 500:
                    reduction_ratio *= 1.05
                elif mh > 900:
                    reduction_ratio *= 0.92
        except (ValueError, TypeError):
            pass

    # Safety clamp
    reduction_ratio = max(0.0, min(reduction_ratio, 0.5))

    reduced_co2 = base_co2 * (1 - reduction_ratio)
    return round(reduced_co2, 2)

@app.route("/apply_intervention", methods=["POST"])
def apply_intervention():
    """
    Apply an intervention to either:
      - baseline CO2 (station_co2), or
      - live CO2 estimate (station_co2_live),
    depending on what the frontend sends in `target`.
    target can be: "baseline", "live", or omitted (auto → baseline then live).
    """
    data = request.get_json()
    play_snapshot = data.get("play_snapshot") or {}
    station_name = data.get("station")
    station_id = data.get("station_id")  # optional
    efficiency = data.get("efficiency")  # 0–50%
    target = data.get("target") or data.get("applied_to") or "auto"

    # NEW: also read city + intervention name from payload (sent by frontend)
    station_city = data.get("city") or _get_station_city(station_name)
    method_name = data.get("intervention")

    sent_token = data.get("integrity_token")

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
            base_value = station_co2_live[station_name]
            applied_to = "live"

    elif target == "live":
        if station_name in station_co2_live:
            base_value = station_co2_live[station_name]
            applied_to = "live"
        elif station_name in station_co2:
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
            "error": "No CO2 value found for this station (neither baseline nor live)."
        }), 404

    # Hard clamp base_value
    base_value = _sanitize_co2(base_value, default=400.0, min_val=350.0, max_val=2000.0)

    # ---------- Integrity verification (HMAC-SHA256) ----------
    if sent_token is not None:
        # if city wasn't in payload, we already derived station_city above
        expected_token = _compute_station_integrity_token(
            name=station_name,
            city=station_city,
            co2=base_value,
            ndvi=ndvi,
            albedo=albedo,
            lulc=lulc_str,
        )

        if not hmac.compare_digest(sent_token, expected_token):
            return jsonify({
                "success": False,
                "error": "Integrity check failed for station payload."
            }), 400

    # --- Compute new CO2 after intervention ---
    reduced_co2 = intervention_effect(base_value, ndvi, albedo, lulc_factor, efficiency)
    reduced_co2 = _sanitize_co2(reduced_co2, default=base_value, min_val=300.0, max_val=2000.0)

    # --- Persist in the correct in-memory store ---
    if applied_to == "baseline":
        station_co2[station_name] = reduced_co2
    else:  # "live"
        station_co2_live[station_name] = reduced_co2

    # --- NEW: Log this action into activities table ---
    try:
        username = session.get("user") or "anonymous"

        conn = sqlite3.connect("activities.db")
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO activities
              (user_id, city, station, intervention, efficiency, base_co2, after_co2)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                station_city,
                station_name,
                method_name,
                float(efficiency) if efficiency is not None else None,
                float(base_value),
                float(reduced_co2),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # do not break main flow if logging fails
        print("[activity] failed to insert row:", e)

    # NEW: compute updated integrity token for the new CO2 value (optional but useful)
    new_token = None
    try:
        new_token = _compute_station_integrity_token(
            name=station_name,
            city=station_city,
            co2=reduced_co2,
            ndvi=ndvi,
            albedo=albedo,
            lulc=lulc_str,
        )
    except Exception:
        new_token = None

    
    return jsonify({
        "success": True,
        "station": station_name,
        "applied_to": applied_to,
        "base_co2": base_value,
        "co2_after": reduced_co2,
        "ndvi": ndvi,
        "albedo": albedo,
        "lulc": lulc_factor,
        "integrity_token": new_token
    })

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import base64, os, time
from datetime import datetime

def _fmt_float(x, nd=1, default="–"):
    try:
        if x is None:
            return default
        v = float(x)
        return f"{v:.{nd}f}"
    except (TypeError, ValueError):
        return default

def _describe_env(env):
    """
    Build a short natural-language description of NDVI / albedo / LULC.
    """
    if not isinstance(env, dict):
        return "No local land-use metadata was attached to this intervention."

    ndvi = env.get("ndvi")
    albedo = env.get("albedo")
    lulc = env.get("lulc")

    bits = []

    # NDVI text
    if isinstance(ndvi, (int, float)):
        if ndvi < 0.25:
            bits.append(f"Very low NDVI {_fmt_float(ndvi, 2)} → hard, built-up surface with limited vegetation.")
        elif ndvi < 0.35:
            bits.append(f"Moderate NDVI {_fmt_float(ndvi, 2)} → mixed built-up and scattered greenery.")
        elif ndvi < 0.45:
            bits.append(f"Healthy NDVI {_fmt_float(ndvi, 2)} → significant vegetation already present.")
        else:
            bits.append(f"High NDVI {_fmt_float(ndvi, 2)} → strongly vegetated surroundings.")
    else:
        bits.append("NDVI not available.")

    # Albedo text
    if isinstance(albedo, (int, float)):
        if albedo < 0.14:
            bits.append(f"Low albedo {_fmt_float(albedo, 2)} → darker surfaces that absorb more heat and can trap pollution.")
        elif albedo < 0.20:
            bits.append(f"Medium albedo {_fmt_float(albedo, 2)} → typical urban surface reflectance.")
        else:
            bits.append(f"High albedo {_fmt_float(albedo, 2)} → relatively reflective surfaces that warm less and aid dispersion.")
    else:
        bits.append("Surface albedo not available.")

    # LULC text
    if lulc:
        bits.append(f"LULC tag: <b>{lulc}</b>, used as a proxy for local emission mix (transport / industry / power).")
    else:
        bits.append("No LULC category recorded for this station.")

    return " ".join(bits)

def _describe_weather(weather):
    """
    Short explanation of weather impact from play-mode JSON.
    """
    if not isinstance(weather, dict):
        return "No explicit weather scenario was attached to this intervention."

    temp = weather.get("temp")
    wind = weather.get("wind_ms")
    mixing = weather.get("mixing_height")
    stag = (weather.get("stagnation") or "").strip()

    parts = []

    if isinstance(temp, (int, float)):
        parts.append(f"Temperature scenario: <b>{_fmt_float(temp, 1)} °C</b>.")
    if isinstance(wind, (int, float)):
        wind_note = ""
        if wind < 1.5:
            wind_note = "very calm winds → poor dispersion, higher local build-up."
        elif wind < 4.0:
            wind_note = "light winds → moderate dispersion."
        else:
            wind_note = "stronger winds → better dispersion, plume spreads out faster."
        parts.append(f"Wind speed: <b>{_fmt_float(wind, 2)} m/s</b>, {wind_note}")
    if isinstance(mixing, (int, float)):
        mix_note = ""
        if mixing < 500:
            mix_note = "Shallow mixing height → pollutants trapped closer to ground."
        elif mixing > 900:
            mix_note = "High mixing height → column of air is well-mixed."
        else:
            mix_note = "Moderate mixing height → some build-up but still partial dilution."
        parts.append(f"Mixing height: <b>{int(mixing)} m</b>. {mix_note}")
    if stag:
        parts.append(f"Stagnation risk label: <b>{stag}</b>.")

    if not parts:
        return "Weather scenario fields were present but incomplete."

    return " ".join(parts)

def _explain_reduction(entry):
    """
    Build an explainable narrative for a single intervention:
    what drove the CO2 reduction vs baseline.
    """
    base = entry.get("base_co2")
    after = entry.get("co2_after")
    drop = entry.get("reduction")
    eff = entry.get("efficiency")
    method = entry.get("method") or "Intervention"
    env = entry.get("env") or {}
    weather = entry.get("weather") or {}
    play_mode = bool(entry.get("play_mode"))

    base_txt = _fmt_float(base, 1, default="–")
    after_txt = _fmt_float(after, 1, default="–")
    drop_txt = _fmt_float(drop, 1, default="0.0")
    eff_txt = _fmt_float(eff, 0, default="–")

    # High-level driver classification (pseudo feature-importance, but readable)
    driver_bits = []

    # Planner / policy lever
    if isinstance(eff, (int, float, complex)) or isinstance(eff, (int, float)):
        if eff >= 40:
            driver_bits.append("A <b>high efficiency setting</b> (planner choice) is the main driver of the drop.")
        elif eff >= 25:
            driver_bits.append("A <b>moderate efficiency setting</b> contributes meaningfully to the drop.")
        else:
            driver_bits.append("The efficiency setting is relatively conservative; local context and weather matter more here.")
    else:
        driver_bits.append("The intervention efficiency parameter was not clearly recorded.")

    # Built form / LULC driver
    ndvi = env.get("ndvi")
    lulc = env.get("lulc")
    if isinstance(ndvi, (int, float)):
        if ndvi < 0.25:
            driver_bits.append("Because NDVI is low, the area is likely hard, built-up → extra roadside capture brings clear marginal benefit.")
        elif ndvi > 0.40:
            driver_bits.append("NDVI is already high; the marginal benefit is more about <b>shaping the plume</b> than simply adding greenery.")
    if lulc:
        if "Industrial" in str(lulc):
            driver_bits.append("The LULC tag includes <b>Industrial</b>; reductions here are leveraged across process + stack emissions.")
        elif "Urban" in str(lulc):
            driver_bits.append("The LULC tag is <b>Urban / Mixed Urban</b>, so transport and dense traffic are key beneficiaries.")
        elif "Residential" in str(lulc):
            driver_bits.append("The LULC tag is <b>Residential</b>, so the benefit is more distributed across households and streets.")

    # Weather driver (only if play-mode scenario present)
    if weather and any(weather.values()):
        wind = weather.get("wind_ms")
        mixing = weather.get("mixing_height")
        stag = (weather.get("stagnation") or "").lower()

        if stag.startswith("high") or (isinstance(wind, (int, float)) and wind < 1.5):
            driver_bits.append(
                "The scenario assumes <b>stagnant or low-wind conditions</b>, "
                "so local capture has a stronger visible effect on concentrations."
            )
        elif isinstance(wind, (int, float)) and wind > 5.0:
            driver_bits.append(
                "With <b>higher winds</b>, the local concentration drop is smaller but the benefit is spread over a bigger footprint."
            )
        if isinstance(mixing, (int, float)) and mixing < 500:
            driver_bits.append(
                "A shallow mixing layer amplifies both the problem and the visible impact of interventions near ground level."
            )

    # Fallback if nothing got added
    if not driver_bits:
        driver_bits.append("The model combines CO2 level, local land-use, and efficiency into a single reduction factor for this station.")

    driver_text = " ".join(driver_bits)

    return (
        f"<b>{method}</b> reduced CO2 from <b>{base_txt} ppm</b> to "
        f"<b>{after_txt} ppm</b> (drop of <b>{drop_txt} ppm</b>) "
        f"with an efficiency setting of <b>{eff_txt}%</b>. "
        + driver_text
    )

@app.route("/generate_report", methods=["POST"])
def generate_report():
    payload = request.get_json(force=True) or {}

    log   = payload.get("log", []) or []
    charts = payload.get("charts", []) or []
    kpis  = payload.get("kpis", {}) or {}
    scope = (payload.get("scope") or "session").upper()
    focus_city = payload.get("city") or "All cities"

    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    file_name = f"report_{int(time.time())}.pdf"
    doc = SimpleDocTemplate(
        file_name,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    # Slightly denser body text
    styles.add(ParagraphStyle(
        name="Body",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=11.5,
    ))
    styles.add(ParagraphStyle(
        name="Small",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=10,
        textColor=colors.grey
    ))

    title_style = styles["Title"]
    h2 = ParagraphStyle(
        "Heading2Tight",
        parent=styles["Heading2"],
        spaceBefore=6,
        spaceAfter=4
    )
    h3 = ParagraphStyle(
        "Heading3Tight",
        parent=styles["Heading3"],
        spaceBefore=4,
        spaceAfter=2,
        fontSize=10.5
    )

    story = []

    # ========= PAGE 1: Title + ABSTRACT + executive view =========
    story.append(Paragraph("Digital Twin CO2 Planning Report", title_style))
    story.append(Paragraph(
        f"Generated on {ts_str} · Scope: {scope} · Focus city: {focus_city}",
        styles["Small"]
    ))
    story.append(Spacer(1, 4))

    # ---- NEW: Abstract Summary at the very top ----
    total_int = kpis.get("totalInterventions", len(log))
    total_drop = kpis.get("totalDrop", 0)
    best_drop = kpis.get("bestDrop", 0)
    best_loc  = kpis.get("bestLocation")

    story.append(Paragraph("Summary", h2))

    abstract_lines = []
    abstract_lines.append(
        f"In this run, the India CO2 Digital Twin simulated "
        f"<b>{_fmt_float(total_int, 0, default='0')}</b> intervention(s), "
        f"with an estimated cumulative reduction of "
        f"<b>{_fmt_float(total_drop, 1)} ppm</b> against the selected baseline."
    )
    if best_loc:
        abstract_lines.append(
            f"The single highest-impact intervention achieved a drop of "
            f"<b>{_fmt_float(best_drop, 1)} ppm</b> at <b>{best_loc}</b>."
        )
    abstract_lines.append(
        "This abstract provides a quick planning view before diving into the per-station "
        "explanations, so decision-makers can immediately see overall impact and where "
        "the strongest reductions occurred."
    )

    story.append(Paragraph(" ".join(abstract_lines), styles["Body"]))
    story.append(Spacer(1, 6))

    # ---- Existing intro (kept as is, just below Abstract) ----
    story.append(Paragraph(
        "This report explains how the India CO2 Digital Twin evaluated interventions, "
        "showing both the <b>numerical impact</b> on CO2 and the <b>reasons behind each change</b>. "
        "Instead of treating CO2 as a black box, the report surfaces the land-use, vegetation, "
        "surface properties, and weather conditions that shaped each result.",
        styles["Body"]
    ))
    story.append(Spacer(1, 6))

    # --- KPIs block (compact) ---
    story.append(Paragraph("Session-Level Key Performance Indicators", h2))

    kpi_lines = [
        f"<b>Total interventions run:</b> {_fmt_float(total_int, 0, default='0')}",
        f"<b>Cumulative reduction vs. baseline:</b> {_fmt_float(total_drop, 1)} ppm",
    ]
    if best_loc:
        kpi_lines.append(
            f"<b>Best single intervention:</b> {_fmt_float(best_drop, 1)} ppm at {best_loc}"
        )

    for line in kpi_lines:
        story.append(Paragraph(line, styles["Body"]))

    story.append(Spacer(1, 6))

    # --- Short narrative overview ---
    story.append(Paragraph("Scenario Overview", h2))
    story.append(Paragraph(
        "The digital twin starts from either <b>baseline CO2 snapshots</b> (historic days) "
        "or <b>live estimates</b> built from CPCB / OpenAQ pollutant data. Each monitoring "
        "station is tagged with simplified land-use (LULC), vegetation index (NDVI), and "
        "surface albedo. Interventions – roadside capture units, biofilters, vertical gardens – "
        "are then simulated as local reductions, modulated by these factors and (optionally) "
        "weather scenarios from Play Mode.",
        styles["Body"]
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "The tables and explanations on the next pages summarise how CO2 values changed "
        "after interventions, and the text explains why the model judged some locations to "
        "be higher impact than others.",
        styles["Body"]
    ))

    # ========= PAGE 2: Model & XAI explanation =========
    story.append(PageBreak())
    story.append(Paragraph("How the Model Thinks About CO2 (Explainable Summary)", h2))

    story.append(Paragraph(
        "Although the underlying calculations are numerical, they follow a transparent structure:"
        "<br/><br/>"
        "1. <b>CO2 level at each station</b> is derived either from historic CO measurements "
        "or from a pollutant mix (PM2.₅, PM₁₀, NO2, CO) using a heuristic proxy. "
        "This creates a consistent ppm-like value for mapping and comparison."
        "<br/>"
        "2. <b>Local environment</b> is captured via NDVI (vegetation density), albedo "
        "(surface reflectivity), and LULC (urban / industrial / residential / campus, etc.). "
        "These determine how much additional greenery or capture infrastructure can still "
        "change local concentrations."
        "<br/>"
        "3. <b>Planner efficiency</b> encodes how aggressively an intervention is assumed to operate "
        "(0–50%). Higher settings mean stronger capture or higher utilisation of the installed asset."
        "<br/>"
        "4. <b>Weather and seasonal play-mode inputs</b> – temperature, wind speed, mixing height, "
        "and stagnation labels – describe how easily pollutants build up or disperse during "
        "a given month or scenario."
        "<br/>"
        "5. These ingredients are combined into a <b>reduction ratio</b>, which is then applied "
        "to the starting CO2 value at that station. The report below decomposes this effect "
        "into human-readable drivers for each intervention.",
        styles["Body"]
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Opening the CO2 ‘Black Box’", h3))
    story.append(Paragraph(
        "In many dashboards CO2 curves appear opaque – numbers go up or down, but the reasons "
        "remain hidden. Here, for every logged intervention, the report explicitly states how "
        "<b>land-use context</b>, <b>planner choices</b> (efficiency), and <b>weather / season</b> "
        "shaped the outcome. This is not a formal SHAP value calculation, but a structured, "
        "explainable narrative that mirrors feature-importance thinking in Explainable AI.",
        styles["Body"]
    ))

    # ========= PAGE 3: Per-intervention explainable narratives =========
    story.append(PageBreak())
    story.append(Paragraph("Explainable Breakdown of Interventions", h2))

    if not log:
        story.append(Paragraph(
            "No interventions were logged for this session. Enable reporting in the dashboard "
            "before applying changes to generate a detailed explanation section.",
            styles["Body"]
        ))
    else:
        # Compact table first
        story.append(Paragraph("Numerical Summary (All Logged Interventions)", h3))

        table_data = [[
            "City", "Station", "Method",
            "Base (ppm)", "After (ppm)", "Drop (ppm)", "Eff. (%)"
        ]]
        for e in log:
            table_data.append([
                e.get("city", "–"),
                e.get("station", "–"),
                e.get("method", "–"),
                _fmt_float(e.get("base_co2"), 1),
                _fmt_float(e.get("co2_after"), 1),
                _fmt_float(e.get("reduction"), 1),
                _fmt_float(e.get("efficiency"), 0)
            ])

        tbl = Table(table_data, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ALIGN", (0, 1), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 6))

        story.append(Paragraph("Intervention-Level Explanations", h3))
        story.append(Paragraph(
            "Below, each intervention is unpacked into its local context, the planner’s settings, "
            "and the weather / seasonal conditions. This is the human-readable ‘why’ behind the numbers.",
            styles["Body"]
        ))
        story.append(Spacer(1, 4))

        for idx, e in enumerate(log, start=1):
            title = f"{idx}. {e.get('station', 'Unknown station')} · {e.get('city', 'Unknown city')}"
            story.append(Paragraph(title, h3))

            # Explain reduction
            story.append(Paragraph(_explain_reduction(e), styles["Body"]))

            # Env explanation
            env_desc = _describe_env(e.get("env") or {})
            story.append(Paragraph(env_desc, styles["Body"]))

            # Weather explanation (only if play-mode / weather attached)
            if e.get("play_mode") and e.get("weather"):
                weather_desc = _describe_weather(e.get("weather"))
                story.append(Paragraph(weather_desc, styles["Body"]))
            else:
                story.append(Paragraph(
                    "This intervention used the default seasonal assumptions for the current month "
                    "without an explicit Play Mode override.",
                    styles["Body"]
                ))

            story.append(Spacer(1, 6))

    # ========= PAGE 4: Play-mode analysis + TABLES instead of only graphs =========
    story.append(PageBreak())
    story.append(Paragraph("Play-Mode What-If Analysis", h2))

    play_entries = [e for e in log if e.get("play_mode")]
    if not play_entries:
        story.append(Paragraph(
            "No interventions were logged under Play Mode. To generate this section, enable "
            "Play Mode in the dashboard, adjust NDVI / LULC / albedo / weather sliders, and "
            "then apply interventions while reporting is turned on.",
            styles["Body"]
        ))
    else:
        story.append(Paragraph(
            f"{len(play_entries)} intervention(s) were run with Play Mode active. These runs treat "
            "NDVI, albedo, LULC, and weather fields as controllable ‘what-if’ inputs rather than "
            "fixed measurements.",
            styles["Body"]
        ))
        story.append(Spacer(1, 4))

        avg_drop_play = sum((e.get("reduction") or 0) for e in play_entries) / max(len(play_entries), 1)
        story.append(Paragraph(
            f"On average, Play Mode interventions achieved a drop of "
            f"<b>{_fmt_float(avg_drop_play, 1)} ppm</b> per application, under the chosen scenarios.",
            styles["Body"]
        ))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "You can interpret these results as <b>sensitivity tests</b>: "
            "if winds become calmer, or if the built form stays dark and low-vegetation, the "
            "same infrastructure will behave differently. The digital twin makes these dependencies explicit.",
            styles["Body"]
        ))

    story.append(Spacer(1, 6))
    story.append(Paragraph("Key Visuals from the Session (Tabular Form)", h2))

    img_paths = []

    for idx, chart in enumerate(charts):
        title = chart.get("title") or ""
        if title:
            story.append(Paragraph(title, h3))

        # Prefer tabular data if provided by frontend
        table_spec = chart.get("table") or chart.get("data")

        if isinstance(table_spec, dict):
            headers = table_spec.get("headers") or []
            rows = table_spec.get("rows") or []

            if headers and rows:
                table_data = [headers] + rows
                chart_tbl = Table(table_data, repeatRows=1)
                chart_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    ("ALIGN", (0, 1), (-1, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ]))
                story.append(chart_tbl)
            else:
                story.append(Paragraph(
                    "Tabular data for this chart was not fully provided by the frontend.",
                    styles["Body"]
                ))
        else:
            # Fallback: keep your old image behaviour if no table info
            b64 = chart.get("image")
            if b64:
                img_bytes = base64.b64decode(b64)
                img_path = f"report_chart_{int(time.time())}_{idx}.png"
                with open(img_path, "wb") as f:
                    f.write(img_bytes)
                img_paths.append(img_path)
                story.append(Image(img_path, width=480, height=260))

        # 3–4 line narrative explaining what the table/graph is showing
        summary_text = chart.get("summary")
        if not summary_text:
            summary_text = (
                "This table captures the same values that were visualised as a graph in the "
                "dashboard. It lets planners read exact numbers for each category or station, "
                "compare before/after CO₂ levels, and spot which locations or scenarios are "
                "delivering the strongest reductions."
            )

        story.append(Paragraph(summary_text, styles["Body"]))
        story.append(Spacer(1, 6))

    story.append(Paragraph("City-Level Hotspots & Planning Tips", h2))

    if log:
        city_stats = {}
        for e in log:
            city = e.get("city") or "Unknown"
            drop = e.get("reduction") or 0
            after = e.get("co2_after")
            if city not in city_stats:
                city_stats[city] = {"drop": 0.0, "max_after": after}
            city_stats[city]["drop"] += float(drop)
            if after is not None:
                if (city_stats[city]["max_after"] is None or
                        float(after) > float(city_stats[city]["max_after"])):
                    city_stats[city]["max_after"] = after

        ordered = sorted(
            city_stats.items(),
            key=lambda kv: kv[1]["drop"],
            reverse=True
        )

        for city, stats in ordered[:5]:
            line = (
                f"<b>{city}</b>: total simulated reduction "
                f"{_fmt_float(stats['drop'], 1)} ppm; "
                f"worst remaining station after interventions around "
                f"{_fmt_float(stats['max_after'], 1)} ppm."
            )
            story.append(Paragraph("• " + line, styles["Body"]))
    else:
        story.append(Paragraph(
            "No city-level statistics are available because no interventions were logged in this run.",
            styles["Body"]
        ))

    story.append(Spacer(1, 1))
    story.append(Paragraph(
        "Policy teams can use this section to prioritise where to deepen interventions, where to mix "
        "roadside capture with land-use changes, and where seasonal stagnation means that "
        "the same tonnes of CO2 captured deliver more local benefit.",
        styles["Body"]
    ))

    # ========= FINAL PAGE: Summary + Donut Explanation =========
    story.append(PageBreak())
    story.append(Paragraph("Overall Summary & Sector Classification Donut", h2))

    story.append(Paragraph(
        "This session used the India CO2 Digital Twin to test how targeted urban "
        "interventions could reduce local CO2 build-up at monitoring stations. The model "
        "combined baseline or live CO2 values, land-use context (LULC), vegetation (NDVI), "
        "surface albedo, and – where enabled – Play Mode weather scenarios to estimate the "
        "reduction at each station.",
        styles["Body"]
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "Taken together, the interventions simulated in this run show where relatively modest "
        "investments (for example, roadside capture units or vertical greening) could produce "
        "noticeable ppm-level reductions, and where structural changes in land-use or transport "
        "planning would be required to shift the underlying emission profile.",
        styles["Body"]
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Key Takeaways from This Run", h3))
    story.append(Paragraph(
        f"\u2022 Total logged interventions: <b>{_fmt_float(total_int, 0, default='0')}</b><br/>"
        f"\u2022 Cumulative simulated reduction vs. baseline: "
        f"<b>{_fmt_float(total_drop, 1)} ppm</b><br/>"
        + (
            f"\u2022 Highest single drop: <b>{_fmt_float(best_drop, 1)} ppm</b> "
            f"at <b>{best_loc}</b><br/>"
            if best_loc else ""
        ) +
        "\u2022 Play Mode cases highlight how changes in wind, mixing height and stagnation "
        "can amplify or dampen the effect of the same physical asset.",
        styles["Body"]
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("How to Read the Sector Donut Chart", h3))
    story.append(Paragraph(
        "The sector donut chart in the dashboard (and in this report as the "
        "“Sectoral Emission Mix” graphic) is a compact classifier that explains "
        "what is driving CO2 at a station or city level:",
        styles["Body"]
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "\u2022 Each slice represents a <b>sector share</b> – transport, industry, or power – "
        "inferred from the station’s land-use label (LULC). A station in a dense roadside "
        "corridor will show a high transport share, while an industrial cluster will show a "
        "dominant industry slice.<br/>"
        "\u2022 The donut therefore acts as a <b>classification lens</b> over the same CO2 "
        "number, telling you <i>why</i> a hotspot exists: is it road traffic, nearby factories, "
        "or power / building demand?<br/>"
        "\u2022 When you apply an intervention at a station, you should read the CO2 drop "
        "together with its sector donut – for example, a strong reduction at a transport-dominated "
        "station suggests that transport-side measures (EV corridors, bus priority, signal timing, "
        "roadside capture) are especially impactful there.",
        styles["Body"]
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "In future planning cycles, decision-makers can use this classification to build a "
        "balanced portfolio of measures: tackling high-transport nodes with mobility policies, "
        "industrial nodes with stack controls and process changes, and power-dominated nodes "
        "with building efficiency and grid decarbonisation.",
        styles["Body"]
    ))

    # --- Build PDF ---
    doc.build(story)

    # Clean up any temporary chart images (if fallback used)
    for p in img_paths:
        try:
            os.remove(p)
        except OSError:
            pass

    return send_from_directory(".", file_name, as_attachment=True)

@app.route("/set_month_baseline", methods=["POST", "OPTIONS"])
def set_month_baseline():
    """
    Switch the baseline CO2 snapshot in memory to a different
    (month, day) from station_day_df.

    Expected JSON:
      { "month": 8, "day": 8 }

    This rebuilds station_co2 using rows from station_day_df
    where Date.month == month and Date.day == day.
    """
    global station_co2

    # Handle CORS preflight if browser sends OPTIONS
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    month = payload.get("month")
    day = payload.get("day")

    # --- Basic validation ---
    try:
        month = int(month)
        day = int(day)
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "month and day must be integers"
        }), 400

    if not (1 <= month <= 12) or not (1 <= day <= 31):
        return jsonify({
            "success": False,
            "error": "month must be 1–12 and day 1–31"
        }), 400

    # --- Filter the encrypted daily dataset for that (month, day) combo ---
    df_sel = station_day_df[
        (station_day_df["Date"].dt.month == month) &
        (station_day_df["Date"].dt.day == day)
    ]

    if df_sel.empty:
        # Don't wipe existing baseline, just report no match
        return jsonify({
            "success": False,
            "error": f"No station_day rows found for month={month}, day={day}",
            "month": month,
            "day": day
        }), 404

    # --- Rebuild station_co2 for this snapshot ---
    new_baseline = {}
    for _, row in df_sel.iterrows():
        station_id = row.get("StationId")
        station_name = station_map.get(station_id)
        if not station_name:
            continue

        co_val = row.get("CO", None)
        if pd.isna(co_val):
            raw_co2 = None
        else:
            raw_co2 = float(co_val) * 1000.0

        co2_value = _sanitize_co2(raw_co2, default=400.0, min_val=350.0, max_val=2000.0)
        new_baseline[station_name] = co2_value


    station_co2 = new_baseline

    return jsonify({
        "success": True,
        "month": month,
        "day": day,
        "numStations": int(len(df_sel))
    })


# ----------- Run Flask -----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
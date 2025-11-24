from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import pandas as pd
import json
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

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
        "lat": row["Lat"],
        "lon": row["Lon"]
    })

# Load station ID mapping
with open("station_id.json", "r") as f:
    station_map = json.load(f)

# Load historic CO data
station_day_df = pd.read_csv("station_day.csv")
station_day_df['Date'] = pd.to_datetime(station_day_df['Date'])

# Track current CO2 values
station_co2 = {}

def load_today_co2():
    today = datetime.now()
    today_month = today.month
    today_day = today.day

    df_today = station_day_df[
        (station_day_df['Date'].dt.month == today_month) &
        (station_day_df['Date'].dt.day == today_day)
    ]

    for _, row in df_today.iterrows():
        station_name = station_map.get(row['StationId'])
        if station_name:
            co2_value = row['CO'] * 1000 if not pd.isna(row['CO']) else 400
            station_co2[station_name] = co2_value

load_today_co2()

# ----------- Load station environmental factors -----------
env_df = pd.read_csv("station_env_factors.csv")
station_env = {}
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

# ----------- API endpoints -----------
@app.route("/get_stations")
def get_stations():
    data = []
    for s in stations:
        station_name = s["name"]
        co2 = station_co2.get(station_name)
        env_data = None

        # Try to get environmental data using station_id from station_map
        station_id = None
        for sid, name in station_map.items():
            if name == station_name:
                station_id = sid
                break
        if station_id and station_id in station_env:
            env_data = station_env[station_id]

        if co2 is not None and not pd.isna(co2):
            station_info = {
                "name": s["name"],
                "city": s["city"],
                "state": s["state"],
                "lat": s["lat"],
                "lon": s["lon"],
                "co2": float(co2)
            }
            if env_data:
                station_info.update({
                    "ndvi": env_data["ndvi"],
                    "albedo": env_data["albedo"],
                    "lulc": env_data["lulc"]
                })
            data.append(station_info)

    return jsonify(data)

def intervention_effect(base_co2, ndvi, albedo, lulc_factor, user_efficiency=None):
    """
    Compute CO₂ reduction based on NDVI, Albedo, LULC factor and optional
    user-selected efficiency.

    - Higher NDVI/albedo → higher potential reduction.
    - Higher LULC factor (more urban) → lower potential reduction.
    - user_efficiency (0–50%) scales how much of that potential is realized.
    """
    ndvi = max(0, min(ndvi, 1))
    albedo = max(0, min(albedo, 1))
    lulc_factor = max(0.1, min(lulc_factor, 3))

    # Baseline environmental score (max 30% reduction)
    env_score = (0.6 * ndvi + 0.3 * albedo) / lulc_factor
    reduction_ratio = min(env_score * 0.3, 0.3)

    # Scale by user efficiency if provided (0–50%)
    if user_efficiency is not None:
        try:
            eff = float(user_efficiency)
        except (TypeError, ValueError):
            eff = None

        if eff is not None:
            eff = max(0.0, min(eff, 50.0))
            # 0% → no effect, 50% → full modeled effect
            reduction_ratio *= (eff / 50.0)

    reduced_co2 = base_co2 * (1 - reduction_ratio)
    return round(reduced_co2, 2)

@app.route("/apply_intervention", methods=["POST"])
def apply_intervention():
    data = request.get_json()
    station_name = data.get("station")
    station_id = data.get("station_id")  # Optional: allow using ID directly
    efficiency = data.get("efficiency")  # from frontend (0–50%)

    # Determine key for environmental lookup
    key = None
    if station_id and station_id in station_env:
        key = station_id
    else:
        for sid, name in station_map.items():
            if name == station_name:
                key = sid
                break

    if not key or key not in station_env:
        return jsonify({"success": False, "error": "Station environmental data not found"}), 404

    env = station_env[key]
    ndvi = env["ndvi"]
    albedo = env["albedo"]
    lulc_str = env["lulc"]
    lulc_factor = lulc_mapping.get(lulc_str, 1.5)  # default if unknown

    if station_name not in station_co2:
        return jsonify({"success": False, "error": "Station CO₂ data not found"}), 404

    base_co2 = station_co2[station_name]
    reduced_co2 = intervention_effect(base_co2, ndvi, albedo, lulc_factor, efficiency)
    station_co2[station_name] = reduced_co2

    return jsonify({
        "success": True,
        "station": station_name,
        "base_co2": base_co2,
        "co2_after": reduced_co2,
        "ndvi": ndvi,
        "albedo": albedo,
        "lulc": lulc_factor
    })

# ----------- Run Flask -----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

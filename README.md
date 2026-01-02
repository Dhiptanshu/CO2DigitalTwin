# CO2 Digital Twin: India CO2 Monitoring & Planning Tool

A 3D interactive dashboard for monitoring and simulating CO2 levels across India. This application leverages CesiumJS for 3D geospatial visualization, providing real time and baseline data analysis to support urban planning and environmental interventions.

## Overview

The CO2 Digital Twin provides a comprehensive view of CO2 data, combining varying sources:
*   **Baseline Data**: Historic sensor data.
*   **Satellite Data**: NDVI (Normalized Difference Vegetation Index) and Albedo data fetched from Google Earth Engine.
*   **Live Simulation**: Interactive manipulation of environmental factors to simulate CO2 reduction strategies.

## Tools & Technologies

*   **Frontend**: HTML5, CSS3, JavaScript (Vanilla).
*   **Visualization**: CesiumJS (3D Globe), Plotly.js (Charts).
*   **Backend**: Python (Flask).
*   **Database**: SQLite (`users.db`, `activities.db`).
*   **Data Processing**: Pandas, NumPy.
*   **Satellite Data**: Google Earth Engine (MODIS/Landsat).
*   **Weather Data**: OpenWeatherMap API.

## Features

*   **3D National Context**: Interactive 3D globe visualizing CO2 hotspots.
*   **Intervention Simulation**: Simulate the impact of roadside capture units, vertical gardens, and biofilters.
*   **Play Mode**: Manually manipulate environmental variables (NDVI, Wind Speed, Mixing Height) to see projected efficiency changes.
*   **PWA Support**: Installable as a Progressive Web App on mobile and desktop.
*   **Admin Dashboard**: Dedicated interface for monitoring user activities and interventions.
*   **Secure Data**: Encrypted dataset handling and secure API key management.

## Setup & Installation

### Prerequisites

*   Python 3.8 or higher
*   Google Earth Engine Account (if regenerating satellite data)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *(Note: Ensure you have `flask`, `pandas`, `cryptography`, `python-dotenv`, `requests`, `earthengine-api` installed)*

3.  **Environment Configuration:**
    The application automatically loads configuration from a `.env` file. Ensure this file contains:
    *   `DATA_FERNET_KEY`: Key for decrypting datasets.
    *   `FLASK_SECRET_KEY`: Secret key for session management.
    *   `INTEGRITY_SECRET`: Secret for station data validation.
    *   `OPENWEATHER_API_KEY`: API key for weather data.
    *   `OPENAQ_API_KEY`: API key for OpenAQ data.
    *   `CESIUM_ION_TOKEN`: Token for CesiumJS.

    *Note: For shared instances, the configuration is embedded in `app_config.bin` and loaded automatically if `.env` is missing.*

## Running the Application

### Local Development

1.  **Start the Main Application:**
    ```bash
    python app.py
    ```
2.  **Access the Dashboard:**
    Open your browser and navigate to `http://127.0.0.1:5000`.

### Remote Access via Ngrok

To expose the application securely to the internet (e.g., for mobile PWA testing):

1.  **Install Ngrok:**
    Download and install from [ngrok.com](https://ngrok.com/download), or use a package manager:
    ```bash
    choco install ngrok  # Windows (Chocolatey)
    ```

2.  **Authenticate Ngrok:**
    ```bash
    ngrok config add-authtoken <your_auth_token>
    ```

3.  **Run Ngrok:**
    Open a terminal and run:
    ```bash
    ngrok http 5000
    ```
    Copy the forwarding URL (e.g., `https://xxxx-xxxx.ngrok-free.app`) and open it on your mobile device.

## Admin Dashboard

A separate administrative interface tracks user interventions and reported activities.

1.  **Start the Admin Server:**
    Open a new terminal, navigate to the project root, and run:
    ```bash
    cd backend
    python app.py
    ```
    *Note: This runs a separate Flask instance on port **5001**.*

2.  **Access the Dashboard:**
    Open your browser and navigate to `http://127.0.0.1:5001`.

## Security & Integrity

*   **Encryption**: Sensitive source datasets (`csv`) are encrypted (`.enc`) using Fernet symmetric encryption. The application only decrypts them in memory at runtime.
*   **Key Management**: All API keys and secrets are loaded from environment variables and never hardcoded in the source.
*   **Data Integrity**: Station data snapshots are signed using HMAC SHA256 (`_compute_station_integrity_token`) to prevent tampering during transmission.
*   **Secure Injection**: The Cesium Ion token is securely injected into the frontend template by the backend, preventing exposure in static files.

## Project Structure

*   `app.py`: Main Flask application entry point.
*   `backend/`: Contains the Admin Dashboard application.
*   `static/`: CSS, JavaScript, and asset files.
*   `templates/`: HTML templates (Jinja2).
*   `encrypted/`: Encrypted dataset files.
*   `tools/`: Utility scripts for data encryption (`encrypt_datasets.py`) and fetching satellite data (`get_station_env_factors.py`).

let stations = [];
let viewer;
let dispersionEnabled = false;        // controlled by toggle
let dispersionCells = [];             // raw grid cells from backend
let dispersionEntities = [];          // Cesium entities for the grid

// Base URL for API calls
const BASE_URL = window.location.origin;

// Dropdowns / controls
const citySelect = document.getElementById('citySelect');
const citySearchInput = document.getElementById('citySearch');
const cityRecommendationsEl = document.getElementById('cityRecommendations');
const citySuggestionsEl = document.getElementById('citySearchSuggestions');
const stationSelect = document.getElementById('stationSelect');
const methodSelect = document.getElementById('methodSelect');
const reductionInput = document.getElementById('reductionInput');
const startReportBtn = document.getElementById('startReportBtn');
const downloadReportBtn = document.getElementById('downloadReportBtn');
const reportScopeSelect = document.getElementById('reportScope'); // may be null if you didn't add it

// ---- Play mode controls ----
const playToggleBtn = document.getElementById("playModeBtn");
const playPanel = document.getElementById("playPanel");
const playApplyBtn = document.getElementById("playApplyBtn");

// Environmental factor inputs
const playNdviInput = document.getElementById("playNdviInput");
const playLulcInput = document.getElementById("playLulcInput");
const playAlbedoInput = document.getElementById("playAlbedoInput");

// Weather factor inputs
const playTempInput = document.getElementById("playTempInput");
const playWindInput = document.getElementById("playWindInput");   // wind (m/s)
const playMixingInput = document.getElementById("playMixingInput"); // mixing height (m)
const playStagInput = document.getElementById("playStagInput");   // stagnation: High / Elevated / Low / Moderate

// Play mode flag
let playModeActive = false;

// ---- Weather state ----
let currentWeatherRaw = null;     // latest backend weather for selected city
let currentWeatherCity = null;
const weatherMonthEl = document.getElementById('weatherMonth');

// ---- Station focus & highlight ----
const stationEntityByName = {};   // map station.name -> main Cesium entity
let lastSelectedEntity = null;    // currently highlighted entity

// ---------- Reporting state ----------
let displayMode = 'baseline'; // 'baseline' | 'live' | 'both'
let colorMode = 'co2';        // 'co2' | 'sector'
let recommendedCities = [];
let availableCities = [];
let suggestionActiveIndex = -1;

let reportingActive = false;
let reportLog = []; // array of interventions to send to backend
let lastReportBlob = null;


const playColorMode = document.getElementById("playColorMode");
if (playColorMode) {
  playColorMode.addEventListener("change", () => {
    colorMode = playColorMode.value;
    drawEntities();
  });
}

function setEfficiency(eff) {
  reductionInput.value = eff;
  if (playReductionInput) {
    playReductionInput.value = eff;
  }
}


// ---- Sync Efficiency between Intervention & Manipulate Factors ----
const playReductionInput = document.getElementById("playReductionInput");
// Initialize Manipulate Factors efficiency from Intervention Settings
if (playReductionInput && reductionInput) {
  playReductionInput.value = reductionInput.value;
}

function updateEfficiencySuggestion() {
  const stationName = stationSelect.value;
  if (!stationName) return;

  const station = stations.find(s => s.name === stationName);
  const method = methodSelect.value;

  const eff = autoSuggestEfficiency(station, method);

  reductionInput.value = eff;

  if (playReductionInput) {
    playReductionInput.value = eff;
  }
}


// Manual change from Intervention Settings
reductionInput.addEventListener("input", () => {
  setEfficiency(reductionInput.value);
});

// Manual change from Manipulate Factors
if (playReductionInput) {
  playReductionInput.addEventListener("input", () => {
    setEfficiency(playReductionInput.value);
  });
}



// ---------- Themed alert / toast helper ----------

function showToast(message, options = {}) {
  const {
    type = "info",         // "info" | "success" | "warning" | "error"
    title = null,
    timeout = 3500         // ms, set to 0 for persistent
  } = options;

  // Ensure a container exists
  let container = document.querySelector(".toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container";
    document.body.appendChild(container);
  }

  // Build toast element
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;

  const icon = document.createElement("div");
  icon.className = "toast-icon";

  const content = document.createElement("div");
  content.className = "toast-content";

  const titleEl = document.createElement("div");
  titleEl.className = "toast-title";
  titleEl.textContent =
    title ||
    (type === "success"
      ? "Success"
      : type === "error"
        ? "Something went wrong"
        : type === "warning"
          ? "Heads up"
          : "Notice");

  const msgEl = document.createElement("div");
  msgEl.className = "toast-message";
  msgEl.textContent = message;

  content.appendChild(titleEl);
  content.appendChild(msgEl);

  const closeBtn = document.createElement("button");
  closeBtn.className = "toast-close";
  closeBtn.innerHTML = "&times;";
  closeBtn.onclick = () => dismissToast(toast);

  toast.appendChild(icon);
  toast.appendChild(content);
  toast.appendChild(closeBtn);

  container.appendChild(toast);

  // trigger animation
  requestAnimationFrame(() => {
    toast.classList.add("show");
  });

  // Auto-dismiss
  if (timeout && timeout > 0) {
    setTimeout(() => dismissToast(toast), timeout);
  }

  return toast;
}

function dismissToast(toast) {
  if (!toast) return;
  toast.classList.remove("show");
  setTimeout(() => {
    if (toast.parentNode) toast.parentNode.removeChild(toast);
  }, 180);
}

// Frontend LULC mapping (for suggestions)
const LULC_FACTORS = {
  "Urban": 2.0, "Industrial": 2.5, "Residential": 1.8, "Campus": 1.5,
  "Rural": 1.0, "Mixed Urban": 2.0, "Industrial/Residential": 2.2,
  "Urban Vegetation": 1.3, "Airport": 2.5, "Sports Complex": 1.5,
  "Government": 1.8, "Mixed Forest": 1.0
};

// Sector weights inferred from LULC (for map + pie)
const SECTOR_WEIGHTS = {
  "Urban": { transport: 0.6, industry: 0.3, power: 0.1 },
  "Industrial": { transport: 0.15, industry: 0.7, power: 0.15 },
  "Industrial/Residential": { transport: 0.3, industry: 0.5, power: 0.2 },
  "Residential": { transport: 0.5, industry: 0.2, power: 0.3 },
  "Mixed Urban": { transport: 0.5, industry: 0.35, power: 0.15 },
  "Campus": { transport: 0.4, industry: 0.1, power: 0.5 },
  "Government": { transport: 0.4, industry: 0.2, power: 0.4 },
  "Airport": { transport: 0.85, industry: 0.1, power: 0.05 },
  "Sports Complex": { transport: 0.6, industry: 0.1, power: 0.3 },
  "Urban Vegetation": { transport: 0.4, industry: 0.1, power: 0.5 },
  "Mixed Forest": { transport: 0.1, industry: 0.05, power: 0.05 },
  "Rural": { transport: 0.3, industry: 0.1, power: 0.6 }
};

function populatePlayPanelFromSelection() {
  if (!playPanel) return;

  const station = getSelectedStation();
  const city = citySelect.value || "";

  // --- Station env factors (NDVI, Albedo, LULC) ---
  if (station) {
    if (playNdviInput) {
      playNdviInput.value =
        typeof station.ndvi === "number" ? station.ndvi.toFixed(3) : "";
    }
    if (playAlbedoInput) {
      playAlbedoInput.value =
        typeof station.albedo === "number" ? station.albedo.toFixed(3) : "";
    }
    if (playLulcInput) {
      playLulcInput.value = station.lulc || "";
    }
  }

  // --- City weather factors (temperature, wind, mixing height, stagnation) ---
  if (city && currentWeatherRaw) {
    if (playTempInput && typeof currentWeatherRaw.temperature === "number") {
      playTempInput.value = currentWeatherRaw.temperature.toFixed(1);
    }

    // Convert backend km/h to m/s for the play input
    if (playWindInput && typeof currentWeatherRaw.windspeed === "number") {
      const windMs = currentWeatherRaw.windspeed / 3.6;
      playWindInput.value = windMs.toFixed(2);
    }
  }

  const mixingEl = document.getElementById("weatherMixing");
  const stagEl = document.getElementById("weatherStagnation");

  if (playMixingInput && mixingEl && mixingEl.textContent.trim() !== "–") {
    playMixingInput.value = mixingEl.textContent.trim();
  }
  if (playStagInput && stagEl && stagEl.textContent.trim()) {
    playStagInput.value = stagEl.textContent.trim();
  }
}

// ---------------- Efficiency suggestion ----------------
function autoSuggestEfficiency(station, method) {
  if (!station) return 20;
  const co2 = station.co2 ?? station.co2_estimated;
  if (co2 === undefined || isNaN(co2)) return 20;

  // --- base severity from CO₂ level ---
  let severity = 0;
  if (co2 >= 430 && co2 <= 450) severity = 1;
  else if (co2 > 450) severity = 2;

  // --- base env from station ---
  let ndvi = (typeof station.ndvi === "number")
    ? Math.max(0, Math.min(station.ndvi, 1))
    : 0.3;

  let lulcFactor = LULC_FACTORS[station.lulc] || 1.5;

  // default albedo if not present
  let albedo = (typeof station.albedo === "number")
    ? Math.max(0.05, Math.min(station.albedo, 0.5))
    : 0.18;

  // --- Play mode: override env + read weather inputs ---
  let windMs = null;
  let mixingHeight = null;
  let stagText = null;

  if (playModeActive) {
    // NDVI override
    if (playNdviInput) {
      const v = parseFloat(playNdviInput.value);
      if (!isNaN(v)) {
        ndvi = Math.max(0, Math.min(v, 1));
      }
    }

    // LULC override – match key in LULC_FACTORS if possible
    if (playLulcInput) {
      const label = (playLulcInput.value || "").trim();
      if (label && LULC_FACTORS[label] != null) {
        lulcFactor = LULC_FACTORS[label];
      }
    }

    // Albedo override
    if (playAlbedoInput) {
      const v = parseFloat(playAlbedoInput.value);
      if (!isNaN(v)) {
        albedo = Math.max(0.05, Math.min(v, 0.5));
      }
    }

    // Weather overrides (all optional)
    if (playWindInput) {
      const v = parseFloat(playWindInput.value);
      if (!isNaN(v)) windMs = v;          // m/s
    }
    if (playMixingInput) {
      const v = parseFloat(playMixingInput.value);
      if (!isNaN(v)) mixingHeight = v;    // meters
    }
    if (playStagInput) {
      stagText = (playStagInput.value || "").trim().toLowerCase();
    }
  }

  // --- Method boost ---
  let methodBoost = 0;
  if (method === "Roadside Capture Unit") methodBoost = 8;
  else if (method === "Biofilter") methodBoost = 6;
  else if (method === "Vertical Garden") methodBoost = 4;

  // === Base efficiency from CO₂ + env (main driver) ===
  let eff = 10
    + severity * 7
    + (1 - ndvi) * 8
    + (lulcFactor - 1) * 3
    + methodBoost;

  // Albedo effect (reduced): darker → slightly higher
  const albedoRef = 0.18;              // typical urban mid value
  const albDelta = albedoRef - albedo; // positive if darker than reference
  eff += albDelta * 40;

  // === Weather modulation ONLY in play mode (softened) ===
  if (playModeActive) {
    if (windMs != null) {
      if (windMs < 1.5) eff += 3;
      else if (windMs > 5.0) eff -= 4;
    }

    if (mixingHeight != null) {
      if (mixingHeight < 500) eff += 2;
      else if (mixingHeight > 900) eff -= 3;
    }

    if (stagText) {
      if (stagText.startsWith("high")) eff += 4;
      else if (stagText.startsWith("elevated")) eff += 2;
      else if (stagText.startsWith("low")) eff -= 4;
    }
  }

  // Final clamp and round
  eff = Math.round(Math.max(5, Math.min(50, eff)));
  return eff;
}

function updateEfficiencySuggestion() {
  const stationName = stationSelect.value;
  if (!stationName) return;

  const station = stations.find(s => s.name === stationName);
  const method = methodSelect.value;

  const eff = autoSuggestEfficiency(station, method);
  setEfficiency(eff);
}


// --------------- Cesium init ---------------
async function initCesium() {
  viewer = new Cesium.Viewer('cesiumContainer', {
    terrainProvider: await Cesium.CesiumTerrainProvider.fromIonAssetId(1),
    imageryProvider: new Cesium.IonImageryProvider({ assetId: 2 }),
    timeline: false,
    animation: false,
    infoBox: false,
    selectionIndicator: false
  });
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(78.9629, 20.5937, 2500000)
  });
}

function getColor(co2) {
  if (co2 === undefined || isNaN(co2)) return Cesium.Color.GRAY;
  if (co2 <= 420) return Cesium.Color.fromBytes(34, 197, 94);
  else if (co2 <= 450) return Cesium.Color.fromBytes(251, 191, 36);
  else return Cesium.Color.fromBytes(248, 113, 113);
}

// 🔹 Colour ramp for dispersion strength [0–1] → blue → yellow → red
function getDispersionCesiumColor(strength) {
  const s = Math.max(0, Math.min(1, strength || 0));

  let r, g, b;
  if (s <= 0.5) {
    // 0.0 → 0.5 : blue (low build-up) → yellow (medium)
    const t = s / 0.5;
    const start = [56, 189, 248];   // blue
    const end = [234, 179, 8];    // yellow
    r = start[0] + (end[0] - start[0]) * t;
    g = start[1] + (end[1] - start[1]) * t;
    b = start[2] + (end[2] - start[2]) * t;
  } else {
    // 0.5 → 1.0 : yellow → red (high build-up)
    const t = (s - 0.5) / 0.5;
    const start = [234, 179, 8];    // yellow
    const end = [239, 68, 68];    // red
    r = start[0] + (end[0] - start[0]) * t;
    g = start[1] + (end[1] - start[1]) * t;
    b = start[2] + (end[2] - start[2]) * t;
  }

  const alpha = 0.25 + 0.55 * s; // low → faint, high → solid
  return new Cesium.Color(r / 255, g / 255, b / 255, alpha);
}

// ---------- Display mode handling ----------
function stationHasForMode(s, mode) {
  if (!s) return false;
  const hasBase = (s.co2 !== undefined && !isNaN(s.co2));
  const hasLive = (s.co2_estimated !== undefined && !isNaN(s.co2_estimated));
  if (mode === 'baseline') return hasBase;
  if (mode === 'live') return hasLive;
  return hasBase || hasLive; // both
}

// rebuild city list based on displayMode
function rebuildCityDropdown() {
  const citySet = new Set();
  stations.forEach(s => {
    if (s.city && stationHasForMode(s, displayMode)) citySet.add(s.city);
  });

  const cities = Array.from(citySet).sort((a, b) => a.localeCompare(b));
  availableCities = cities;   // <-- store for suggestions

  const prev = citySelect.value;
  const searchQuery = citySearchInput ? citySearchInput.value.trim() : "";

  citySelect.innerHTML = "";
  citySelect.add(new Option("City", "", true, true));
  citySelect.options[0].disabled = true;
  cities.forEach(c => citySelect.add(new Option(c, c)));

  if (cities.includes(prev)) {
    citySelect.value = prev;
  } else {
    citySelect.value = "";
    stationSelect.innerHTML = "";
    stationSelect.add(new Option("Station", "", true, true));
    stationSelect.options[0].disabled = true;
  }

  // If user was already typing, preserve the text and re-filter
  if (searchQuery && citySearchInput) {
    citySearchInput.value = searchQuery;
    filterCitiesBySearch();
    updateCitySuggestions();
  }
}

function filterCitiesBySearch() {
  if (!citySearchInput || !citySelect) return;
  const query = citySearchInput.value.trim().toLowerCase();

  Array.from(citySelect.options).forEach((opt, idx) => {
    // keep placeholder visible
    if (idx === 0) {
      opt.hidden = false;
      return;
    }

    if (!query) {
      opt.hidden = false;
      return;
    }

    const match = opt.value.toLowerCase().includes(query);
    opt.hidden = !match;
  });
}

function updateCitySuggestions() {
  if (!citySuggestionsEl || !citySearchInput) return;
  const query = citySearchInput.value.trim().toLowerCase();

  citySuggestionsEl.innerHTML = "";

  if (!query) {
    citySuggestionsEl.style.display = "none";
    suggestionActiveIndex = -1;
    return;
  }

  const matches = availableCities
    .filter(c => c.toLowerCase().includes(query))
    .slice(0, 7);  // limit to top 7 suggestions

  if (!matches.length) {
    citySuggestionsEl.style.display = "none";
    suggestionActiveIndex = -1;
    return;
  }

  suggestionActiveIndex = -1; // nothing selected initially

  matches.forEach(city => {
    const item = document.createElement('div');
    item.className = 'city-suggestion-item';

    const lower = city.toLowerCase();
    const idx = lower.indexOf(query);

    if (idx === -1) {
      item.textContent = city;
    } else {
      const before = city.slice(0, idx);
      const match = city.slice(idx, idx + query.length);
      const after = city.slice(idx + query.length);

      item.innerHTML =
        `<span class="city-suggestion-rest">${before}</span>` +
        `<span class="city-suggestion-match">${match}</span>` +
        `<span class="city-suggestion-rest">${after}</span>`;
    }

    item.onclick = () => {
      citySelect.value = city;
      citySearchInput.value = city;
      citySuggestionsEl.style.display = "none";
      suggestionActiveIndex = -1;
      citySelect.dispatchEvent(new Event('change'));
    };

    citySuggestionsEl.appendChild(item);
  });

  citySuggestionsEl.style.display = "block";
  refreshSuggestionHighlight();
}

function refreshSuggestionHighlight() {
  if (!citySuggestionsEl) return;
  const items = citySuggestionsEl.querySelectorAll('.city-suggestion-item');
  items.forEach((el, idx) => {
    el.classList.toggle('active', idx === suggestionActiveIndex);
  });
}

// Update stations list for selected city + mode
function updateStations() {
  const city = citySelect.value;
  const filtered = stations.filter(s =>
    s.city === city &&
    s.lat !== undefined && !isNaN(s.lat) &&
    s.lon !== undefined && !isNaN(s.lon) &&
    stationHasForMode(s, displayMode)
  );

  stationSelect.innerHTML = "";
  stationSelect.add(new Option("Station", "", true, true));
  stationSelect.options[0].disabled = true;

  filtered.forEach(s => {
    const label =
      (s.co2 === undefined || isNaN(s.co2))
        ? `${s.name} (est)`
        : s.name;
    stationSelect.add(new Option(label, s.name));
  });
}

function computeCityRecommendations() {
  const cityMax = {};

  stations.forEach(s => {
    if (!s.city) return;
    const v = getDisplayValue(s) ??
      (typeof s.co2 === 'number' ? s.co2 : null) ??
      (typeof s.co2_estimated === 'number' ? s.co2_estimated : null);

    if (v == null || isNaN(v)) return;
    if (!cityMax[s.city] || v > cityMax[s.city]) {
      cityMax[s.city] = v; // track worst hotspot per city
    }
  });

  recommendedCities = Object.entries(cityMax)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([city]) => city);
}

function renderCityRecommendations() {
  if (!cityRecommendationsEl) return;
  cityRecommendationsEl.innerHTML = "";

  if (!recommendedCities.length) {
    cityRecommendationsEl.style.display = "none";
    return;
  }

  cityRecommendationsEl.style.display = "flex";

  recommendedCities.forEach(city => {
    const chip = document.createElement('span');
    chip.className = 'chip chip-recommendation';
    chip.textContent = city;
    chip.onclick = () => {
      citySelect.value = city;
      if (citySearchInput) citySearchInput.value = city;
      updateStations();
      drawEntities();
      drawSectorChartForSelection();
    };
    cityRecommendationsEl.appendChild(chip);
  });
}

// Fetch stations
async function fetchStations() {
  const res = await fetch(`${BASE_URL}/get_stations`);
  const rawStations = await res.json();

  stations = rawStations.map(s => ({ ...s, baseline_co2: s.co2 }));

  rebuildCityDropdown();
  computeCityRecommendations();
  renderCityRecommendations();
  updateStations();
  drawEntities();
}

async function updateCityWeather() {
  const city = citySelect.value;
  const summaryEl = document.getElementById('weatherSummary');
  const metaEl = document.getElementById('weatherMeta');

  if (!city) {
    currentWeatherRaw = null;
    if (summaryEl) summaryEl.textContent = 'Select a city';
    if (metaEl) {
      metaEl.textContent =
        'Winters in north India often show higher CO₂ / pollution due to low wind & mixing height.';
    }
    Plotly.purge('monthlyChart');
    return;
  }

  try {
    const res = await fetch(`${BASE_URL}/get_weather?city=` + encodeURIComponent(city));
    const data = await res.json();

    if (!data.success) {
      console.warn('Weather backend returned error', data);
      currentWeatherRaw = null;
      if (summaryEl) summaryEl.textContent = `Weather for ${city} not available`;
      return;
    }

    currentWeatherRaw = {
      city: data.city,
      temperature: data.temperature,   // °C
      windspeed: data.windspeed,      // km/h from backend
      winddirection: data.winddirection,
      season: data.season,
      month_factor: data.month_factor
    };

    const monthSelect = document.getElementById('weatherMonth');
    const selectedVal = monthSelect ? monthSelect.value : 'auto';
    applyWeatherScenario(selectedVal);

    if (playModeActive) {
      populatePlayPanelFromSelection();
      updateEfficiencySuggestion();
    }

  } catch (err) {
    console.error('Weather fetch failed', err);
    if (summaryEl) summaryEl.textContent = `Weather for ${city} not available`;
  }
}

// 🔹 Call backend to get dispersion grid for this city
async function loadDispersionForCity(city) {
  clearDispersionLayer();
  dispersionCells = [];

  if (!city || !dispersionEnabled) return;

  try {
    const res = await fetch(
      `${BASE_URL}/get_dispersion?city=` + encodeURIComponent(city)
    );
    if (!res.ok) {
      console.warn("Dispersion API error:", await res.text());
      return;
    }

    const data = await res.json();

    let cells = [];

    if (Array.isArray(data)) {
      cells = data;
    } else if (data && data.success === false) {
      console.warn("No dispersion data:", data.error || data);
      return;
    } else if (data && Array.isArray(data.cells)) {
      cells = data.cells;
    } else if (data && Array.isArray(data.points)) {
      cells = data.points;
    } else if (data && Array.isArray(data.grid)) {
      cells = data.grid;
    } else {
      console.warn("Unexpected dispersion payload:", data);
      return;
    }

    dispersionCells = cells;
    drawDispersionLayer();
  } catch (err) {
    console.error("Failed to load dispersion:", err);
  }
}

// ------------- KPIs + helpers -------------
function getDisplayValue(s) {
  if (displayMode === 'baseline') {
    return (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : null;
  } else if (displayMode === 'live') {
    return (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : null;
  } else {
    if (s.co2 !== undefined && !isNaN(s.co2)) return s.co2;
    if (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) return s.co2_estimated;
    return null;
  }
}

function updateSummary(data) {
  const city = citySelect.value || "No city selected";
  const kpiCityEl = document.getElementById("kpiCity");
  const kpiStationsEl = document.getElementById("kpiStations");
  const kpiAvgEl = document.getElementById("kpiAvg");
  const kpiMaxEl = document.getElementById("kpiMax");
  const kpiMaxStationEl = document.getElementById("kpiMaxStation");
  const kpiReductionEl = document.getElementById("kpiReduction");
  if (!kpiCityEl) return;

  const withDisplay = data.map(s => {
    const display = getDisplayValue(s);
    return { ...s, displayCO2: display };
  });

  const valid = withDisplay.filter(s => s.displayCO2 !== null && !isNaN(s.displayCO2));
  const count = valid.length;

  kpiCityEl.textContent = city === "" ? "All / None" : city;
  kpiStationsEl.textContent = `${count} monitoring station${count === 1 ? "" : "s"}`;

  if (count === 0) {
    kpiAvgEl.textContent = "–";
    kpiMaxEl.textContent = "–";
    kpiMaxStationEl.textContent = "No data";
    kpiReductionEl.textContent = "0 ppm";
    return;
  }

  const avg = valid.reduce((sum, s) => sum + s.displayCO2, 0) / count;
  kpiAvgEl.textContent = `${avg.toFixed(1)} ppm`;

  let worst = valid[0];
  for (const s of valid) {
    if (s.displayCO2 > worst.displayCO2) worst = s;
  }
  kpiMaxEl.textContent = `${worst.displayCO2.toFixed(1)} ppm`;
  kpiMaxStationEl.textContent = `${worst.name} · ${worst.city}`;

  let totalDrop = 0;
  withDisplay.forEach(s => {
    if (typeof s.baseline_co2 === "number" && !isNaN(s.baseline_co2) && s.displayCO2 !== null) {
      const diff = s.baseline_co2 - s.displayCO2;
      if (diff > 0) totalDrop += diff;
    }
  });
  kpiReductionEl.textContent = `${totalDrop.toFixed(1)} ppm`;
}

// ---------- Reporting helpers ----------
function capturePlaySnapshotForReport() {
  const station = getSelectedStation();
  const city = citySelect.value || null;

  const tempEl = document.getElementById('weatherTemp');
  const windEl = document.getElementById('weatherWind');
  const mixingEl = document.getElementById('weatherMixing');
  const stagEl = document.getElementById('weatherStagnation');
  const monthSel = document.getElementById('weatherMonth');

  const toNumber = (el) => {
    if (!el) return null;
    const v = parseFloat(el.textContent);
    return isNaN(v) ? null : v;
  };

  const snapshot = {
    playModeActive: !!playModeActive,
    city,
    station: station ? station.name : null,

    env: {
      ndvi: (() => {
        if (playModeActive && playNdviInput && playNdviInput.value !== "") {
          const v = parseFloat(playNdviInput.value);
          return isNaN(v) ? null : v;
        }
        return typeof station?.ndvi === "number" ? station.ndvi : null;
      })(),
      albedo: (() => {
        if (playModeActive && playAlbedoInput && playAlbedoInput.value !== "") {
          const v = parseFloat(playAlbedoInput.value);
          return isNaN(v) ? null : v;
        }
        return typeof station?.albedo === "number" ? station.albedo : null;
      })(),
      lulc: (() => {
        if (playModeActive && playLulcInput && playLulcInput.value) {
          return playLulcInput.value.trim();
        }
        return station?.lulc || null;
      })()
    },

    weather: {
      temperature_C: toNumber(tempEl),
      wind_ms: toNumber(windEl),
      mixing_height_m: toNumber(mixingEl),
      stagnation_risk: stagEl ? stagEl.textContent.trim() : null,
      month_mode: monthSel ? monthSel.value || "auto" : "auto"
    }
  };

  return snapshot;
}

function logIntervention(entry) {
  if (!reportingActive) return;

  const playSnapshot = capturePlaySnapshotForReport();

  reportLog.push({
    ...entry,
    timestamp: new Date().toISOString(),
    play: playSnapshot
  });
}

function computeReportKpis(logEntries) {
  if (!logEntries.length) return null;

  let totalDrop = 0;
  let maxDrop = -Infinity;
  let maxDropEntry = null;

  logEntries.forEach(e => {
    const drop = e.reduction ?? 0;
    totalDrop += drop;
    if (drop > maxDrop) {
      maxDrop = drop;
      maxDropEntry = e;
    }
  });

  return {
    totalInterventions: logEntries.length,
    totalDrop: +totalDrop.toFixed(1),
    bestDrop: maxDropEntry ? +maxDropEntry.reduction.toFixed(1) : 0,
    bestLocation: maxDropEntry
      ? `${maxDropEntry.station} · ${maxDropEntry.city || ""}`.trim()
      : null
  };
}

// ------------- Sector helpers -------------
function getSectorWeights(station) {
  if (!station || !station.lulc) return null;
  return SECTOR_WEIGHTS[station.lulc] || null;
}

function getDominantSector(station) {
  const w = getSectorWeights(station);
  if (!w) return null;

  let bestSector = null;
  let bestVal = -Infinity;
  for (const [sector, val] of Object.entries(w)) {
    if (val > bestVal) {
      bestVal = val;
      bestSector = sector;
    }
  }
  return bestSector;
}

function aggregateCitySectorWeights(city) {
  if (!city) return null;

  const cityStations = stations.filter(s => s.city === city);

  let totals = { transport: 0, industry: 0, power: 0 };
  let any = false;

  cityStations.forEach(s => {
    const w = getSectorWeights(s);
    if (!w) return;

    const display = getDisplayValue(s);
    const weight = (display !== null && !isNaN(display)) ? Math.max(display, 1) : 1;

    totals.transport += (w.transport ?? 0) * weight;
    totals.industry += (w.industry ?? 0) * weight;
    totals.power += (w.power ?? 0) * weight;

    any = true;
  });

  if (!any) return null;
  return totals;
}

// --------- Weather helpers (frontend seasonal logic) ----------
function getSeasonalProfileForMonth(monthIndex) {
  const m = monthIndex;
  let co2Factor = 1.0;
  if (m === 11 || m === 12 || m === 1) co2Factor = 1.25;
  else if (m === 10 || m === 2) co2Factor = 1.15;
  else if (m === 4 || m === 5 || m === 6) co2Factor = 0.90;
  else if (m === 7 || m === 8 || m === 9) co2Factor = 0.95;
  else co2Factor = 1.0;

  let tempDelta = 0;
  if (m === 12 || m === 1) tempDelta = -4;
  else if (m === 11 || m === 2) tempDelta = -2;
  else if (m === 3) tempDelta = 1;
  else if (m === 4 || m === 10) tempDelta = 2;
  else if (m === 5 || m === 6) tempDelta = 4;
  else if (m === 7 || m === 8 || m === 9) tempDelta = 1;

  let mixingBase = 600;
  if (m === 11 || m === 12 || m === 1) mixingBase = 350;
  else if (m === 10 || m === 2) mixingBase = 450;
  else if (m === 3) mixingBase = 550;
  else if (m === 4 || m === 5 || m === 6) mixingBase = 900;
  else if (m === 7 || m === 8 || m === 9) mixingBase = 750;

  return { co2Factor, tempDelta, mixingBase };
}

function drawMonthlyChart(cityName) {
  const container = document.getElementById('monthlyChart');
  if (!container) return;

  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const factors = [];
  for (let m = 1; m <= 12; m++) {
    const profile = getSeasonalProfileForMonth(m);
    factors.push(profile.co2Factor);
  }

  const selectedEl = document.getElementById('weatherMonth');
  const selectedVal = selectedEl ? selectedEl.value : 'auto';
  const selIndex = selectedVal === 'auto'
    ? (new Date().getMonth())
    : (parseInt(selectedVal, 10) - 1);

  const barColors = factors.map((f, idx) => {
    const alpha = idx === selIndex ? 0.95 : 0.45;
    const base = idx === selIndex ? [56, 189, 248] : [148, 163, 184];
    return `rgba(${base[0]},${base[1]},${base[2]},${alpha})`;
  });

  const data = [{
    type: 'bar',
    x: months,
    y: factors,
    marker: {
      color: barColors,
      line: { width: 1, color: 'rgba(15,23,42,1)' }
    },
    hovertemplate: '<b>%{x}</b><br>Relative build-up ×%{y:.2f}<extra></extra>'
  }];

  const layout = {
    title: {
      text: cityName ? `${cityName} — monthly stagnation potential` : 'Monthly stagnation potential',
      font: { size: 12, color: '#e5e7eb' }
    },
    margin: { t: 40, l: 40, r: 10, b: 40 },
    paper_bgcolor: 'rgba(15,23,42,0)',
    plot_bgcolor: 'rgba(15,23,42,0.9)',
    xaxis: { tickfont: { size: 11, color: '#9ca3af' } },
    yaxis: { tickfont: { size: 11, color: '#9ca3af' }, title: 'Relative build-up (×)' }
  };

  Plotly.react('monthlyChart', data, layout, { displaylogo: false, responsive: true });
}

function applyWeatherScenario(selectedMonthValue) {
  const city = citySelect.value || '';
  const summaryEl = document.getElementById('weatherSummary');
  const metaEl = document.getElementById('weatherMeta');
  const tempEl = document.getElementById('weatherTemp');
  const windEl = document.getElementById('weatherWind');
  const mixingEl = document.getElementById('weatherMixing');
  const stagEl = document.getElementById('weatherStagnation');

  if (!summaryEl || !currentWeatherRaw) {
    if (summaryEl) summaryEl.textContent = city ? `Weather for ${city}` : 'Select a city';
    return;
  }

  const raw = currentWeatherRaw;
  const now = new Date();

  let m;
  if (!selectedMonthValue || selectedMonthValue === 'auto') {
    m = now.getMonth() + 1;
  } else {
    m = parseInt(selectedMonthValue, 10);
    if (isNaN(m) || m < 1 || m > 12) m = now.getMonth() + 1;
  }

  const profile = getSeasonalProfileForMonth(m);

  const windMs = (typeof raw.windspeed === 'number')
    ? (raw.windspeed / 3.6)
    : null;

  let displayTemp = raw.temperature;
  if (typeof displayTemp === 'number') {
    displayTemp = displayTemp + profile.tempDelta;
  }

  let mixingHeight = profile.mixingBase;
  if (typeof windMs === 'number') {
    mixingHeight += Math.max(0, windMs) * 20;
  }

  let risk = 'Moderate';
  if (windMs != null) {
    if (profile.co2Factor >= 1.2 && windMs < 1.5) risk = 'High';
    else if (profile.co2Factor >= 1.1 && windMs < 3) risk = 'Elevated';
    else if (windMs > 4 && profile.co2Factor <= 1.0) risk = 'Low';
  }

  const monthNamesFull = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const labelMonth = monthNamesFull[m - 1];

  summaryEl.textContent = city
    ? `${city} — ${labelMonth} scenario`
    : `${labelMonth} scenario`;

  if (metaEl) {
    metaEl.textContent =
      `Relative build-up factor ×${profile.co2Factor.toFixed(2)} · ` +
      (risk === 'High' ? 'High stagnation risk (low dispersion)' :
        risk === 'Elevated' ? 'Elevated stagnation during calm days' :
          risk === 'Low' ? 'Good dispersion, lower accumulation' :
            'Moderate dispersion');
  }

  if (tempEl) {
    tempEl.textContent =
      (typeof displayTemp === 'number') ? displayTemp.toFixed(1) : '–';
  }
  if (windEl) {
    windEl.textContent =
      (typeof windMs === 'number') ? windMs.toFixed(1) : '–';
  }
  if (mixingEl) {
    mixingEl.textContent = mixingHeight ? Math.round(mixingHeight) : '–';
  }
  if (stagEl) {
    stagEl.textContent = risk;
    stagEl.className = 'weather-metric-badge';
    stagEl.classList.add(
      risk === 'High' ? 'badge-high' :
        risk === 'Elevated' ? 'badge-elevated' :
          risk === 'Low' ? 'badge-low' :
            'badge-moderate'
    );
  }

  drawMonthlyChart(city);
}

function getSectorCesiumColor(sector) {
  switch (sector) {
    case 'transport': return Cesium.Color.fromBytes(59, 130, 246);
    case 'industry': return Cesium.Color.fromBytes(239, 68, 68);
    case 'power': return Cesium.Color.fromBytes(234, 179, 8);
    default: return Cesium.Color.GRAY;
  }
}

function getSectorRgbString(sector, alpha = 0.95) {
  let c;
  switch (sector) {
    case 'transport': c = [59, 130, 246]; break;
    case 'industry': c = [239, 68, 68]; break;
    case 'power': c = [234, 179, 8]; break;
    default: c = [148, 163, 184]; break;
  }
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
}

// ---------- Station highlight & focus helpers ----------
function resetEntityStyle(entity) {
  if (!entity || !entity.point) return;

  const id = (entity.id || "").toString();

  let baseSize = 15;
  let outlineWidth = 2;

  if (id.endsWith("_live")) {
    baseSize = 22;
    outlineWidth = 1;
  } else if (id.endsWith("_base")) {
    baseSize = 12;
    outlineWidth = 2;
  }

  entity.point.pixelSize = baseSize;
  entity.point.outlineWidth = outlineWidth;
  entity.point.outlineColor = Cesium.Color.WHITE;

  if (entity.label) {
    entity.label.show = false;
  }
}

function highlightEntity(entity) {
  if (!entity || !entity.point) return;

  const id = (entity.id || "").toString();

  let baseSize = 15;
  if (id.endsWith("_live")) {
    baseSize = 22;
  } else if (id.endsWith("_base")) {
    baseSize = 12;
  }

  entity.point.pixelSize = baseSize * 1.5;
  entity.point.outlineWidth = 4;
  entity.point.outlineColor = Cesium.Color.fromBytes(234, 179, 8);

  if (entity.label) {
    entity.label.show = true;
  }
}

function focusOnStationByName(stationName) {
  if (!viewer || !stationName) return;

  const entity = stationEntityByName[stationName];
  if (!entity) {
    console.warn("No entity found for station:", stationName);
    return;
  }

  if (lastSelectedEntity && lastSelectedEntity !== entity) {
    resetEntityStyle(lastSelectedEntity);
  }

  highlightEntity(entity);
  lastSelectedEntity = entity;

  viewer.flyTo(entity, {
    duration: 1.5,
    offset: new Cesium.HeadingPitchRange(
      viewer.camera.heading,
      Cesium.Math.toRadians(-35),
      5000
    )
  });
}

// ---------- Sector pie chart ----------
function getSelectedStation() {
  const stationName = stationSelect.value;
  if (!stationName) return null;
  return stations.find(s => s.name === stationName) || null;
}

function drawSectorChartForSelection() {
  const container = document.getElementById('sectorChart');
  if (!container) return;

  const station = getSelectedStation();

  if (station) {
    const weights = getSectorWeights(station);
    if (!weights) {
      Plotly.purge('sectorChart');
      container.innerHTML = "<div class='empty-state'>No sector mix inferred for this station.</div>";
      return;
    }

    const labels = ['Transport', 'Industry', 'Power'];
    const rawValues = [
      weights.transport ?? 0,
      weights.industry ?? 0,
      weights.power ?? 0
    ];

    const sum = rawValues.reduce((a, b) => a + b, 0) || 1;
    const values = rawValues.map(v => +(v / sum * 100).toFixed(1));

    const colors = [
      getSectorRgbString('transport', 0.95),
      getSectorRgbString('industry', 0.95),
      getSectorRgbString('power', 0.95)
    ];

    const data = [{
      type: 'pie',
      labels: labels,
      values: values,
      marker: { colors: colors },
      textinfo: 'label+percent',
      hovertemplate: "<b>%{label}</b><br>%{value:.1f} %<extra></extra>",
      hole: 0.35
    }];

    const layout = {
      title: {
        text: station.name,
        font: { size: 13, color: '#e5e7eb' }
      },
      paper_bgcolor: 'rgba(15,23,42,0)',
      plot_bgcolor: 'rgba(15,23,42,0)',
      showlegend: false,
      margin: { t: 40, l: 20, r: 20, b: 20 }
    };

    Plotly.react('sectorChart', data, layout, { displaylogo: false, responsive: true });
    return;
  }

  const city = citySelect.value;
  if (!city) {
    Plotly.purge('sectorChart');
    container.innerHTML = "<div class='empty-state'>Select a city or station to view sector mix.</div>";
    return;
  }

  const totals = aggregateCitySectorWeights(city);
  if (!totals) {
    Plotly.purge('sectorChart');
    container.innerHTML = "<div class='empty-state'>No sector mix inferred for this city.</div>";
    return;
  }

  const labels = ['Transport', 'Industry', 'Power'];
  const rawValues = [
    totals.transport,
    totals.industry,
    totals.power
  ];
  const sum = rawValues.reduce((a, b) => a + b, 0) || 1;
  const values = rawValues.map(v => +(v / sum * 100).toFixed(1));

  const colors = [
    getSectorRgbString('transport', 0.95),
    getSectorRgbString('industry', 0.95),
    getSectorRgbString('power', 0.95)
  ];

  const data = [{
    type: 'pie',
    labels: labels,
    values: values,
    marker: { colors: colors },
    textinfo: 'label+percent',
    hovertemplate: "<b>%{label}</b><br>%{value:.1f} %<extra></extra>",
    hole: 0.35
  }];

  const layout = {
    title: {
      text: `${city} — city-level mix`,
      font: { size: 13, color: '#e5e7eb' }
    },
    paper_bgcolor: 'rgba(15,23,42,0)',
    plot_bgcolor: 'rgba(15,23,42,0)',
    showlegend: false,
    margin: { t: 40, l: 20, r: 20, b: 20 }
  };

  Plotly.react('sectorChart', data, layout, { displaylogo: false, responsive: true });
}

// ------------- Entities + table + chart -------------
function drawEntities() {
  if (!viewer) return;
  viewer.entities.removeAll();

  // reset station -> entity map on each redraw
  Object.keys(stationEntityByName).forEach(k => delete stationEntityByName[k]);

  const city = citySelect.value;

  const filtered = stations.filter(s =>
    s.city === city &&
    s.lat !== undefined && !isNaN(s.lat) &&
    s.lon !== undefined && !isNaN(s.lon) &&
    (
      (displayMode === 'baseline' && (s.co2 !== undefined && !isNaN(s.co2))) ||
      (displayMode === 'live' && (s.co2_estimated !== undefined && !isNaN(s.co2_estimated))) ||
      (displayMode === 'both' && (
        (s.co2 !== undefined && !isNaN(s.co2)) ||
        (s.co2_estimated !== undefined && !isNaN(s.co2_estimated))
      ))
    )
  );

  filtered.forEach(s => {
    const baseExists = (s.co2 !== undefined && !isNaN(s.co2));
    const liveExists = (s.co2_estimated !== undefined && !isNaN(s.co2_estimated));
    const dominantSector = getDominantSector(s);

    const getPointColorForValue = (value) => {
      if (colorMode === 'sector' && dominantSector) {
        return getSectorCesiumColor(dominantSector);
      }
      return getColor(value);
    };

    let mainEntity = null;

    if (displayMode === 'both' && baseExists && liveExists) {
      const baseColor = getPointColorForValue(s.co2);
      const baseEntity = viewer.entities.add({
        id: s.name + "_base",
        name: `${s.name} (baseline)`,
        position: Cesium.Cartesian3.fromDegrees(s.lon, s.lat, 0),
        point: {
          pixelSize: 12,
          color: baseColor,
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 2
        },
        label: {
          text: `${s.name}\n${s.co2.toFixed(1)} ppm (base)`,
          font: "11px 'Segoe UI', sans-serif",
          fillColor: Cesium.Color.WHITE,
          style: Cesium.LabelStyle.FILL,
          outlineWidth: 0,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -20),
          showBackground: true,
          backgroundColor: new Cesium.Color(0, 0, 0, 0.7),
          backgroundPadding: new Cesium.Cartesian2(5, 3),
          show: false
        },
        description:
          `<b>Station:</b> ${s.name}` +
          `<br><b>City:</b> ${s.city}` +
          `<br><b>State:</b> ${s.state}` +
          `<br><b>CO₂ (baseline):</b> ${s.co2?.toFixed ? s.co2.toFixed(1) : s.co2} ppm` +
          `<br><b>CO₂ (live est):</b> ${s.co2_estimated?.toFixed ? s.co2_estimated.toFixed(1) : s.co2_estimated} ppm` +
          (dominantSector ? `<br><b>Dominant sector:</b> ${dominantSector}` : "") +
          `<br><b>Coordinates:</b> ${s.lat}, ${s.lon}` +
          (s.live_ts ? `<br><small>live_ts: ${s.live_ts}</small>` : "")
      });

      const liveColor = getPointColorForValue(s.co2_estimated);
      const liveBytes = liveColor.toBytes();
      const liveEntity = viewer.entities.add({
        id: s.name + "_live",
        name: `${s.name} (live)`,
        position: Cesium.Cartesian3.fromDegrees(s.lon, s.lat, 0),
        point: {
          pixelSize: 22,
          color: Cesium.Color.fromBytes(liveBytes[0], liveBytes[1], liveBytes[2], 180),
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 1
        },
        label: {
          text: `${s.name}\n${s.co2_estimated.toFixed(1)} ppm (live)`,
          font: "11px 'Segoe UI', sans-serif",
          fillColor: Cesium.Color.WHITE,
          style: Cesium.LabelStyle.FILL,
          outlineWidth: 0,
          verticalOrigin: Cesium.VerticalOrigin.TOP,
          pixelOffset: new Cesium.Cartesian2(0, 18),
          showBackground: true,
          backgroundColor: new Cesium.Color(0, 0, 0, 0.7),
          backgroundPadding: new Cesium.Cartesian2(5, 3),
          show: false
        },
        description:
          `<b>Station:</b> ${s.name}` +
          `<br><b>City:</b> ${s.city}` +
          `<br><b>State:</b> ${s.state}` +
          `<br><b>CO₂ (baseline):</b> ${s.co2?.toFixed ? s.co2.toFixed(1) : s.co2} ppm` +
          `<br><b>CO₂ (live est):</b> ${s.co2_estimated?.toFixed ? s.co2_estimated.toFixed(1) : s.co2_estimated} ppm` +
          (dominantSector ? `<br><b>Dominant sector:</b> ${dominantSector}` : "") +
          `<br><b>Coordinates:</b> ${s.lat}, ${s.lon}` +
          (s.live_ts ? `<br><small>live_ts: ${s.live_ts}</small>` : "")
      });

      mainEntity = liveEntity;

    } else {
      const displayCO2 = getDisplayValue(s);
      if (displayCO2 === null) return;

      const isEstimated =
        !(s.co2 !== undefined && !isNaN(s.co2)) &&
        (s.co2_estimated !== undefined && !isNaN(s.co2_estimated));

      const color = getPointColorForValue(displayCO2);

      const singleEntity = viewer.entities.add({
        id: s.name,
        name: `${s.name}`,
        position: Cesium.Cartesian3.fromDegrees(s.lon, s.lat, 0),
        point: {
          pixelSize: 15,
          color: color,
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 2
        },
        label: {
          text: `${s.name}\n${displayCO2.toFixed ? displayCO2.toFixed(1) : displayCO2} ppm${isEstimated ? " (est)" : ""}`,
          font: "12px 'Segoe UI', sans-serif",
          fillColor: Cesium.Color.WHITE,
          style: Cesium.LabelStyle.FILL,
          outlineWidth: 0,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -24),
          showBackground: true,
          backgroundColor: new Cesium.Color(0, 0, 0, 0.7),
          backgroundPadding: new Cesium.Cartesian2(6, 4),
          show: false
        },
        description:
          `<b>Station:</b> ${s.name}` +
          `<br><b>City:</b> ${s.city}` +
          `<br><b>State:</b> ${s.state}` +
          `<br><b>CO₂ (baseline):</b> ${s.co2 ?? "N/A"} ppm` +
          `<br><b>CO₂ (live est):</b> ${s.co2_estimated ?? "N/A"} ppm` +
          (dominantSector ? `<br><b>Dominant sector:</b> ${dominantSector}` : "") +
          `<br><b>Coordinates:</b> ${s.lat}, ${s.lon}` +
          (s.live_ts ? `<br><small>live_ts: ${s.live_ts}</small>` : "")
      });

      mainEntity = singleEntity;
    }

    if (mainEntity) {
      stationEntityByName[s.name] = mainEntity;
    }
  });

  if (filtered.length > 0) {
    viewer.zoomTo(viewer.entities);
  }

  updateSummary(filtered);
  drawTable(filtered);
  drawChart(filtered);
}

// 🔹 Remove old dispersion tiles
function clearDispersionLayer() {
  if (!viewer) return;
  dispersionEntities.forEach(ent => viewer.entities.remove(ent));
  dispersionEntities = [];
}

// 🔹 Show/hide all dispersion tiles (used by toggle)
function updateDispersionVisibility() {
  dispersionEntities.forEach(ent => {
    ent.show = dispersionEnabled;
    if (ent.label) ent.label.show = false;
  });
}

// 🔹 Build Cesium rectangles from dispersionCells
function drawDispersionLayer() {
  if (!viewer) return;

  clearDispersionLayer();
  if (!dispersionEnabled || !dispersionCells.length) return;

  dispersionCells.forEach((cell, idx) => {
    const lat = cell.lat;
    const lon = cell.lon;
    if (lat == null || lon == null) return;

    const sizeM = cell.size_m || cell.size || 2000;

    let strength = null;
    if (typeof cell.strength === "number") {
      strength = cell.strength;
    } else if (typeof cell.score === "number") {
      strength = cell.score;
    } else if (typeof cell.co2 === "number") {
      strength = (cell.co2 - 400) / 200;
    }

    if (!isFinite(strength)) strength = 0;
    strength = Math.max(0, Math.min(1, strength));

    const metersPerDegLat = 111_000;
    const metersPerDegLon = 111_000 * Math.cos(lat * Math.PI / 180);
    const dLat = (sizeM / 2) / metersPerDegLat;
    const dLon = (sizeM / 2) / metersPerDegLon;

    const color = getDispersionCesiumColor(strength);

    const rectEntity = viewer.entities.add({
      id: `dispersion_cell_${idx}`,
      rectangle: {
        coordinates: Cesium.Rectangle.fromDegrees(
          lon - dLon, lat - dLat,
          lon + dLon, lat + dLat
        ),
        material: color,
        classificationType: Cesium.ClassificationType.BOTH
      },
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 20),
      label: {
        text: `Dispersion score: ${strength.toFixed(2)}`,
        font: "11px 'Segoe UI', sans-serif",
        fillColor: Cesium.Color.WHITE,
        style: Cesium.LabelStyle.FILL,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -12),
        showBackground: true,
        backgroundColor: new Cesium.Color(15 / 255, 23 / 255, 42 / 255, 0.85),
        backgroundPadding: new Cesium.Cartesian2(4, 2),
        show: false
      },
      description:
        `<b>Dispersion score:</b> ${strength.toFixed(2)}` +
        `<br><b>Cell size:</b> ~${Math.round(sizeM)} m`
    });

    dispersionEntities.push(rectEntity);
  });

  updateDispersionVisibility();
}

function drawTable(data) {
  let html = `
    <table>
      <thead>
        <tr>
          <th>Station</th>
          <th>City</th>
          <th>State</th>
          ${displayMode === 'baseline'
      ? '<th>CO₂ (baseline) ppm</th>'
      : displayMode === 'live'
        ? '<th>CO₂ (live est) ppm</th>'
        : '<th>CO₂ (baseline) ppm</th><th>CO₂ (live est) ppm</th>'
    }
        </tr>
      </thead>
      <tbody>
  `;

  data.forEach(s => {
    html += `<tr><td>${s.name}</td><td>${s.city}</td><td>${s.state}</td>`;
    if (displayMode === 'baseline') {
      const base = (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : "N/A";
      html += `<td>${base}</td>`;
    } else if (displayMode === 'live') {
      const live = (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : "N/A";
      html += `<td>${live}</td>`;
    } else {
      const base = (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : "N/A";
      const live = (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : "N/A";
      html += `<td>${base}</td><td>${live}</td>`;
    }
    html += `</tr>`;
  });

  html += `</tbody></table>`;
  document.getElementById('table').innerHTML = html;
}

// ---------- Heatmap helper ----------
function updateHeatmapFromChart(names, baselineY, liveY) {
  const heatmapDiv = document.getElementById('heatmapChart');
  if (!heatmapDiv) return;

  const x = ['Baseline', 'Live'];
  const y = names || [];

  const z = (names || []).map((_, idx) => {
    const base = baselineY && baselineY[idx] != null && !isNaN(baselineY[idx]) ? baselineY[idx] : null;
    const live = liveY && liveY[idx] != null && !isNaN(liveY[idx]) ? liveY[idx] : null;
    return [base, live];
  });

  const data = [{
    x,
    y,
    z,
    type: 'heatmap',
    colorscale: 'RdYlGn_r',
    colorbar: { title: 'CO₂ (ppm)' },
    hovertemplate:
      'Station: %{y}<br>' +
      'Series: %{x}<br>' +
      'CO₂: %{z:.1f} ppm<extra></extra>'
  }];

  const layout = {
    margin: { l: 80, r: 20, t: 20, b: 40 },
    paper_bgcolor: 'rgba(15,23,42,0)',
    plot_bgcolor: 'rgba(15,23,42,0.85)',
    xaxis: { tickfont: { size: 11, color: '#9ca3af' } },
    yaxis: { tickfont: { size: 11, color: '#9ca3af' }, automargin: true }
  };

  Plotly.react('heatmapChart', data, layout, { responsive: true, displaylogo: false });
}

function drawChart(data) {
  const names = data.map(s => s.name);

  // Baseline only
  if (displayMode === 'baseline') {
    const y = data.map(s =>
      (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : null
    );

    const colors = data.map(s => {
      if (s.co2 !== undefined && !isNaN(s.co2)) {
        const c = getColor(s.co2).toBytes();
        return `rgba(${c[0]},${c[1]},${c[2]},0.95)`;
      }
      return 'rgba(0,0,0,0)';
    });

    const trace = {
      x: names, y: y, type: 'bar', name: 'Baseline CO₂',
      marker: { color: colors, line: { width: 1, color: 'rgba(15,23,42,1)' } },
      hovertemplate: "<b>%{x}</b><br>Baseline CO₂: %{y} ppm<extra></extra>"
    };
    const layout = {
      title: {
        text: `CO₂ Levels (${citySelect.value || "Select a City"})`,
        font: { size: 14, color: '#e5e7eb' },
        x: 0.02, y: 0.97
      },
      margin: { t: 40, l: 40, r: 20, b: 120 },
      paper_bgcolor: 'rgba(15,23,42,0)',
      plot_bgcolor: 'rgba(15,23,42,0.85)',
      xaxis: { tickangle: -40, tickfont: { size: 11, color: '#9ca3af' } },
      yaxis: { title: 'ppm', tickfont: { size: 11, color: '#9ca3af' } }
    };
    Plotly.react('barChart', [trace], layout, { responsive: true, displaylogo: false });
    updateHeatmapFromChart(names, y, null);
    return;
  }

  // Live only
  if (displayMode === 'live') {
    const y = data.map(s =>
      (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : null
    );
    const colors = data.map(s => {
      if (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) {
        const c = getColor(s.co2_estimated).toBytes();
        return `rgba(${c[0]},${c[1]},${c[2]},0.9)`;
      }
      return 'rgba(0,0,0,0)';
    });
    const trace = {
      x: names, y: y, type: 'bar', name: 'Live Estimate',
      marker: { color: colors, line: { width: 1, color: 'rgba(15,23,42,1)' } },
      hovertemplate: "<b>%{x}</b><br>Live estimate: %{y} ppm<extra></extra>"
    };
    const layout = {
      title: {
        text: `CO₂ Levels (${citySelect.value || "Select a City"})`,
        font: { size: 14, color: '#e5e7eb' },
        x: 0.02, y: 0.97
      },
      margin: { t: 40, l: 40, r: 20, b: 120 },
      paper_bgcolor: 'rgba(15,23,42,0)',
      plot_bgcolor: 'rgba(15,23,42,0.85)',
      xaxis: { tickangle: -40, tickfont: { size: 11, color: '#9ca3af' } },
      yaxis: { title: 'ppm', tickfont: { size: 11, color: '#9ca3af' } }
    };
    Plotly.react('barChart', [trace], layout, { responsive: true, displaylogo: false });
    updateHeatmapFromChart(names, null, y);
    return;
  }

  // Both
  const baselineY = data.map(s =>
    (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : null
  );
  const liveY = data.map(s =>
    (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : null
  );

  const baselineColors = data.map(s => {
    if (s.co2 !== undefined && !isNaN(s.co2)) {
      const c = getColor(s.co2).toBytes();
      return `rgba(${c[0]},${c[1]},${c[2]},0.95)`;
    }
    return 'rgba(0,0,0,0)';
  });

  const liveColors = data.map(s => {
    if (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) {
      const c = getColor(s.co2_estimated).toBytes();
      return `rgba(${c[0]},${c[1]},${c[2]},0.7)`;
    }
    return 'rgba(0,0,0,0)';
  });

  const baselineTrace = {
    x: names, y: baselineY, name: 'Baseline CO₂', type: 'bar',
    marker: { color: baselineColors, line: { width: 1, color: 'rgba(15,23,42,1)' } },
    hovertemplate: "<b>%{x}</b><br>Baseline CO₂: %{y} ppm<extra></extra>"
  };
  const liveTrace = {
    x: names, y: liveY, name: 'Live Estimate', type: 'bar',
    marker: { color: liveColors, line: { width: 1, color: 'rgba(10,10,10,0.5)' } },
    hovertemplate: "<b>%{x}</b><br>Live estimate: %{y} ppm<extra></extra>"
  };

  const layout = {
    title: {
      text: `CO₂ Levels (${citySelect.value || "Select a City"})`,
      font: { size: 14, color: '#e5e7eb' },
      x: 0.02, y: 0.97
    },
    barmode: 'group',
    margin: { t: 40, l: 40, r: 20, b: 120 },
    paper_bgcolor: 'rgba(15,23,42,0)',
    plot_bgcolor: 'rgba(15,23,42,0.85)',
    xaxis: { tickangle: -40, tickfont: { size: 11, color: '#9ca3af' } },
    yaxis: { title: 'ppm', tickfont: { size: 11, color: '#9ca3af' } }
  };

  const traces = [];
  if (baselineY.some(v => v !== null && v !== undefined)) traces.push(baselineTrace);
  if (liveY.some(v => v !== null && v !== undefined)) traces.push(liveTrace);

  Plotly.react('barChart', traces, layout, { responsive: true, displaylogo: false });
  updateHeatmapFromChart(names, baselineY, liveY);
}

// ---------- Hover labels (show on mouse-over only) ----------
let lastHoveredEntity = null;

function setupHoverLabels() {
  if (!viewer) return;
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

  handler.setInputAction((movement) => {
    const picked = viewer.scene.pick(movement.endPosition);

    // Hide previous hover label, but not the selected one
    if (lastHoveredEntity &&
      lastHoveredEntity !== lastSelectedEntity &&
      lastHoveredEntity.label) {
      lastHoveredEntity.label.show = false;
    }
    lastHoveredEntity = null;

    if (Cesium.defined(picked) && picked.id && picked.id.label) {
      picked.id.label.show = true;
      lastHoveredEntity = picked.id;
    }
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
}

// ---------- Zoom-based label styling ----------
function setupZoomLabelStyling() {
  if (!viewer) return;

  viewer.camera.changed.addEventListener(() => {
    const height = viewer.camera.positionCartographic.height;
    const closeThreshold = 300000.0;

    const isClose = height < closeThreshold;

    const allEntities = viewer.entities.values;
    for (let i = 0; i < allEntities.length; i++) {
      const ent = allEntities[i];
      if (!ent.label) continue;

      ent.label.fillColor = Cesium.Color.WHITE;
      ent.label.showBackground = true;
      ent.label.backgroundColor = new Cesium.Color(0, 0, 0, 0.7);

      ent.label.font = isClose
        ? "10px 'Segoe UI', sans-serif"
        : "13px 'Segoe UI', sans-serif";
    }
  });
}

// ----------- Event wiring -----------

const displaySeg = document.getElementById('displayMode');
if (displaySeg) {
  const labels = Array.from(displaySeg.querySelectorAll('label'));
  labels.forEach(lbl => {
    const input = lbl.querySelector('input[type=radio]');
    if (!input) return;
    if (input.value === displayMode) lbl.classList.add('selected');
    input.addEventListener('change', () => {
      displayMode = input.value;
      labels.forEach(l => l.classList.remove('selected'));
      lbl.classList.add('selected');
      rebuildCityDropdown();
      computeCityRecommendations();
      renderCityRecommendations();
      updateStations();
      drawEntities();
      drawSectorChartForSelection();
    });
  });
}

// ---------- Play Mode Toggle ----------
if (playToggleBtn && playPanel) {
  playToggleBtn.addEventListener("click", () => {
    playModeActive = !playModeActive;

    // Show / hide play panel
    playPanel.classList.toggle("hidden", !playModeActive);

    // Button label
    playToggleBtn.textContent = playModeActive ? "■ Exit Play" : "▶ Play";

    // Populate play inputs when activated
    if (playModeActive) {
      populatePlayPanelFromSelection();
      updateEfficiencySuggestion(); // reflect play overrides if any
    }
  });
}


// ---------- Play Apply button ----------
if (playApplyBtn) {
  playApplyBtn.addEventListener("click", () => {
    if (!playModeActive) return;

    const station = getSelectedStation();
    if (!station) return;

    const method = methodSelect.value;
    const eff = autoSuggestEfficiency(station, method);

    setEfficiency(eff);   // 🔴 THIS keeps both panels in sync

    drawEntities();
  });

}

if (citySearchInput) {
  citySearchInput.addEventListener('input', () => {
    filterCitiesBySearch();
    updateCitySuggestions();
  });

  citySearchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const firstSuggestion = citySuggestionsEl
        ? citySuggestionsEl.querySelector('.city-suggestion-item')
        : null;

      if (firstSuggestion) {
        firstSuggestion.click();
        e.preventDefault();
      }
    } else if (e.key === 'Escape') {
      citySearchInput.value = "";
      if (citySuggestionsEl) citySuggestionsEl.style.display = "none";
      filterCitiesBySearch();
    }
  });
}

const colorModeSelect = document.getElementById('colorMode');
if (colorModeSelect) {
  colorModeSelect.onchange = () => {
    colorMode = colorModeSelect.value;
    drawEntities();
  };
}

// 🔹 Dispersion toggle (checkbox or button)
const dispersionToggle = document.getElementById("dispersionToggle");

if (dispersionToggle) {
  if (dispersionToggle.type === "checkbox") {
    dispersionEnabled = dispersionToggle.checked;

    dispersionToggle.addEventListener("change", () => {
      dispersionEnabled = dispersionToggle.checked;
      updateDispersionVisibility();

      if (dispersionEnabled) {
        const city = citySelect.value || "";
        if (city) loadDispersionForCity(city);
      }
    });
  } else {
    dispersionToggle.addEventListener("click", () => {
      dispersionEnabled = !dispersionEnabled;
      dispersionToggle.textContent = dispersionEnabled
        ? "Hide dispersion"
        : "Show dispersion";
      updateDispersionVisibility();

      if (dispersionEnabled) {
        const city = citySelect.value || "";
        if (city) loadDispersionForCity(city);
      }
    });
  }
}

// --- Weather month selector: connect weather + baseline CO₂ snapshot ---
if (weatherMonthEl) {
  weatherMonthEl.addEventListener('change', async () => {
    const selectedCity = citySelect.value || null;
    const rawValue = weatherMonthEl.value;

    const today = new Date();
    const todayMonth = today.getMonth() + 1;
    const todayDay = today.getDate();

    let monthForBaseline;
    if (rawValue === "auto") {
      monthForBaseline = todayMonth;
    } else {
      monthForBaseline = parseInt(rawValue, 10);
      if (Number.isNaN(monthForBaseline)) {
        monthForBaseline = todayMonth;
      }
    }

    const dayForBaseline = todayDay;

    try {
      const resp = await fetch(`${BASE_URL}/set_month_baseline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          month: monthForBaseline,
          day: dayForBaseline
        })
      });

      const result = await resp.json();
      if (!resp.ok || !result.success) {
        console.error("Failed to update month baseline:", result);
      } else {
        await fetchStations();
        drawEntities();
        drawSectorChartForSelection();
      }
    } catch (err) {
      console.error("Error calling /set_month_baseline:", err);
    }

    if (selectedCity && currentWeatherRaw) {
      applyWeatherScenario(rawValue);
    }
  });
}

citySelect.onchange = () => {
  if (citySearchInput) {
    citySearchInput.value = citySelect.value || "";
    if (citySuggestionsEl) {
      citySuggestionsEl.style.display = "none";
    }
    suggestionActiveIndex = -1;
  }

  updateStations();
  drawEntities();
  drawSectorChartForSelection();
  updateCityWeather();
  const city = citySelect.value || "";
  loadDispersionForCity(city);
};

stationSelect.onchange = () => {
  drawEntities();
  updateEfficiencySuggestion();
  drawSectorChartForSelection();

  if (playModeActive) {
    populatePlayPanelFromSelection();
  }

  const station = getSelectedStation();
  if (station) {
    focusOnStationByName(station.name);
  }
};

methodSelect.onchange = () => {
  updateEfficiencySuggestion();
};

// ---------- Reporting buttons ----------
if (startReportBtn) {
  startReportBtn.onclick = () => {
    reportingActive = true;
    reportLog = [];
    showToast(
      "All subsequent interventions will be captured into a sharable report.",
      { type: "success", title: "Reporting started" }
    );

    if (downloadReportBtn) downloadReportBtn.disabled = false;
    const shareBtn = document.getElementById('shareReportBtn');
    if (shareBtn) shareBtn.disabled = false;
  };
}

async function buildReportBlob() {
  if (!reportLog.length) {
    showToast(
      "Click ‘Start Reporting’ and run at least one intervention before generating a report.",
      { type: "warning", title: "No interventions logged" }
    );
    throw new Error("NO_LOG");
  }

  const scope = reportScopeSelect ? reportScopeSelect.value : 'session';
  const currentCity = citySelect.value || null;

  let scopedLog = reportLog;
  if (scope === 'city' && currentCity) {
    scopedLog = reportLog.filter(e => e.city === currentCity);
    if (!scopedLog.length) {
      showToast(
        `No interventions have been logged for ${currentCity} in this session.`,
        { type: "info", title: "Nothing to report for this city" }
      );
      throw new Error("NO_CITY_LOG");
    }
  }

  const kpis = computeReportKpis(scopedLog) || {};

  const charts = [];

  if (document.getElementById('barChart')) {
    try {
      const url = await Plotly.toImage('barChart', { format: 'png', width: 900, height: 450 });
      charts.push({
        id: 'barChart',
        title: 'CO₂ Levels — Baseline vs Live',
        image: url.split(',')[1]
      });
    } catch (e) {
      console.log("Could not capture bar chart:", e);
    }
  }

  if (document.getElementById('sectorChart')) {
    try {
      const url = await Plotly.toImage('sectorChart', { format: 'png', width: 700, height: 400 });
      charts.push({
        id: 'sectorChart',
        title: 'Sectoral Emission Mix',
        image: url.split(',')[1]
      });
    } catch (e) {
      console.log("Could not capture sector chart:", e);
    }
  }

  const res = await fetch(`${BASE_URL}/generate_report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scope,
      city: currentCity,
      display_mode: displayMode,
      log: scopedLog,
      kpis,
      charts
    })
  });

  if (!res.ok) {
    const txt = await res.text();
    console.error("Report generation failed:", txt);
    showToast(
      "Backend could not generate the PDF. Try again in a moment.",
      { type: "error", title: "Report failed" }
    );
    throw new Error("REPORT_FAILED");
  }

  const blob = await res.blob();
  lastReportBlob = blob;
  return blob;
}

if (downloadReportBtn) {
  downloadReportBtn.onclick = async () => {
    try {
      const blob = await buildReportBlob();

      const shareBtn = document.getElementById('shareReportBtn');
      if (shareBtn) shareBtn.disabled = false;

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = "CO2_Digital_Twin_Report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (err && err.message) {
        console.log("Download aborted:", err.message);
      }
    }
  };
}

const shareReportBtn = document.getElementById('shareReportBtn');

if (shareReportBtn) {
  shareReportBtn.onclick = async () => {
    try {
      const blob = lastReportBlob || await buildReportBlob();

      if (navigator.share && navigator.canShare) {
        const file = new File([blob], "CO2_Digital_Twin_Report.pdf", {
          type: "application/pdf"
        });

        if (navigator.canShare({ files: [file] })) {
          await navigator.share({
            title: "CO₂ Digital Twin Report",
            text: "CO₂ planning scenario from the India CO₂ Digital Twin tool.",
            files: [file]
          });
          return;
        }
      }

      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      alert("Direct sharing is not supported in this browser. The report has been opened in a new tab – you can download or share it from there.");
    } catch (err) {
      if (err && err.name === 'AbortError') {
        return;
      }
      console.error("Share failed:", err);
    }
  };
}

// ---------- Apply intervention ----------
document.getElementById('applyBtn').onclick = async () => {
  const stationName = stationSelect.value;
  const cityName = citySelect.value;
  const interventionName = methodSelect.value;
  const efficiency = parseFloat(reductionInput.value);

  if (!stationName || isNaN(efficiency)) {
    showToast(
      "Pick a station and set an efficiency value before applying an intervention.",
      { type: "warning", title: "Missing inputs" }
    );
    return;
  }

  const selectedStation = stations.find(s => s.name === stationName);
  const integrityToken = selectedStation ? selectedStation.integrity_token : null;

  let target = displayMode;
  if (displayMode === 'both') target = 'baseline';

  const playSnapshot = capturePlaySnapshotForReport();

  const res = await fetch(`${BASE_URL}/apply_intervention`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      station: stationName,
      city: cityName,
      intervention: interventionName,
      efficiency: efficiency,
      integrity_token: integrityToken,
      target: target,
      play_snapshot: playSnapshot
    })
  });

  const result = await res.json();
  if (!result.success) {
    showToast(
      result.error || "Intervention could not be applied.",
      { type: "error", title: "Intervention failed" }
    );
    return;
  }

  logIntervention({
    city: cityName,
    station: stationName,
    method: interventionName,
    base_co2: result.base_co2,
    co2_after: result.co2_after,
    reduction: result.base_co2 - result.co2_after,
    efficiency: efficiency,
    play_mode: !!playModeActive,
    env: {
      ndvi: (function () {
        if (playModeActive && playNdviInput && playNdviInput.value !== "") {
          const v = parseFloat(playNdviInput.value);
          return isNaN(v) ? null : v;
        }
        if (selectedStation && typeof selectedStation.ndvi === "number") {
          return selectedStation.ndvi;
        }
        return null;
      })(),
      albedo: (function () {
        if (playModeActive && playAlbedoInput && playAlbedoInput.value !== "") {
          const v = parseFloat(playAlbedoInput.value);
          return isNaN(v) ? null : v;
        }
        if (selectedStation && typeof selectedStation.albedo === "number") {
          return selectedStation.albedo;
        }
        return null;
      })(),
      lulc: (function () {
        if (playModeActive && playLulcInput && playLulcInput.value.trim() !== "") {
          return playLulcInput.value.trim();
        }
        if (selectedStation && selectedStation.lulc) {
          return selectedStation.lulc;
        }
        return null;
      })()
    },
    weather: (function () {
      if (!playModeActive) return null;

      const temp = playTempInput ? parseFloat(playTempInput.value) : NaN;
      const windMs = playWindInput ? parseFloat(playWindInput.value) : NaN;
      const mixing = playMixingInput ? parseFloat(playMixingInput.value) : NaN;
      const stag = playStagInput ? (playStagInput.value || "").trim() : "";

      return {
        temp: isNaN(temp) ? null : temp,
        wind_ms: isNaN(windMs) ? null : windMs,
        mixing_height: isNaN(mixing) ? null : mixing,
        stagnation: stag || null
      };
    })()
  });

  stations = stations.map(s => {
    if (s.name !== stationName) return s;

    const updated = { ...s };

    if (result.applied_to === "baseline") {
      updated.co2 = result.co2_after;
    } else if (result.applied_to === "live") {
      updated.co2_estimated = result.co2_after;
    } else {
      updated.co2 = result.co2_after;
    }

    if (result.integrity_token) {
      updated.integrity_token = result.integrity_token;
    }

    return updated;
  });

  drawEntities();
  drawSectorChartForSelection();
  const currentCity = citySelect.value || "";
  if (currentCity) {
    loadDispersionForCity(currentCity);
  }

  if (stationName) {
    focusOnStationByName(stationName);
  }
};

// ----------- Load -----------
window.onload = async () => {
  await initCesium();
  await fetchStations();
  drawSectorChartForSelection();
  drawMonthlyChart(null);
  setupHoverLabels();
  setupZoomLabelStyling();
};

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./service-worker.js")
      .then(reg => console.log("Service Worker registered:", reg))
      .catch(err => console.log("SW registration failed:", err));
  });
}

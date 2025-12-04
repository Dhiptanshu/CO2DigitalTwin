Cesium.Ion.defaultAccessToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJiNjFjNDkyOS1hZmZlLTQ0YmUtODViOS1lZDUxMDExYWIwZTciLCJpZCI6MzQ2Mjc4LCJpYXQiOjE3NTkzMTY3NDd9.awxOsdnDLokLuS9p-NWVaIJSGk8u5r46bjxz1jh2pi8';

let stations = [];
let viewer;

// Base URL for API calls
const BASE_URL = window.location.origin;

// Dropdowns
const citySelect = document.getElementById('citySelect');
const citySearchInput = document.getElementById('citySearch');
const cityRecommendationsEl = document.getElementById('cityRecommendations');
const citySuggestionsEl = document.getElementById('citySearchSuggestions');
const stationSelect = document.getElementById('stationSelect');
const methodSelect = document.getElementById('methodSelect');
const reductionInput = document.getElementById('reductionInput');


// Frontend LULC mapping (for suggestions)
const LULC_FACTORS = {
  "Urban": 2.0, "Industrial": 2.5, "Residential": 1.8, "Campus": 1.5,
  "Rural": 1.0, "Mixed Urban": 2.0, "Industrial/Residential": 2.2,
  "Urban Vegetation": 1.3, "Airport": 2.5, "Sports Complex": 1.5,
  "Government": 1.8, "Mixed Forest": 1.0
};

// Sector weights inferred from LULC (for map + pie)
const SECTOR_WEIGHTS = {
  "Urban":               { transport: 0.6,  industry: 0.3,  power: 0.1 },
  "Industrial":          { transport: 0.15, industry: 0.7,  power: 0.15 },
  "Industrial/Residential": { transport: 0.3, industry: 0.5, power: 0.2 },
  "Residential":         { transport: 0.5,  industry: 0.2,  power: 0.3 },
  "Mixed Urban":         { transport: 0.5,  industry: 0.35, power: 0.15 },
  "Campus":              { transport: 0.4,  industry: 0.1,  power: 0.5 },
  "Government":          { transport: 0.4,  industry: 0.2,  power: 0.4 },
  "Airport":             { transport: 0.85, industry: 0.1,  power: 0.05 },
  "Sports Complex":      { transport: 0.6,  industry: 0.1,  power: 0.3 },
  "Urban Vegetation":    { transport: 0.4,  industry: 0.1,  power: 0.5 },
  "Mixed Forest":        { transport: 0.1,  industry: 0.05, power: 0.05 },
  "Rural":               { transport: 0.3,  industry: 0.1,  power: 0.6 }
};

// ---------------- Efficiency suggestion ----------------
function autoSuggestEfficiency(station, method) {
  if (!station) return 20;
  const co2 = station.co2 ?? station.co2_estimated;
  if (co2 === undefined || isNaN(co2)) return 20;

  let severity = 0;
  if (co2 >= 430 && co2 <= 450) severity = 1;
  else if (co2 > 450) severity = 2;

  const ndvi = (typeof station.ndvi === "number") ? Math.max(0, Math.min(station.ndvi,1)) : 0.3;
  const lulcFactor = LULC_FACTORS[station.lulc] || 1.5;

  let methodBoost = 0;
  if (method === "Roadside Capture Unit") methodBoost = 8;
  else if (method === "Biofilter") methodBoost = 6;
  else if (method === "Vertical Garden") methodBoost = 4;

  let eff = 10 + severity*8 + (1-ndvi)*10 + (lulcFactor-1)*4 + methodBoost;
  eff = Math.round(Math.max(5, Math.min(50, eff)));
  return eff;
}

function updateEfficiencySuggestion() {
  const stationName = stationSelect.value;
  if (!stationName) return;
  const station = stations.find(s => s.name === stationName);
  const method = methodSelect.value;
  reductionInput.value = autoSuggestEfficiency(station, method);
}

// --------------- Cesium init ---------------
async function initCesium(){
  viewer = new Cesium.Viewer('cesiumContainer',{
      terrainProvider: await Cesium.CesiumTerrainProvider.fromIonAssetId(1),
      imageryProvider: new Cesium.IonImageryProvider({assetId:2}),
      timeline:false, animation:false, infoBox:false, selectionIndicator:false
  });
  viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(78.9629, 20.5937, 2500000)
  });
}

function getColor(co2){
  if(co2 === undefined || isNaN(co2)) return Cesium.Color.GRAY;
  if(co2 <= 420) return Cesium.Color.fromBytes(34, 197, 94);
  else if(co2 <= 450) return Cesium.Color.fromBytes(251, 191, 36);
  else return Cesium.Color.fromBytes(248, 113, 113);
}

// ---------- Display mode handling ----------
let displayMode = 'baseline'; // 'baseline' | 'live' | 'both'
let colorMode = 'co2';        // 'co2' | 'sector'
let recommendedCities = [];
let availableCities = [];
let suggestionActiveIndex = -1;


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

  const cities = Array.from(citySet).sort((a,b)=> a.localeCompare(b));
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

  // Show/hide options based on search text
  Array.from(citySelect.options).forEach((opt, idx) => {
    // Always keep placeholder visible
    if (idx === 0) {
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
      // fallback – shouldn’t really happen due to filter
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
function updateStations(){
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

  filtered.forEach(s=>{
    const label = (s.co2 === undefined || isNaN(s.co2)) ? `${s.name} (est)` : s.name;
    stationSelect.add(new Option(label, s.name));
  });
}

function computeCityRecommendations() {
  const cityMax = {};

  stations.forEach(s => {
    if (!s.city) return;
    // Use display value (respects displayMode) or fallback to baseline/live
    const v = getDisplayValue(s) ??
              (typeof s.co2 === 'number' ? s.co2 : null) ??
              (typeof s.co2_estimated === 'number' ? s.co2_estimated : null);

    if (v == null || isNaN(v)) return;
    if (!cityMax[s.city] || v > cityMax[s.city]) {
      cityMax[s.city] = v; // track worst hotspot per city
    }
  });

  recommendedCities = Object.entries(cityMax)
    .sort((a, b) => b[1] - a[1]) // highest CO₂ first
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
async function fetchStations(){
  const res = await fetch(`${BASE_URL}/get_stations`);
  const rawStations = await res.json();

  stations = rawStations.map(s => ({...s, baseline_co2: s.co2}));

  rebuildCityDropdown();
  computeCityRecommendations();
  renderCityRecommendations();
  updateStations();
  drawEntities();
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
    return {...s, displayCO2: display};
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
    totals.industry  += (w.industry  ?? 0) * weight;
    totals.power     += (w.power     ?? 0) * weight;

    any = true;
  });

  if (!any) return null;
  return totals;
}

function getSectorCesiumColor(sector) {
  switch (sector) {
    case 'transport': return Cesium.Color.fromBytes(59, 130, 246);
    case 'industry':  return Cesium.Color.fromBytes(239, 68, 68);
    case 'power':     return Cesium.Color.fromBytes(234, 179, 8);
    default:          return Cesium.Color.GRAY;
  }
}

function getSectorRgbString(sector, alpha = 0.95) {
  let c;
  switch (sector) {
    case 'transport': c = [59, 130, 246]; break;
    case 'industry':  c = [239, 68, 68]; break;
    case 'power':     c = [234, 179, 8]; break;
    default:          c = [148, 163, 184]; break;
  }
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
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

    const sum = rawValues.reduce((a,b) => a + b, 0) || 1;
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

    Plotly.react('sectorChart', data, layout, {displaylogo:false, responsive:true});
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
  const sum = rawValues.reduce((a,b) => a + b, 0) || 1;
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

  Plotly.react('sectorChart', data, layout, {displaylogo:false, responsive:true});
}

// ------------- Entities + table + chart -------------
function drawEntities(){
  if (!viewer) return;
  viewer.entities.removeAll();

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

    if (displayMode === 'both' && baseExists && liveExists) {
      const baseColor = getPointColorForValue(s.co2);
      viewer.entities.add({
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
          font: "12px 'Segoe UI'",
          fillColor: Cesium.Color.WHITE,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -20)
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
      viewer.entities.add({
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
          font: "12px 'Segoe UI'",
          fillColor: Cesium.Color.WHITE,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
          verticalOrigin: Cesium.VerticalOrigin.TOP,
          pixelOffset: new Cesium.Cartesian2(0, 18)
        }
      });

    } else {
      const displayCO2 = getDisplayValue(s);
      if (displayCO2 === null) return;

      const isEstimated =
        !(s.co2 !== undefined && !isNaN(s.co2)) &&
        (s.co2_estimated !== undefined && !isNaN(s.co2_estimated));

      const color = getPointColorForValue(displayCO2);

      viewer.entities.add({
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
          font: "13px 'Segoe UI', sans-serif",
          fillColor: Cesium.Color.WHITE,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -24),
          showBackground: true,
          backgroundColor: new Cesium.Color(0.08,0.09,0.12,0.7),
          backgroundPadding: new Cesium.Cartesian2(6,4)
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
    }
  });

  if (filtered.length > 0) {
    viewer.zoomTo(viewer.entities);
  }

  updateSummary(filtered);
  drawTable(filtered);
  drawChart(filtered);
}

function drawTable(data){
  let html = "<table><thead><tr><th>Station</th><th>City</th><th>State</th>";
  if (displayMode === 'baseline') html += "<th>CO₂ (baseline) ppm</th>";
  else if (displayMode === 'live') html += "<th>CO₂ (live est) ppm</th>";
  else html += "<th>CO₂ (baseline) ppm</th><th>CO₂ (live est) ppm</th>";
  html += "</tr></thead><tbody>";

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

  html += "</tbody></table>";
  document.getElementById('table').innerHTML = html;
}

function drawChart(data){
  const names = data.map(s => s.name);

  if (displayMode === 'baseline') {
    const y = data.map(s => (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : null);
    const colors = data.map(s => {
      if (s.co2 !== undefined && !isNaN(s.co2)) {
        const c = getColor(s.co2).toBytes();
        return `rgba(${c[0]},${c[1]},${c[2]},0.95)`;
      }
      return 'rgba(0,0,0,0)';
    });
    const trace = {
      x: names, y: y, type:'bar', name:'Baseline CO₂',
      marker:{color: colors, line:{width:1,color:'rgba(15,23,42,1)'}},
      hovertemplate:"<b>%{x}</b><br>Baseline CO₂: %{y} ppm<extra></extra>"
    };
    const layout = {
      title: { text: `CO₂ Levels (${citySelect.value || "Select a City"})`, font:{size:14,color:'#e5e7eb'}, x:0.02, y:0.97 },
      margin:{t:40,l:40,r:20,b:120},
      paper_bgcolor:'rgba(15,23,42,0)',
      plot_bgcolor:'rgba(15,23,42,0.85)',
      xaxis:{tickangle:-40,tickfont:{size:11,color:'#9ca3af'}},
      yaxis:{title:'ppm', tickfont:{size:11,color:'#9ca3af'}}
    };
    Plotly.react('barChart',[trace],layout,{responsive:true, displaylogo:false});
    return;
  }

  if (displayMode === 'live') {
    const y = data.map(s => (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : null);
    const colors = data.map(s => {
      if (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) {
        const c = getColor(s.co2_estimated).toBytes();
        return `rgba(${c[0]},${c[1]},${c[2]},0.9)`;
      }
      return 'rgba(0,0,0,0)';
    });
    const trace = {
      x: names, y: y, type:'bar', name:'Live Estimate',
      marker:{color: colors, line:{width:1,color:'rgba(15,23,42,1)'}},
      hovertemplate:"<b>%{x}</b><br>Live estimate: %{y} ppm<extra></extra>"
    };
    const layout = {
      title: { text: `CO₂ Levels (${citySelect.value || "Select a City"})`, font:{size:14,color:'#e5e7eb'}, x:0.02, y:0.97 },
      margin:{t:40,l:40,r:20,b:120},
      paper_bgcolor:'rgba(15,23,42,0)',
      plot_bgcolor:'rgba(15,23,42,0.85)',
      xaxis:{tickangle:-40,tickfont:{size:11,color:'#9ca3af'}},
      yaxis:{title:'ppm', tickfont:{size:11,color:'#9ca3af'}}
    };
    Plotly.react('barChart',[trace],layout,{responsive:true, displaylogo:false});
    return;
  }

  const baselineY = data.map(s => (s.co2 !== undefined && !isNaN(s.co2)) ? s.co2 : null);
  const liveY = data.map(s => (s.co2_estimated !== undefined && !isNaN(s.co2_estimated)) ? s.co2_estimated : null);

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
    title:{ text: `CO₂ Levels (${citySelect.value || "Select a City"})`, font:{size:14,color:'#e5e7eb'}, x:0.02, y:0.97 },
    barmode:'group',
    margin:{t:40,l:40,r:20,b:120},
    paper_bgcolor:'rgba(15,23,42,0)',
    plot_bgcolor:'rgba(15,23,42,0.85)',
    xaxis:{tickangle:-40,tickfont:{size:11,color:'#9ca3af'}},
    yaxis:{title:'ppm', tickfont:{size:11,color:'#9ca3af'}}
  };

  const traces = [];
  if (baselineY.some(v => v !== null && v !== undefined)) traces.push(baselineTrace);
  if (liveY.some(v => v !== null && v !== undefined)) traces.push(liveTrace);

  Plotly.react('barChart', traces, layout, {responsive:true, displaylogo:false});
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

if (citySearchInput) {
  citySearchInput.addEventListener('input', () => {
    // filterCitiesBySearch();
    updateCitySuggestions();
  });

  citySearchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const firstSuggestion = citySuggestionsEl
        ? citySuggestionsEl.querySelector('.city-suggestion-item')
        : null;

      if (firstSuggestion) {
        firstSuggestion.click();  // pick top suggestion
        e.preventDefault();
      } else {
        // fallback...
      }
    } else if (e.key === 'Escape') {
      if (citySuggestionsEl) citySuggestionsEl.style.display = "none";
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

citySelect.onchange = () => {
  if (citySearchInput) {
    citySearchInput.value = citySelect.value || "";

    // Hide suggestions when a city is chosen
    if (citySuggestionsEl) {
      citySuggestionsEl.style.display = "none";
    }
    suggestionActiveIndex = -1;
  }

  updateStations();
  drawEntities();
  drawSectorChartForSelection();
};

stationSelect.onchange = () => {
  drawEntities();
  updateEfficiencySuggestion();
  drawSectorChartForSelection();
};

methodSelect.onchange = () => {
  updateEfficiencySuggestion();
};

document.getElementById('applyBtn').onclick = async () => {
  const stationName = stationSelect.value;
  const efficiency = parseFloat(reductionInput.value);

  if (!stationName || isNaN(efficiency)) {
    return alert("Select a station and enter a valid reduction.");
  }

  let target = displayMode;
  if (displayMode === 'both') {
    target = 'baseline'; // or 'live' if you prefer
  }

  const res = await fetch(`${BASE_URL}/apply_intervention`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      station: stationName,
      efficiency: efficiency,
      target: target
    })
  });

  const result = await res.json();
  if (result.success) {
    const appliedTo = result.applied_to; // "baseline" or "live"

    stations = stations.map(s => {
      if (s.name !== stationName) return s;
      if (appliedTo === "baseline") {
        return { ...s, co2: result.co2_after };
      } else {
        return { ...s, co2_estimated: result.co2_after };
      }
    });

    drawEntities();
    drawSectorChartForSelection();
  } else {
    alert("Error applying intervention: " + result.error);
  }
};

// ----------- Load -----------

window.onload = async () => {
  await initCesium();
  await fetchStations();
  drawSectorChartForSelection();
};

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./service-worker.js")
      .then(reg => console.log("Service Worker registered:", reg))
      .catch(err => console.log("SW registration failed:", err));
  });
}

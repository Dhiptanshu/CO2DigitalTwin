const CACHE_NAME = "co2-dashboard-v1";
const urlsToCache = [
  "./cesium_map.html",
  "./manifest.json",
  "./service-worker.js",
  "https://cdn.jsdelivr.net/npm/cesium@1.125/Build/Cesium/Cesium.js",
  "https://cdn.jsdelivr.net/npm/cesium@1.125/Build/Cesium/Widgets/widgets.css",
  "https://cdn.plot.ly/plotly-2.26.2.min.js"
];

// Install SW and cache files
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
  );
});

// Serve from cache if offline, but ignore API calls
self.addEventListener("fetch", event => {
  const requestUrl = new URL(event.request.url);

  // Don't cache API requests
  if (requestUrl.pathname.startsWith("/get_stations") || requestUrl.pathname.startsWith("/apply_intervention")) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(response => response || fetch(event.request))
  );
});

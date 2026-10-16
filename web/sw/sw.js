// Backcountry service worker. vite.config.ts fills in the version and the app-shell list at build time.
// - App shell: cached at install, served cache-first, so the app opens with no signal.
// - USGS tiles: served from saved-trip caches (trip-*) first, then the network. Only the app's
//   save code writes tiles; this worker never caches them on its own.
// - /api/*: always the network. Never cached here.
// Matches ignore Vary: servers send `Vary: Origin`, and module scripts are requested with an Origin
// header that the install-time fetch didn't have, so a Vary-respecting match would miss offline.
const VERSION = "__VERSION__";
const SHELL = __SHELL__;
const SHELL_CACHE = `shell-${VERSION}`;
const TILE = /^https:\/\/basemap\.nationalmap\.gov\/arcgis\/rest\/services\/[^/]+\/MapServer\/tile\/\d+\/\d+\/\d+$/;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n.startsWith("shell-") && n !== SHELL_CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

// The page asks for this only after the user taps "Reload" on the update banner.
self.addEventListener("message", (event) => { if (event.data === "skipWaiting") self.skipWaiting(); });

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (TILE.test(req.url)) {
    if (req.cache === "reload") return; // the app is (re)saving tiles: fetch fresh ones, not the saved copies
    event.respondWith(fromTrips(req));
    return;
  }
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") {
    event.respondWith(caches.match("/index.html", { cacheName: SHELL_CACHE, ignoreVary: true }).then((hit) => hit || fetch(req)));
    return;
  }
  event.respondWith(caches.match(req, { cacheName: SHELL_CACHE, ignoreVary: true }).then((hit) => hit || fetch(req)));
});

async function fromTrips(req) {
  for (const name of await caches.keys()) {
    if (!name.startsWith("trip-")) continue;
    const hit = await (await caches.open(name)).match(req.url, { ignoreVary: true });
    if (hit) return hit;
  }
  return fetch(req);
}

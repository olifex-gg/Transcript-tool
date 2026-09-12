/* Minimal service worker: makes the page installable and keeps the shell
   available offline. API requests always go to the network. */
const CACHE = "yt-transcript-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/") || url.pathname === "/t") return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && (url.pathname.startsWith("/static/") || url.pathname === "/")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(url.pathname === "/" || url.pathname.startsWith("/jobs/") ? "/" : e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(url.pathname.startsWith("/jobs/") ? "/" : e.request))
  );
});

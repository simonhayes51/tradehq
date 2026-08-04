// Minimal, safety-first service worker. This used to cache-first EVERYTHING
// forever (no versioning, no expiry, no activate cleanup) and precached
// paths from a pre-Vite build ('/static/js/bundle.js') that don't exist in
// this app - once the browser cached index.html under that scheme, it kept
// serving that exact stale document (with dead, since-rebuilt JS chunk
// filenames) to every visitor, on every subsequent deploy, forever. That's
// what produced "Expected a JavaScript module but the server responded
// with text/html" - the browser was requesting JS filenames from a build
// that no longer exists, and the host's SPA fallback returned index.html.
//
// Rules now:
//   - Navigation requests (the HTML document) are ALWAYS network-first.
//     This is a live trading dashboard - a stale document is not a
//     convenience, it's actively misleading (dead price/fair-value data
//     under a UI that thinks it's live). Falls back to a tiny offline page
//     only if the network is genuinely unreachable.
//   - Same-origin build assets (/assets/*) are cache-first: safe, because
//     Vite content-hashes these filenames - a changed file gets a new
//     name, so a cached old one is simply never requested again once
//     index.html updates.
//   - Everything else (API calls, cross-origin, non-GET) passes straight
//     through to the network untouched - never cached.
const CACHE_VERSION = "v2";
const CACHE_NAME = `futhub-assets-${CACHE_VERSION}`;

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) {
    return; // let the browser handle it normally - never touch API/mutations
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match(req).then((r) => r || Response.error()))
    );
    return;
  }

  if (new URL(req.url).pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(req).then(
        (cached) =>
          cached ||
          fetch(req).then((res) => {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy));
            return res;
          })
      )
    );
  }
  // Everything else (manifest, icons, etc.) - pass through untouched.
});

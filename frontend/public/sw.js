/*
 * The Watchtower service worker.
 *
 * Two jobs:
 *   1. Static asset caching (production only).
 *   2. Making the Offline Library reachable: "/offline" is precached at install
 *      and successful navigations are cached so a saved dossier's shell can load
 *      with no network. The API stays network-only — cached news would read as
 *      current fact. Offline pages render an explicitly labelled saved snapshot
 *      or the library, never stale reporting presented as live.
 */

const VERSION = "gl-lens-v4";
const SHELL = `${VERSION}-shell`;
const ASSETS = `${VERSION}-assets`;

// Dev serves unhashed chunks that change on every edit. Registering the worker
// for push is required even in development, but caching those chunks would
// serve a stale bundle and break hot reload, so caching is production-only.
const DEV =
  self.location.hostname === "localhost" ||
  self.location.hostname === "127.0.0.1";

// Never cache these: either live data or an error page that would masquerade
// as real content.
const NEVER_CACHE = ["/api/", "/graphql", "_next/webpack-hmr"];

const CURRENT = new Set([SHELL, ASSETS]);

self.addEventListener("install", (event) => {
  // Precache the Offline Library. It is a static client route with no user
  // data, so it is safe to store and it guarantees an offline entry point.
  // skipWaiting must be inside waitUntil, otherwise the install can be marked
  // complete before it takes effect.
  event.waitUntil(
    (async () => {
      try {
        const cache = await caches.open(SHELL);
        await cache.add(new Request("/offline", { cache: "reload" }));
      } catch (err) {
        // A failed precache must not block activation; the worker still serves
        // the network and the offline fallback page.
      }
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            // Compare against the exact current names. Testing
            // `key.endsWith(VERSION)` looks right but never matches
            // "gl-lens-v1-assets", so it deleted the live cache on every
            // activation and the asset cache never retained anything.
            .filter((key) => key.startsWith("gl-lens-") && !CURRENT.has(key))
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

const OFFLINE_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offline — The Watchtower</title>
<style>body{background:#030712;color:#e5e7eb;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:1.5rem}
div{max-width:24rem;text-align:center}h1{font-size:1.25rem;margin:0 0 .75rem}
p{font-size:.875rem;color:#9ca3af;line-height:1.6;margin:0 0 1rem}
a{color:#60a5fa;font-size:.875rem}</style></head><body><div>
<h1>You are offline</h1>
<p>The Watchtower could not reach the network. Event intelligence is served live
and never cached, so nothing is shown here rather than risk presenting stale
reporting as current.</p>
<a href="/offline">Open your Offline Library</a> &nbsp;·&nbsp; <a href="/">Try again</a></div></body></html>`;

function offlineResponse() {
  return new Response(OFFLINE_PAGE, {
    status: 503,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

function isAsset(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    /\.(?:svg|png|jpg|jpeg|webp|woff2?|ico|css|js)$/.test(url.pathname)
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (NEVER_CACHE.some((prefix) => url.pathname.startsWith(prefix))) return;

  // In dev, let the network serve assets untouched (see DEV above).
  if (DEV && isAsset(url)) return;

  // Static build output is content-hashed, so a hit is always correct.
  if (isAsset(url)) {
    event.respondWith(
      caches.open(ASSETS).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        try {
          const response = await fetch(request);
          if (response.ok && response.type === "basic") {
            cache.put(request, response.clone());
          }
          return response;
        } catch (err) {
          // Offline with no cache entry: let the failure surface rather than
          // substituting a stale page.
          throw err;
        }
      })
    );
    return;
  }

  // Navigations: network first, so a deploy is picked up immediately. On
  // failure, serve a previously cached shell for the same path, then the
  // Offline Library, then the fallback page. Cached HTML is a client-rendered
  // shell carrying no news data; offline the page shows an explicitly labelled
  // saved snapshot or the library, never stale reporting as if it were live.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        const cache = await caches.open(SHELL);
        const fallback = async () =>
          (await cache.match(new URL(request.url).pathname)) ||
          (await cache.match("/offline")) ||
          offlineResponse();

        // A prerendered route is also in the browser's own HTTP cache, so a
        // plain fetch() while offline can be answered from that cache and
        // succeed, showing an empty shell that reads as "no news". Checking
        // onLine first makes the offline state explicit.
        if (self.navigator.onLine === false) return fallback();

        try {
          const response = await fetch(request, { cache: "no-store" });
          if (response.ok && response.type === "basic") {
            cache.put(new URL(request.url).pathname, response.clone());
          }
          return response;
        } catch (err) {
          return fallback();
        }
      })()
    );
  }
});

// ── Web Push ──────────────────────────────────────────────────────────────────
// The server sends a JSON payload {title, body, url, tag, data}. The push
// service delivers it end-to-end encrypted; we decrypt by reading event.data.
self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = { title: "The Watchtower", body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "The Watchtower";
  const options = {
    body: payload.body || "",
    icon: "/icon-dark-32x32.png",
    badge: "/icon-dark-32x32.png",
    tag: payload.tag || "globe-lens",
    // Re-alert for a newer story under the same tag rather than silently
    // replacing it, since each push is a distinct intelligence event.
    renotify: true,
    data: { url: payload.url || "/", ...(payload.data || {}) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Focus an existing The Watchtower tab on the target URL, or open a new one.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = data.url || "/";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of windows) {
        if (client.url.includes(target) && "focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })()
  );
});
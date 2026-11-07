// GlobeLens AI service worker: receives Web Push messages and turns them into
// notifications. Registered lazily from the settings page the first time the
// user enables alerts; produced payloads come from build_payload_for_digest.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    // Non-JSON payloads are still shown with default copy.
  }
  const title = data.title || "GlobeLens AI";
  const options = {
    body: data.body || "A new intelligence brief is ready.",
    icon: "/apple-icon.png",
    badge: "/icon-dark-32x32.png",
    data: data.data || {},
  };
  if (data.tag) options.tag = data.tag;
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((windowClients) => {
        for (const client of windowClients) {
          if ("focus" in client) {
            client.navigate(url);
            return client.focus();
          }
        }
        return self.clients.openWindow(url);
      })
  );
});
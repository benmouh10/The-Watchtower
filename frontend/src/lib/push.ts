"use client";

// Browser-side Web Push + topic-preference helpers. All network calls go to the
// API with the JWT that login stores under localStorage["admin_token"] (the
// same key every other page of the app uses).

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface PushConfig {
  enabled: boolean;
  vapid_public_key: string | null;
  subject: string;
}

export interface Device {
  id: string;
  endpoint: string;
  user_agent: string | null;
  created_at: string | null;
}

export interface Preferences {
  preferred_topics: string[];
  preferred_countries: string[];
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("admin_token");
}

export async function fetchPushConfig(): Promise<PushConfig> {
  const res = await fetch(`${API_BASE_URL}/api/v1/push/config`);
  if (!res.ok) throw new Error("Could not load push configuration.");
  return res.json();
}

export async function listSubscriptions(token: string): Promise<Device[]> {
  const res = await fetch(`${API_BASE_URL}/api/v1/push/subscriptions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not load your alert devices.");
  const body = await res.json();
  return (body.subscriptions ?? []) as Device[];
}

export async function unregisterDevice(token: string, deviceId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/push/subscriptions/${deviceId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not remove the device.");
}

async function arrayBufferToBase64Url(buf: ArrayBuffer): Promise<string> {
  // The Push API returns raw key bytes; the API stores them URL-safe base64.
  if (typeof btoa === "undefined") return "";
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function registerDevice(token: string, sub: PushSubscription): Promise<void> {
  const p256dh = sub.getKey("p256dh");
  const auth = sub.getKey("auth");
  const res = await fetch(`${API_BASE_URL}/api/v1/push/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      endpoint: sub.endpoint,
      p256dh: p256dh ? await arrayBufferToBase64Url(p256dh) : "",
      auth: auth ? await arrayBufferToBase64Url(auth) : "",
      user_agent: typeof navigator !== "undefined" ? navigator.userAgent : undefined,
    }),
  });
  if (res.status === 409) return; // this browser is already registered
  if (!res.ok) throw new Error("The push service rejected this device.");
}

export async function disablePush(token: string): Promise<void> {
  const devices = await listSubscriptions(token);
  for (const device of devices) {
    await unregisterDevice(token, device.id);
  }
}

export async function getFollowing(token: string): Promise<string[]> {
  const res = await fetch(`${API_BASE_URL}/api/v1/push/following`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not load followed topics.");
  const body = await res.json();
  return (body.topics ?? []) as string[];
}

export async function followTopic(token: string, topic: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/push/following/${encodeURIComponent(topic)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not follow the topic.");
}

export async function unfollowTopic(token: string, topic: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/push/following/${encodeURIComponent(topic)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not unfollow the topic.");
}

export async function fetchPreferences(token: string): Promise<Preferences> {
  const res = await fetch(`${API_BASE_URL}/api/v1/users/me/preferences`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not load preferences.");
  return res.json();
}

export async function savePreferences(token: string, prefs: Preferences): Promise<Preferences> {
  const res = await fetch(`${API_BASE_URL}/api/v1/users/me/preferences`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(prefs),
  });
  if (!res.ok) throw new Error("Could not save preferences.");
  return res.json();
}

let swRegistration: ServiceWorkerRegistration | null = null;

export async function getServiceWorkerRegistration(): Promise<ServiceWorkerRegistration> {
  if (typeof navigator === "undefined" || typeof window === "undefined") {
    throw new Error("Service workers are only available in a browser.");
  }
  if (!("serviceWorker" in navigator)) {
    throw new Error("This browser does not support service workers.");
  }
  if (!swRegistration) {
    swRegistration = await navigator.serviceWorker.register("/sw.js");
  }
  return swRegistration;
}

export async function enablePush(token: string, config: PushConfig): Promise<void> {
  const sw = await getServiceWorkerRegistration();
  if (!config.vapid_public_key) {
    throw new Error("Push alerts are not fully configured on the server.");
  }
  const sub = await sw.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: config.vapid_public_key,
  });
  await registerDevice(token, sub);
}
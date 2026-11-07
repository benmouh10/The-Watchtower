"use client";

// Self-service digest newsletter helpers. Config read is public; the
// subscription lifecycle requires the JWT that login stores under
// localStorage["admin_token"]. The one-click unsubscribe link in the email
// footer is handled by the dedicated page at /newsletter/unsubscribe, which
// POSTs its bearer token here.

import { API_BASE_URL } from "./push";

export interface NewsletterConfig {
  enabled: boolean;
  digest_enabled: boolean;
  delivered_via: "smtp" | "log";
  poll_seconds: number;
}

export interface NewsletterStatus {
  subscribed: boolean;
  subscriber_since: string | null;
  unsubscribed_at: string | null;
  last_digest: {
    id: string;
    title: string;
    generated_at: string;
  } | null;
}

export async function fetchNewsletterConfig(): Promise<NewsletterConfig> {
  const res = await fetch(`${API_BASE_URL}/api/v1/newsletter/config`);
  if (!res.ok) throw new Error("Could not load newsletter configuration.");
  return res.json();
}

export async function fetchNewsletterStatus(token: string): Promise<NewsletterStatus> {
  const res = await fetch(`${API_BASE_URL}/api/v1/newsletter/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not load newsletter status.");
  return res.json();
}

export async function subscribeToNewsletter(token: string): Promise<NewsletterStatus> {
  const res = await fetch(`${API_BASE_URL}/api/v1/newsletter/subscribe`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not subscribe to the newsletter.");
  return res.json();
}

export async function unsubscribeFromNewsletter(token: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/newsletter/subscribe`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not unsubscribe from the newsletter.");
}

export async function unsubscribeWithToken(token: string): Promise<{
  subscribed: boolean;
  reason?: string;
  unsubscribed_at: string | null;
}> {
  const res = await fetch(`${API_BASE_URL}/api/v1/newsletter/unsubscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) throw new Error("This unsubscribe link is invalid or has expired.");
  return res.json();
}
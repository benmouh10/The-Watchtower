"use client";

// API keys + outbound webhook helpers for the settings page. API keys are
// scoped machine credentials (raw secret shown exactly once at creation);
// webhooks are HMAC-signed POST endpoints that fire on processed events.

import { API_BASE_URL } from "./push";

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
}

export interface ApiKeyCreated extends ApiKey {
  raw_key: string;
  warning: string;
}

export interface WebhookDelivery {
  id: string;
  event_id: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string | null;
}

export interface Webhook {
  id: string;
  url: string;
  event_types: string[];
  is_active: boolean;
  created_at: string | null;
  deliveries: WebhookDelivery[];
}

export interface WebhookList {
  items: Webhook[];
  max_endpoints: number;
  count: number;
}

export const WEBHOOK_EVENT_TYPES = ["event.created"] as const;

export async function createApiKey(token: string, name: string): Promise<ApiKeyCreated> {
  const res = await fetch(`${API_BASE_URL}/api/v1/api-keys`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name, scopes: ["read:events"] }),
  });
  if (!res.ok) throw new Error("Could not create the API key.");
  return res.json();
}

export async function listApiKeys(token: string): Promise<ApiKey[]> {
  const res = await fetch(`${API_BASE_URL}/api/v1/api-keys`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not load API keys.");
  const body = await res.json();
  return body.items || [];
}

export async function revokeApiKey(token: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/api-keys/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not revoke the API key.");
}

export async function listWebhooks(token: string): Promise<WebhookList> {
  const res = await fetch(`${API_BASE_URL}/api/v1/webhooks`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not load webhook endpoints.");
  return res.json();
}

export async function registerWebhook(
  token: string,
  url: string,
  eventTypes: string[],
): Promise<Webhook & { secret: string }> {
  const res = await fetch(`${API_BASE_URL}/api/v1/webhooks`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ url, event_types: eventTypes }),
  });
  if (!res.ok) throw new Error("Could not register the webhook endpoint.");
  return res.json();
}

export async function deleteWebhook(token: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/webhooks/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Could not deactivate the webhook endpoint.");
}
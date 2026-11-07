export interface Subscription {
  plan: string;
  payment_provider: string | null;
  has_payment_method: boolean;
  message: string;
}

export interface MeterUsage {
  used: number;
  limit: number | null;
}

export interface MyUsage {
  plan: string;
  reset_at: string | null;
  meters: Record<string, MeterUsage>;
}

export interface CheckoutResult {
  url: string;
  session_id: string;
  plan: string;
}

export interface BillingError extends Error {
  status?: number;
}

function getToken(): string | null {
  try {
    return typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
  } catch {
    return null;
  }
}

async function handle<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err: BillingError = new Error(body.detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return body as T;
}

const BILLING_API = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/v1`;

export async function fetchSubscription(token: string | null): Promise<Subscription> {
  const t = token ?? getToken();
  if (!t) throw new Error("Not signed in");
  return handle(
    await fetch(`${BILLING_API}/billing/subscription`, {
      headers: { Authorization: `Bearer ${t}` },
    })
  );
}

export async function fetchMyUsage(token: string | null): Promise<MyUsage> {
  const t = token ?? getToken();
  if (!t) throw new Error("Not signed in");
  return handle(
    await fetch(`${BILLING_API}/usage/me`, {
      headers: { Authorization: `Bearer ${t}` },
    })
  );
}

export async function createCheckout(token: string | null, plan: string): Promise<CheckoutResult> {
  const t = token ?? getToken();
  if (!t) throw new Error("Not signed in");
  return handle(
    await fetch(`${BILLING_API}/billing/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` },
      body: JSON.stringify({ plan }),
    })
  );
}
export interface SeatUsage {
  used: number;
  limit: number | null;
  plan: string;
}

export interface OrgMember {
  user_id: string;
  name: string;
  email: string;
  role: string;
  created_at: string | null;
}

export interface CompetitorTopic {
  id: string;
  competitor_name: string;
  keywords: string[];
  is_active: boolean;
}

export interface OrgDashboard {
  id: string;
  name: string;
  plan: string;
  created_at: string | null;
  seats: SeatUsage;
  members: OrgMember[];
  competitors: CompetitorTopic[];
  current_user_id: string;
}

function getToken(): string | null {
  try {
    return typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
  } catch {
    return null;
  }
}

function authHeaders(token?: string | null): Record<string, string> {
  const t = token ?? getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401 || res.status === 403) {
    throw new Error("Please log in again");
  }
  if (res.status === 409 || res.status === 422 || res.status === 404) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  if (!res.ok) {
    throw new Error(res.statusText);
  }
  return res.json();
}

const ORG_API = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/v1/org`;

export async function fetchOrg(token: string | null): Promise<OrgDashboard> {
  return handle(await fetch(`${ORG_API}`, { headers: authHeaders(token) }));
}

export async function createOrg(token: string | null, name: string): Promise<OrgDashboard> {
  return handle(
    await fetch(`${ORG_API}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ name }),
    })
  );
}

export async function addMember(
  token: string | null,
  email: string,
  role: string
): Promise<OrgMember> {
  return handle(
    await fetch(`${ORG_API}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ email, role }),
    })
  );
}

export async function setMemberRole(
  token: string | null,
  userId: string,
  role: string
): Promise<void> {
  await handle(
    await fetch(`${ORG_API}/members/${userId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ role }),
    })
  );
}

export async function removeMember(token: string | null, userId: string): Promise<void> {
  await handle(await fetch(`${ORG_API}/members/${userId}`, { method: "DELETE", headers: authHeaders(token) }));
}

export async function upsertCompetitor(
  token: string | null,
  competitorName: string,
  keywords: string[],
  isActive = true
): Promise<CompetitorTopic> {
  return handle(
    await fetch(`${ORG_API}/competitors`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ competitor_name: competitorName, keywords, is_active: isActive }),
    })
  );
}

export async function deleteCompetitor(token: string | null, id: string): Promise<void> {
  await handle(await fetch(`${ORG_API}/competitors/${id}`, { method: "DELETE", headers: authHeaders(token) }));
}
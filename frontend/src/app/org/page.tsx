"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Building2, Plus, Shield, Users, X } from "lucide-react";
import {
  CompetitorTopic,
  OrgDashboard,
  addMember,
  createOrg,
  deleteCompetitor,
  fetchOrg,
  removeMember,
  setMemberRole,
  upsertCompetitor,
} from "../../lib/org";

const ROLE_OPTIONS = ["owner", "admin", "member"];

export default function OrgDashboardPage() {
  const [loading, setLoading] = useState(true);
  const [org, setOrg] = useState<OrgDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [orgName, setOrgName] = useState("");
  const [creating, setCreating] = useState(false);

  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setNewMemberRole] = useState("member");
  const [memberNotice, setMemberNotice] = useState<string | null>(null);
  const [memberError, setMemberError] = useState<string | null>(null);

  const [compName, setCompName] = useState("");
  const [compKeywords, setCompKeywords] = useState("");
  const [compNotice, setCompNotice] = useState<string | null>(null);
  const [compError, setCompError] = useState<string | null>(null);

  const getToken = useCallback(() => {
    try {
      return typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    fetchOrg(getToken())
      .then((data) => {
        setOrg(data);
        setOrgName(data.name);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [getToken]);

  const reload = useCallback(async () => {
    const data = await fetchOrg(getToken());
    setOrg(data);
    setOrgName(data.name);
  }, [getToken]);

  const handleCreate = async () => {
    if (!orgName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await createOrg(getToken(), orgName.trim());
      setOrg(created);
      setOrgName(created.name);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleAddMember = async () => {
    if (!memberEmail.trim()) return;
    setMemberError(null);
    setMemberNotice(null);
    try {
      await addMember(getToken(), memberEmail.trim(), memberRole);
      setMemberEmail("");
      setMemberNotice(`Added ${memberEmail.trim()}.`);
      await reload();
    } catch (e: any) {
      setMemberError(e.message);
    }
  };

  const handleRoleChange = async (user_id: string, role: string) => {
    try {
      await setMemberRole(getToken(), user_id, role);
      await reload();
    } catch (e: any) {
      setMemberError(e.message);
    }
  };

  const handleRemoveMember = async (user_id: string) => {
    try {
      await removeMember(getToken(), user_id);
      await reload();
    } catch (e: any) {
      setMemberError(e.message);
    }
  };

  const handleAddCompetitor = async () => {
    if (!compName.trim()) return;
    setCompError(null);
    setCompNotice(null);
    const keywords = compKeywords
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    try {
      await upsertCompetitor(getToken(), compName.trim(), keywords, true);
      setCompName("");
      setCompKeywords("");
      setCompNotice("Competitor topic saved.");
      await reload();
    } catch (e: any) {
      setCompError(e.message);
    }
  };

  const handleRemoveCompetitor = async (id: string) => {
    try {
      await deleteCompetitor(getToken(), id);
      await reload();
    } catch (e: any) {
      setCompError(e.message);
    }
  };

  const me = org?.members.find((m) => m.user_id === org.current_user_id)?.role;
  const canWrite = me === "owner" || me === "admin";

  return (
    <main className="min-h-screen bg-background text-on-surface">
      <div className="max-w-5xl mx-auto px-4 py-8">
        <header className="mb-8">
          <Link
            href="/search"
            className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-cyber-purple transition-colors"
          >
            <ArrowLeft className="w-4 h-4" /> Back to search
          </Link>
          <h1 className="text-2xl font-bold tracking-tight mt-4 flex items-center gap-3">
            <Building2 className="w-6 h-6 text-cyber-purple" /> Organization
          </h1>
          <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-1">
            Enterprise seats, members and competitive intelligence
          </p>
        </header>

        {loading ? (
          <p className="text-sm text-zinc-400">Loading workspace…</p>
        ) : !org ? (
          <section className="max-w-md mx-auto mt-16 p-6 rounded-2xl bg-surface-container/50 border border-outline-variant/40">
            <h2 className="text-lg font-semibold">Start your organization</h2>
            <p className="text-sm text-zinc-400 mt-2">
              Create an org to add teammates, track shared usage and watch competitors.
            </p>
            <input
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              placeholder="Organization name"
              className="mt-4 w-full px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-cyber-purple/50"
            />
            <button
              onClick={handleCreate}
              disabled={creating || !orgName.trim()}
              className="mt-3 w-full px-4 py-2 rounded-lg bg-cyber-purple/15 hover:bg-cyber-purple/25 border border-cyber-purple/35 text-cyber-purple font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {creating ? "Creating…" : "Create organization"}
            </button>
            {error ? <p className="mt-3 text-xs text-cyber-rose">{error}</p> : null}
          </section>
        ) : (
          <div className="space-y-8">
            {error ? <p className="text-sm text-cyber-rose">{error}</p> : null}

            <section>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">{org.name}</h2>
                  <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">
                    {org.plan} plan · {org.seats.used}/
                    {org.seats.limit ?? "∞"} seats
                  </p>
                </div>
                <div className="w-48 h-2 rounded-full bg-zinc-800 overflow-hidden">
                  <div
                    className="h-full bg-cyber-purple/70 transition-all"
                    style={{
                      width: org.seats.limit
                        ? `${Math.min(100, (org.seats.used / org.seats.limit) * 100)}%`
                        : "100%",
                    }}
                  />
                </div>
              </div>
            </section>

            <section className="p-5 rounded-2xl bg-surface-container/50 border border-outline-variant/40">
              <h3 className="flex items-center gap-2 text-sm font-semibold">
                <Users className="w-4 h-4 text-cyber-purple" /> Members
              </h3>
              {memberNotice ? (
                <p className="mt-2 text-xs text-emerald-400">{memberNotice}</p>
              ) : null}
              {memberError ? <p className="mt-2 text-xs text-cyber-rose">{memberError}</p> : null}
              {canWrite ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <input
                    value={memberEmail}
                    onChange={(e) => setMemberEmail(e.target.value)}
                    placeholder="teammate@company.com"
                    className="flex-1 min-w-[220px] px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-cyber-purple/50"
                  />
                  <select
                    value={memberRole}
                    onChange={(e) => setNewMemberRole(e.target.value)}
                    className="px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface focus:outline-none focus:border-cyber-purple/50"
                  >
                    {ROLE_OPTIONS.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={handleAddMember}
                    disabled={!memberEmail.trim()}
                    className="px-4 py-2 rounded-lg bg-cyber-purple/15 hover:bg-cyber-purple/25 border border-cyber-purple/35 text-cyber-purple font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50"
                  >
                    <Plus className="w-3.5 h-3.5 inline-block mr-1" /> Add
                  </button>
                </div>
              ) : (
                <p className="mt-3 text-xs text-zinc-500">Owners and admins manage members.</p>
              )}
              <ul className="mt-4 divide-y divide-outline-variant/40">
                {org.members.map((m) => (
                  <li key={m.user_id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <p className="text-sm text-on-surface truncate">
                        {m.name} {m.user_id === org.current_user_id ? "· you" : ""}
                      </p>
                      <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider">
                        {m.email}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {canWrite && m.role !== "owner" ? (
                        <select
                          value={m.role}
                          onChange={(e) => handleRoleChange(m.user_id, e.target.value)}
                          className="px-2 py-1.5 rounded-md bg-zinc-950/60 border border-indigo-950/60 text-xs text-on-surface focus:outline-none"
                        >
                          {ROLE_OPTIONS.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="px-2 py-1 rounded-md border border-outline-variant text-[10px] font-mono-data uppercase tracking-wider text-zinc-400">
                          {m.role}
                        </span>
                      )}
                      {canWrite && m.role !== "owner" ? (
                        <button
                          onClick={() => handleRemoveMember(m.user_id)}
                          className="p-1.5 rounded-md border border-outline-variant text-zinc-500 hover:text-cyber-rose hover:border-cyber-rose/50 transition-colors"
                          aria-label={`Remove ${m.name}`}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section className="p-5 rounded-2xl bg-surface-container/50 border border-outline-variant/40">
              <h3 className="flex items-center gap-2 text-sm font-semibold">
                <Shield className="w-4 h-4 text-cyber-purple" /> Competitive Intelligence
              </h3>
              <p className="font-mono-data text-[9px] text-zinc-500 mt-1 uppercase tracking-wider">
                Track rival coverage by keyword across ingested feeds
              </p>
              {compNotice ? <p className="mt-2 text-xs text-emerald-400">{compNotice}</p> : null}
              {compError ? <p className="mt-2 text-xs text-cyber-rose">{compError}</p> : null}
              {canWrite ? (
                <div className="mt-3 space-y-2">
                  <input
                    value={compName}
                    onChange={(e) => setCompName(e.target.value)}
                    placeholder="Competitor name e.g. RivalCo"
                    className="w-full px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-cyber-purple/50"
                  />
                  <input
                    value={compKeywords}
                    onChange={(e) => setCompKeywords(e.target.value)}
                    placeholder="Keywords (comma separated) e.g. pricing, roadmap"
                    className="w-full px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-cyber-purple/50"
                  />
                  <button
                    onClick={handleAddCompetitor}
                    disabled={!compName.trim()}
                    className="px-4 py-2 rounded-lg bg-cyber-purple/15 hover:bg-cyber-purple/25 border border-cyber-purple/35 text-cyber-purple font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50"
                  >
                    Track competitor
                  </button>
                </div>
              ) : null}
              {org.competitors.length > 0 ? (
                <ul className="mt-4 space-y-2">
                  {org.competitors.map((c: CompetitorTopic) => (
                    <li
                      key={c.id}
                      className="flex items-start justify-between gap-3 p-3 rounded-xl bg-zinc-950/50 border border-outline-variant/40"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-on-surface">{c.competitor_name}</p>
                        <p className="font-mono-data text-[10px] text-zinc-500 mt-0.5">
                          {c.keywords.length ? c.keywords.join(", ") : "no keywords"} ·{" "}
                          {c.is_active ? "active" : "paused"}
                        </p>
                      </div>
                      {canWrite ? (
                        <button
                          onClick={() => handleRemoveCompetitor(c.id)}
                          className="p-1.5 rounded-md border border-outline-variant text-zinc-500 hover:text-cyber-rose hover:border-cyber-rose/50 transition-colors"
                          aria-label={`Remove ${c.competitor_name}`}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-xs text-zinc-400">No competitors tracked yet.</p>
              )}
            </section>
          </div>
        )}
      </div>
    </main>
  );
}

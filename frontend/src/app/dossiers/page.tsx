"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bookmark, Trash2, Globe, AlertTriangle } from "lucide-react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SavedEvent {
  id: string;
  title: string;
  summary: string | null;
  topic: string | null;
  country: string | null;
  importance_score: number | null;
  bias_lean: string | null;
  is_classified: boolean;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  source_count: number;
  source_names: string[];
}

interface BookmarkRow {
  id: string;
  created_at: string | null;
  event: SavedEvent | null;
}

export default function DossiersPage() {
  const [rows, setRows] = useState<BookmarkRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const token =
    typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;

  const load = useCallback(async () => {
    if (!token) {
      // No credential: say so plainly rather than rendering an empty list that
      // looks like the user has no saved dossiers.
      setError("Sign in to view your saved dossiers.");
      setRows(null);
      return;
    }
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/bookmarks?limit=200`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401 || res.status === 403) {
        setError("Your session expired. Sign in again to view your dossiers.");
        setRows(null);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRows(data.items ?? []);
      setError(null);
    } catch {
      setError("Could not reach the dossier service.");
      setRows(null);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const remove = async (eventId: string) => {
    if (!token) return;
    setRemoving(eventId);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/bookmarks/events/${eventId}`,
        { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setRows((prev) => (prev ?? []).filter((r) => r.event?.id !== eventId));
      setNotice("Removed from your dossier.");
    } catch {
      setNotice("Could not remove that dossier. Nothing was changed.");
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md">
      <header className="flex items-center gap-3 px-margin-desktop w-full h-16 border-b border-indigo-950/40">
        <Globe className="w-5 h-5 text-primary" />
        <h1 className="font-display-md text-on-surface">My Dossiers</h1>
      </header>

      <main className="px-margin-desktop py-8 max-w-4xl mx-auto space-y-4">
        {error ? (
          <div className="flex items-start gap-3 border border-amber-500/40 bg-amber-500/10 rounded p-4">
            <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
            <p className="text-body-sm text-on-surface">{error}</p>
          </div>
        ) : null}

        {notice ? (
          <p className="text-[11px] font-mono-data text-zinc-400" role="status">
            {notice}
          </p>
        ) : null}

        {rows === null && !error ? <p className="text-body-sm text-zinc-400">Loading…</p> : null}

        {rows !== null && rows.length === 0 ? (
          <div className="border border-outline-variant/40 rounded p-8 text-center space-y-2">
            <Bookmark className="w-6 h-6 mx-auto text-zinc-500" />
            <p className="text-body-sm text-on-surface">No saved dossiers yet.</p>
            <p className="text-[11px] text-zinc-500">
              Use “Bookmark Intel” on an event to save it here.
            </p>
          </div>
        ) : null}

        {rows?.map((row) => {
          const ev = row.event;
          if (!ev) return null;
          return (
            <article
              key={row.id}
              className="border border-outline-variant/30 bg-surface-container rounded p-4 flex items-start justify-between gap-4"
            >
              <div className="min-w-0 space-y-1">
                <Link
                  href={`/events/${ev.id}`}
                  className="font-body-lg text-on-surface hover:text-primary block truncate"
                >
                  {ev.title}
                </Link>
                {ev.summary ? (
                  <p className="text-body-sm text-secondary line-clamp-2">
                    {ev.summary}
                  </p>
                ) : null}
                <p className="text-[10px] font-mono-data text-zinc-500 uppercase tracking-wider">
                  {ev.source_count} source{ev.source_count === 1 ? "" : "s"}
                  {ev.country ? ` · ${ev.country}` : ""}
                  {ev.topic ? ` · ${ev.topic}` : ""}
                  {ev.is_classified ? ` · ${String(ev.bias_lean).replace(/_/g, " ")}` : ""}
                </p>
              </div>
              <button
                onClick={() => remove(ev.id)}
                disabled={removing === ev.id}
                aria-label={`Remove ${ev.title} from dossier`}
                className="shrink-0 text-zinc-500 hover:text-red-400 disabled:opacity-50 p-1"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </article>
          );
        })}
      </main>
    </div>
  );
}

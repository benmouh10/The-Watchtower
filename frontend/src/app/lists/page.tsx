"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Globe, Library, Plus, Trash2, AlertTriangle } from "lucide-react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ReadingListRow {
  id: string;
  title: string;
  description: string | null;
  is_private: boolean;
  item_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export default function ReadingListsPage() {
  const [rows, setRows] = useState<ReadingListRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  const token =
    typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;

  const load = useCallback(async () => {
    if (!token) {
      setError("Sign in to manage your reading lists.");
      setRows(null);
      return;
    }
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists?limit=100`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401 || res.status === 403) {
        setError("Your session expired. Sign in again to manage your lists.");
        setRows(null);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRows(data.items ?? []);
      setError(null);
    } catch {
      setError("Could not reach the reading list service.");
      setRows(null);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !title.trim()) return;
    setCreating(true);
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: title.trim(),
          description: description.trim() || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setNotice(body?.detail || "Could not create that list.");
        return;
      }
      const created = await res.json();
      setRows((prev) => [created, ...(prev ?? [])]);
      setTitle("");
      setDescription("");
      setNotice("List created.");
    } catch {
      setNotice("Network error. Nothing was created.");
    } finally {
      setCreating(false);
    }
  };

  const remove = async (listId: string, listTitle: string) => {
    if (!token) return;
    setDeleting(listId);
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists/${listId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setRows((prev) => (prev ?? []).filter((r) => r.id !== listId));
      setNotice(`Deleted "${listTitle}".`);
    } catch {
      setNotice("Could not delete that list. Nothing was changed.");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md">
      <header className="flex items-center gap-3 px-margin-desktop w-full h-16 border-b border-indigo-950/40">
        <Globe className="w-5 h-5 text-primary" />
        <h1 className="font-display-md text-on-surface">My Reading Lists</h1>
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

        {!error ? (
          <form
            onSubmit={create}
            className="border border-outline-variant/30 bg-surface-container rounded p-4 space-y-3"
          >
            <div className="flex flex-col md:flex-row gap-3">
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="List title (required)"
                maxLength={200}
                required
                className="flex-1 bg-background border border-outline-variant rounded px-3 py-2 text-body-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-primary"
              />
              <button
                type="submit"
                disabled={creating || !title.trim()}
                className="inline-flex items-center justify-center gap-2 bg-primary hover:bg-primary-fixed text-on-primary font-label-caps text-[12px] px-4 py-2 rounded tracking-wider uppercase font-bold transition-all disabled:opacity-50"
              >
                <Plus className="w-4 h-4" />
                {creating ? "Creating…" : "New List"}
              </button>
            </div>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              maxLength={2000}
              className="w-full bg-background border border-outline-variant rounded px-3 py-2 text-body-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-primary"
            />
          </form>
        ) : null}

        {rows === null && !error ? (
          <p className="text-body-sm text-zinc-400">Loading…</p>
        ) : null}

        {rows !== null && rows.length === 0 ? (
          <div className="border border-outline-variant/40 rounded p-8 text-center space-y-2">
            <Library className="w-6 h-6 mx-auto text-zinc-500" />
            <p className="text-body-sm text-on-surface">No reading lists yet.</p>
            <p className="text-[11px] text-zinc-500">
              Create one above, then save events into it from any event page.
            </p>
          </div>
        ) : null}

        {rows?.map((row) => (
          <article
            key={row.id}
            className="border border-outline-variant/30 bg-surface-container rounded p-4 flex items-start justify-between gap-4"
          >
            <div className="min-w-0 space-y-1">
              <Link
                href={`/lists/${row.id}`}
                className="font-body-lg text-on-surface hover:text-primary block truncate"
              >
                {row.title}
              </Link>
              {row.description ? (
                <p className="text-body-sm text-secondary line-clamp-1">
                  {row.description}
                </p>
              ) : null}
              <p className="text-[10px] font-mono-data text-zinc-500 uppercase tracking-wider">
                {row.item_count} event{row.item_count === 1 ? "" : "s"}
                {row.updated_at
                  ? ` · updated ${new Date(row.updated_at).toLocaleDateString()}`
                  : ""}
                {row.is_private ? " · private" : ""}
              </p>
            </div>
            <button
              onClick={() => remove(row.id, row.title)}
              disabled={deleting === row.id}
              aria-label={`Delete ${row.title}`}
              className="shrink-0 text-zinc-500 hover:text-red-400 disabled:opacity-50 p-1"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </article>
        ))}
      </main>
    </div>
  );
}
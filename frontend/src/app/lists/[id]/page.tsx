"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Globe,
  ArrowLeft,
  Trash2,
  ArrowUp,
  ArrowDown,
  Pencil,
  Printer,
  FileDown,
  AlertTriangle,
} from "lucide-react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ItemEvent {
  id: string;
  title: string;
  summary: string | null;
  topic: string | null;
  country: string | null;
  importance_score: number | null;
  bias_lean: string | null;
  is_classified: boolean;
  status: string;
  source_count: number;
}

interface ListItem {
  list_item_id: string;
  position: number;
  added_at: string | null;
  event: ItemEvent | null;
}

interface ReadingListDetail {
  id: string;
  title: string;
  description: string | null;
  is_private: boolean;
  item_count: number;
  created_at: string | null;
  updated_at: string | null;
  items: ListItem[];
}

export default function ReadingListDetailPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [list, setList] = useState<ReadingListDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const [moving, setMoving] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [printing, setPrinting] = useState(false);
  const [downloadingPdf, setDownloadingPdf] = useState(false);

  const token =
    typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;

  const load = useCallback(async () => {
    if (!token) {
      setError("Sign in to view your reading list.");
      setList(null);
      return;
    }
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401 || res.status === 403) {
        setError("Your session expired. Sign in again to view this list.");
        setList(null);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setList(data);
      setError(null);
    } catch {
      setError("Could not reach the reading list service.");
      setList(null);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  const beginRename = () => {
    if (!list) return;
    setTitleDraft(list.title);
    setEditingTitle(true);
  };

  const saveTitle = async () => {
    if (!token || !list || !titleDraft.trim()) return;
    setSavingTitle(true);
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists/${id}`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: titleDraft.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setNotice(body?.detail || "Could not rename that list.");
        return;
      }
      const updated = await res.json();
      setList((prev) => (prev ? { ...prev, title: updated.title } : prev));
      setEditingTitle(false);
      setNotice("List renamed.");
    } catch {
      setNotice("Network error. Nothing was changed.");
    } finally {
      setSavingTitle(false);
    }
  };

  const buildEventIds = (items: ListItem[]): string[] =>
    items
      .filter((i) => i.event)
      .map((i) => (i.event as ItemEvent).id);

  const reorder = async (fromIndex: number, toIndex: number) => {
    if (!token || !list) return;
    const items = [...list.items].sort((a, b) => a.position - b.position);
    if (fromIndex < 0 || toIndex < 0 || toIndex >= items.length) return;
    const swapped = [...items];
    const [moved] = swapped.splice(fromIndex, 1);
    swapped.splice(toIndex, 0, moved);
    setMoving(moved.list_item_id);
    setNotice(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/reading-lists/${id}/events/order`,
        {
          method: "PUT",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ event_ids: buildEventIds(swapped) }),
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setNotice(body?.detail || "Could not reorder the list.");
        return;
      }
      await load();
      setNotice("Order saved.");
    } catch {
      setNotice("Network error. Order was not changed.");
    } finally {
      setMoving(null);
    }
  };

  const removeItem = async (eventId: string) => {
    if (!token) return;
    setRemoving(eventId);
    setNotice(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/reading-lists/${id}/events/${eventId}`,
        { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
      setNotice("Removed from this list.");
    } catch {
      setNotice("Could not remove that event. Nothing was changed.");
    } finally {
      setRemoving(null);
    }
  };

  const printExport = async () => {
    if (!token || !list) return;
    setPrinting(true);
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists/${id}/export`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const html = await res.text();
      const blob = new Blob([html], { type: "text/html" });
      const url = URL.createObjectURL(blob);

      // Hidden same-origin iframe so the browser's own Print → Save as PDF can
      // render the export. We fetch with the auth header ourselves rather than
      // navigating a window to the endpoint, which cannot attach credentials.
      const frame = document.createElement("iframe");
      frame.setAttribute("aria-hidden", "true");
      frame.style.position = "fixed";
      frame.style.right = "0";
      frame.style.bottom = "0";
      frame.style.width = "0";
      frame.style.height = "0";
      frame.style.border = "0";
      document.body.appendChild(frame);

      frame.onload = () => {
        try {
          frame.contentWindow?.focus();
          frame.contentWindow?.print();
        } finally {
          setTimeout(() => {
            document.body.removeChild(frame);
            URL.revokeObjectURL(url);
          }, 60_000);
        }
      };
      frame.onerror = () => {
        document.body.removeChild(frame);
        URL.revokeObjectURL(url);
        setNotice("Could not open the print view.");
      };
      frame.src = url;
      setPrinting(false);
    } catch {
      setPrinting(false);
      setNotice("Could not generate the print view.");
    }
  };

  const pdfExport = async () => {
    if (!token || !list) return;
    setDownloadingPdf(true);
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reading-lists/${id}/export.pdf`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const detail = res.status === 503 ? (await res.json().catch(() => null))?.detail : null;
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `globelens-reading-list-${id}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch {
      setNotice("Could not generate the PDF right now. Try the print view instead.");
    } finally {
      setDownloadingPdf(false);
    }
  };

  const orderedItems = list ? [...list.items].sort((a, b) => a.position - b.position) : [];
  const lastIndex = orderedItems.length - 1;

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md">
      <header className="flex items-center gap-3 px-margin-desktop w-full h-16 border-b border-indigo-950/40">
        <Globe className="w-5 h-5 text-primary" />
        <h1 className="font-display-md text-on-surface">Reading List</h1>
      </header>

      <main className="px-margin-desktop py-8 max-w-4xl mx-auto space-y-4">
        <button
          onClick={() => router.push("/lists")}
          className="inline-flex items-center gap-2 text-[12px] font-mono-data text-zinc-400 hover:text-primary uppercase tracking-wider"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          All lists
        </button>

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

        {list === null && !error ? (
          <p className="text-body-sm text-zinc-400">Loading…</p>
        ) : null}

        {list !== null ? (
          <>
            <div className="border border-outline-variant/30 bg-surface-container rounded p-4 space-y-3">
              <div className="flex items-start justify-between gap-4">
                {editingTitle ? (
                  <div className="flex-1 flex gap-2">
                    <input
                      type="text"
                      value={titleDraft}
                      onChange={(e) => setTitleDraft(e.target.value)}
                      maxLength={200}
                      className="flex-1 bg-background border border-outline-variant rounded px-3 py-1.5 text-body-sm text-on-surface focus:outline-none focus:border-primary"
                    />
                    <button
                      onClick={saveTitle}
                      disabled={savingTitle || !titleDraft.trim()}
                      className="bg-primary hover:bg-primary-fixed text-on-primary font-label-caps text-[11px] px-3 py-1.5 rounded tracking-wider uppercase font-bold transition-all disabled:opacity-50"
                    >
                      {savingTitle ? "Saving…" : "Save"}
                    </button>
                    <button
                      onClick={() => setEditingTitle(false)}
                      className="text-[11px] font-mono-data text-zinc-400 hover:text-on-surface uppercase px-2"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      <h2 className="font-display-md text-on-surface truncate">
                        {list.title}
                      </h2>
                      <button
                        onClick={beginRename}
                        aria-label="Rename list"
                        className="text-zinc-500 hover:text-primary p-1 shrink-0"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    {list.description ? (
                      <p className="text-body-sm text-secondary">{list.description}</p>
                    ) : null}
                    <p className="text-[10px] font-mono-data text-zinc-500 uppercase tracking-wider">
                      {list.item_count} event{list.item_count === 1 ? "" : "s"}
                      {list.updated_at
                        ? ` · updated ${new Date(list.updated_at).toLocaleDateString()}`
                        : ""}
                    </p>
                  </div>
                )}

                <button
                  onClick={printExport}
                  disabled={printing}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant rounded font-body-sm text-body-sm text-on-surface hover:border-primary transition-colors disabled:opacity-60 shrink-0"
                >
                  <Printer className="w-[18px] h-[18px]" />
                  {printing ? "Preparing…" : "Print / PDF"}
                </button>
                <button
                  onClick={pdfExport}
                  disabled={downloadingPdf}
                  className="inline-flex items-center gap-2 px-3 py-1.5 bg-primary hover:bg-primary-fixed text-on-primary rounded font-body-sm text-body-sm transition-colors disabled:opacity-60 shrink-0"
                >
                  <FileDown className="w-[18px] h-[18px]" />
                  {downloadingPdf ? "Rendering…" : "Download PDF"}
                </button>
              </div>
            </div>

            {orderedItems.length === 0 ? (
              <div className="border border-outline-variant/40 rounded p-8 text-center space-y-2">
                <p className="text-body-sm text-on-surface">This list is empty.</p>
                <p className="text-[11px] text-zinc-500">
                  Open any event and use “Save to list” to add it here.
                </p>
              </div>
            ) : null}

            {orderedItems.map((item, index) => {
              const ev = item.event;
              if (!ev) return null;
              return (
                <article
                  key={item.list_item_id}
                  className="border border-outline-variant/30 bg-surface-container rounded p-4 flex items-start justify-between gap-3"
                >
                  <div className="min-w-0 space-y-1">
                    <Link
                      href={`/events/${ev.id}`}
                      className="font-body-lg text-on-surface hover:text-primary block truncate"
                    >
                      <span className="text-zinc-500 font-mono-data text-[12px] mr-2">
                        {index + 1}.
                      </span>
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
                      {ev.is_classified
                        ? ` · ${String(ev.bias_lean).replace(/_/g, " ")}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-2 shrink-0">
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => reorder(index, index - 1)}
                        disabled={index === 0 || moving === item.list_item_id}
                        aria-label="Move up"
                        className="text-zinc-500 hover:text-primary disabled:opacity-30 p-1"
                      >
                        <ArrowUp className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => reorder(index, index + 1)}
                        disabled={
                          index === lastIndex || moving === item.list_item_id
                        }
                        aria-label="Move down"
                        className="text-zinc-500 hover:text-primary disabled:opacity-30 p-1"
                      >
                        <ArrowDown className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => removeItem(ev.id)}
                        disabled={removing === ev.id}
                        aria-label={`Remove ${ev.title} from list`}
                        className="text-zinc-500 hover:text-red-400 disabled:opacity-50 p-1"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </>
        ) : null}
      </main>
    </div>
  );
}
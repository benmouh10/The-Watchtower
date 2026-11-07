"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Scale, CircleSlash, ExternalLink, AlertTriangle, Search } from "lucide-react";
import { biasColor } from "../components/BiasSpectrum";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SpectrumBucket {
  bias_lean: string;
  bias_score: number;
  publishers: number;
  articles: number;
}

interface SpectrumResponse {
  buckets: SpectrumBucket[];
  unclassified: { publishers: number; articles: number };
  unrated_credibility: number;
  total_publishers: number;
  classified_publishers: number;
  // Null when no publisher is classified. Not zero: an unmeasured lean is not a
  // centred one.
  mean_bias: number | null;
  note: string;
}

interface SourceRow {
  id: string;
  name: string;
  url: string;
  country: string | null;
  bias_lean: string | null;
  bias_score: number | null;
  is_classified: boolean;
  credibility_score: number | null;
  is_rated: boolean;
  article_count: number;
}

const BIAS_LABEL: Record<string, string> = {
  LEFT: "Left",
  CENTER_LEFT: "Center-left",
  CENTER: "Center",
  CENTER_RIGHT: "Center-right",
  RIGHT: "Right",
};

const BUCKET_ORDER = ["LEFT", "CENTER_LEFT", "CENTER", "CENTER_RIGHT", "RIGHT"];

function axisPosition(score: number | null | undefined): number | null {
  if (score === null || score === undefined) return null;
  return 50 + (Math.max(-1, Math.min(1, score)) * 50);
}

export default function SourcesPage() {
  const [spectrum, setSpectrum] = useState<SpectrumResponse | null>(null);
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [specRes, listRes] = await Promise.all([
          fetch(`${API_BASE_URL}/api/v1/sources/bias-spectrum`, { signal: controller.signal }),
          fetch(`${API_BASE_URL}/api/v1/sources?limit=500`, { signal: controller.signal }),
        ]);
        if (!specRes.ok || !listRes.ok) {
          setError(`Could not load publisher data (HTTP ${specRes.status}/${listRes.status}).`);
          return;
        }
        const spec = (await specRes.json()) as SpectrumResponse;
        const list = (await listRes.json()) as { sources: SourceRow[] };
        if (cancelled) return;
        setSpectrum(spec);
        setSources(list.sources || []);
      } catch (err) {
        if ((err as Error).name !== "AbortError" && !cancelled) {
          setError("Could not reach the API.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  const filtered = query.trim()
    ? sources.filter((s) => s.name.toLowerCase().includes(query.trim().toLowerCase()))
    : sources;

  return (
    <main className="min-h-screen bg-background text-on-surface px-4 py-10 sm:px-8">
      <div className="mx-auto max-w-5xl flex flex-col gap-8">
        <header className="flex flex-col gap-2">
          <h1 className="font-headline-lg text-headline-lg flex items-center gap-2">
            <Scale className="w-5 h-5 text-primary" aria-hidden />
            Publisher Bias Landscape
          </h1>
          <p className="text-[11px] text-zinc-500 leading-relaxed max-w-2xl">
            Every publisher GlobeLens tracks, and where it sits on the bias spectrum. Outlets without a
            stored classification are reported separately: they are not placed at centre, because
            absence of a rating is not evidence of a neutral lean.
          </p>
        </header>

        {loading && <p className="text-[11px] text-zinc-500 font-mono-data">Loading publisher data…</p>}
        {error && <p className="text-[11px] text-amber-400/90">{error}</p>}

        {spectrum && (
          <>
            {/* Aggregate spectrum. */}
            <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5 flex flex-col gap-4">
              <header className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">Distribution across {spectrum.total_publishers} publishers</h2>
                <span className="text-[9px] font-mono-data text-zinc-500">
                  {spectrum.classified_publishers} classified
                  {spectrum.unclassified.publishers > 0 && ` · ${spectrum.unclassified.publishers} unclassified`}
                </span>
              </header>

              {spectrum.classified_publishers === 0 ? (
                <p className="text-[11px] text-zinc-500 leading-relaxed flex items-start gap-2">
                  <CircleSlash className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" aria-hidden />
                  <span>
                    No tracked publisher carries a bias classification yet, so no distribution can be
                    drawn. The full publisher list is below with each outlet marked unclassified.
                  </span>
                </p>
              ) : (
                <>
                  <div className="grid grid-cols-5 gap-2">
                    {BUCKET_ORDER.map((key) => {
                      const bucket = spectrum.buckets.find((b) => b.bias_lean === key);
                      const count = bucket?.publishers ?? 0;
                      const max = Math.max(1, ...spectrum.buckets.map((b) => b.publishers));
                      return (
                        <div key={key} className="flex flex-col items-center gap-1.5">
                          <span
                            className="text-sm font-mono-data font-bold"
                            style={{ color: count > 0 ? biasColor(key) : "#3f3f46" }}
                          >
                            {count}
                          </span>
                          <div className="w-full h-16 bg-zinc-900/60 border border-zinc-800/50 rounded flex items-end overflow-hidden">
                            <div
                              className="w-full transition-all"
                              style={{
                                height: `${Math.max(count > 0 ? 8 : 0, (count / max) * 100)}%`,
                                backgroundColor: biasColor(key),
                                opacity: count > 0 ? 0.75 : 0,
                              }}
                            />
                          </div>
                          <span className="text-[9px] font-mono-data uppercase tracking-wider text-zinc-500 text-center">
                            {BIAS_LABEL[key]}
                          </span>
                        </div>
                      );
                    })}
                  </div>

                  {spectrum.mean_bias !== null && (
                    <p className="text-[10px] text-zinc-500 font-mono-data">
                      Mean lean across classified publishers:{" "}
                      <span className="text-zinc-300">
                        {spectrum.mean_bias > 0 ? "+" : ""}
                        {spectrum.mean_bias.toFixed(2)}
                      </span>{" "}
                      on a −1 (left) to +1 (right) scale.
                    </p>
                  )}
                </>
              )}

              {spectrum.unclassified.publishers > 0 && (
                <p className="text-[10px] text-zinc-500 border-t border-outline-variant/30 pt-3">
                  {spectrum.unclassified.publishers} publisher
                  {spectrum.unclassified.publishers === 1 ? "" : "s"} excluded from the spectrum for
                  lack of a classification.
                  {spectrum.unrated_credibility > 0 &&
                    ` ${spectrum.unrated_credibility} also have no credibility rating on record.`}
                </p>
              )}
            </section>

            {/* Publisher table. */}
            <section className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">All tracked publishers ({filtered.length})</h2>
                <label className="flex items-center gap-2 bg-zinc-900/60 border border-zinc-800/50 rounded px-2.5 py-1.5">
                  <Search className="w-3.5 h-3.5 text-zinc-600" aria-hidden />
                  <span className="sr-only">Filter publishers</span>
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Filter by name"
                    className="bg-transparent text-[11px] text-zinc-200 placeholder:text-zinc-600 outline-none w-40"
                  />
                </label>
              </div>

              {filtered.length === 0 ? (
                <p className="text-[11px] text-zinc-600">No publisher matches that filter.</p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {filtered.map((source) => {
                    const pos = axisPosition(source.bias_score);
                    return (
                      <li
                        key={source.id}
                        className="flex flex-wrap items-center gap-3 bg-zinc-900/40 border border-zinc-800/40 rounded px-3 py-2"
                      >
                        <span
                          className="w-2 h-2 rounded-full flex-shrink-0"
                          style={{
                            backgroundColor: biasColor(source.bias_lean),
                            ...(pos === null ? { border: "1px dashed #6b7280" } : {}),
                          }}
                        />
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[11px] text-zinc-200 hover:text-cyan-300 min-w-0 truncate"
                        >
                          {source.name}
                          <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-text-bottom" aria-hidden />
                        </a>

                        {source.is_classified ? (
                          <span
                            className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded border"
                            style={{
                              color: biasColor(source.bias_lean),
                              borderColor: `${biasColor(source.bias_lean)}55`,
                            }}
                          >
                            {BIAS_LABEL[source.bias_lean as string] ?? source.bias_lean}
                          </span>
                        ) : (
                          <span className="text-[9px] font-mono-data uppercase tracking-wider text-zinc-600 border border-dashed border-zinc-700 px-1.5 py-0.5 rounded">
                            unclassified
                          </span>
                        )}

                        {source.country && (
                          <span className="text-[9px] font-mono-data text-zinc-600 truncate">{source.country}</span>
                        )}

                        <span className="ml-auto flex items-center gap-3 flex-shrink-0">
                          <span className="text-[9px] font-mono-data text-zinc-500">
                            {source.article_count} art.
                          </span>
                          {source.is_rated ? (
                            <span className="text-[9px] font-mono-data text-zinc-500">
                              {Math.round(source.credibility_score as number * 100)}% trust
                            </span>
                          ) : (
                            <span
                              title="No credibility rating on record for this publisher"
                              className="text-[9px] font-mono-data text-zinc-600"
                            >
                              not rated
                            </span>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </>
        )}

        <p className="text-[10px] text-zinc-600 flex items-start gap-2">
          <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" aria-hidden />
          <span>
            Bias classifications are editorial judgements recorded per publisher. A publisher with no
            classification listed here has not been assessed, not been found neutral.
          </span>
        </p>
      </div>
    </main>
  );
}

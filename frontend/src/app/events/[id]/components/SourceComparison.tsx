"use client";

import React, { useEffect, useState } from "react";
import { Scale, ExternalLink, AlertTriangle, CircleSlash, Newspaper } from "lucide-react";
import { biasColor } from "../../../components/BiasSpectrum";
import type { Contradiction } from "../../../components/TransparencyPanel";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ComparisonOutlet {
  source_id: string;
  source_name: string;
  source_url: string;
  country: string | null;
  // Null when the publisher has no stored classification. Never defaulted.
  bias_lean: string | null;
  bias_score: number | null;
  is_classified: boolean;
  credibility_score: number | null;
  article_count: number;
  headlines: string[];
  excerpts: string[];
  article_ids: string[];
  article_urls: string[];
  published_at: string[];
}

interface ComparisonResponse {
  event_id: string;
  title: string;
  comparable: boolean;
  outlet_count: number;
  classified_outlet_count: number;
  outlets: ComparisonOutlet[];
  unclassified_outlets: ComparisonOutlet[];
  contradiction_count: number;
  contradiction_max_confidence: number | null;
  contradiction_level: string | null;
  contradictions: Contradiction[];
  note: string;
}

const BIAS_LABEL: Record<string, string> = {
  LEFT: "Left",
  CENTER_LEFT: "Center-left",
  CENTER: "Center",
  CENTER_RIGHT: "Center-right",
  RIGHT: "Right",
};

/**
 * Map a bias_score in [-1, 1] to a horizontal percentage.
 *
 * The API returns null for an unclassified outlet, and null maps to null here so
 * the caller can render the outlet outside the axis entirely. Returning 50
 * instead would place it at "Center", asserting a leaning we have no record of.
 */
function axisPosition(score: number | null): number | null {
  if (score === null || score === undefined) return null;
  return 50 + (Math.max(-1, Math.min(1, score)) * 50);
}

function CredibilityBadge({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return (
      <span
        title="No credibility rating on record for this publisher"
        className="text-[9px] font-mono-data text-zinc-600 border border-dashed border-zinc-700 px-1.5 py-0.5 rounded"
      >
        not rated
      </span>
    );
  }
  return (
    <span className="text-[9px] font-mono-data text-zinc-500">
      {Math.round(score * 100)}% trust
    </span>
  );
}

function OutletCard({ outlet, isUnclassified }: { outlet: ComparisonOutlet; isUnclassified: boolean }) {
  const headline = outlet.headlines[0];
  const excerpt = outlet.excerpts[0];
  // Deep-link the headline to the exact article that outlet published when the
  // backend supplied one; only fall back to the publisher homepage otherwise.
  const headlineHref = outlet.article_urls?.[0] || outlet.source_url;

  return (
    <article className="bg-zinc-900/50 border border-zinc-800/50 rounded p-3 flex flex-col gap-2 min-w-0">
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-zinc-100 truncate">{outlet.source_name}</span>
        {isUnclassified ? (
          <span className="text-[9px] font-mono-data uppercase tracking-wider text-zinc-500 border border-dashed border-zinc-700 px-1.5 py-0.5 rounded">
            unclassified
          </span>
        ) : (
          <span
            className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded border"
            style={{ color: biasColor(outlet.bias_lean), borderColor: `${biasColor(outlet.bias_lean)}55` }}
          >
            {BIAS_LABEL[outlet.bias_lean as string] ?? outlet.bias_lean}
          </span>
        )}
        {outlet.article_count > 1 && (
          <span className="text-[9px] font-mono-data text-zinc-500">
            {outlet.article_count} articles
          </span>
        )}
      </header>

      {/* The outlet's own words. Headline and excerpt are never rewritten, so
          the "how did each source frame it" comparison shows real framing. */}
      {headline ? (
        <a
          href={headlineHref}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-zinc-300 hover:text-cyan-300 leading-snug"
        >
          {headline}
          <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-text-bottom" aria-hidden />
        </a>
      ) : (
        <p className="text-[11px] text-zinc-600 italic">No headline recorded.</p>
      )}

      {excerpt && (
        <p className="text-[10px] text-zinc-500 leading-relaxed line-clamp-4">{excerpt}</p>
      )}

      <footer className="mt-auto flex items-center gap-3 pt-2 border-t border-zinc-800/40">
        <CredibilityBadge score={outlet.credibility_score} />
        {outlet.country && (
          <span className="text-[9px] font-mono-data text-zinc-600 truncate">{outlet.country}</span>
        )}
      </footer>
    </article>
  );
}

export default function SourceComparison({ eventId }: { eventId: string }) {
  const [data, setData] = useState<ComparisonResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/events/${eventId}/comparison`, {
          signal: controller.signal,
        });
        if (!res.ok) {
          // The error is surfaced verbatim. Substituting a friendlier claim
          // about the data would risk implying we have sources when we do not.
          setError(res.status === 404 ? "Event not found." : `Could not load outlet comparison (HTTP ${res.status}).`);
          return;
        }
        const body = (await res.json()) as ComparisonResponse;
        if (!cancelled) setData(body);
      } catch (err) {
        if ((err as Error).name !== "AbortError" && !cancelled) {
          setError("Could not reach the API for outlet comparison.");
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
  }, [eventId]);

  if (loading) {
    return (
      <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5">
        <p className="text-[11px] text-zinc-500 font-mono-data">Loading outlet comparison…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5">
        <p className="text-[11px] text-amber-400/90">{error}</p>
      </section>
    );
  }

  if (!data) return null;

  const { outlets, unclassified_outlets, comparable, outlet_count, contradiction_count, contradiction_max_confidence, contradiction_level } = data;

  // Nothing to compare: either no outlets at all, or none of them classified.
  if (outlets.length === 0) {
    return (
      <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5 flex flex-col gap-3">
        <header className="flex items-center gap-2">
          <Scale className="w-4 h-4 text-primary" />
          <h2 className="font-headline-lg text-headline-lg text-on-surface">
            Outlet Comparison
          </h2>
        </header>
        <p className="text-[11px] text-zinc-500 leading-relaxed flex items-start gap-2">
          <CircleSlash className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" aria-hidden />
          <span>
            {outlet_count === 0
              ? "No publisher is attached to any article in this event, so there is nothing to compare."
              : `This event draws on ${outlet_count} publisher${outlet_count === 1 ? "" : "s"}, but none carry a stored bias classification. They are listed below without a position on the spectrum — absence of a rating is not a centre rating.`}
          </span>
        </p>
        {unclassified_outlets.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {unclassified_outlets.map((o) => (
              <OutletCard key={o.source_id} outlet={o} isUnclassified />
            ))}
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5 flex flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Scale className="w-4 h-4 text-primary" />
          <h2 className="font-headline-lg text-headline-lg text-on-surface">
            Outlet Comparison
          </h2>
        </div>
        <span className="text-[9px] font-mono-data text-zinc-500">
          {outlets.length} classified publisher{outlets.length === 1 ? "" : "s"}
          {unclassified_outlets.length > 0 && ` · ${unclassified_outlets.length} unclassified`}
        </span>
      </header>

      {/* Cross-source conflicts recovered by the contradiction detector. The
          comparison endpoint ships the same signal as the event detail, so this
          panel surfaces it in context — a disagreement between the outlets is
          the most relevant thing a side-by-side can reveal. */}
      {contradiction_count > 0 && (
        <p className="text-[11px] text-amber-300/90 flex items-start gap-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" aria-hidden />
          <span>
            {contradiction_count} cross-source conflict{contradiction_count === 1 ? "" : "s"} detected across these
            outlets
            {contradiction_level === "reported"
              ? " (recorded without a confidence score)"
              : contradiction_max_confidence !== null && contradiction_max_confidence !== undefined
                ? ` · strongest at ${Math.round(contradiction_max_confidence * 100)}% confidence`
                : ""}
            . See the Article Transparency panel for the side-by-side pairs.
          </span>
        </p>
      )}

      {/* A single publisher is not a comparison. Saying so is more honest than
          rendering a one-column "side-by-side" that implies a spread. */}
      {!comparable && (
        <p className="text-[11px] text-amber-400/90 flex items-start gap-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" aria-hidden />
          <span>
            Only one classified publisher covers this event, so there is no side-by-side framing
            to show. Add a second independent publisher for a real comparison.
          </span>
        </p>
      )}

      {/* The axis. Markers are placed by each outlet&apos;s own score, so the
          spacing reflects real ratings rather than an assumed ordering. */}
      <div className="relative pt-9 pb-1">
        <div
          className="absolute inset-x-0 top-11 h-1.5 rounded-full"
          style={{
            background:
              "linear-gradient(90deg, #1d4ed8 0%, #3b82f6 22%, #9ca3af 50%, #f87171 78%, #dc2626 100%)",
            opacity: 0.45,
          }}
        />
        <div className="absolute inset-x-0 top-11">
          {(
            () => {
              // Stagger markers that share a position so overlapping outlets
              // remain individually visible instead of stacking into one dot.
              const atPosition = new Map<number, number>();
              return outlets.map((o) => {
                const pos = axisPosition(o.bias_score);
                if (pos === null) return null;
                const at = atPosition.get(pos) ?? 0;
                atPosition.set(pos, at + 1);
                const stagger = (at % 2 === 0 ? 1 : -1) * Math.ceil(at / 2) * 16;
                return (
                  <div
                    key={o.source_id}
                    className="absolute flex flex-col items-center pointer-events-none"
                    style={{ left: `${pos}%`, transform: `translateX(calc(-50% + ${stagger}px))` }}
                    title={`${o.source_name}: ${BIAS_LABEL[o.bias_lean as string] ?? o.bias_lean}`}
                  >
                    <span
                      className="w-3 h-3 rounded-full border-2 border-[#080c16] shadow"
                      style={{ backgroundColor: biasColor(o.bias_lean) }}
                    />
                    <span
                      className="mt-1 text-[8px] font-mono-data font-bold uppercase tracking-wide whitespace-nowrap"
                      style={{ color: biasColor(o.bias_lean) }}
                    >
                      {o.source_name}
                    </span>
                  </div>
                );
              });
            }
          )()}
        </div>
      </div>
      <div className="flex justify-between text-[9px] text-zinc-600 font-mono-data uppercase tracking-wider">
        <span>Left</span>
        <span>Center</span>
        <span>Right</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {outlets.map((o) => (
          <OutletCard key={o.source_id} outlet={o} isUnclassified={false} />
        ))}
      </div>

      {/* Unclassified publishers are kept visually separate from the spectrum,
          because they have no position on it. */}
      {unclassified_outlets.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-outline-variant/30 pt-3">
          <p className="text-[9px] font-mono-data uppercase tracking-wider text-zinc-600 flex items-center gap-1.5">
            <Newspaper className="w-3 h-3" aria-hidden />
            Unclassified publishers — no position on the spectrum
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {unclassified_outlets.map((o) => (
              <OutletCard key={o.source_id} outlet={o} isUnclassified />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

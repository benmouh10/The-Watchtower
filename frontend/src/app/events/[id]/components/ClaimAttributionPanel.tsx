"use client";

import React from "react";
import { ExternalLink, CircleSlash, CheckCircle, AlertTriangle, HelpCircle, Shield } from "lucide-react";

export interface ClaimSource {
  index: number;
  article_id: string;
  title: string;
  url: string;
  published_at?: string | null;
  source_name?: string | null;
  source_bias_lean?: string | null;
}

export interface EventClaim {
  id: string;
  ordinal: number;
  text: string;
  status: string | null;
  is_adjudicated: boolean;
  confidence: number | null;
  is_scored: boolean;
  citation_indices: number[];
  sources: ClaimSource[];
  unresolved_indices: number[];
  reviewed_by: string | null;
  review_url: string | null;
}

// Verdict styling. Each verdict keeps its own colour and icon so a status is
// never communicated by colour alone.
const VERDICT_STYLES: Record<string, { label: string; className: string; Icon: typeof CheckCircle }> = {
  "Corroborated": {
    label: "Corroborated",
    className: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
    Icon: CheckCircle,
  },
  "Partially verified": {
    label: "Partially verified",
    className: "text-amber-300 border-amber-500/40 bg-amber-500/10",
    Icon: AlertTriangle,
  },
  "Disputed": {
    label: "Disputed",
    className: "text-rose-300 border-rose-500/40 bg-rose-500/10",
    Icon: CircleSlash,
  },
  "Unverified": {
    label: "Unverified",
    className: "text-zinc-300 border-zinc-600/50 bg-zinc-700/20",
    Icon: HelpCircle,
  },
};

// An unrecognised status is shown verbatim rather than being coerced into one of
// the known verdicts. Mapping an unknown value onto "Unverified" would assert a
// judgement the system never made.
function styleFor(status: string | null) {
  if (status && VERDICT_STYLES[status]) return VERDICT_STYLES[status];
  return {
    label: status || "Not adjudicated",
    className: "text-zinc-400 border-zinc-700/60 bg-zinc-800/30",
    Icon: HelpCircle,
  };
}

/**
 * Confidence is rendered as a "not scored" state whenever the model expressed
 * no value. A zero-width or mid-grey bar would read as a low or middling score,
 * which is a different claim from "we did not score this".
 */
export function ConfidenceMeter({ value, isScored }: { value: number | null; isScored: boolean }) {
  if (!isScored || value === null) {
    return (
      <span className="text-[10px] uppercase tracking-wider text-zinc-500 border border-dashed border-zinc-700 px-1.5 py-0.5">
        Not scored
      </span>
    );
  }

  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span className="inline-flex items-center gap-1.5" title={`Model confidence in this verdict: ${pct}%`}>
      <span className="relative inline-block h-1 w-12 bg-zinc-700 overflow-hidden">
        <span
          className="absolute inset-y-0 left-0 bg-cyan-400"
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-[10px] font-mono text-zinc-400">{pct}%</span>
    </span>
  );
}

function InlineCitations({ claim }: { claim: EventClaim }) {
  const sources = claim.sources.filter((s) => s.url);
  const dangling = claim.unresolved_indices;
  if (sources.length === 0 && dangling.length === 0) return null;

  return (
    <sup className="inline-flex items-baseline gap-0.5 ml-1 not-italic align-super">
      {sources.map((source) => (
        <a
          key={source.article_id}
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Source ${source.index}: ${source.source_name || "source"} — ${source.title}`}
          title={`${source.source_name || "Unknown source"} — ${source.title}`}
          className="font-mono text-[11px] font-semibold text-cyan-400 hover:text-cyan-300 underline underline-offset-2 decoration-cyan-400/30"
        >
          [{source.index}]
        </a>
      ))}
      {dangling.map((idx) => (
        <span
          key={idx}
          aria-label={`Citation index ${idx} could not be resolved to an article`}
          title="Could not be resolved to an article in this cluster"
          className="font-mono text-[11px] text-amber-400/70"
        >
          [{idx}]
        </span>
      ))}
    </sup>
  );
}

function ClaimCard({ claim }: { claim: EventClaim }) {
  const { label, className, Icon } = styleFor(claim.status);
  const hasSources = claim.sources.length > 0;

  return (
    <li className="bg-zinc-900/50 border border-zinc-800/50 rounded p-3">
      <div className="flex items-start gap-3">
        <Icon className="w-4 h-4 mt-0.5 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-on-surface leading-relaxed">
            {claim.text}
            <InlineCitations claim={claim} />
          </p>

          <div className="flex flex-wrap items-center gap-2 mt-3">
            <span
              className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wider border px-1.5 py-0.5 ${className}`}
            >
              {label}
            </span>
            <ConfidenceMeter value={claim.confidence} isScored={claim.is_scored} />
          </div>

          {/* Sources. An empty list is stated in words, because a claim with no
              attribution looks identical to a claim whose attribution failed
              to load. */}
          {hasSources ? (
            <ul className="mt-3 space-y-1.5">
              {claim.sources.map((source) => (
                <li key={source.article_id} className="flex items-start gap-2 text-xs">
                  <span className="font-mono text-cyan-400 shrink-0">[{source.index}]</span>
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-zinc-300 hover:text-cyan-300 underline underline-offset-2 min-w-0"
                  >
                    {source.source_name || "Unknown source"}
                    <span className="text-zinc-500"> — </span>
                    <span className="text-zinc-400">{source.title}</span>
                    <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-text-bottom" aria-hidden />
                  </a>
                  {source.source_bias_lean && (
                    <span className="text-[10px] uppercase tracking-wider text-zinc-500 shrink-0">
                      {source.source_bias_lean.replace(/_/g, " ").toLowerCase()}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-xs text-zinc-500 flex items-center gap-1.5">
              <CircleSlash className="w-3 h-3 shrink-0" aria-hidden />
              No source in this cluster supports this claim.
            </p>
          )}

          {/* A dangling index is a data bug, so it is surfaced rather than
              silently resolved to some other article. */}
          {claim.unresolved_indices.length > 0 && (
            <p className="mt-2 text-[10px] text-amber-400/80 font-mono">
              unresolved citation index: {claim.unresolved_indices.join(", ")}
            </p>
          )}

          {/* Only rendered when a real third-party feed populated these. */}
          {claim.reviewed_by && (
            <a
              href={claim.review_url || undefined}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-violet-300 hover:text-violet-200"
            >
              <Shield className="w-3 h-3" aria-hidden />
              Reviewed by {claim.reviewed_by}
            </a>
          )}
        </div>
      </div>
    </li>
  );
}

export default function ClaimAttributionPanel({ claims }: { claims?: EventClaim[] }) {
  const list = claims || [];

  // Only render the panel when claims were actually extracted. An empty panel
  // is not the same as "we checked and found no claims", and a heading reading
  // "0 claims" on every unprocessed event would imply a completed analysis.
  if (list.length === 0) return null;

  const corroborated = list.filter((c) => c.status === "Corroborated").length;
  const disputed = list.filter((c) => c.status === "Disputed").length;
  const unsupported = list.filter((c) => c.sources.length === 0).length;

  return (
    <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5 flex flex-col gap-4" aria-labelledby="claims-heading">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-outline-variant/40 pb-3">
        <h2 id="claims-heading" className="flex items-center gap-2 text-sm font-semibold text-on-surface">
          Claim-by-claim sources
        </h2>
        <p className="text-[10px] uppercase tracking-wider text-zinc-500">
          {corroborated} corroborated
          {disputed > 0 && ` · ${disputed} disputed`}
          {unsupported > 0 && ` · ${unsupported} unsupported`}
        </p>
      </header>

      <p className="text-xs text-zinc-500 leading-relaxed">
        The numbered markers after each claim are links to the specific articles that support it; the
        reference list below names each one. A marker with no resolved article is shown but disabled.
        Confidence reflects the model&apos;s certainty in the verdict, not the truth of the claim.
      </p>

      <ul className="space-y-3">
        {list.map((claim) => (
          <ClaimCard key={claim.id} claim={claim} />
        ))}
      </ul>
    </section>
  );
}

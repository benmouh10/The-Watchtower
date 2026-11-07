"use client";

import React from "react";
import { AlertTriangle, ShieldCheck, Users, FileText, Info } from "lucide-react";
import ContradictionBadge, { conflictIntensity } from "./ContradictionBadge";

export interface Transparency {
  article_count: number;
  source_count: number;
  outlets: string[];
  mean_source_credibility: number;
  credibility_rated_sources: number;
  unrated_sources: number;
  contradictions_detected: number;
  editor_confidence_score: number;
  confidence_factors: {
    source_credibility: number;
    source_diversity: number;
    agreement: number;
  };
  /** False when fewer than two sources exist, so agreement was not measured. */
  agreement_applicable: boolean;
  /** null when no outlet carries a bias classification. */
  bias_consensus: number | null;
  unclassified_sources: number;
  /** "verified" = every inline marker resolves; "partial" = some markers orphaned; "unavailable" = pre-provenance event. */
  citation_provenance: "verified" | "partial" | "unavailable";
  citation_markers?: number;
  citation_sources_count?: number;
  citation_unresolved_markers?: number;
}

export interface Contradiction {
    type: string;
    nature: string;
    // None when the row was written before the web detector existed (the offline
    // pipeline emitted pairs without a score). Rendering "Not scored" is honest;
    // subtracting a fabricated 0 from agreement terms was never legitimate.
    confidence: number | null;
    // Stable identifier derived from the unordered article-ID pair, so the same
    // conflict keeps the same id across re-runs and can be linked to or cited.
    // Optional because rows detected before ids existed have none; rendering it
    // as absent is correct, and inventing a fallback would break linking.
    contradiction_id?: string;
    claim_a: { text: string; source: string; article_id: string };
    claim_b: { text: string; source: string; article_id: string };
  }

const FACTOR_LABELS: Record<string, string> = {
  source_credibility: "Source credibility",
  source_diversity: "Source diversity",
  agreement: "Outlet agreement",
};

// The API reports bias on the canonical -1..1 axis (LEFT=-1 ... RIGHT=1, see
// _BIAS_SCALE in event_controller). Map it onto the 1..5 spectrum and apply the
// same thresholds the admin dashboard uses for its aggregated lean, so the two
// surfaces always agree on where a consensus sits.
const BIAS_SCALE_1to5 = (value: number) => ((value + 1) / 2) * 4 + 1;

function biasLabel(value: number): string {
  const v = BIAS_SCALE_1to5(value);
  if (v < 2.2) return "Left";
  if (v < 2.8) return "Center-left";
  if (v < 3.2) return "Center";
  if (v < 3.8) return "Center-right";
  return "Right";
}

function confidenceTone(score: number) {
  if (score >= 70) return { text: "text-emerald-400", bar: "bg-emerald-500", label: "HIGH" };
  if (score >= 45) return { text: "text-amber-400", bar: "bg-amber-500", label: "MODERATE" };
  return { text: "text-rose-400", bar: "bg-rose-500", label: "LOW" };
}

export default function TransparencyPanel({
  transparency,
  contradictions,
  updatedAt,
  contradictionSignal,
}: {
  transparency: Transparency | null;
  contradictions: Contradiction[];
  updatedAt?: string | null;
  // Event-level signal derived by the backend serialiser. Optional so callers
  // that only have the stored pairs can fall back to the pairs themselves.
  contradictionSignal?: { count: number; max_confidence: number | null; level: string } | null;
}) {
  if (!transparency) return null;

  const tone = confidenceTone(transparency.editor_confidence_score);
  const hasContradictions = contradictions.length > 0;
  const provenanceVerified = transparency.citation_provenance === "verified";
  const provenancePartial = transparency.citation_provenance === "partial";
  const hiddenCount = Math.max(
    0,
    (transparency.article_count ?? 0) - (transparency.source_count ?? 0),
  );

  return (
    <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5 flex flex-col gap-5">
      <header className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-primary" />
          <h2 className="font-headline-lg text-headline-lg text-on-surface">
            Article Transparency
          </h2>
        </div>
        <span
          className={`text-[9px] font-bold uppercase tracking-wider px-2 py-1 rounded border ${tone.text} ${
            transparency.editor_confidence_score >= 70
              ? "border-emerald-900/50 bg-emerald-950/40"
              : transparency.editor_confidence_score >= 45
              ? "border-amber-900/50 bg-amber-950/40"
              : "border-rose-900/50 bg-rose-950/40"
          }`}
        >
          {tone.label} CONFIDENCE
        </span>
      </header>

      <div className="flex items-center gap-4">
        <div className="flex-1 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[10px] font-mono-data uppercase tracking-wider">
            <span className="text-zinc-500">Editorial confidence</span>
            <span className={`font-bold ${tone.text}`}>
              {transparency.editor_confidence_score.toFixed(1)} / 100
            </span>
          </div>
          <div className="h-1.5 w-full bg-zinc-800 rounded-full overflow-hidden">
            <div
              className={`h-full ${tone.bar} transition-all duration-500`}
              style={{ width: `${Math.max(0, Math.min(100, transparency.editor_confidence_score))}%` }}
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        <Metric
          icon={<FileText className="w-3 h-3" />}
          label="Sources used"
          value={`${transparency.source_count}`}
          sub={
            hiddenCount > 0
              ? `${hiddenCount} stored article${hiddenCount === 1 ? "" : "s"} condensed out`
              : `${transparency.article_count} article${transparency.article_count === 1 ? "" : "s"}`
          }
        />
        <Metric
          icon={<Users className="w-3 h-3" />}
          label="Outlets"
          value={`${transparency.outlets.length}`}
          sub={transparency.outlets.slice(0, 2).join(", ") || "—"}
        />
        <Metric
          icon={<AlertTriangle className="w-3 h-3" />}
          label="Contradictions"
          value={`${transparency.contradictions_detected}`}
          sub={hasContradictions ? "cross-source conflict" : "none detected"}
          alert={hasContradictions}
        />
        <Metric
          icon={<ShieldCheck className="w-3 h-3" />}
          label="Mean trust"
          value={
            transparency.credibility_rated_sources > 0
              ? `${Math.round(transparency.mean_source_credibility * 100)}%`
              : "Not rated"
          }
          sub={
            transparency.unrated_sources > 0
              ? `${transparency.credibility_rated_sources} rated, ${transparency.unrated_sources} unrated`
              : "across outlets"
          }
        />
        <Metric
          icon={<Users className="w-3 h-3" />}
          label="Bias consensus"
          value={
            transparency.bias_consensus !== null && transparency.bias_consensus !== undefined
              ? biasLabel(transparency.bias_consensus)
              : "Not rated"
          }
          sub={
            transparency.bias_consensus !== null && transparency.bias_consensus !== undefined
              ? transparency.unclassified_sources > 0
                ? `${transparency.unclassified_sources} outlet${transparency.unclassified_sources === 1 ? "" : "s"} unclassified`
                : "all outlets classified"
              : "no outlet classified"
          }
        />
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">
          How this score is calculated
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {Object.entries(transparency.confidence_factors).map(([key, value]) => (
            <div key={key} className="bg-zinc-900/50 border border-zinc-800/50 rounded p-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] text-zinc-400 uppercase tracking-wide">
                  {FACTOR_LABELS[key] ?? key}
                </span>
                <span className="text-[10px] font-mono-data font-bold text-zinc-200">
                  {key === "agreement" && !transparency.agreement_applicable
                    ? "not measured"
                    : `${Math.round(value * 100)}%`}
                </span>
              </div>
              <div className="h-1 w-full bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary/70"
                  style={{
                    width:
                      key === "agreement" && !transparency.agreement_applicable
                        ? "0%"
                        : `${Math.round(value * 100)}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-zinc-600 leading-relaxed">
          45% source credibility + 25% source diversity + 30% outlet agreement.
          Contradictions reduce the agreement term. Agreement is only measured
          across two or more independent outlets, so a single-source dossier
          scores on credibility and diversity alone.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">
          What this assessment cannot claim
        </span>
        <ul className="flex flex-col gap-1.5">
          {[
            {
              applies: !transparency.agreement_applicable,
              text: "Agreement was not measured: fewer than two independent outlets cover this event.",
            },
            {
              applies: transparency.unrated_sources > 0,
              text: `${transparency.unrated_sources} outlet${transparency.unrated_sources === 1 ? "" : "s"} ha${transparency.unrated_sources === 1 ? "s" : "ve"} no credibility rating and was not scored on trust (it is neither penalized nor assumed neutral).`,
            },
            {
              applies: transparency.unclassified_sources > 0,
              text: `${transparency.unclassified_sources} outlet${transparency.unclassified_sources === 1 ? "" : "s"} ha${transparency.unclassified_sources === 1 ? "s" : "ve"} no bias classification so the consensus excludes ${transparency.unclassified_sources === 1 ? "it" : "them"}.`,
            },
          ]
            .filter((item) => item.applies)
            .map((item) => (
              <li
                key={item.text}
                className="flex items-start gap-2 text-[10px] text-amber-300/80 leading-relaxed"
              >
                <Info className="w-3 h-3 mt-0.5 flex-shrink-0" />
                <span>{item.text}</span>
              </li>
            ))}
          <li className="flex items-start gap-2 text-[10px] text-zinc-600 leading-relaxed">
            <Info className="w-3 h-3 mt-0.5 flex-shrink-0" />
            <span>
              Confidence and consensus are transparency formulas over the sources
              read during synthesis — they rate reporting coverage, not the
              factual truth of the underlying claims.
            </span>
          </li>
        </ul>
      </div>

      <div
        className={`flex items-start gap-2 rounded p-3 border text-[10px] leading-relaxed ${
          provenanceVerified
            ? "border-emerald-900/40 bg-emerald-950/20 text-emerald-300/90"
            : "border-amber-900/40 bg-amber-950/20 text-amber-300/90"
        }`}
      >
        <Info className="w-3 h-3 mt-0.5 flex-shrink-0" />
        <span>
          {provenanceVerified
            ? `Every inline citation in this summary is linked to the exact article that was read during synthesis.`
            : provenancePartial
              ? `${transparency.citation_unresolved_markers} inline citation${(transparency.citation_unresolved_markers ?? 0) === 1 ? "" : "s"} could not be linked to the articles read during synthesis; only the ${transparency.citation_sources_count} recorded sources are shown as links.`
              : "Citation provenance is unavailable for this event. It was synthesized before source tracking was recorded, so inline citation numbers cannot be resolved to specific articles and are not shown as links."}
        </span>
      </div>

      {updatedAt && (
        <p className="text-[10px] text-zinc-600 font-mono-data">
          Last synthesis update: {new Date(updatedAt).toUTCString()}
        </p>
      )}

      {hasContradictions && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <span className="text-[9px] text-rose-400 uppercase tracking-wider font-mono-data">
              Side-by-side contradictions
            </span>
            {contradictionSignal && contradictionSignal.count > 0 && (
              <ContradictionBadge
                level={contradictionSignal.level}
                count={contradictionSignal.count}
                maxConfidence={contradictionSignal.max_confidence}
              />
            )}
          </div>
          {contradictions.map((pair, idx) => {
            const tone = conflictIntensity(pair.confidence);
            return (
              <div
                // The stable id is preferred as the React key so a re-detected
                // set is reconciled by identity. Index is only a fallback for
                // legacy rows that predate ids.
                key={pair.contradiction_id ?? idx}
                className="grid grid-cols-1 md:grid-cols-2 gap-3 bg-rose-950/10 border border-rose-900/30 rounded p-3"
              >
                <ClaimCard claim={pair.claim_a} align="left" />
                <ClaimCard claim={pair.claim_b} align="right" />
                <div className="md:col-span-2 flex items-center gap-3 pt-1 border-t border-rose-900/20 flex-wrap">
                  <span
                    className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded border border-rose-900/30 text-rose-300/80"
                    title="Detector verdict: a numeric mismatch or a negation between outlets"
                  >
                    {pair.nature.replace(/_/g, " ")}
                  </span>
                  {pair.confidence === null || pair.confidence === undefined ? (
                    <span
                      className="text-[9px] font-mono-data uppercase tracking-wider text-zinc-500 border border-dashed border-zinc-700 px-1.5 py-0.5 rounded"
                      title="This conflict was recorded before the detector assigned scores, so no degree of confidence is known."
                    >
                      Not scored
                    </span>
                  ) : (
                    <>
                      <div
                        className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden max-w-[160px]"
                        title={`Detector confidence this is a real conflict between these two outlets: ${Math.round(
                          pair.confidence * 100,
                        )}%`}
                      >
                        <div
                          className={`h-full ${tone.bar}`}
                          style={{ width: `${Math.max(0, Math.min(100, pair.confidence * 100))}%` }}
                        />
                      </div>
                      <span className={`text-[9px] font-mono-data font-bold ${tone.text}`}>
                        {tone.label} · {Math.round(pair.confidence * 100)}% confidence
                      </span>
                    </>
                  )}
                  {/* Only rendered when the detector assigned a stable id. */}
                  {pair.contradiction_id && (
                    <span
                      className="text-[9px] font-mono-data text-zinc-500 ml-auto"
                      title="Stable identifier for this contradiction, stable across re-detection"
                    >
                      id: {pair.contradiction_id}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Metric({
  icon,
  label,
  value,
  sub,
  alert,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  alert?: boolean;
}) {
  return (
    <div
      className={`rounded p-2.5 flex flex-col gap-0.5 border ${
        alert ? "bg-rose-950/20 border-rose-900/40" : "bg-zinc-900/50 border-zinc-800/50"
      }`}
    >
      <span
        className={`text-[9px] uppercase tracking-wider font-mono-data flex items-center gap-1 ${
          alert ? "text-rose-400" : "text-zinc-500"
        }`}
      >
        {icon}
        {label}
      </span>
      <span className={`text-sm font-bold ${alert ? "text-rose-300" : "text-zinc-100"}`}>
        {value}
      </span>
      <span className="text-[9px] text-zinc-500 truncate">{sub}</span>
    </div>
  );
}

function ClaimCard({
  claim,
  align,
}: {
  claim: { text: string; source: string };
  align: "left" | "right";
}) {
  return (
    <div
      className={`bg-zinc-900/60 border border-zinc-800/60 rounded p-3 flex flex-col gap-1.5 ${
        align === "right" ? "md:text-right" : ""
      }`}
    >
      <span className="text-[9px] font-mono-data uppercase tracking-wider text-primary font-bold">
        {claim.source}
      </span>
      <p className="text-[11px] text-zinc-300 leading-relaxed">{claim.text}</p>
    </div>
  );
}

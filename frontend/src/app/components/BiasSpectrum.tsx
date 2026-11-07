"use client";

import React from "react";
import { Scale } from "lucide-react";

export interface BiasSource {
  name: string;
  bias_lean: string | null;
  credibility_score: number | null;
  country?: string | null;
  articleCount: number;
}

const BIAS_POSITION: Record<string, number> = {
  LEFT: 10,
  CENTER_LEFT: 30,
  CENTER: 50,
  CENTER_RIGHT: 70,
  RIGHT: 90,
};

const BIAS_LABEL: Record<string, string> = {
  LEFT: "Left",
  CENTER_LEFT: "Center-left",
  CENTER: "Center",
  CENTER_RIGHT: "Center-right",
  RIGHT: "Right",
};

/**
 * Resolve an outlet's position on the spectrum, or null when it has no
 * classification. Returning null matters: defaulting to 50 would place an
 * unclassified outlet exactly at "Center", asserting a political leaning we
 * have no evidence for and making it indistinguishable from a genuinely
 * centred one.
 */
function positionFor(lean: string | null): number | null {
  if (!lean) return null;
  return BIAS_POSITION[lean] ?? null;
}

export function biasColor(lean: string | null): string {
  switch (lean) {
    case "LEFT":
      return "#3b82f6";
    case "CENTER_LEFT":
      return "#60a5fa";
    case "CENTER":
      return "#9ca3af";
    case "CENTER_RIGHT":
      return "#f87171";
    case "RIGHT":
      return "#ef4444";
    default:
      // Unclassified — a hatched/dashed neutral, deliberately distinct from
      // the solid grey that means an actual CENTER rating.
      return "#4b5563";
  }
}

function biasTextColor(lean: string | null): string {
  switch (lean) {
    case "LEFT":
      return "text-blue-400";
    case "CENTER_LEFT":
      return "text-blue-300";
    case "CENTER":
      return "text-zinc-400";
    case "CENTER_RIGHT":
      return "text-red-300";
    case "RIGHT":
      return "text-red-400";
    default:
      return "text-zinc-600";
  }
}

export default function BiasSpectrum({ sources }: { sources: BiasSource[] }) {
  if (sources.length === 0) return null;

  const classified = sources.filter((s) => positionFor(s.bias_lean) !== null);
  const unclassified = sources.filter((s) => positionFor(s.bias_lean) === null);

  // Spread is measured only across classified outlets, so an unclassified one
  // can neither create nor hide polarization.
  const positions = classified.map((s) => positionFor(s.bias_lean) as number);
  const spread =
    positions.length > 1 ? Math.max(...positions) - Math.min(...positions) : 0;
  const isPolarized = spread >= 40;

  return (
    <section className="glass-dossier rounded-lg border border-outline-variant/50 p-5 flex flex-col gap-4">
      <header className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Scale className="w-4 h-4 text-primary" />
          <h2 className="font-headline-lg text-headline-lg text-on-surface">
            Bias Distribution
          </h2>
        </div>
        <span
          className={`text-[9px] font-bold uppercase tracking-wider px-2 py-1 rounded border ${
            isPolarized
              ? "border-amber-900/50 bg-amber-950/40 text-amber-400"
              : "border-zinc-800 bg-zinc-900 text-zinc-400"
          }`}
        >
          {isPolarized ? "Ideological spread" : "Concentrated"}
        </span>
      </header>

      {classified.length === 0 ? (
        <p className="text-[11px] text-zinc-500 leading-relaxed">
          No outlets in this dossier carry a publisher bias classification, so
          no distribution can be shown. Absence of classification is not
          evidence of a centre-right or centre-left leaning.
        </p>
      ) : (
        <>
          <div className="relative pt-8 pb-2">
            <div className="absolute inset-x-0 top-10 h-1.5 rounded-full"
              style={{
                background:
                  "linear-gradient(90deg, #1d4ed8 0%, #3b82f6 22%, #9ca3af 50%, #f87171 78%, #dc2626 100%)",
                opacity: 0.55,
              }}
            />
            <div className="absolute inset-x-0 top-10 flex justify-between pointer-events-none">
              {(
                () => {
                  // Stagger markers sharing a position so sources at the same
                  // lean stay individually readable rather than stacked.
                  const atPosition = new Map<number, number>();
                  return classified.map((src, idx) => {
                    const pos = positionFor(src.bias_lean) as number;
                    const at = atPosition.get(pos) ?? 0;
                    atPosition.set(pos, at + 1);
                    const stagger = (at % 2 === 0 ? 1 : -1) * Math.ceil(at / 2) * 16;
                    return (
                      <div
                        key={`${src.name}-${idx}`}
                        className="absolute flex flex-col items-center"
                        style={{ left: `${pos}%`, transform: `translateX(calc(-50% + ${stagger}px))` }}
                      >
                        <span
                          className="w-3.5 h-3.5 rounded-full border-2 border-[#080c16] shadow-lg"
                          style={{ backgroundColor: biasColor(src.bias_lean) }}
                        />
                        <span
                          className="mt-1.5 text-[9px] font-mono-data font-bold uppercase tracking-wide whitespace-nowrap"
                          style={{ color: biasColor(src.bias_lean) }}
                        >
                          {src.name}
                        </span>
                        <span className="text-[8px] text-zinc-500 font-mono-data whitespace-nowrap">
                          {BIAS_LABEL[src.bias_lean as string] ?? src.bias_lean}
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
        </>
      )}

      <div className="flex flex-col gap-1.5 border-t border-outline-variant/30 pt-3">
        {sources.map((src, idx) => (
          <div
            key={`row-${src.name}-${idx}`}
            className="flex items-center justify-between gap-3 bg-zinc-900/40 border border-zinc-800/40 rounded px-2.5 py-1.5"
          >
            <div className="flex items-center gap-2 min-w-0">
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{
                  backgroundColor: biasColor(src.bias_lean),
                  ...(positionFor(src.bias_lean) === null
                    ? { border: "1px dashed #6b7280" }
                    : {}),
                }}
              />
              <span className="text-[11px] text-zinc-200 truncate">{src.name}</span>
              {src.country && (
                <span className="text-[9px] text-zinc-600 font-mono-data truncate">
                  {src.country}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              <span className="text-[9px] text-zinc-500 font-mono-data">
                {src.articleCount} art.
              </span>
              {src.credibility_score === null || src.credibility_score === undefined ? (
                <span
                  title="No credibility rating on record for this publisher"
                  className="text-[9px] text-zinc-600 font-mono-data"
                >
                  not rated
                </span>
              ) : (
                <span className="text-[9px] text-zinc-500 font-mono-data">
                  {Math.round(src.credibility_score * 100)}% trust
                </span>
              )}
              <span
                className={`text-[9px] font-bold uppercase tracking-wide w-24 text-right ${biasTextColor(
                  src.bias_lean
                )}`}
              >
                {src.bias_lean
                  ? BIAS_LABEL[src.bias_lean] ?? src.bias_lean
                  : "Unclassified"}
              </span>
            </div>
          </div>
        ))}
      </div>

      {unclassified.length > 0 && (
        <p className="text-[10px] text-zinc-600 leading-relaxed">
          {unclassified.length} outlet{unclassified.length === 1 ? "" : "s"}{" "}
          {unclassified.length === 1 ? "has" : "have"} no bias classification on
          record and {unclassified.length === 1 ? "is" : "are"} excluded from
          the spectrum above.
        </p>
      )}

      <p className="text-[10px] text-zinc-600 leading-relaxed">
        Lean is the publisher&apos;s editorial classification, not a score of any individual
        article. Position reflects the outlet&apos;s standing category. An outlet
        marked unclassified has no rating on record — that is missing data, not a
        centre position.
      </p>
    </section>
  );
}

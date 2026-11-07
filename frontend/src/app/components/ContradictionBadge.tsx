"use client";

import React from "react";
import { AlertTriangle } from "lucide-react";

// Event-level contradiction signal derived by the backend at serialisation time
// (see _contradiction_signal in event_controller). "reported" means conflict
// pairs exist but none carried a stored confidence score; "none" means no
// conflict detected. Both are distinct from a low score.
export type ContradictionLevel =
  | "strong"
  | "moderate"
  | "weak"
  | "minimal"
  | "reported"
  | "none";

const LEVEL_STYLES: Record<string, { label: string; cls: string }> = {
  strong: {
    label: "Strong conflict",
    cls: "text-rose-300 border-rose-900/50 bg-rose-950/40",
  },
  moderate: {
    label: "Moderate conflict",
    cls: "text-amber-300 border-amber-900/50 bg-amber-950/40",
  },
  weak: {
    label: "Weak conflict",
    cls: "text-zinc-400 border-zinc-700/60 bg-zinc-900/60",
  },
  minimal: {
    label: "Minimal conflict",
    cls: "text-zinc-500 border-zinc-700/60 bg-zinc-900/60",
  },
  reported: {
    label: "Unscored conflicts",
    cls: "text-zinc-500 border-dashed border-zinc-700 bg-zinc-900/60",
  },
};

// Per-pair intensity on the 0..1 confidence scale used by the detector. A pair
// without a stored score (confidence null) is unscored, never a 0%.
export function conflictIntensity(confidence: number | null): {
  text: string;
  bar: string;
  label: string;
} {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) {
    return { text: "text-zinc-500", bar: "bg-zinc-600", label: "Unscored" };
  }
  if (confidence >= 0.75) return { text: "text-rose-400", bar: "bg-rose-500", label: "Strong" };
  if (confidence >= 0.5) return { text: "text-amber-400", bar: "bg-amber-500", label: "Moderate" };
  return { text: "text-zinc-400", bar: "bg-zinc-500", label: "Weak" };
}

export function contradictionLevelLabel(level: string | null | undefined): string {
  return LEVEL_STYLES[level ?? ""]?.label ?? "Unscored conflicts";
}

export default function ContradictionBadge({
  level,
  count,
  maxConfidence,
  className = "",
}: {
  level?: string | null;
  count?: number;
  maxConfidence?: number | null;
  className?: string;
}) {
  if (!count || count <= 0 || !level || level === "none") return null;

  const style = LEVEL_STYLES[level] ?? LEVEL_STYLES.reported;
  const title =
    maxConfidence !== null && maxConfidence !== undefined
      ? `${count} cross-source conflict${count === 1 ? "" : "s"} · strongest detected at ${Math.round(
          maxConfidence * 100,
        )}% confidence`
      : `${count} cross-source conflict${count === 1 ? "" : "s"} recorded without a confidence score`;

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border ${style.cls} ${className}`}
      title={title}
    >
      <AlertTriangle className="w-3 h-3" />
      {style.label}
    </span>
  );
}
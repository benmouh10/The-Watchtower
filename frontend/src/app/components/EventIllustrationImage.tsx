/**
 * Event illustration, rendered where artwork is available.
 *
 * Deliberately renders nothing when the API reports no image: most events will
 * not have one for a long while, and a grey placeholder box on every card would
 * read as a broken page rather than as a missing feature.
 *
 * A plain <img> rather than next/image on purpose: these are already-compressed
 * 1024px PNGs served from our own volume, and Next's optimiser would add a
 * resizing hop plus an on-disk cache for no gain.
 */
/* eslint-disable @next/next/no-img-element */
export interface EventIllustration {
  url: string;
  width?: number;
  height?: number;
  attribution?: string;
  provider?: string;
  model?: string;
}

interface IllustrationProps {
  illustration?: EventIllustration | null;
  /** Extra classes for the <img>; height is left to the caller. */
  className?: string;
  alt?: string;
  /** Rendered small and cropped for a card; larger and full-width on detail. */
  variant?: "card" | "hero" | "fill";
}

export default function EventIllustrationImage({
  illustration,
  className = "",
  alt,
  variant = "card",
}: IllustrationProps) {
  if (!illustration?.url) return null;

  // Only the variants that actually render the caption can point at it; on a
  // card the caption is omitted, so an aria-describedby there would dangle.
  const describedBy =
    illustration.attribution && variant !== "card"
      ? "illustration-attribution"
      : undefined;

  // "fill" hands the whole <img> over to a caller that owns the frame and the
  // caption: the hero box on a detail page has its own fixed height and
  // overflow-hidden, so a <figure> inside it would clip the attribution. The
  // caller renders that below the frame instead.
  if (variant === "fill") {
    return (
      <img
        src={illustration.url}
        alt={alt ?? ""}
        aria-describedby={describedBy}
        width={illustration.width || undefined}
        height={illustration.height || undefined}
        className={`w-full h-full object-cover ${className}`}
      />
    );
  }

  return (
    <figure className="m-0">
      <img
        src={illustration.url}
        // The headline is the accessible label; the artwork is decorative
        // context for it, and repeating it would be noise for screen readers.
        alt={alt ?? ""}
        aria-describedby={describedBy}
        loading={variant === "hero" ? "eager" : "lazy"}
        decoding="async"
        width={illustration.width || undefined}
        height={illustration.height || undefined}
        className={
          variant === "hero"
            ? `w-full h-auto rounded-lg border border-outline-variant/60 object-cover ${className}`
            : `w-full object-cover rounded-md border border-outline-variant/40 ${className}`
        }
      />
      {variant === "hero" && illustration.attribution ? (
        <figcaption
          id="illustration-attribution"
          className="font-mono-data text-[10px] text-on-surface-variant mt-2"
        >
          {illustration.attribution}
        </figcaption>
      ) : null}
    </figure>
  );
}
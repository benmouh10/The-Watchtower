"use client";

import React, { useEffect } from "react";
import L from "leaflet";
import { MapContainer, TileLayer, Marker, useMap, Tooltip, Popup } from "react-leaflet";
import type { EventIllustration } from "./EventIllustrationImage";

interface MapEvent {
  id: string;
  title: string;
  summary: string;
  topic: string;
  country: string;
  latitude: number;
  longitude: number;
  importance_score: number;
  source_count: number;
  bias_lean?: string;
  /** Sent by /api/v1/events/map; present once the event has artwork. */
  illustration?: EventIllustration | null;
}

/**
 * Thumbnail for a marker preview. Plain <img> rather than EventIllustrationImage:
 * that component owns a <figure>, a caption and full-width sizing, and a marker
 * preview needs neither — it needs a fixed square that will not push the
 * tooltip around. The permanent "generated image" label lives on the event
 * detail page, where the artwork is presented; here it is offered as a tooltip
 * so the credit is still reachable without crowding a 220px popup.
 */
function MarkerThumb({
  illustration,
  className = "h-11 w-11",
}: {
  illustration?: EventIllustration | null;
  /** Base size class; the cluster tooltip uses a smaller one to fit three. */
  className?: string;
}) {
  if (!illustration?.url) return null;
  return (
    <img
      src={illustration.url}
      alt=""
      title={illustration.attribution || undefined}
      loading="lazy"
      decoding="async"
      className={`${className} shrink-0 rounded border border-outline-variant/40 object-cover`}
    />
  );
}

interface MapProps {
  events: MapEvent[];
  selectedEvent: MapEvent | null;
  onSelectEvent: (event: MapEvent) => void;
  /** If set, only markers matching this topic (case-insensitive) are fully visible */
  activeTopicFilter?: string | null;
}

// ─── Topic color palette ───────────────────────────────────────────────────────
// Each entry: { dot CSS color, ping CSS color, hex for tooltip label }
const TOPIC_PALETTE: Record<string, { dot: string; ping: string; hex: string; label: string }> = {
  POLITICS:       { dot: "bg-rose-500    border-rose-300",   ping: "bg-rose-500/40",    hex: "#f43f5e", label: "Politics"    },
  GEOPOLITICS:    { dot: "bg-rose-600    border-rose-400",   ping: "bg-rose-600/40",    hex: "#e11d48", label: "Geopolitics" },
  ECONOMY:        { dot: "bg-emerald-400 border-emerald-200",ping: "bg-emerald-400/40", hex: "#34d399", label: "Economy"     },
  MACROECONOMICS: { dot: "bg-emerald-500 border-emerald-300",ping: "bg-emerald-500/40", hex: "#10b981", label: "Economy"     },
  TECHNOLOGY:     { dot: "bg-violet-500  border-violet-300", ping: "bg-violet-500/40",  hex: "#8b5cf6", label: "Technology"  },
  "TECH & CYBER": { dot: "bg-violet-600  border-violet-400", ping: "bg-violet-600/40",  hex: "#7c3aed", label: "Technology"  },
  SPORTS:         { dot: "bg-sky-400     border-sky-200",    ping: "bg-sky-400/40",     hex: "#38bdf8", label: "Sports"      },
  HEALTH:         { dot: "bg-purple-400  border-purple-200", ping: "bg-purple-400/40",  hex: "#c084fc", label: "Health"      },
  WORLD:          { dot: "bg-amber-400   border-amber-200",  ping: "bg-amber-400/40",   hex: "#fbbf24", label: "World"       },
  DEFAULT:        { dot: "bg-cyan-400    border-cyan-200",   ping: "bg-cyan-400/40",    hex: "#22d3ee", label: "General"     },
};

function getTopicPalette(topic: string) {
  const key = (topic || "").toUpperCase().trim();
  return TOPIC_PALETTE[key] ?? TOPIC_PALETTE.DEFAULT;
}

// ─── MapController: smooth fly-to on event selection ──────────────────────────
function MapController({ selectedEvent }: { selectedEvent: MapEvent | null }) {
  const map = useMap();
  useEffect(() => {
    if (
      selectedEvent &&
      typeof selectedEvent.latitude === "number" &&
      typeof selectedEvent.longitude === "number"
    ) {
      map.flyTo([selectedEvent.latitude, selectedEvent.longitude], 6, {
        animate: true,
        duration: 1.5,
      });
      map.closePopup();
    }
  }, [selectedEvent, map]);
  return null;
}

// ─── Custom marker icon: topic-colored + importance-sized ─────────────────────
const createMarkerIcon = (
  importanceScore: number,
  sourceCount: number,
  count: number,
  topic: string,
  dimmed: boolean
) => {
  // ── Size formula (all values kept small) ──
  // Base: 8px for a single event
  // + importance bonus: 0–8px (score 0–10)
  // + source bonus: 0–4px logarithmic (more articles → slightly bigger)
  // + cluster bonus: 0–8px logarithmic (more events stacked → noticeably bigger)
  // Hard cap: 28px
  const importanceBonus = importanceScore * 0.85;           // score 10 → +8.5px
  const sourceBonus = Math.log(sourceCount + 1) * 1.2;      // 10 sources → +2.8px
  const clusterBonus = count > 1 ? Math.log(count + 1) * 3.5 : 0; // 10 events → +8px, 30 → +12px
  const baseSize = 8;
  const size = Math.max(8, Math.min(28, baseSize + importanceBonus + sourceBonus + clusterBonus));

  const palette = getTopicPalette(topic);

  // Dimmed markers: greyed out with reduced opacity
  const dotClass = dimmed
    ? "bg-zinc-700 border-zinc-600"
    : palette.dot;
  const pingClass = dimmed
    ? "bg-zinc-700/20"
    : (importanceScore >= 7.0 ? `${palette.ping} animate-ping` : `${palette.ping} animate-pulse`);
  const opacity = dimmed ? "0.25" : "1";

  // Inner dot: 72% of container size so it fits cleanly with the ping ring
  const dotPx = Math.round(size * 0.72);
  const badgeFontSize = dotPx < 14 ? "7px" : "9px";

  const badgeHtml =
    count > 1
      ? `<div class="absolute inset-0 flex items-center justify-center font-bold text-slate-950 font-mono-data select-none z-30" style="font-size:${badgeFontSize}">${count}</div>`
      : "";

  return L.divIcon({
    html: `
      <div class="relative flex items-center justify-center cursor-pointer group" style="width:${size}px;height:${size}px;opacity:${opacity}">
        <div class="absolute inset-0 rounded-full ${pingClass}"></div>
        <div class="relative rounded-full ${dotClass} border border-surface shadow-md shadow-black/50 transition-transform group-hover:scale-110 flex items-center justify-center" style="width:${dotPx}px;height:${dotPx}px">
          ${badgeHtml}
        </div>
      </div>
    `,
    className: "custom-marker-icon-wrapper",
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
};

// ─── Main Map component ────────────────────────────────────────────────────────
export default function Map({ events, selectedEvent, onSelectEvent, activeTopicFilter }: MapProps) {
  const centerPosition: [number, number] = [20, 0];

  // Group events sharing the exact same coordinates
  const groupedEvents: { [key: string]: MapEvent[] } = {};
  events.forEach((event) => {
    if (typeof event.latitude === "number" && typeof event.longitude === "number") {
      const key = `${event.latitude.toFixed(5)},${event.longitude.toFixed(5)}`;
      if (!groupedEvents[key]) groupedEvents[key] = [];
      groupedEvents[key].push(event);
    }
  });

  return (
    <div className="w-full h-full relative bg-surface-container-lowest">
      <MapContainer
        center={centerPosition}
        zoom={2}
        minZoom={2}
        maxZoom={10}
        className="w-full h-full z-10"
        style={{ height: "100%", width: "100%" }}
        zoomControl={false}
      >
        {/* Esri World Dark Gray Canvas — raster, dark theme, no API key.
            CARTO's basemaps.cartocdn.com now answers every tile request with an
            "API KEY REQUIRED" placeholder image, so it cannot be used keyless. */}
        <TileLayer
          attribution='&copy; <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
        />

        <MapController selectedEvent={selectedEvent} />

        {Object.values(groupedEvents).map((group) => {
          const rep = group[0];
          const count = group.length;
          const maxImportance = Math.max(...group.map((e) => e.importance_score));
          const totalSources = group.reduce((acc, e) => acc + (e.source_count || 1), 0);
          // Dominant topic in the group (most common)
          const topicCounts: Record<string, number> = {};
          group.forEach((e) => {
            const t = (e.topic || "DEFAULT").toUpperCase();
            topicCounts[t] = (topicCounts[t] || 0) + 1;
          });
          const dominantTopic = Object.entries(topicCounts).sort((a, b) => b[1] - a[1])[0][0];

          // Determine dimming: if a filter is active and this group doesn't match, dim it
          const isDimmed =
            !!activeTopicFilter &&
            !group.some(
              (e) => e.topic.toUpperCase().trim() === activeTopicFilter.toUpperCase().trim()
            );

          const palette = getTopicPalette(dominantTopic);

          return (
            <Marker
              key={rep.id}
              position={[rep.latitude, rep.longitude]}
              icon={createMarkerIcon(maxImportance, totalSources, count, dominantTopic, isDimmed)}
              eventHandlers={{
                click: () => {
                  if (count === 1) onSelectEvent(rep);
                },
              }}
            >
              {/* Hover Tooltip */}
              <Tooltip direction="top" offset={[0, -10]} opacity={0.97} className="custom-map-tooltip">
                {count === 1 ? (
                  <div className="flex gap-2 max-w-[240px]">
                    <MarkerThumb illustration={rep.illustration} />
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center justify-between gap-2 text-[9px] font-mono-data text-zinc-400">
                        <span className="font-bold uppercase" style={{ color: palette.hex }}>
                          {rep.topic}
                        </span>
                        <span>Score: {rep.importance_score.toFixed(1)}</span>
                      </div>
                      <h4 className="text-xs font-semibold text-white leading-snug line-clamp-2">
                        {rep.title}
                      </h4>
                      <span className="text-[9px] text-zinc-500 font-mono-data mt-0.5">
                        {rep.country || "Global"} · {rep.source_count || 1} source{rep.source_count !== 1 ? "s" : ""}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-1.5 max-w-[240px]">
                    <span className="text-[9px] font-bold uppercase tracking-wider font-mono-data" style={{ color: palette.hex }}>
                      {count} Dossiers · {rep.country || "Global"}
                    </span>
                    <div className="text-[10px] text-zinc-300 font-medium">
                      Click to view all events at this location
                    </div>
                    <div className="border-t border-zinc-800/60 pt-1 mt-1 flex flex-col gap-1">
                      {group.slice(0, 3).map((evt) => (
                        <div key={evt.id} className="flex items-center gap-1.5">
                          <MarkerThumb illustration={evt.illustration} className="h-7 w-7" />
                          <span className="text-[9px] text-zinc-400 truncate min-w-0 flex-1">
                            • {evt.title}
                          </span>
                        </div>
                      ))}
                      {count > 3 && (
                        <div className="text-[8px] text-zinc-500 italic">+ {count - 3} more...</div>
                      )}
                    </div>
                  </div>
                )}
              </Tooltip>

              {/* Multiple Events Popup */}
              {count > 1 && (
                <Popup className="custom-map-popup" offset={[0, -10]}>
                  <div className="w-[300px] flex flex-col gap-3">
                    <div className="border-b border-indigo-950/40 pb-2">
                      <span
                        className="text-[10px] font-bold uppercase tracking-wider font-mono-data"
                        style={{ color: palette.hex }}
                      >
                        {count} Dossiers Here
                      </span>
                      <h4 className="text-xs text-zinc-400 font-semibold mt-0.5">
                        {rep.country || "Global"}
                      </h4>
                    </div>
                    <div className="flex flex-col gap-2 max-h-[220px] overflow-y-auto pr-1 no-scrollbar">
                      {group.map((evt) => {
                        const evtPalette = getTopicPalette(evt.topic);
                        return (
                          <button
                            key={evt.id}
                            onClick={() => onSelectEvent(evt)}
                            className="w-full text-left p-2 rounded-lg bg-zinc-900/50 hover:bg-primary-container border border-outline-variant/30 hover:border-primary/50 transition-all flex gap-2 items-start group"
                          >
                            <MarkerThumb illustration={evt.illustration} />
                            <div className="flex flex-col gap-1 min-w-0 flex-1">
                              <div className="flex justify-between items-center text-[9px]">
                                <span className="font-bold uppercase" style={{ color: evtPalette.hex }}>
                                  {evt.topic}
                                </span>
                                <span className="px-1.5 py-0.5 bg-zinc-800 rounded font-mono-data text-zinc-400">
                                  Score: {evt.importance_score.toFixed(1)}
                                </span>
                              </div>
                              <p className="text-xs text-zinc-200 group-hover:text-white font-semibold line-clamp-2 leading-snug">
                                {evt.title}
                              </p>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </Popup>
              )}
            </Marker>
          );
        })}
      </MapContainer>
    </div>
  );
}

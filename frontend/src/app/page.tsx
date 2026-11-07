"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { 
  Globe, 
  MapPin, 
  TrendingUp, 
  AlertTriangle,
  ChevronRight,
  Shield,
  Layers,
  ArrowRight,
  HelpCircle,
  Undo2,
  Calendar,
  FilterX,
  FileText,
  Bookmark,
  RefreshCw,
  Clock,
  BookOpen,
  Bot
} from "lucide-react";
import SearchBar from "./components/SearchBar";
import PillNav from "./components/PillNav";
import MapFilterPanel from "./components/MapFilterPanel";
import ChatbotPanel from "./components/ChatbotPanel";
import ContradictionBadge from "./components/ContradictionBadge";


// Dynamically import the Map component to prevent window undefined SSR issues
const Map = dynamic(() => import("./components/Map"), { ssr: false });

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface EventItem {
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
  // Cross-source conflict signal derived from stored contradiction rows. level
  // reflects the strongest scored pair (see _contradiction_signal) and is
  // absent for events fetched before the field shipped.
  contradiction_count?: number;
  contradiction_max_confidence?: number | null;
  contradiction_level?: string;
  created_at?: string;
}

export default function HomePage() {
  const [events, setEvents] = useState<EventItem[]>([]);
  // Real most-recent ingest, from GET /events/latest. Used by the sidebar that
  // used to render hard-coded fake wire items.
  const [latestEvents, setLatestEvents] = useState<EventItem[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<EventItem | null>(null);
  const [searchActive, setSearchActive] = useState(false);
  const [searchEmpty, setSearchEmpty] = useState(false);
  const [loading, setLoading] = useState(true);
  
  // Views toggle: "map" vs "standard"
  const [viewMode, setViewMode] = useState<"map" | "standard">("map");

  // Map topic filter (category selector panel)
  const [mapTopicFilter, setMapTopicFilter] = useState<string | null>(null);

  // Chatbot Open State
  const [chatbotOpen, setChatbotOpen] = useState(false);


  // Standard Feed State
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalEventsCount, setTotalEventsCount] = useState(0);
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);

  // Check URL view param on mount
  useEffect(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const view = params.get("view");
      if (view === "standard") {
        setViewMode("standard");
      } else if (view === "map") {
        setViewMode("map");
      }
    }
  }, []);

  // Fetch events based on viewMode and pagination
  const fetchEvents = useCallback(async (page = 1) => {
    setLoading(true);
    try {
      if (viewMode === "standard") {
        const response = await fetch(`${API_BASE_URL}/api/v1/events?page=${page}&limit=5`);
        if (response.ok) {
          const data = await response.json();
          setEvents(data.events || []);
          setTotalEventsCount(data.total || 0);
          setTotalPages(Math.ceil((data.total || 0) / 5) || 1);
          setCurrentPage(page);
          setSearchEmpty((data.events || []).length === 0);
        }
      } else {
        const response = await fetch(`${API_BASE_URL}/api/v1/events/map`);
        if (response.ok) {
          const data = await response.json();
          setEvents(data.events || []);
          setSearchEmpty(false);
        }
      }
    } catch (err) {
      console.error("Failed to fetch events", err);
    } finally {
      setLoading(false);
    }
  }, [viewMode]);

  const fetchLatest = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/events/latest?limit=6`);
      if (!response.ok) return;
      const data = await response.json();
      setLatestEvents(data.events || []);
      // Anchor the reconnect catch-up at the newest event we know about, so the
      // first SSE open has a baseline and every later reconnect can ask the
      // backend for anything created after it. ISO timestamps compare
      // lexicographically, so max() over them is safe.
      for (const e of data.events || []) {
        if (e.created_at && (!lastLiveAtRef.current || e.created_at > lastLiveAtRef.current)) {
          lastLiveAtRef.current = e.created_at;
        }
      }
    } catch {
      // A failure here must not blank the sidebar or throw; the panel simply
      // falls back to the current page's events and says nothing false.
    }
  };

  const catchUpLive = useCallback(async () => {
    // Reconnects must not disturb a search or a different view; the stream
    // handler itself is gated identically. Catch-up is a best effort: any
    // failure just leaves the next reconnect to retry.
    if (searchActive || viewMode !== "standard") return;
    const since = lastLiveAtRef.current;
    if (!since) return;
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/events/latest?limit=100&since=${encodeURIComponent(since)}`
      );
      if (!res.ok) return;
      const data = await res.json();
      const items: EventItem[] = data.events || [];
      if (items.length === 0) return;
      const fresh = items.filter((i) => !liveSeenRef.current.has(i.id));
      if (fresh.length === 0) return;
      for (const i of fresh) {
        liveSeenRef.current.add(i.id);
        if (i.created_at && i.created_at > (lastLiveAtRef.current ?? "")) {
          lastLiveAtRef.current = i.created_at;
        }
      }
      const { page, total } = liveStateRef.current;
      if (page > 1) {
        setLiveNewCount((c) => c + fresh.length);
        return;
      }
      setEvents((prev) => {
        const next = [...fresh, ...prev];
        return next.length > 100 ? next.slice(0, 100) : next;
      });
      setLatestEvents((prev) => {
        const next = [...fresh, ...prev];
        return next.length > 8 ? next.slice(0, 8) : next;
      });
      const nextTotal = total + fresh.length;
      liveStateRef.current.total = nextTotal;
      setTotalEventsCount(nextTotal);
      setTotalPages(Math.ceil(nextTotal / 5) || 1);
    } catch {
      // never crash the page on a catch-up hiccup
    }
  }, [viewMode, searchActive]);

  useEffect(() => {
    fetchEvents(1);
  }, [viewMode, fetchEvents]);

  useEffect(() => {
    fetchLatest();
  }, []);

  // ── Real-time stream ──────────────────────────────────────────────────────
  // SSE feed that announces events the moment a batch run marks them PROCESSED
  // (backend /api/v1/events/stream). It is a live enhancement on top of the
  // plain fetch feed: any failure drops the stream silently and the page
  // behaves exactly as before. EventSource reconnects on its own.
  const [liveConnected, setLiveConnected] = useState(false);
  const [liveNewCount, setLiveNewCount] = useState(0);
  // The message handler is attached once per (viewMode, searchActive) change,
  // but pagination state moves independently. Mirror page/total through a ref
  // so the handler reads current values instead of stale closure ones.
  const liveStateRef = useRef({ page: currentPage, total: totalEventsCount });
  useEffect(() => {
    liveStateRef.current = { page: currentPage, total: totalEventsCount };
  }, [currentPage, totalEventsCount]);
  // Newest event timestamp seen so far, used as the ?since= anchor for the
  // reconnect catch-up, and the set of event ids already merged live so a
  // catch-up never duplicates an announcement the stream already delivered.
  const lastLiveAtRef = useRef<string | null>(null);
  const liveSeenRef = useRef<Set<string>>(new Set());
  // The first `open` establishes the baseline; only later opens (real
  // reconnects after a drop) should ask the server for anything missed.
  const everOpenedRef = useRef(false);

  useEffect(() => {
    if (searchActive || viewMode !== "standard" || typeof EventSource === "undefined") {
      return;
    }
    const source = new EventSource(`${API_BASE_URL}/api/v1/events/stream`);
    source.onopen = () => {
      setLiveConnected(true);
      if (everOpenedRef.current) {
        void catchUpLive();
      }
      everOpenedRef.current = true;
    };
    source.onerror = () => setLiveConnected(false);
    source.addEventListener("new-event", (raw) => {
      let item: EventItem;
      try {
        item = JSON.parse((raw as MessageEvent).data) as EventItem;
      } catch {
        return;
      }
      liveSeenRef.current.add(item.id);
      if (item.created_at && item.created_at > (lastLiveAtRef.current ?? "")) {
        lastLiveAtRef.current = item.created_at;
      }
      const { page, total } = liveStateRef.current;
      if (page > 1) {
        // Prepending into a later page would silently corrupt that page's
        // scroll position; surface a "view latest" prompt instead.
        setLiveNewCount((c) => c + 1);
        return;
      }
      setEvents((prev) => {
        if (prev.some((e) => e.id === item.id)) return prev;
        const next = [item, ...prev];
        return next.length > 100 ? next.slice(0, 100) : next;
      });
      setLatestEvents((prev) => {
        if (prev.some((e) => e.id === item.id)) return prev;
        const next = [item, ...prev];
        return next.length > 8 ? next.slice(0, 8) : next;
      });
      const nextTotal = total + 1;
      liveStateRef.current.total = nextTotal;
      setTotalEventsCount(nextTotal);
      setTotalPages(Math.ceil(nextTotal / 5) || 1);
    });
    return () => {
      source.close();
      setLiveConnected(false);
    };
  }, [viewMode, searchActive, catchUpLive]);

  const handleSearchResults = (results: EventItem[] | null) => {
    if (results === null) {
      setSearchActive(false);
      setSearchEmpty(false);
      fetchEvents(1);
    } else if (results.length === 0) {
      setSearchEmpty(true);
      setSearchActive(true);
      setEvents([]);
    } else {
      setSearchEmpty(false);
      setSearchActive(true);
      setEvents(results);
    }
  };

  const handleClearSearch = () => {
    setSearchActive(false);
    setSearchEmpty(false);
    fetchEvents(1);
  };

  const handleResetFilters = () => {
    setSelectedTopic(null);
    handleClearSearch();
  };

  // Helper to format topic pills
  const getTopicBadgeStyle = (topic: string) => {
    switch (topic.toUpperCase()) {
      case "POLITICS":
        return "bg-rose-950/40 text-rose-400 border-rose-900/50";
      case "ECONOMY":
        return "bg-amber-950/40 text-amber-400 border-amber-900/50";
      case "TECHNOLOGY":
        return "bg-blue-950/40 text-blue-400 border-blue-900/50";
      case "SPORTS":
        return "bg-emerald-950/40 text-emerald-400 border-emerald-900/50";
      case "HEALTH":
        return "bg-purple-950/40 text-purple-400 border-purple-900/50";
      default:
        return "bg-zinc-900 text-zinc-300 border-zinc-800";
    }
  };

  // Filter events client-side when a tag is selected
  const displayEvents = selectedTopic 
    ? events.filter(e => e.topic.toUpperCase() === selectedTopic.toUpperCase())
    : events;

  // Promoted / Priority Brief: Sort by importance score descending and select first
  const priorityEvent = displayEvents.length > 0 
    ? [...displayEvents].sort((a, b) => b.importance_score - a.importance_score)[0]
    : null;

  const otherEvents = priorityEvent 
    ? displayEvents.filter(e => e.id !== priorityEvent.id)
    : displayEvents;

  // Render search results empty state (exact port of no_results_intelligence_search)
  if (searchEmpty) {
    return (
      <div className="bg-background text-on-background min-h-screen flex flex-col font-body-md relative overflow-hidden">
        {/* TopNavBar */}
        <header className="flex justify-between items-center px-margin-desktop w-full h-16 bg-[#080c16]/80 backdrop-blur-md border-b border-indigo-950/40 z-50">
          <PillNav
            logo="/logo.svg"
            logoAlt="GlobeLens AI Logo"
            items={[
              { 
                label: 'Standard', 
                href: '/?view=standard',
                onClick: (e) => {
                  e.preventDefault();
                  setViewMode("standard");
                  setSearchEmpty(false);
                  if (typeof window !== "undefined") {
                    window.history.pushState(null, "", "?view=standard");
                  }
                }
              },
              { 
                label: 'Map', 
                href: '/?view=map',
                onClick: (e) => {
                  e.preventDefault();
                  setViewMode("map");
                  setSearchEmpty(false);
                  if (typeof window !== "undefined") {
                    window.history.pushState(null, "", "?view=map");
                  }
                }
              },
              { label: 'Admin', href: '/admin/dashboard' },
              { label: 'Fact Checker', href: '/fact-checker' },
          { label: 'Sources', href: '/sources' },
          { label: 'Lists', href: '/lists' },
          { label: 'Settings', href: '/settings' }
            ]}
            activeHref={viewMode === "standard" ? "/?view=standard" : "/?view=map"}
            baseColor="#080c16"
            pillColor="#0c101b"
            hoveredPillTextColor="#22d3ee"
            pillTextColor="#94a3b8"
            initialLoadAnimation={false}
          />
          <div className="flex items-center gap-4">
            <SearchBar 
              onSelectEvent={setSelectedEvent} 
              onSearchResults={handleSearchResults} 
              onClearSearch={handleClearSearch} 
            />
          </div>
        </header>

        {/* Ambient Grid and Glow Background */}
        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(69,70,77,0.06)_1px,transparent_1px),linear-gradient(to_bottom,rgba(69,70,77,0.06)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none z-0"></div>
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-radial-glow opacity-10 pointer-events-none"></div>

        {/* Main Canvas: Empty State */}
        <main className="flex-grow flex flex-col items-center justify-center px-margin-mobile md:px-margin-desktop py-stack-lg z-10 relative">
          <div className="relative mb-8 group">
            <div className="absolute inset-0 bg-primary/10 rounded-full blur-xl group-hover:bg-primary/20 transition-all duration-500"></div>
            <div className="w-32 h-32 rounded-full bg-surface-container-high/80 backdrop-blur-md border border-outline-variant flex items-center justify-center relative shadow-sm">
              <span className="material-symbols-outlined text-6xl text-on-surface-variant opacity-80 select-none">
                troubleshoot
              </span>
              <div className="absolute -bottom-2 -right-2 bg-surface-container border border-outline-variant rounded-full p-2 shadow-sm">
                <span className="material-symbols-outlined text-sm text-error">error</span>
              </div>
            </div>
          </div>

          <div className="max-w-2xl text-center mb-8">
            <h1 className="font-headline-xl text-headline-xl text-primary mb-3 tracking-tight">Zero Intelligence Recovered</h1>
            <p className="font-body-lg text-body-lg text-on-surface-variant leading-relaxed">
              The current parametric filters did not yield any matches within the active dossier feed. Your query architecture may be overly restrictive relative to the available global dataset.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 w-full max-w-4xl mb-8">
            <div className="bg-surface-container-low border border-outline-variant rounded-lg p-5 flex flex-col gap-1 hover:border-primary/50 transition-colors cursor-default">
              <div className="flex items-center gap-2 mb-2">
                <Calendar className="w-4 h-4 text-secondary" />
                <span className="font-label-caps text-label-caps text-secondary uppercase tracking-wider">Temporal</span>
              </div>
              <h3 className="font-body-md text-body-md font-medium text-primary">Broaden Horizon</h3>
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                Expand the date range to encompass historical contexts and archived intelligence reports.
              </p>
            </div>
            <div className="bg-surface-container-low border border-outline-variant rounded-lg p-5 flex flex-col gap-1 hover:border-primary/50 transition-colors cursor-default">
              <div className="flex items-center gap-2 mb-2">
                <FilterX className="w-4 h-4 text-secondary" />
                <span className="font-label-caps text-label-caps text-secondary uppercase tracking-wider">Topical</span>
              </div>
              <h3 className="font-body-md text-body-md font-medium text-primary">Reduce Constraints</h3>
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                Remove highly specific topic filters to widen the analytical net across adjacent subjects.
              </p>
            </div>
            <div className="bg-surface-container-low border border-outline-variant rounded-lg p-5 flex flex-col gap-1 hover:border-primary/50 transition-colors cursor-default">
              <div className="flex items-center gap-2 mb-2">
                <FileText className="w-4 h-4 text-secondary" />
                <span className="font-label-caps text-label-caps text-secondary uppercase tracking-wider">Syntax</span>
              </div>
              <h3 className="font-body-md text-body-md font-medium text-primary">Verify Query</h3>
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                Ensure precise keyword spelling and check for alternative operational designations.
              </p>
            </div>
          </div>

          <button 
            onClick={handleResetFilters}
            className="bg-primary hover:bg-primary-fixed text-on-primary font-label-caps text-[12px] px-8 py-3 rounded tracking-wider uppercase font-bold transition-all hover:scale-98 active:scale-95 shadow-md"
          >
            Reset All Filters
          </button>
        </main>
      </div>
    );
  }

  return (
    <div className="bg-[#030712] text-on-background h-screen w-screen overflow-hidden flex flex-col font-body-md">
      {/* TopNavBar */}
      <header className="flex justify-between items-center px-margin-desktop w-full h-16 sticky top-0 z-50 bg-[#080c16]/80 backdrop-blur-lg border-b border-indigo-950/40 flex-shrink-0">
        <PillNav
          logo="/logo.svg"
          logoAlt="GlobeLens AI Logo"
          items={[
            { 
              label: 'Standard', 
              href: '/?view=standard',
              onClick: (e) => {
                e.preventDefault();
                setViewMode("standard");
                if (typeof window !== "undefined") {
                  window.history.pushState(null, "", "?view=standard");
                }
              }
            },
            { 
              label: 'Map', 
              href: '/?view=map',
              onClick: (e) => {
                e.preventDefault();
                setViewMode("map");
                if (typeof window !== "undefined") {
                  window.history.pushState(null, "", "?view=map");
                }
              }
            },
            { label: 'Admin', href: '/admin/dashboard' },
            { label: 'Fact Checker', href: '/fact-checker' },
          { label: 'Sources', href: '/sources' },
          { label: 'Lists', href: '/lists' },
          { label: 'Settings', href: '/settings' }
          ]}
          activeHref={viewMode === "standard" ? "/?view=standard" : "/?view=map"}
          baseColor="#080c16"
          pillColor="#0c101b"
          hoveredPillTextColor="#22d3ee"
          pillTextColor="#94a3b8"
          initialLoadAnimation={true}
        />
        <div className="flex items-center gap-4">
          <SearchBar 
            onSelectEvent={setSelectedEvent} 
            onSearchResults={handleSearchResults} 
            onClearSearch={handleClearSearch} 
          />
        </div>
      </header>

      {/* Main Content Area */}
      {viewMode === "map" ? (
        /* 1. MAP VIEW */
        <main className="relative flex-grow w-full bg-surface-dim overflow-hidden">
          <div className="absolute inset-0 w-full h-full z-10">
            <Map 
              events={events} 
              selectedEvent={selectedEvent} 
              onSelectEvent={(evt) => {
                setSelectedEvent(evt);
                if (evt) setChatbotOpen(false);
              }}
              activeTopicFilter={mapTopicFilter}
            />

          </div>

          <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.015)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.015)_1px,transparent_1px)] bg-[size:48px_48px] pointer-events-none z-20 mix-blend-overlay"></div>

          {/* ── Category Filter Panel (left side overlay) ── */}
          <div className="absolute top-4 left-4 z-30 bg-[#0d1117]/85 backdrop-blur-xl border border-white/[0.07] rounded-2xl px-3 py-4 shadow-[0_8px_32px_rgba(0,0,0,0.5)]">
            <MapFilterPanel
              events={events}
              activeTopic={mapTopicFilter}
              onSelectTopic={setMapTopicFilter}
            />
          </div>

          {searchActive && (
            <div className="absolute top-4 left-[200px] z-30 bg-zinc-950/90 backdrop-blur-md border border-zinc-800 rounded-xl px-4 py-2 text-xs flex items-center gap-3 shadow-lg">
              <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse"></span>
              <span className="text-white font-medium">Filter Active: {events.length} results matching query</span>
              <button 
                onClick={handleClearSearch} 
                className="text-primary hover:underline font-bold text-[10px]"
              >
                CLEAR
              </button>
            </div>
          )}

          {/* Dossier Side Panel */}
          {selectedEvent && renderDossierPanel(selectedEvent, setSelectedEvent, getTopicBadgeStyle)}

          {/* Chatbot Trigger Floating Button */}
          {!chatbotOpen && !selectedEvent && (
            <button
              onClick={() => {
                setChatbotOpen(true);
                setSelectedEvent(null);
              }}
              className="absolute bottom-6 right-6 z-30 p-4 rounded-full bg-gradient-to-r from-cyber-indigo to-indigo-650 hover:from-indigo-650 hover:to-indigo-750 text-white shadow-[0_4px_20px_rgba(99,102,241,0.4)] hover:shadow-[0_4px_25px_rgba(99,102,241,0.6)] hover:scale-105 transition-all duration-300 flex items-center gap-2 group border border-indigo-500/30"
            >
              <Bot className="w-6 h-6 animate-pulse" />
              <span className="max-w-0 overflow-hidden group-hover:max-w-xs transition-all duration-500 ease-out font-mono-data text-xs font-bold tracking-wider uppercase whitespace-nowrap">
                Consult News-Scout
              </span>
            </button>
          )}

          {/* Chatbot Panel Overlay */}
          {chatbotOpen && (
            <div className="absolute top-4 right-4 bottom-4 w-96 z-30 animate-in slide-in-from-right duration-350 ease-out">
              <ChatbotPanel onClose={() => setChatbotOpen(false)} />
            </div>
          )}
        </main>

      ) : (
        /* 2. STANDARD FEED VIEW (home_feed_globelens_ai port) */
        <div className="flex-grow w-full overflow-y-auto no-scrollbar flex flex-col bg-surface-container-lowest">
          <div className="flex-grow flex w-full max-w-container-max-width mx-auto relative px-margin-mobile md:px-margin-desktop py-stack-lg">
            
{/* Primary Feed Column */}
              <main className="flex-grow w-full lg:w-3/4 pr-0 lg:pr-gutter pb-32 flex flex-col gap-stack-lg z-10">

              {/* Live stream status + pending live events. Hidden when the
                  stream is disconnected AND nothing is pending, so an
                  unavailable feed stays just as quiet as before. */}
              {(liveConnected || liveNewCount > 0) && (
                <div className="flex items-center gap-3">
                  {liveConnected && (
                    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-emerald-900/50 bg-emerald-950/40 text-emerald-400 text-[10px] font-bold uppercase tracking-wider">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                      Live
                    </span>
                  )}
                  {liveNewCount > 0 && (
                    <button
                      onClick={() => { setLiveNewCount(0); fetchEvents(1); }}
                      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-cyber-cyan/40 bg-cyber-cyan/10 text-cyber-cyan text-[11px] font-bold hover:bg-cyber-cyan/20 transition-colors"
                    >
                      <ArrowRight className="w-3.5 h-3.5" />
                      {liveNewCount} new event{liveNewCount > 1 ? "s" : ""} · view latest
                    </button>
                  )}
                </div>
              )}

              {/* Topic Onboarding Widget */}
              <section className="glass-panel p-stack-md md:p-gutter rounded-lg relative overflow-hidden group border border-outline-variant/60">
                <div className="absolute inset-0 bg-gradient-to-r from-primary/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500"></div>
                <div className="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-stack-md">
                  <div>
                    <h2 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-primary font-bold mb-1">Refine Your Intelligence Feed</h2>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">Select operational domains to tune AI synthesis.</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {["GEOPOLITICS", "TECHNOLOGY", "ECONOMY", "SPORTS", "HEALTH", "WORLD"].map((topic) => (
                      <button 
                        key={topic}
                        onClick={() => setSelectedTopic(selectedTopic === topic ? null : topic)}
                        className={`px-3 py-1.5 rounded-full border text-label-caps font-label-caps transition-all ${
                          selectedTopic === topic 
                            ? "bg-primary border-primary text-on-primary font-bold"
                            : "bg-surface border-outline-variant text-on-surface-variant hover:border-primary hover:text-primary"
                        }`}
                      >
                        {topic.charAt(0) + topic.slice(1).toLowerCase()}
                      </button>
                    ))}
                  </div>
                </div>
              </section>

              {/* Priority / Highlighted Brief */}
              {priorityEvent && (
                <article className="bg-surface-container border border-outline-variant/80 rounded-lg overflow-hidden flex flex-col md:flex-row relative border-l-4 border-l-primary group shadow-md hover:border-outline transition-colors duration-200">
                  <div className="absolute top-4 right-4 z-20 flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded text-[10px] uppercase font-bold tracking-wider bg-primary text-on-primary">Priority Brief</span>
                  </div>
                  
                  <div className="md:w-2/5 h-48 md:h-auto relative overflow-hidden bg-zinc-900 border-r border-outline-variant/35 flex items-center justify-center">
                    <div className="absolute inset-0 bg-[radial-gradient(#bec6e0_1px,transparent_1px)] [background-size:16px_16px] opacity-15"></div>
                    <span className="material-symbols-outlined text-8xl text-outline/30 select-none">
                      spatial_tracking
                    </span>
                  </div>
                  
                  <div className="md:w-3/5 p-stack-md md:p-gutter flex flex-col justify-between z-10 bg-zinc-950/40">
                    <div>
                      <div className="flex items-center gap-3 mb-3">
                        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-950/40 border border-emerald-900/50 text-emerald-400 text-[10px] font-bold uppercase">
                          <span className="material-symbols-outlined text-[12px]">verified</span> Score: {priorityEvent.importance_score.toFixed(1)}
                        </span>
                        <ContradictionBadge
                          level={priorityEvent.contradiction_level}
                          count={priorityEvent.contradiction_count}
                          maxConfidence={priorityEvent.contradiction_max_confidence}
                        />
                        <span className="font-mono-data text-mono-data text-on-surface-variant text-xs flex items-center gap-1">
                          <MapPin className="w-3.5 h-3.5 text-zinc-500" />
                          {priorityEvent.country || "Global"}
                        </span>
                      </div>
                      
                      <h3 
                        onClick={() => setSelectedEvent(priorityEvent)}
                        className="font-headline-xl text-headline-xl text-primary font-bold mb-3 leading-tight group-hover:text-white transition-colors cursor-pointer"
                      >
                        {priorityEvent.title}
                      </h3>
                      
                      <p className="font-body-md text-body-md text-on-surface-variant line-clamp-3 mb-4 leading-relaxed">
                        {priorityEvent.summary ? priorityEvent.summary.split("\n\n")[0] : "Aggregation and synthesis of wirehouse feeds currently in progress."}
                      </p>
                    </div>
                    
                    <div className="flex items-center justify-between mt-auto pt-4 border-t border-outline-variant/40">
                      <span className="font-mono-data text-mono-data text-on-surface-variant text-[11px]">
                        {priorityEvent.source_count} Independent Publisher wires
                      </span>
                      <button 
                        onClick={() => setSelectedEvent(priorityEvent)}
                        className="text-primary hover:text-white transition-colors flex items-center gap-1 font-label-caps text-label-caps text-xs uppercase"
                      >
                        Read Full Dossier <ArrowRight className="w-3.5 h-3.5 ml-1" />
                      </button>
                    </div>
                  </div>
                </article>
              )}

              {/* Standard Feed Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-gutter">
                {otherEvents.map((evt) => (
                  <article 
                    key={evt.id} 
                    onClick={() => setSelectedEvent(evt)}
                    className="bg-surface-container border border-outline-variant/60 rounded-lg p-5 flex flex-col justify-between h-full cursor-pointer hover:border-primary/60 hover:-translate-y-0.5 transition-all duration-200 shadow-sm"
                  >
                    <div>
                      <div className="flex justify-between items-start mb-3">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-400 text-[10px] font-mono-data">
                          Score: {evt.importance_score.toFixed(1)}
                        </span>
                        <ContradictionBadge
                          level={evt.contradiction_level}
                          count={evt.contradiction_count}
                          maxConfidence={evt.contradiction_max_confidence}
                        />
                        <span className={`inline-flex border px-2 py-0.5 rounded font-label-caps text-[9px] uppercase tracking-wider ${getTopicBadgeStyle(evt.topic)}`}>
                          {evt.topic}
                        </span>
                      </div>
                      
                      <h3 className="font-headline-lg-mobile text-headline-lg-mobile font-bold text-on-surface mb-2 group-hover:text-primary transition-colors leading-snug">
                        {evt.title}
                      </h3>
                      
                      <p className="font-body-sm text-body-sm text-on-surface-variant line-clamp-3 mb-4 leading-relaxed">
                        {evt.summary ? evt.summary.split("\n\n")[0] : "Intelligence dossiers are active. Publisher feeds are being merged."}
                      </p>
                    </div>
                    
                    <div className="flex items-center justify-between pt-3 border-t border-outline-variant/40 mt-auto">
                      <span className="font-mono-data text-mono-data text-on-surface-variant flex items-center gap-1 text-[11px]">
                        <MapPin className="w-3 h-3 text-zinc-500" /> {evt.country || "Global"}
                      </span>
                      <span className="font-mono-data text-mono-data text-on-surface-variant text-[11px] flex items-center gap-1">
                        <Clock className="w-3 h-3 text-zinc-500" />
                        {evt.created_at ? new Date(evt.created_at).toLocaleDateString() : "Active Now"}
                      </span>
                    </div>
                  </article>
                ))}
              </div>

              {/* Pagination Controls */}
              {totalPages > 1 && (
                <div className="flex justify-center mt-stack-md gap-2 items-center">
                  <button 
                    disabled={currentPage === 1}
                    onClick={() => fetchEvents(currentPage - 1)}
                    className="w-8 h-8 flex items-center justify-center rounded border border-outline-variant text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors disabled:opacity-30"
                  >
                    ‹
                  </button>
                  {Array.from({ length: totalPages }).map((_, idx) => (
                    <button 
                      key={idx}
                      onClick={() => fetchEvents(idx + 1)}
                      className={`w-8 h-8 flex items-center justify-center rounded font-mono-data text-xs transition-colors ${
                        currentPage === idx + 1 
                          ? "bg-primary text-on-primary font-bold" 
                          : "border border-outline-variant text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                      }`}
                    >
                      {idx + 1}
                    </button>
                  ))}
                  <button 
                    disabled={currentPage === totalPages}
                    onClick={() => fetchEvents(currentPage + 1)}
                    className="w-8 h-8 flex items-center justify-center rounded border border-outline-variant text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors disabled:opacity-30"
                  >
                    ›
                  </button>
                </div>
              )}
            </main>

            {/* Social Flux Sidebar (Right Column) */}
            <aside className="hidden lg:flex flex-col w-80 bg-zinc-950/40 border border-outline-variant/60 rounded-lg p-4 h-[calc(100vh-8rem)] sticky top-6 overflow-hidden z-10 flex-shrink-0 backdrop-blur-md">
              <div className="mb-6 flex flex-col border-b border-outline-variant/40 pb-4">
                <span className="font-label-caps text-label-caps text-secondary mb-1 uppercase tracking-widest text-[10px]">Real-Time Feed</span>
                <div className="flex items-center justify-between">
                  <h2 className="font-body-md text-body-md font-bold text-primary flex items-center gap-1.5">
                    <span className="material-symbols-outlined text-[18px]">radar</span>
                    Social Flux
                  </h2>
                  <button 
                    onClick={() => { fetchLatest(); fetchEvents(currentPage); }}
                    className="text-zinc-400 hover:text-primary transition-colors flex items-center text-[10px] font-bold tracking-wider uppercase gap-1"
                  >
                    <RefreshCw className="w-3 h-3 animate-spin-slow" />
                    Refresh
                  </button>
                </div>
              </div>

              {/* Feed items */}
              {/*
                This sidebar previously rendered three hard-coded "wire" items
                attributed to Reuters RSS, an X account (@IntelWire) and
                "Orbital Telemetry", each with a fabricated relative timestamp
                ("Just Now", "4m ago"). Nothing was fetched, and attributing
                invented headlines to real newsrooms is the most damaging kind of
                fabrication this product could ship.

                It now shows the genuinely most recent ingested events from the
                API. When the real-time stream is connected, newly PROCESSED
                events are prepended live here as the backend announces them.
                Ingesting new articles still requires an administrator-triggered
                sync from configured publisher RSS feeds; the live feed never
                invents coverage.
              */}
              <div className="flex-grow flex flex-col gap-3 overflow-y-auto pr-1 no-scrollbar">
                {(latestEvents.length > 0 ? latestEvents : events).slice(0, 6).map((e) => (
                  <div key={e.id} className="p-3 bg-zinc-900/40 rounded border border-outline-variant/30 relative">
                    <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-cyber-cyan rounded-l"></div>
                    <div className="flex justify-between items-start mb-1 text-[9px] text-zinc-500 font-mono-data">
                      <span>{e.country || "Global"}</span>
                      <span>{e.topic || "WORLD"}</span>
                    </div>
                    <p className="font-body-sm text-body-sm text-zinc-300 leading-snug">{e.title}</p>
                  </div>
                ))}
                {(latestEvents.length > 0 ? latestEvents : events).length === 0 && (
                  <div className="p-3 bg-zinc-900/40 rounded border border-outline-variant/30">
                    <p className="font-body-sm text-body-sm text-zinc-400 leading-snug">
                      No events ingested yet. Articles arrive when an administrator
                      triggers a sync from configured publisher RSS feeds.
                    </p>
                  </div>
                )}
              </div>
            </aside>
          </div>

          {/* Dossier Side Panel */}
          {selectedEvent && renderDossierPanel(selectedEvent, setSelectedEvent, getTopicBadgeStyle)}

          {/* Institutional Footer */}
          <footer className="bg-surface-container-low border-t border-outline-variant/50 w-full py-6 px-margin-desktop mt-auto flex flex-col md:flex-row justify-between items-center z-10 relative">
            <div className="mb-4 md:mb-0">
              <span className="font-headline-lg text-headline-lg font-bold text-primary block mb-1">GlobeLens AI</span>
              <p className="font-body-sm text-body-sm text-on-surface-variant">© 2026 GlobeLens AI. Authoritative Editorial Intelligence.</p>
            </div>
            <nav className="flex flex-wrap gap-4 md:gap-6 items-center justify-center text-xs">
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Institutional Briefs</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Privacy Guidelines</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">System Ethics Policy</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Uplink API Portal</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="/settings">Settings</a>
            </nav>
          </footer>
        </div>
      )}
    </div>
  );
}

// Sub-component render: dossier side panel sliding drawer
function renderDossierPanel(
  selectedEvent: EventItem,
  setSelectedEvent: React.Dispatch<React.SetStateAction<EventItem | null>>,
  getTopicBadgeStyle: (topic: string) => string
) {
  return (
    <div className="fixed top-20 right-4 bottom-4 w-[calc(100%-2rem)] sm:w-[480px] z-40 bg-[#080c16]/95 backdrop-blur-2xl border border-indigo-500/20 rounded-2xl shadow-[0_0_50px_rgba(6,182,212,0.15)] p-6 flex flex-col justify-between overflow-y-auto no-scrollbar animate-fade-in-up">
      <div>
        {/* Header */}
        <div className="flex justify-between items-start mb-6 border-b border-indigo-950/40 pb-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-cyber-cyan/10 border border-cyber-cyan/30 text-cyber-cyan text-[10px] font-bold uppercase tracking-wider font-mono-data">
                <Globe className="w-3 h-3" />
                {selectedEvent.country || "Global"}
              </span>
              <span className={`inline-flex border px-2.5 py-0.5 rounded-full font-label-caps text-[9px] uppercase tracking-wider ${getTopicBadgeStyle(selectedEvent.topic)}`}>
                {selectedEvent.topic}
              </span>
            </div>
            <h3 className="text-xl font-bold text-white leading-snug drop-shadow-sm">{selectedEvent.title}</h3>
          </div>
          <button
            onClick={() => setSelectedEvent(null)}
            className="text-zinc-400 hover:text-white bg-zinc-900/40 border border-zinc-800/60 p-2 rounded-xl transition-all hover:scale-105 active:scale-95"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="space-y-6">
          <div>
            <h4 className="text-[10px] font-bold text-cyber-cyan/70 uppercase tracking-widest mb-3 flex items-center gap-1.5 font-mono-data">
              <FileText className="w-3.5 h-3.5" />
              AI Intelligence synthesis
            </h4>
            <div className="text-xs text-zinc-300 space-y-4 leading-relaxed font-body-sm">
              {selectedEvent.summary ? (
                selectedEvent.summary.split("\n\n").map((para, i) => (
                  <p key={i} className="text-zinc-300/95">{para}</p>
                ))
              ) : (
                <p className="text-zinc-400 italic">Geopolitical analytics are processing. Real-time reports from multiple publisher networks are being aggregated.</p>
              )}
            </div>
          </div>

          {/* Bias Lean Spectrum */}
          <div className="border-t border-indigo-950/40 pt-4">
            <h4 className="text-[10px] font-bold text-cyber-indigo/70 uppercase tracking-widest mb-3 font-mono-data">Geopolitical Bias Spectrum</h4>
            {(() => {
              // A null lean means the event was never classified. It must not be
              // rendered as a centred interpretation, which is a real assessment
              // we have not made. The marker is withheld instead.
              const lean = (selectedEvent.bias_lean || "").toUpperCase();
              let percentage: number | null = null;
              let label = "NOT CLASSIFIED";

              if (lean === "LEFT") {
                percentage = 10;
                label = "LEFT IDEOLOGY LEAN";
              } else if (lean === "CENTER_LEFT") {
                percentage = 30;
                label = "CENTER-LEFT LEAN";
              } else if (lean === "CENTER") {
                percentage = 50;
                label = "CENTERED INTERPRETATION";
              } else if (lean === "CENTER_RIGHT") {
                percentage = 70;
                label = "CENTER-RIGHT LEAN";
              } else if (lean === "RIGHT") {
                percentage = 90;
                label = "RIGHT IDEOLOGY LEAN";
              }
              
              return (
                <div className="space-y-2">
                  <div className="relative pt-2">
                    <div className="h-2.5 w-full rounded-full bg-gradient-to-r from-cyber-rose via-zinc-650 to-cyber-emerald border border-zinc-800/80"></div>
                    {percentage !== null ? (
                      <div
                        className="absolute top-0 flex flex-col items-center transition-all duration-500"
                        style={{ left: `calc(${percentage}% - 6px)` }}
                      >
                        <div className="h-5 w-3 bg-white border border-slate-950 rounded shadow-[0_0_8px_rgba(255,255,255,0.4)] flex items-center justify-center">
                          <div className="w-1.5 h-1.5 rounded-full bg-cyber-indigo"></div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                  <div className="flex justify-between text-[9px] text-zinc-500 font-bold uppercase font-mono-data">
                    <span className="text-cyber-rose/80">Left Bias</span>
                    <span className="text-cyber-indigo">{label}</span>
                    <span className="text-cyber-emerald/80">Right Bias</span>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Metadata Grid */}
          <div className="grid grid-cols-2 gap-4 border-t border-indigo-950/40 pt-4 text-xs font-body-sm">
            <div>
              <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider mb-1 font-mono-data">Geographic Coordinates</h5>
              <span className="text-cyber-cyan font-mono-data font-semibold">
                {selectedEvent.latitude ? selectedEvent.latitude.toFixed(4) : "0.0000"}, {selectedEvent.longitude ? selectedEvent.longitude.toFixed(4) : "0.0000"}
              </span>
            </div>
            <div>
              <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider mb-1 font-mono-data">Source Coverage</h5>
              <span className="text-white font-semibold">
                {selectedEvent.source_count} publisher wireheads
              </span>
            </div>
            <div>
              <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider mb-1 font-mono-data">Geopolitical Importance</h5>
              <span className="text-cyber-amber font-bold">
                {selectedEvent.importance_score.toFixed(1)} / 10.0
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="border-t border-indigo-950/40 pt-4 mt-6 flex gap-3">
        <button
          onClick={() => setSelectedEvent(null)}
          className="flex-1 py-3 bg-zinc-900/60 border border-zinc-800/80 hover:bg-zinc-800 hover:text-white rounded-xl text-xs font-bold transition-all text-center text-zinc-400 active:scale-95"
        >
          Close Dossier
        </button>
        <a
          href={`/events/${selectedEvent.id}`}
          className="flex-1 py-3 bg-gradient-to-r from-cyber-cyan to-cyber-indigo hover:brightness-110 text-slate-950 font-bold rounded-xl text-xs transition-all text-center flex items-center justify-center gap-1 shadow-[0_0_15px_rgba(6,182,212,0.2)] active:scale-95"
        >
          <BookOpen className="w-3.5 h-3.5" />
          Full Analysis
        </a>
      </div>
    </div>
  );
}

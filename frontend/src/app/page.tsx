"use client";

import React, { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
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
  Bot,
  LogOut
} from "lucide-react";
import SearchBar from "./components/SearchBar";
import PrimaryNav from "./components/PrimaryNav";
import MapFilterPanel from "./components/MapFilterPanel";
import ChatbotPanel from "./components/ChatbotPanel";
import EventIllustrationImage, { EventIllustration } from "./components/EventIllustrationImage";


// Dynamically import the Map component to prevent window undefined SSR issues
const Map = dynamic(() => import("./components/Map"), { ssr: false });

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type PageItem = number | "ellipsis";

// Collapses a full page range into a short, scannable run: first page, a small
// window around the active page, last page, and ellipses for the gaps
// (e.g. 1 … 22 23 [24] 25 26 … 59). Renders every page verbatim when the range
// is small enough that truncation would not save anything.
function getPaginationItems(currentPage: number, totalPages: number): PageItem[] {
  const window = 2;
  const pages = new Set<number>();

  for (let p = currentPage - window; p <= currentPage + window; p++) {
    if (p >= 1 && p <= totalPages) pages.add(p);
  }
  pages.add(1);
  pages.add(totalPages);

  const sorted = Array.from(pages).sort((a, b) => a - b);
  const items: PageItem[] = [];
  let prev = 0;
  for (const p of sorted) {
    if (prev && p - prev > 1) items.push("ellipsis");
    items.push(p);
    prev = p;
  }
  return items;
}

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
  created_at?: string;
  illustration?: EventIllustration | null;
}

export default function HomePage() {
  const router = useRouter();
  const [events, setEvents] = useState<EventItem[]>([]);
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

  // Session indicator for the header logout control + role-aware navigation
  const [authed, setAuthed] = useState(false);
  const [userRole, setUserRole] = useState<string | null>(null);


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

  // Surface the logout control and resolve the role once a token is present
  useEffect(() => {
    if (typeof window === "undefined") return;
    const token = localStorage.getItem("admin_token");
    if (!token) return;
    setAuthed(true);
    fetch(`${API_BASE_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((me) => setUserRole(me && me.role ? me.role : "AUTH_USER"))
      .catch(() => setUserRole("AUTH_USER"));
  }, []);

  // Admin console is only relevant to administrators
  const showAdmin = userRole === "ADMIN";

  const handleLogout = async () => {
    const token = localStorage.getItem("admin_token");
    try {
      if (token) {
        await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }).catch(() => {});
      }
    } finally {
      localStorage.removeItem("admin_token");
      document.cookie = "admin_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;";
      router.push("/login");
    }
  };

  // Fetch events based on viewMode and pagination
  const fetchEvents = async (page = 1) => {
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
  };

  useEffect(() => {
    fetchEvents(1);
  }, [viewMode]);

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
          <PrimaryNav
            activeHref={viewMode === "standard" ? "/?view=standard" : "/?view=map"}
            role={userRole}
            onStandard={(e) => {
              e.preventDefault();
              setViewMode("standard");
              setSearchEmpty(false);
              if (typeof window !== "undefined") {
                window.history.pushState(null, "", "?view=standard");
              }
            }}
            onMap={(e) => {
              e.preventDefault();
              setViewMode("map");
              setSearchEmpty(false);
              if (typeof window !== "undefined") {
                window.history.pushState(null, "", "?view=map");
              }
            }}
          />
          <div className="flex items-center gap-4 flex-1 min-w-0 justify-end ml-4">
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
        <PrimaryNav
          activeHref={viewMode === "standard" ? "/?view=standard" : "/?view=map"}
          role={userRole}
          initialLoadAnimation={true}
          onStandard={(e) => {
            e.preventDefault();
            setViewMode("standard");
            if (typeof window !== "undefined") {
              window.history.pushState(null, "", "?view=standard");
            }
          }}
          onMap={(e) => {
            e.preventDefault();
            setViewMode("map");
            if (typeof window !== "undefined") {
              window.history.pushState(null, "", "?view=map");
            }
          }}
        />
        <div className="flex items-center gap-4 flex-1 min-w-0 justify-end ml-4">
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
        /* 2. STANDARD FEED VIEW (home_feed_the_watchtower port) */
        <div className="flex-grow w-full overflow-y-auto no-scrollbar flex flex-col bg-surface-container-lowest">
          <div className="flex-grow flex w-full max-w-container-max-width mx-auto relative px-margin-mobile md:px-margin-desktop py-stack-lg">
            
            {/* Primary Feed Column */}
            <main className="flex-grow w-full lg:w-3/4 pr-0 lg:pr-gutter pb-32 flex flex-col gap-stack-lg z-10 min-w-0">
              
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
                      {evt.illustration ? (
                        <EventIllustrationImage
                          illustration={evt.illustration}
                          className="h-40 mb-4"
                        />
                      ) : null}

                      <div className="flex justify-between items-start mb-3">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-400 text-[10px] font-mono-data">
                          Score: {evt.importance_score.toFixed(1)}
                        </span>
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
                <div className="flex flex-wrap items-center justify-center gap-2 mt-stack-md">
                  <button 
                    disabled={currentPage === 1}
                    onClick={() => fetchEvents(currentPage - 1)}
                    className="w-8 h-8 flex items-center justify-center rounded border border-outline-variant text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors disabled:opacity-30"
                    aria-label="Previous page"
                  >
                    ‹
                  </button>
                  {getPaginationItems(currentPage, totalPages).map((item, idx) =>
                    item === "ellipsis" ? (
                      <span
                        key={`ellipsis-${idx}`}
                        className="w-8 h-8 flex items-center justify-center text-on-surface-variant select-none"
                        aria-hidden="true"
                      >
                        …
                      </span>
                    ) : (
                      <button 
                        key={item}
                        onClick={() => fetchEvents(item)}
                        aria-current={currentPage === item ? "page" : undefined}
                        className={`w-8 h-8 flex items-center justify-center rounded font-mono-data text-xs transition-colors ${
                          currentPage === item 
                            ? "bg-primary text-on-primary font-bold" 
                            : "border border-outline-variant text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                        }`}
                      >
                        {item}
                      </button>
                    )
                  )}
                  <button 
                    disabled={currentPage === totalPages}
                    onClick={() => fetchEvents(currentPage + 1)}
                    className="w-8 h-8 flex items-center justify-center rounded border border-outline-variant text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors disabled:opacity-30"
                    aria-label="Next page"
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
                    onClick={() => fetchEvents(currentPage)}
                    className="text-zinc-400 hover:text-primary transition-colors flex items-center text-[10px] font-bold tracking-wider uppercase gap-1"
                  >
                    <RefreshCw className="w-3 h-3 animate-spin-slow" />
                    Refresh
                  </button>
                </div>
              </div>

              {/* Feed items */}
              <div className="flex-grow flex flex-col gap-3 overflow-y-auto pr-1 no-scrollbar">
                <div className="p-3 bg-zinc-900/40 rounded border border-outline-variant/30 relative">
                  <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-blue-500 rounded-l"></div>
                  <div className="flex justify-between items-start mb-1 text-[9px] text-zinc-500 font-mono-data">
                    <span>Reuters RSS Feed</span>
                    <span>Just Now</span>
                  </div>
                  <p className="font-body-sm text-body-sm text-zinc-300 leading-snug">Energy ministry officials deny upcoming offshore drilling ban rumors, citing stable supply metrics.</p>
                </div>
                
                <div className="p-3 bg-zinc-900/40 rounded border border-outline-variant/30 relative">
                  <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-rose-500 rounded-l"></div>
                  <div className="flex justify-between items-start mb-1 text-[9px] text-zinc-500 font-mono-data">
                    <span>X \ @IntelWire</span>
                    <span>4m ago</span>
                  </div>
                  <p className="font-body-sm text-body-sm text-zinc-300 leading-snug">Ambassadors in Brussels decline to comment on draft AI regulatory leaks circulating among ministers.</p>
                </div>

                <div className="p-3 bg-zinc-900/40 rounded border border-outline-variant/30 relative">
                  <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-amber-500 rounded-l"></div>
                  <div className="flex justify-between items-start mb-1 text-[9px] text-zinc-500 font-mono-data">
                    <span>Orbital Telemetry</span>
                    <span>1h ago</span>
                  </div>
                  <p className="font-body-sm text-body-sm text-zinc-300 leading-snug">Noticeable thermal anomalies detected over Northern Atlantic shipping channels, exceeding seasonal averages.</p>
                </div>
              </div>
            </aside>
          </div>

          {/* Dossier Side Panel */}
          {selectedEvent && renderDossierPanel(selectedEvent, setSelectedEvent, getTopicBadgeStyle)}

          {/* Institutional Footer */}
          <footer className="bg-surface-container-low border-t border-outline-variant/50 w-full py-6 px-margin-desktop mt-auto flex flex-col md:flex-row justify-between items-center z-10 relative">
            <div className="mb-4 md:mb-0">
              <span className="font-headline-lg text-headline-lg font-bold text-primary block mb-1">The Watchtower</span>
              <p className="font-body-sm text-body-sm text-on-surface-variant">© 2026 The Watchtower. Authoritative Editorial Intelligence.</p>
            </div>
            <nav className="flex flex-wrap gap-4 md:gap-6 items-center justify-center text-xs">
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Institutional Briefs</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Privacy Guidelines</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">System Ethics Policy</a>
              <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Uplink API Portal</a>
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
  const handleSubscribe = () => {
    alert(`Clearance confirmed. Dispatch alerts configured for dossier: ${selectedEvent.title}`);
  };

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
              const lean = (selectedEvent.bias_lean || "CENTER").toUpperCase();
              let percentage = 50;
              let label = "CENTERED INTERPRETATION";
              
              if (lean === "LEFT") {
                percentage = 10;
                label = "LEFT IDEOLOGY LEAN";
              } else if (lean === "CENTER_LEFT") {
                percentage = 30;
                label = "CENTER-LEFT LEAN";
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
                    <div 
                      className="absolute top-0 flex flex-col items-center transition-all duration-500"
                      style={{ left: `calc(${percentage}% - 6px)` }}
                    >
                      <div className="h-5 w-3 bg-white border border-slate-950 rounded shadow-[0_0_8px_rgba(255,255,255,0.4)] flex items-center justify-center">
                        <div className="w-1.5 h-1.5 rounded-full bg-cyber-indigo"></div>
                      </div>
                    </div>
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
              <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider mb-1 font-mono-data">Confidence Score</h5>
              <span className="text-cyber-emerald font-bold">
                {(8.5 + (selectedEvent.importance_score % 1.5)).toFixed(1)}/10.0
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

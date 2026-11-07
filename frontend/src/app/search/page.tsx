"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { FileText, MapPin, Search, TrendingUp } from "lucide-react";
import SearchBar from "../components/SearchBar";
import PillNav from "../components/PillNav";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SearchResult {
  id: string;
  title: string;
  summary?: string;
  topic?: string;
  location_country?: string;
  latitude?: number;
  longitude?: number;
  importance_score?: number;
}

function getTopicBadgeStyle(topic: string) {
  switch ((topic || "").toUpperCase()) {
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
}

function firstParagraph(text?: string) {
  if (!text) return "";
  return text.split("\n\n")[0];
}

function SearchResultsBody() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const q = (searchParams.get("q") || "").trim();

  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!q) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    fetch(`${API_BASE_URL}/api/v1/search/query?q=${encodeURIComponent(q)}&limit=20`)
      .then(async (r) => {
        const data = r.ok ? await r.json() : [];
        if (!cancelled) setResults(Array.isArray(data) ? (data as SearchResult[]) : []);
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
          setResults([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [q]);

  const handleSubmit = useCallback(
    (query: string) => {
      router.replace(`/search?q=${encodeURIComponent(query)}`);
    },
    [router]
  );

  const handleSuggestion = useCallback(
    (event: any) => {
      if (event && event.id) router.push(`/events/${event.id}`);
    },
    [router]
  );

  const navItems = [
    { label: "Standard", href: "/?view=standard" },
    { label: "Map", href: "/?view=map" },
    { label: "Admin", href: "/admin/dashboard" },
    { label: "Fact Checker", href: "/fact-checker" },
    { label: "Sources", href: "/sources" },
    { label: "Lists", href: "/lists" },
    { label: "Settings", href: "/settings" },
  ];

  return (
    <div className="bg-[#030712] text-on-background min-h-screen flex flex-col font-body-md">
      {/* TopNavBar */}
      <header className="flex justify-between items-center px-margin-mobile md:px-margin-desktop w-full h-16 sticky top-0 z-50 bg-[#080c16]/80 backdrop-blur-lg border-b border-indigo-950/40 flex-shrink-0">
        <PillNav
          logo="/logo.svg"
          logoAlt="GlobeLens AI Logo"
          items={navItems}
          activeHref="/search"
          baseColor="#080c16"
          pillColor="#0c101b"
          hoveredPillTextColor="#22d3ee"
          pillTextColor="#94a3b8"
          initialLoadAnimation={false}
        />
        <div className="flex items-center gap-4">
          <SearchBar
            onSubmit={handleSubmit}
            onSuggestionSelect={handleSuggestion}
          />
        </div>
      </header>

      <main className="flex-grow w-full max-w-container-max-width mx-auto px-margin-mobile md:px-margin-desktop py-stack-lg">
        <div className="flex items-center gap-3 mb-stack-md">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-cyber-cyan/40 bg-cyber-cyan/10 text-cyber-cyan font-bold uppercase tracking-wider text-[10px] font-mono-data">
            <Search className="w-3.5 h-3.5" />
            {loading ? "Searching..." : "Intelligence Search"}
          </span>
          {q && (
            <h1 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-primary font-bold truncate">
              Results for &ldquo;{q}&rdquo;
            </h1>
          )}
        </div>

        {!q && (
          <div className="glass-panel p-stack-lg rounded-lg border border-outline-variant/60 text-center">
            <div className="flex items-center justify-center gap-2 mb-3 text-cyber-cyan">
              <TrendingUp className="w-6 h-6" />
            </div>
            <h2 className="font-headline-lg-mobile text-headline-lg-mobile font-bold text-primary mb-2">
              Search the Intelligence Feed
            </h2>
            <p className="font-body-md text-body-md text-on-surface-variant max-w-xl mx-auto leading-relaxed">
              Full-text search across every indexed event dossier — headlines,
              AI-summarized body copy, topic, and geography. Start typing above
              or select a suggested dossier.
            </p>
          </div>
        )}

        {q && loading && (
          <div className="text-center py-stack-xl">
            <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse inline-block mr-1" />
            <span className="h-2 w-2 rounded-full bg-blue-400 animate-pulse inline-block mr-1" />
            <span className="h-2 w-2 rounded-full bg-blue-300 animate-pulse inline-block" />
            <p className="mt-3 font-body-sm text-body-sm text-on-surface-variant">
              Querying the Elasticsearch index for &ldquo;{q}&rdquo;
            </p>
          </div>
        )}

        {q && !loading && failed && (
          <div className="text-center py-stack-xl">
            <p className="font-body-md text-body-md text-error">Search unavailable right now. Try again shortly.</p>
          </div>
        )}

        {q && !loading && !failed && results.length === 0 && (
          <div className="text-center py-stack-xl">
            <span className="material-symbols-outlined text-6xl text-on-surface-variant opacity-60 select-none">
              troubleshoot
            </span>
            <p className="mt-4 font-body-md text-body-md text-on-surface-variant">
              Zero intelligence recovered for &ldquo;{q}&rdquo;. Broaden the query or check the spelling.
            </p>
            <Link
              href="/?view=standard"
              className="inline-block mt-6 bg-primary hover:bg-primary-fixed text-on-primary font-label-caps text-[12px] px-6 py-2.5 rounded tracking-wider uppercase font-bold transition-all hover:scale-98 active:scale-95 shadow-md"
            >
              Back to the Feed
            </Link>
          </div>
        )}

        {/* Results Grid */}
        {q && !loading && !failed && results.length > 0 && (
          <>
            <p className="font-mono-data text-mono-data text-on-surface-variant text-xs mb-stack-md">
              {results.length} matched dossier{results.length === 1 ? "" : "s"}
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-gutter">
              {results.map((evt) => (
                <Link
                  key={evt.id}
                  href={`/events/${evt.id}`}
                  className="bg-surface-container border border-outline-variant/60 rounded-lg p-5 flex flex-col justify-between h-full cursor-pointer hover:border-primary/60 hover:-translate-y-0.5 transition-all duration-200 shadow-sm"
                >
                  <div>
                    <div className="flex justify-between items-start mb-3">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-400 text-[10px] font-mono-data">
                        Score: {typeof evt.importance_score === "number" ? evt.importance_score.toFixed(1) : "—"}
                      </span>
                      <span className={`inline-flex border px-2 py-0.5 rounded font-label-caps text-[9px] uppercase tracking-wider ${getTopicBadgeStyle(evt.topic || "")}`}>
                        {evt.topic || "WORLD"}
                      </span>
                    </div>

                    <h3 className="font-headline-lg-mobile text-headline-lg-mobile font-bold text-on-surface mb-2 transition-colors leading-snug group-hover:text-primary">
                      {evt.title}
                    </h3>

                    <p className="font-body-sm text-body-sm text-on-surface-variant line-clamp-3 mb-4 leading-relaxed">
                      {firstParagraph(evt.summary) || "Intelligence dossiers are active. Publisher feeds are being merged."}
                    </p>
                  </div>

                  <div className="flex items-center justify-between pt-3 border-t border-outline-variant/40 mt-auto">
                    <span className="font-mono-data text-mono-data text-on-surface-variant flex items-center gap-1 text-[11px]">
                      <MapPin className="w-3 h-3 text-zinc-500" /> {evt.location_country || "Global"}
                    </span>
                    <span className="font-mono-data text-mono-data text-on-surface-variant text-[11px] flex items-center gap-1">
                      <FileText className="w-3 h-3 text-zinc-500" /> Read dossier
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<div className="bg-[#030712] text-on-background min-h-screen" />}>
      <SearchResultsBody />
    </Suspense>
  );
}
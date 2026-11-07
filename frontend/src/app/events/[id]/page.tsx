"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Image from "next/image";
import { 
  Globe, 
  ArrowLeft, 
  MapPin, 
  TrendingUp, 
  AlertTriangle,
  ChevronRight,
  Shield,
  Layers,
  ArrowRight,
  HelpCircle,
  Calendar,
  FileText,
  Bookmark,
  RefreshCw,
  Clock,
  BookOpen,
  CheckCircle,
  CircleSlash,
  ExternalLink,
  Newspaper,
  ListPlus,
  Check,
  Bell
} from "lucide-react";
import PillNav from "../../components/PillNav";
import TransparencyPanel, {
  Transparency,
  Contradiction,
} from "../../components/TransparencyPanel";
import ContradictionBadge from "../../components/ContradictionBadge";
import CommentSection from "../../components/CommentSection";
import BiasSpectrum, { BiasSource } from "../../components/BiasSpectrum";
import ClaimAttributionPanel, {
  type ClaimSource,
  type EventClaim,
  ConfidenceMeter,
} from "./components/ClaimAttributionPanel";
import SourceComparison from "./components/SourceComparison";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Source {
  name: string;
  // Both are null when the API has no rating on record. They must not be
  // defaulted: a 0 credibility or a CENTER lean would both be invented claims.
  credibility_score: number | null;
  bias_lean?: string | null;
  country?: string | null;
  is_classified?: boolean;
}

interface Article {
  id: string;
  title: string;
  content: string;
  url: string;
  published_at?: string;
  is_hidden?: boolean;
  // Media the source actually published for the story, captured at ingest
  // time. image_url is null for an article with no image — never a placeholder.
  image_url?: string | null;
  image_caption?: string | null;
  media_type?: string | null;
  // AI caption written by the config-gated vision adapter. Null until an
  // operator enables VISION_PROVIDER and a caption has run.
  image_ai_caption?: string | null;
  image_ai_caption_model?: string | null;
  source: Source;
}

interface ClaimItem {
  text: string;
  status: string;
  // 0..1 model confidence in the verdict; absent/None means "not scored",
  // which is rendered distinctly from a score of zero.
  confidence?: number | null;
  is_scored?: boolean;
  reviewed_by?: string;
  review_url?: string;
  // Real third-party fact-checks returned by the configured external feed, if
  // any. Absent when no feed is connected; never synthesised.
  external_reviews?: Array<{
    publisher: string;
    url: string;
    title: string;
    verdict: string;
    review_date: string;
    language: string;
  }>;
}

interface HistoricalMatchItem {
  title: string;
  last_active: string;
  match_percentage: number;
}

interface FactCheckResult {
  credibility_score: number;
  trust_risks: string[];
  independent_cross_references: number;
  claims: ClaimItem[];
  historical_matches: HistoricalMatchItem[];
  summary?: string;
}

interface EventDetail {
  id: string;
  title: string;
  summary: string;
  topic: string;
  country: string;
  latitude: number;
  longitude: number;
  importance_score: number;
  bias_lean: string | null;
  is_classified?: boolean;
  status: string;
  created_at?: string;
  updated_at?: string;
  citation_sources?: string[] | null;
  contradictions?: Contradiction[];
  // Event-level cross-source conflict signal derived at serialisation time.
  contradiction_count?: number;
  contradiction_max_confidence?: number | null;
  contradiction_level?: string;
  transparency?: Transparency;
  // The API ships the article metadata behind each citation index alongside the
  // index itself, so the UI never has to join `citation_indices` against
  // `articles` positionally — a client-side join could silently misalign and
  // show an outlet backing a claim it never reported. `ClaimSource`/`EventClaim`
  // live in ClaimAttributionPanel so the page and the panel share one shape.
  claims?: EventClaim[];
  claim_count?: number;
  articles: Article[];
}

function ArticleThumb({ article }: { article: Article }) {
  const [failed, setFailed] = useState(false);
  const [aiCaption, setAiCaption] = useState(article.image_ai_caption || null);
  const [aiModel, setAiModel] = useState(article.image_ai_caption_model || null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    setAnalyzing(true);
    setAnalyzeError(null);
    const token =
      typeof window !== "undefined"
        ? localStorage.getItem("admin_token")
        : null;
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      const body = new URLSearchParams();
      body.set("image_url", article.image_url ?? "");
      const res = await fetch(`${API_BASE_URL}/api/v1/media/analyze`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setAnalyzeError(
          data.detail
            ? String(data.detail)
            : "AI captioning is unavailable (see server logs)."
        );
        return;
      }
      setAiCaption(data.caption || null);
      setAiModel(data.model || null);
    } catch {
      setAnalyzeError("Could not reach the captioning service.");
    } finally {
      setAnalyzing(false);
    }
  };

  const visibleCaption = aiCaption || article.image_caption;

  return (
    <div className="-mx-4 -mt-4 mb-3">
      {article.image_url && !failed && (
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block relative overflow-hidden h-36"
          aria-label={`Image for: ${article.title}`}
        >
          <Image
            src={article.image_url}
            alt={article.image_caption || article.title}
            fill
            sizes="100%"
            loading="lazy"
            unoptimized
            onError={() => setFailed(true)}
            className="object-cover"
          />
        </a>
      )}
      {(visibleCaption || article.image_url) && (
        <div className="mt-2 flex items-start gap-2">
          <div className="flex-1 text-xs text-zinc-400 leading-relaxed">
            {visibleCaption ? (
              <span>{visibleCaption}</span>
            ) : (
              <span className="text-zinc-600 italic">
                No caption yet for this image.
              </span>
            )}
            {aiCaption && aiModel && (
              <div className="mt-1 text-[10px] font-mono-data text-cyber-cyan/80 uppercase tracking-wider">
                AI caption · {aiModel}
              </div>
            )}
          </div>
          {article.image_url && (
            <button
              onClick={handleAnalyze}
              disabled={analyzing}
              className="shrink-0 px-2.5 py-1 text-[10px] font-mono-data uppercase tracking-wider border border-outline-variant rounded text-zinc-300 hover:text-cyber-cyan hover:border-cyber-cyan/50 transition-colors disabled:opacity-50"
            >
              {analyzing ? "Analyzing…" : aiCaption ? "Re-analyze" : "AI caption"}
            </button>
          )}
        </div>
      )}
      {analyzeError && (
        <p className="mt-1 text-xs text-amber-400/90">{analyzeError}</p>
      )}
    </div>
  );
}

export default function EventDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [factCheck, setFactCheck] = useState<FactCheckResult | null>(null);
  const [isCheckingFacts, setIsCheckingFacts] = useState(false);
  const [factCheckError, setFactCheckError] = useState<string | null>(null);

  useEffect(() => {
    if (event && !factCheck && !isCheckingFacts) {
      setIsCheckingFacts(true);
      setFactCheckError(null);
      const token =
        typeof window !== "undefined"
          ? localStorage.getItem("admin_token")
          : null;
      const headers: Record<string, string> = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      fetch(`${API_BASE_URL}/api/v1/events/${id}/fact-check`, { headers })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data) => {
          if (data.detail) {
            setFactCheckError(data.detail);
          } else {
            setFactCheck(data);
          }
        })
        .catch((err) =>
          setFactCheckError(
            err?.message === "HTTP 503"
              ? "Claim analysis is temporarily unavailable; no result was produced."
              : "Claim analysis could not be loaded. The analyzer may be offline."
          )
        )
        .finally(() => setIsCheckingFacts(false));
    }
  }, [event, id, factCheck, isCheckingFacts]);
  const [isBookmarked, setIsBookmarked] = useState(false);
  const [bookmarkBusy, setBookmarkBusy] = useState(false);
  const [bookmarkNotice, setBookmarkNotice] = useState<string | null>(null);

  // "Follow this event" — opt in to alert pushes about this specific dossier.
  // State is only meaningful while signed in; a signed-out visitor sees the
  // neutral "off" button and a sign-in hint on tap.
  const [isFollowing, setIsFollowing] = useState(false);
  const [followBusy, setFollowBusy] = useState(false);
  const [followNotice, setFollowNotice] = useState<string | null>(null);

  // "Save to list" popover state. Lists are fetched only when the picker is
  // first opened, so a visitor who never touches it pays no idle request.
  const [listsOpen, setListsOpen] = useState(false);
  const [userLists, setUserLists] = useState<{ id: string; title: string }[] | null>(null);
  const [listBusy, setListBusy] = useState<string | null>(null);
  const [listNotice, setListNotice] = useState<string | null>(null);
  const [newListTitle, setNewListTitle] = useState("");
  const [creatingList, setCreatingList] = useState(false);
  

  
  const fetchEventDetails = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/events/${id}`);
      if (!response.ok) {
        if (response.status === 404) {
          throw new Error("Dossier not found");
        }
        throw new Error("Failed to retrieve event analysis");
      }
      const data = await response.json();
      setEvent(data);
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Failed to connect to synthesis server");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (id) {
      fetchEventDetails();
    }
  }, [id, fetchEventDetails]);

  const handleBookmarkToggle = async () => {
    if (!event) return;

    // The endpoint requires a credential, so a signed-out visitor gets a clear
    // message rather than a save that silently fails.
    const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token) {
      setBookmarkNotice("Sign in to save this dossier.");
      return;
    }

    // Optimistic, but reversible: if the request fails we put the previous value
    // back so the button never claims a save that did not happen.
    const previous = isBookmarked;
    setIsBookmarked(!previous);
    setBookmarkBusy(true);
    setBookmarkNotice(null);

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/bookmarks/events/${id}`, {
        method: previous ? "DELETE" : "POST",
        headers: { Authorization: `Bearer ${token}` },
      });

      if (!res.ok) {
        setIsBookmarked(previous);
        const body = await res.json().catch(() => ({}));
        setBookmarkNotice(
          res.status === 401 || res.status === 403
            ? "Your session expired. Sign in again to save."
            : body?.detail || "Could not update your dossier."
        );
        return;
      }

      const data = await res.json().catch(() => ({}));
      setIsBookmarked(Boolean(data.is_bookmarked));
      setBookmarkNotice(
        data.is_bookmarked ? "Saved to your dossier." : "Removed from your dossier."
      );
    } catch {
      setIsBookmarked(previous);
      setBookmarkNotice("Network error. Your dossier was not changed.");
    } finally {
      setBookmarkBusy(false);
    }
  };

  // Load follow status once signed-in state settles. A signed-out visitor gets
  // no probe (the API would just 401) — they default to "not following".
  useEffect(() => {
    let cancelled = false;
    const token =
      typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token || !id) return undefined;
    fetch(`${API_BASE_URL}/api/v1/events/${id}/follow`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : { following: false }))
      .then((d) => {
        if (!cancelled) setIsFollowing(Boolean(d.following));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleFollowToggle = async () => {
    if (!id) return;
    const token =
      typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token) {
      setFollowNotice("Sign in to follow this event.");
      return;
    }
    const previous = isFollowing;
    setIsFollowing(!previous);
    setFollowBusy(true);
    setFollowNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/events/${id}/follow`, {
        method: previous ? "DELETE" : "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        setIsFollowing(previous);
        const body = await res.json().catch(() => ({}));
        setFollowNotice(body?.detail || "Could not update event follow.");
        return;
      }
      const data = await res.json().catch(() => ({}));
      setIsFollowing(Boolean(data.following));
      setFollowNotice(
        data.following
          ? "Following this event — alerts on update."
          : "No longer following this event."
      );
    } catch {
      setIsFollowing(previous);
      setFollowNotice("Network error. Event follow was not changed.");
    } finally {
      setFollowBusy(false);
    }
  };

  // "Save to list" handlers. Adding to a list is idempotent on the server, so
  // the UI never needs a per-list membership probe; the API reports whether the
  // item was newly added or already present.
  const openListPicker = async () => {
    setListsOpen((prev) => {
      const next = !prev;
      if (next && userLists === null) {
        const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
        if (!token) {
          setListNotice("Sign in to save to a list.");
          return next;
        }
        fetch(`${API_BASE_URL}/api/v1/reading-lists?limit=100`, {
          headers: { Authorization: `Bearer ${token}` },
        })
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            if (d) setUserLists(d.items ?? []);
          })
          .catch(() => setListNotice("Could not load your lists."));
      }
      return next;
    });
  };

  const addToList = async (listId: string, listTitle: string) => {
    const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token) {
      setListNotice("Sign in to save to a list.");
      return;
    }
    setListBusy(listId);
    setListNotice(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/reading-lists/${listId}/events/${id}`,
        { method: "POST", headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setListNotice(body?.detail || "Could not save to that list.");
        return;
      }
      const data = await res.json();
      setListNotice(
        data.added ? `Saved to "${listTitle}".` : `Already in "${listTitle}".`
      );
    } catch {
      setListNotice("Network error. Nothing was saved.");
    } finally {
      setListBusy(null);
    }
  };

  const createAndAddToList = async (e: React.FormEvent) => {
    e.preventDefault();
    const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token || !newListTitle.trim()) return;
    setCreatingList(true);
    setListNotice(null);
    try {
      const c = await fetch(`${API_BASE_URL}/api/v1/reading-lists`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: newListTitle.trim() }),
      });
      if (!c.ok) {
        const body = await c.json().catch(() => ({}));
        setListNotice(body?.detail || "Could not create that list.");
        return;
      }
      const created = await c.json();
      setUserLists((prev) => (prev ? [{ id: created.id, title: created.title }, ...prev] : prev));
      setNewListTitle("");
      await addToList(created.id, created.title);
    } catch {
      setListNotice("Network error. Nothing was saved.");
    } finally {
      setCreatingList(false);
    }
  };

  // Load the real saved state. Public visitors simply stay unsaved; we do not
  // guess, and we do not write a local "saved" flag that the server disagrees with.
  useEffect(() => {
    if (!id) return;
    const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token) {
      setIsBookmarked(false);
      return;
    }

    let cancelled = false;
    fetch(`${API_BASE_URL}/api/v1/bookmarks/events/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d) setIsBookmarked(Boolean(d.is_bookmarked));
      })
      .catch(() => {
        /* leave the unsaved default in place */
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  // Helper to get topic badge style
  const getTopicBadgeStyle = (topic: string) => {
    switch (topic?.toUpperCase()) {
      case "POLITICS":
      case "GEOPOLITICS":
        return "bg-rose-950/40 text-rose-400 border-rose-900/50";
      case "ECONOMY":
      case "MACROECONOMICS":
        return "bg-amber-950/40 text-amber-400 border-amber-900/50";
      case "TECHNOLOGY":
      case "TECH & CYBER":
        return "bg-blue-950/40 text-blue-400 border-blue-900/50";
      case "SPORTS":
        return "bg-emerald-950/40 text-emerald-400 border-emerald-900/50";
      case "HEALTH":
        return "bg-purple-950/40 text-purple-400 border-purple-900/50";
      default:
        return "bg-zinc-900 text-zinc-300 border-zinc-800";
    }
  };

  // Resolve an inline [N] marker to the article that was actually read during
  // synthesis. The backend persists that exact ordering in `citation_sources`;
  // guessing from the response array is what previously misattributed sources.
  const buildCitationIndex = (
    citationSources: string[] | null | undefined,
    articlesList: Article[]
  ): Map<number, Article> => {
    const byId = new Map(articlesList.map((a) => [a.id, a]));
    const resolved = new Map<number, Article>();

    if (!citationSources || citationSources.length === 0) return resolved;

    citationSources.forEach((articleId, idx) => {
      const article = byId.get(articleId);
      if (article) resolved.set(idx + 1, article);
    });

    return resolved;
  };

  // Helper to render body content with hover citations
  const renderCitationsParagraphs = (
    summaryText: string,
    articlesList: Article[],
    citationIndex: Map<number, Article>
  ) => {
    if (!summaryText) return <p className="text-zinc-500 italic">No summary details compiled yet.</p>;

    const paragraphs = summaryText.split("\n\n").filter(p => p.trim().length > 0);
    
    const citationRegex = /\[(\d+)\]/g;

    return paragraphs.map((para, pIdx) => {
      const parts = para.split(citationRegex);

      return (
        <p key={pIdx} className="mb-6 text-on-surface leading-[1.8] text-body-lg font-body-lg">
          {parts.map((part, idx) => {
            if (idx % 2 === 0) {
              return <span key={idx}>{part}</span>;
            } else {
              const marker = parseInt(part, 10);
              const associatedArticle = citationIndex.get(marker);

              if (!associatedArticle) {
                return (
                  <span
                    key={idx}
                    className="inline-flex items-center justify-center w-[18px] h-[18px] mx-0.5 rounded-full bg-zinc-800/60 text-zinc-500 text-[10px] font-bold border border-zinc-700 align-super cursor-not-allowed"
                    title="Citation could not be resolved to a source"
                  >
                    {part}
                  </span>
                );
              }

              const trustValue = associatedArticle.source.credibility_score;
              const trustLabel =
                trustValue === null || trustValue === undefined
                  ? "no trust rating on record"
                  : `${Math.round(trustValue * 100)}% trust`;

              return (
                <a
                  key={idx}
                  href={associatedArticle.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Citation [${marker}]: ${associatedArticle.title} at ${associatedArticle.source.name}`}
                  className="has-tooltip inline-flex items-center justify-center w-[18px] h-[18px] mx-0.5 rounded-full bg-primary/20 text-primary text-[10px] font-bold border border-primary/40 hover:bg-primary/40 hover:text-white transition-colors duration-150 align-super"
                >
                  {part}
                  <span className="ai-tooltip glass-panel p-3 rounded text-left shadow-[0_8px_24px_rgba(0,0,0,0.6)] w-64 z-50 whitespace-normal font-normal normal-case tracking-normal">
                    <strong className="block text-primary font-mono-data mb-1.5 flex items-center gap-1.5 text-xs">
                      <span className="material-symbols-outlined text-[14px]">source</span>
                      {associatedArticle.source.name} ({trustLabel})
                    </strong>
                    <span className="block text-xs font-semibold text-white mb-1 leading-tight">
                      {associatedArticle.title}
                    </span>
                    <span className="inline-flex text-[10px] text-primary items-center gap-1 mt-2">
                      Open source wire <ExternalLink className="w-2.5 h-2.5" />
                    </span>
                  </span>
                </a>
              );
            }
          })}
        </p>
      );
    });
  };

  // Loading skeleton matching loading_feed_globelens_ai aesthetic
  if (loading) {
    return (
      <div className="bg-background text-on-background min-h-screen flex flex-col font-body-md">
        <header className="flex justify-between items-center px-margin-desktop w-full h-16 bg-surface/80 border-b border-outline-variant z-50 animate-pulse">
          <div className="h-6 w-32 bg-surface-container rounded"></div>
          <div className="h-6 w-64 bg-surface-container rounded hidden md:block"></div>
          <div className="h-8 w-8 bg-surface-container rounded-full"></div>
        </header>

        <div className="flex-1 max-w-[1440px] mx-auto w-full grid grid-cols-1 lg:grid-cols-12 gap-gutter px-margin-mobile lg:px-margin-desktop py-stack-lg animate-pulse">
          <main className="col-span-1 lg:col-span-9 flex flex-col gap-6">
            <div className="h-4 w-48 bg-surface-container rounded mb-2"></div>
            <div className="h-10 w-3/4 bg-surface-container rounded mb-4"></div>
            <div className="h-6 w-full bg-surface-container rounded mb-6"></div>
            <div className="h-[300px] md:h-[400px] bg-surface-container rounded border border-outline-variant"></div>
            <div className="h-20 bg-surface-container rounded border border-outline-variant mt-6"></div>
          </main>
          <aside className="hidden lg:block lg:col-span-3 bg-surface-container border border-outline-variant rounded h-[600px]"></aside>
        </div>
      </div>
    );
  }

  // Not Found / Error UI mapping 404_intelligence_gap_globelens_ai
  if (error || !event) {
    return (
      <div className="bg-background text-on-background min-h-screen flex flex-col font-body-md relative overflow-hidden">
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
                  router.push('/?view=standard');
                }
              },
              { 
                label: 'Map', 
                href: '/?view=map',
                onClick: (e) => {
                  e.preventDefault();
                  router.push('/?view=map');
                }
              },
              { label: 'Admin', href: '/admin/dashboard' },
              { label: 'Dossiers', href: '/dossiers' },
              { label: 'Fact Checker', href: '/fact-checker' },
          { label: 'Sources', href: '/sources' },
          { label: 'Lists', href: '/lists' },
          { label: 'Settings', href: '/settings' }
            ]}
            activeHref=""
            baseColor="#080c16"
            pillColor="#0c101b"
            hoveredPillTextColor="#22d3ee"
            pillTextColor="#94a3b8"
            initialLoadAnimation={false}
          />
        </header>

        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(69,70,77,0.06)_1px,transparent_1px),linear-gradient(to_bottom,rgba(69,70,77,0.06)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none z-0"></div>
        
        <main className="flex-grow flex flex-col items-center justify-center px-margin-mobile md:px-margin-desktop py-stack-lg z-10 relative">
          <div className="relative mb-6">
            <div className="absolute inset-0 bg-error/10 rounded-full blur-xl"></div>
            <div className="w-24 h-24 rounded-full bg-surface-container-high/85 border border-outline-variant flex items-center justify-center shadow-sm">
              <span className="material-symbols-outlined text-5xl text-error opacity-80 select-none">
                warning
              </span>
            </div>
          </div>

          <div className="max-w-xl text-center mb-8">
            <h1 className="font-headline-xl text-headline-xl text-primary mb-3 tracking-tight">Dossier Gap Detected</h1>
            <p className="font-body-lg text-body-lg text-on-surface-variant leading-relaxed">
              {error === "Dossier not found" 
                ? "The requested event dossier identifier could not be validated or has been reassigned within the database cluster." 
                : "A protocol failure occurred while retrieving synthesized intelligence reports. Verify backend core availability."}
            </p>
          </div>

          <button 
            onClick={() => router.push("/")}
            className="bg-primary hover:bg-primary-fixed text-on-primary font-label-caps text-[12px] px-8 py-3 rounded tracking-wider uppercase font-bold transition-all shadow-md flex items-center gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Global Map
          </button>
        </main>
      </div>
    );
  }

  const visibleArticles = event.articles.filter((a) => !a.is_hidden);
  const citationIndex = buildCitationIndex(event.citation_sources, visibleArticles);

  // Reverse lookup for the numbered works-cited: which summary-citation number
  // (if any) points at each article. Kept to the first occurrence so a source
  // cited twice still maps to a single unambiguous [N] in the list.
  const citedIndexByArticle = new Map<string, number>();
  if (event.citation_sources) {
    event.citation_sources.forEach((articleId, idx) => {
      if (!citedIndexByArticle.has(articleId)) {
        citedIndexByArticle.set(articleId, idx + 1);
      }
    });
  }

  // Trust average is computed only from stored scores. No invented default.
  // Averaged only over outlets that actually carry a rating. Counting an
  // unrated source as 0 would show a real-looking percentage built partly from
  // missing data.
  const ratedArticles = visibleArticles.filter(
    (art) => art.source.credibility_score !== null && art.source.credibility_score !== undefined
  );
  const avgTrustScore =
    ratedArticles.length > 0
      ? Math.round(
          (ratedArticles.reduce((acc, art) => acc + (art.source.credibility_score as number), 0) /
            ratedArticles.length) *
            100
        )
      : null;

  // Article buckets by publisher lean. Unclassified articles are deliberately
  // NOT folded into the centre bucket: doing so asserts a neutral political
  // position for publishers we have never rated.
  const leftArticles = visibleArticles.filter(
    (a) => a.source.bias_lean === "LEFT" || a.source.bias_lean === "CENTER_LEFT"
  );
  const centerArticles = visibleArticles.filter((a) => a.source.bias_lean === "CENTER");
  const rightArticles = visibleArticles.filter(
    (a) => a.source.bias_lean === "RIGHT" || a.source.bias_lean === "CENTER_RIGHT"
  );
  const unclassifiedArticles = visibleArticles.filter(
    (a) => !a.source.bias_lean || !["LEFT", "CENTER_LEFT", "CENTER", "CENTER_RIGHT", "RIGHT"].includes(a.source.bias_lean)
  );

  const biasSources: BiasSource[] = Object.values(
    visibleArticles.reduce<Record<string, BiasSource>>((acc, art) => {
      const name = art.source.name;
      if (!acc[name]) {
        acc[name] = {
          name,
          bias_lean: art.source.bias_lean ?? null,
          credibility_score: art.source.credibility_score ?? null,
          country: art.source.country ?? null,
          articleCount: 0,
        };
      }
      acc[name].articleCount += 1;
      return acc;
    }, {})
  );

  // Bias lean label & value. An unclassified event is NOT centre: the API
  // returns null instead of a fabricated "CENTER", so the marker is withheld
  // rather than implying a neutral assessment that was never made.
  const biasLabel = event.bias_lean ? event.bias_lean.replace(/_/g, "-") : "Not classified";
  let biasPercentage: number | null = null;
  if (event.bias_lean === "LEFT") biasPercentage = 15;
  else if (event.bias_lean === "CENTER_LEFT") biasPercentage = 35;
  else if (event.bias_lean === "CENTER") biasPercentage = 50;
  else if (event.bias_lean === "CENTER_RIGHT") biasPercentage = 65;
  else if (event.bias_lean === "RIGHT") biasPercentage = 85;

  return (
    <div className="bg-background text-on-background min-h-screen flex flex-col font-body-md relative overflow-x-hidden">
      {/* TopNavBar — PillNav */}
      <header className="flex justify-between items-center px-margin-desktop w-full h-16 sticky top-0 z-50 bg-[#080c16]/80 backdrop-blur-lg border-b border-indigo-950/40 flex-shrink-0">
        <PillNav
          logo="/logo.svg"
          logoAlt="GlobeLens AI Logo"
          items={[
            {
              label: 'Standard',
              href: '/?view=standard',
              onClick: (e) => { e.preventDefault(); router.push('/?view=standard'); }
            },
            {
              label: 'Map',
              href: '/?view=map',
              onClick: (e) => { e.preventDefault(); router.push('/?view=map'); }
            },
            { label: 'Admin', href: '/admin/dashboard' },
            { label: 'Fact Checker', href: '/fact-checker' },
          { label: 'Sources', href: '/sources' },
          { label: 'Lists', href: '/lists' },
          { label: 'Settings', href: '/settings' }
          ]}
          activeHref=""
          baseColor="#080c16"
          pillColor="#0c101b"
          hoveredPillTextColor="#22d3ee"
          pillTextColor="#94a3b8"
          initialLoadAnimation={false}
        />
      </header>

      {/* Main Container */}
      <div className="flex-1 max-w-[1440px] mx-auto w-full grid grid-cols-1 lg:grid-cols-12 gap-gutter px-margin-mobile lg:px-margin-desktop py-stack-lg relative">
        
        {/* Main Content Canvas (9 Cols) */}
        <main className="col-span-1 lg:col-span-9 flex flex-col gap-stack-lg pb-stack-lg">
          
          {/* Article Header Area */}
          <article className="flex flex-col gap-stack-md relative z-10">
            {/* Meta tags row */}
            <div className="flex items-center justify-between flex-wrap gap-4">
              <div className="flex items-center gap-3">
                <span className="text-secondary font-label-caps text-label-caps uppercase tracking-wider">
                  {event.topic}
                </span>
                <span className="text-outline-variant">•</span>
                <span className="text-on-surface-variant font-mono-data text-mono-data">
                  {event.created_at ? new Date(event.created_at).toUTCString() : "Active Intel"}
                </span>
                {/* Global Reliability Score Badge */}
                <div 
                  className="flex items-center gap-1.5 ml-2 px-2.5 py-0.5 bg-surface-container rounded-full border border-outline-variant border-l-2 border-l-primary"
                  title={
                    avgTrustScore === null
                      ? "No publisher carries a trust rating in this dossier"
                      : `Mean publisher trust across ${ratedArticles.length} rated source${ratedArticles.length === 1 ? "" : "s"}`
                  }
                >
                  <Shield className="w-3.5 h-3.5 text-primary" />
                  <span className="font-mono-data text-mono-data text-primary">
                    {avgTrustScore === null ? "Not Rated" : `${avgTrustScore}% Reliable`}
                  </span>
                </div>
                <ContradictionBadge
                  level={event.contradiction_level}
                  count={event.contradiction_count}
                  maxConfidence={event.contradiction_max_confidence}
                />
              </div>

              {/* Bookmark + Reading List Toggles */}
              <div className="flex flex-col items-end gap-1">
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleBookmarkToggle}
                    disabled={bookmarkBusy}
                    aria-pressed={isBookmarked}
                    className={`flex items-center gap-2 px-3 py-1.5 bg-surface-container border rounded font-body-sm text-body-sm transition-colors disabled:opacity-60 disabled:cursor-not-allowed ${
                      isBookmarked
                        ? "border-primary text-primary"
                        : "border-outline-variant text-on-surface hover:border-primary"
                    }`}
                  >
                    <Bookmark className={`w-[18px] h-[18px] ${isBookmarked ? "fill-primary" : ""}`} />
                    <span>
                      {bookmarkBusy
                        ? "Saving…"
                        : isBookmarked
                          ? "Saved to Dossiers"
                          : "Bookmark Intel"}
                    </span>
                  </button>

                  <button
                    onClick={handleFollowToggle}
                    disabled={followBusy}
                    aria-pressed={isFollowing}
                    className={`flex items-center gap-2 px-3 py-1.5 bg-surface-container border rounded font-body-sm text-body-sm transition-colors disabled:opacity-60 disabled:cursor-not-allowed ${
                      isFollowing
                        ? "border-cyber-cyan text-cyber-cyan"
                        : "border-outline-variant text-on-surface hover:border-primary"
                    }`}
                  >
                    <Bell className={`w-[18px] h-[18px] ${isFollowing ? "fill-cyber-cyan" : ""}`} />
                    <span>
                      {followBusy
                        ? "Working…"
                        : isFollowing
                          ? "Following"
                          : "Follow Event"}
                    </span>
                  </button>

                  <div className="relative">
                    <button
                      onClick={openListPicker}
                      aria-expanded={listsOpen}
                      className="flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant rounded font-body-sm text-body-sm text-on-surface hover:border-primary transition-colors"
                    >
                      <ListPlus className="w-[18px] h-[18px]" />
                      Save to List
                    </button>

                    {listsOpen ? (
                      <div
                        className="absolute right-0 top-full mt-2 w-72 z-40 border border-outline-variant/40 bg-[#0b1020] rounded-lg shadow-xl p-3 space-y-2"
                        onMouseLeave={() => setListsOpen(false)}
                      >
                        {listNotice ? (
                          <p
                            className="text-[11px] font-mono-data text-zinc-400"
                            role="status"
                          >
                            {listNotice}
                          </p>
                        ) : null}

                        {userLists === null ? (
                          <p className="text-body-sm text-zinc-400">Loading your lists…</p>
                        ) : null}

                        {userLists !== null && userLists.length === 0 ? (
                          <p className="text-body-sm text-zinc-400">
                            No lists yet. Create one below, then it is added too.
                          </p>
                        ) : null}

                        <ul className="space-y-1 max-h-56 overflow-y-auto">
                          {userLists?.map((l) => (
                            <li key={l.id}>
                              <button
                                onClick={() => addToList(l.id, l.title)}
                                disabled={listBusy === l.id}
                                className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded hover:bg-surface-container text-body-sm text-on-surface disabled:opacity-50"
                              >
                                <span className="w-4 shrink-0">
                                  {listBusy === l.id ? (
                                    <Check className="w-3.5 h-3.5 text-primary" />
                                  ) : null}
                                </span>
                                <span className="truncate">{l.title}</span>
                              </button>
                            </li>
                          ))}
                        </ul>

                        <form
                          onSubmit={createAndAddToList}
                          className="pt-2 border-t border-outline-variant/30 flex gap-2"
                        >
                          <input
                            type="text"
                            value={newListTitle}
                            onChange={(e) => setNewListTitle(e.target.value)}
                            placeholder="New list name"
                            maxLength={200}
                            className="flex-1 min-w-0 bg-background border border-outline-variant rounded px-2 py-1 text-body-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-primary"
                          />
                          <button
                            type="submit"
                            disabled={creatingList || !newListTitle.trim()}
                            className="bg-primary hover:bg-primary-fixed text-on-primary font-label-caps text-[11px] px-3 py-1 rounded tracking-wider uppercase font-bold transition-all disabled:opacity-50"
                          >
                            {creatingList ? "…" : "Add"}
                          </button>
                        </form>
                      </div>
                    ) : null}
                  </div>
                </div>

                {bookmarkNotice ? (
                  <span className="text-[10px] font-mono-data text-zinc-400" role="status">
                    {bookmarkNotice}
                  </span>
                ) : null}

                {followNotice ? (
                  <span className="text-[10px] font-mono-data text-zinc-400" role="status">
                    {followNotice}
                  </span>
                ) : null}
              </div>
            </div>

            {/* Headline & Summary */}
            <h1 className="font-display-lg text-[28px] md:text-display-lg text-on-surface leading-tight font-bold">
              {event.title}
            </h1>
            
            <p className="font-body-lg text-body-lg text-secondary leading-relaxed border-l-4 border-surface-variant pl-4">
              Detailed analytical synthesis tag-linked to {event.country || "Global Region"} operational updates.
            </p>

            {/*
              No hero image. This block previously rendered a hardcoded
              Googleusercontent photograph for every event, which meant an
              unrelated stock image was presented as if it depicted the story.
              A generated coordinate panel is used instead: it is derived from
              the event's own latitude/longitude, so it cannot misattribute.
            */}
            <div
              className="w-full h-[180px] md:h-[240px] rounded border border-outline-variant overflow-hidden mt-2 relative"
              style={{
                backgroundImage:
                  "radial-gradient(120% 120% at 18% 20%, rgba(34,211,238,0.14) 0%, transparent 55%), radial-gradient(120% 120% at 82% 78%, rgba(217,70,239,0.12) 0%, transparent 55%), linear-gradient(160deg, #0b1020 0%, #0d1226 60%, #0a0f1d 100%)",
              }}
            >
              <div className="absolute inset-0 opacity-[0.18] [background-image:linear-gradient(to_right,#64748b_1px,transparent_1px),linear-gradient(to_bottom,#64748b_1px,transparent_1px)] [background-size:44px_44px]"></div>
              {event.latitude !== null && event.latitude !== undefined && (
                <div
                  className="absolute w-2.5 h-2.5 -translate-x-1/2 -translate-y-1/2"
                  style={{
                    // Project the coordinate onto the panel. This is a rendering
                    // of the stored value, not a claim about a real place.
                    left: `${50 + event.longitude * 0.9}%`,
                    top: `${50 - event.latitude * 0.9}%`,
                  }}
                  title={`Reported coordinates: ${event.latitude}, ${event.longitude}`}
                >
                  <span className="absolute inset-0 rounded-full bg-cyan-400/40 animate-ping"></span>
                  <span className="relative block w-full h-full rounded-full bg-cyan-300 border border-cyan-100"></span>
                </div>
              )}
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-background to-transparent h-20 pointer-events-none"></div>
            </div>
          </article>

          {/* Bias Detection & AI Confidence Panel */}
          <section className="glass-panel p-stack-md rounded flex flex-col md:flex-row gap-6 justify-between items-start md:items-center w-full">
            <div className="flex flex-col gap-1">
              <h3 className="font-label-caps text-label-caps text-on-surface-variant uppercase flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[16px]">analytics</span>
                Synthesis Metrics
              </h3>
              <p className="font-body-sm text-body-sm text-on-surface max-w-md">
                Aggregated from {event.transparency?.source_count ?? visibleArticles.length} independent source wire{(event.transparency?.source_count ?? visibleArticles.length) === 1 ? "" : "s"} across global coordinates.
              </p>
            </div>
            
            <div className="flex gap-8 flex-wrap">
              {/* Lean Metric */}
              <div className="flex flex-col gap-2 min-w-[120px]">
                <div className="flex justify-between font-mono-data text-mono-data">
                  <span className="text-on-surface-variant">Editorial Lean</span>
                  <span className="text-on-surface capitalize">{biasLabel.toLowerCase()}</span>
                </div>
                <div className="h-1.5 w-full bg-zinc-950 rounded-full overflow-hidden flex relative">
                  {biasPercentage !== null ? (
                    <div className="absolute top-0 bottom-0 w-3 bg-primary rounded-full transition-all duration-700 shadow-sm" style={{ left: `calc(${biasPercentage}% - 6px)` }}></div>
                  ) : null}
                </div>
              </div>

              {/* Confidence Metric */}
              <div className="flex flex-col gap-2 min-w-[120px]">
                <div className="flex justify-between font-mono-data text-mono-data">
                  <span className="text-on-surface-variant">Importance</span>
                  <span className="text-primary font-bold">{(event.importance_score).toFixed(1)}/10</span>
                </div>
                <div className="h-1.5 w-full bg-zinc-950 rounded-full overflow-hidden">
                  <div 
                    className="h-full bg-primary rounded-r-full transition-all duration-700" 
                    style={{ width: `${event.importance_score * 10}%` }}
                  ></div>
                </div>
              </div>
            </div>
          </section>

          {/* Synthesized Body */}
          <div className="border-b border-outline-variant/40 pb-6">
            <h3 className="font-label-caps text-label-caps text-zinc-500 uppercase tracking-widest text-[10px] mb-4">
              EDITORIAL SYNTHESIS REPORT
            </h3>
            <article className="max-w-3xl">
              {renderCitationsParagraphs(event.summary, visibleArticles, citationIndex)}
            </article>
          </div>

          {/* Article Transparency Panel */}
          <TransparencyPanel
            transparency={event.transparency ?? null}
            contradictions={event.contradictions ?? []}
            updatedAt={event.updated_at}
            contradictionSignal={{
              count: event.contradiction_count ?? event.contradictions?.length ?? 0,
              max_confidence: event.contradiction_max_confidence ?? null,
              level: event.contradiction_level ?? "reported",
            }}
          />

          {/* Per-claim attribution. Sits directly under the summary so a reader
              sees which outlets back which assertion without scrolling, and
              above the aggregate bias view, which summarises all sources rather
              than individual claims. */}
          <ClaimAttributionPanel claims={event.claims} />

          {/* Bias Distribution */}
          <BiasSpectrum sources={biasSources} />

          {/* Side-by-side outlet comparison. Distinct from BiasSpectrum above:
              this loads the per-event comparison endpoint and shows each
              publisher's own headline and excerpt, rather than reusing the
              already-fetched article list. */}
          <SourceComparison eventId={id} />

          {/* AI Fact Checking Section */}
          <section className="space-y-4 pt-6 border-b border-outline-variant/40 pb-6">
            <div className="flex items-center justify-between">
              <h3 className="font-label-caps text-label-caps text-zinc-400 uppercase tracking-widest text-[11px] flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[16px] text-primary">fact_check</span>
                Live Fact-Checking Integration
              </h3>
              {isCheckingFacts && (
                <span className="text-[10px] text-amber-500 font-mono-data animate-pulse flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-amber-500"></span> ANALYZING CLAIMS...
                </span>
              )}
            </div>

            {factCheckError && (
              <div className="bg-rose-950/20 border border-rose-800/40 rounded-xl px-4 py-3 text-xs text-rose-300/90">
                {factCheckError}
              </div>
            )}

            {factCheck && (
              <div className="space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Credibility Score */}
                  <div className="bg-zinc-950/40 border border-indigo-950/50 rounded-xl p-4 shadow-sm hover:border-cyber-indigo/35 transition-colors">
                    <div className="flex justify-between items-start mb-4">
                      <span className="font-bold text-zinc-500 uppercase tracking-widest text-[10px] font-mono-data">Credibility Score</span>
                      <span className="material-symbols-outlined text-cyber-cyan text-[18px]">verified_user</span>
                    </div>
                    <div className="flex items-end gap-1 mb-2">
                      <span className="text-3xl font-extrabold text-white font-mono-data">
                        {factCheck.credibility_score}
                      </span>
                      <span className="text-zinc-500 text-xs font-mono-data mb-1">/ 100</span>
                    </div>
                    <div className="w-full bg-zinc-900 h-1.5 rounded-full overflow-hidden mb-2">
                      <div
                        className={`h-full transition-all duration-1000 ${
                          factCheck.credibility_score >= 70
                            ? "bg-emerald-500"
                            : factCheck.credibility_score <= 40
                              ? "bg-rose-500"
                              : "bg-amber-500"
                        }`}
                        style={{ width: `${factCheck.credibility_score}%` }}
                      ></div>
                    </div>
                    <p className={`font-mono-data text-[9px] uppercase font-bold ${
                      factCheck.credibility_score >= 70 ? "text-emerald-500" : factCheck.credibility_score <= 40 ? "text-rose-500" : "text-amber-500"
                    }`}>
                      {factCheck.credibility_score >= 70 ? "HIGH TRUST CLASSIFICATION" : factCheck.credibility_score <= 40 ? "RISK WARNING THRESHOLD" : "MODERATE VERIFICATION LEVEL"}
                    </p>
                  </div>

                  {/* Trust Risks */}
                  <div className="bg-zinc-950/40 border border-indigo-950/50 rounded-xl p-4 shadow-sm hover:border-cyber-indigo/35 transition-colors">
                    <div className="flex justify-between items-start mb-3">
                      <span className="font-bold text-zinc-500 uppercase tracking-widest text-[10px] font-mono-data">Identified Risks</span>
                      <span className={`material-symbols-outlined text-[18px] ${factCheck.trust_risks.length > 0 ? "text-amber-400" : "text-emerald-500"}`}>warning</span>
                    </div>
                    <div>
                      {factCheck.trust_risks.length > 0 ? (
                        <ul className="space-y-2 text-xs text-zinc-400">
                          {factCheck.trust_risks.map((risk, idx) => (
                            <li key={idx} className="flex items-start gap-1.5">
                              <span className="text-rose-400 font-bold">•</span>
                              <span className="line-clamp-2">{risk}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-xs text-emerald-400 font-semibold mt-4">
                          No structural risks detected.
                        </p>
                      )}
                    </div>
                  </div>
                </div>

                {/* Extracted Claims */}
                <div className="bg-zinc-950/40 border border-indigo-950/50 rounded-xl p-4 shadow-sm">
                  <h4 className="font-bold text-white text-xs mb-4 uppercase tracking-wider font-mono-data">Claim Validation</h4>
                  <div className="space-y-3">
                    {factCheck.claims.map((claim, idx) => (
                      <div key={idx} className="bg-zinc-900/30 border border-zinc-800 rounded p-3 relative flex flex-col gap-2">
                        <p className="text-xs text-zinc-300 leading-snug">&ldquo;{claim.text}&rdquo;</p>
                        <div className="flex items-center justify-between mt-1">
                          <span className={`inline-flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full ${
                            claim.status.toLowerCase() === "corroborated"
                              ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                              : claim.status.toLowerCase() === "disputed"
                                ? "bg-rose-500/20 text-rose-400 border border-rose-500/30"
                                : "bg-amber-500/20 text-amber-400 border border-amber-500/30"
                          }`}>
                            {claim.status.toLowerCase() === "corroborated" && <span className="material-symbols-outlined text-[10px]">check_circle</span>}
                            {claim.status.toLowerCase() === "disputed" && <span className="material-symbols-outlined text-[10px]">cancel</span>}
                            {claim.status.toLowerCase() === "unverified" && <span className="material-symbols-outlined text-[10px]">help</span>}
                            {claim.status}
                          </span>

                          {/*
                            Rendered only when a real feed supplied a reviewer.
                            The link is not conditional on review_url alone:
                            reviewed_by can arrive without a URL, and an
                            <a href={undefined}> navigates to the current page,
                            turning a badge into a broken self-link. The text is
                            plain (not a link) in that case.
                          */}
                          {claim.reviewed_by && (
                            claim.review_url ? (
                              <a href={claim.review_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[9px] font-mono-data font-bold text-cyber-cyan border border-cyber-cyan/30 bg-cyber-cyan/10 px-2 py-0.5 rounded hover:bg-cyber-cyan/20 transition-colors">
                                <span className="material-symbols-outlined text-[10px]">plagiarism</span>
                                Reviewed by {claim.reviewed_by}
                              </a>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-[9px] font-mono-data font-bold text-zinc-500 border border-zinc-700/60 bg-zinc-800/40 px-2 py-0.5 rounded">
                                <span className="material-symbols-outlined text-[10px]">plagiarism</span>
                                Reviewed by {claim.reviewed_by}
                              </span>
                            )
                          )}

                          <ConfidenceMeter
                            value={claim.confidence ?? null}
                            isScored={Boolean(claim.is_scored)}
                          />
                        </div>

                        {/* External fact-checker reviews from the configured
                            adapter. Only rendered when the feed returned real
                            reviews; showing the publisher verdict text (e.g.
                            "Mostly false") verbatim is what makes the
                            attribution inspectable. */}
                        {claim.external_reviews && claim.external_reviews.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 pt-1.5 border-t border-zinc-800/60">
                            {claim.external_reviews.slice(0, 3).map((rev, ridx) => (
                              <a
                                key={ridx}
                                href={rev.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={rev.title || "External fact-check"}
                                className="inline-flex items-center gap-1 text-[9px] font-mono-data text-cyber-cyan/90 border border-cyber-cyan/20 bg-cyber-cyan/5 px-1.5 py-0.5 rounded hover:bg-cyber-cyan/15 transition-colors"
                              >
                                <span className="material-symbols-outlined text-[10px]">plagiarism</span>
                                {rev.publisher}
                                {rev.verdict ? (
                                  <span className="text-zinc-400">· {rev.verdict}</span>
                                ) : null}
                              </a>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Cross-References & Source List */}
          <section className="space-y-4">
            <h3 className="font-label-caps text-label-caps text-zinc-400 uppercase tracking-widest text-[11px] flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[16px] text-primary">fact_check</span>
              Corroborating Source Documentation ({visibleArticles.length})
            </h3>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {visibleArticles.map((art) => {
                const value = art.source.credibility_score;
                const percent = value === null || value === undefined ? null : Math.round(value * 100);
                return (
                  <div 
                    key={art.id}
                    className="bg-zinc-950/40 border border-zinc-900 rounded-xl p-4 flex flex-col justify-between hover:border-zinc-800 transition-colors overflow-hidden"
                  >
                    <div>
                      <ArticleThumb article={art} />
                      <div className="flex justify-between items-start mb-2">
                        <span className="flex items-center gap-1.5">
                          <span className="font-mono-data text-[10px] px-2 py-0.5 rounded bg-zinc-900 text-zinc-400 font-bold border border-zinc-800 uppercase">
                            {art.source.name}
                          </span>
                          {citedIndexByArticle.has(art.id) && (
                            <span
                              title="Cited in the synthesis summary; click the [N] marker in the summary to open the wire"
                              className="font-mono-data text-[10px] px-1.5 py-0.5 rounded bg-primary/20 text-primary font-bold border border-primary/40"
                            >
                              [{citedIndexByArticle.get(art.id)}]
                            </span>
                          )}
                        </span>
                        {percent === null ? (
                          <span
                            title="No credibility rating on record for this publisher"
                            className="text-[10px] font-bold text-zinc-500 flex items-center gap-1"
                          >
                            <CircleSlash className="w-3 h-3" /> Not Rated
                          </span>
                        ) : (
                          <span className="text-[10px] font-bold text-emerald-400 flex items-center gap-1">
                            <CheckCircle className="w-3 h-3" /> {percent}% Trust
                          </span>
                        )}
                      </div>
                      <h4 className="text-sm font-semibold text-white leading-snug mb-3">
                        {art.title}
                      </h4>
                    </div>
                    <div className="flex justify-between items-end mt-4 border-t border-zinc-900/60 pt-2.5">
                      <span className="text-[10px] font-bold uppercase text-zinc-500">
                        Lean:{" "}
                        {art.source.bias_lean
                          ? art.source.bias_lean.replace("_", " ")
                          : "Unclassified"}
                      </span>
                      <a
                        href={art.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:text-white text-xs flex items-center gap-1 font-label-caps text-[10px] uppercase w-max"
                      >
                        View Source Wire <ExternalLink className="w-3 h-3" />
                      </a>
                    </div>
                  </div>
                );
              })}
              {visibleArticles.length === 0 && (
                <div className="col-span-2 p-6 rounded bg-zinc-950/20 border border-dashed border-zinc-800 text-center">
                  <p className="text-zinc-500 text-sm">No external wire references associated with this event.</p>
                </div>
              )}
            </div>
          </section>

          {/* Bias Detection Visualization */}
          {visibleArticles.length > 0 && (
            <section className="space-y-4 pt-6 border-t border-outline-variant/40">
              <div className="flex items-center justify-between">
                <h3 className="font-label-caps text-label-caps text-zinc-400 uppercase tracking-widest text-[11px] flex items-center gap-1.5">
                  <span className="material-symbols-outlined text-[16px] text-primary">balance</span>
                  Bias Detection & Comparison
                </h3>
                <span className="text-[10px] text-zinc-500 font-mono-data">CROSS-SPECTRAL ANALYSIS</span>
              </div>
              <p className="text-xs text-zinc-400 mb-4">
                Analyze how different outlets are reporting on this event based on their historical political leanings.
              </p>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* LEFT */}
                <div className="bg-blue-950/10 border border-blue-900/30 rounded-xl p-4 flex flex-col gap-3">
                  <div className="border-b border-blue-900/30 pb-2">
                    <span className="text-[10px] font-bold text-blue-400 uppercase tracking-wider">Left-Leaning Sources</span>
                  </div>
                  {leftArticles.map((art) => (
                    <div key={art.id} className="bg-zinc-950/40 p-3 rounded border border-blue-900/20">
                      <span className="text-[9px] text-blue-300 font-mono-data uppercase mb-1 block">{art.source.name}</span>
                      <p className="text-xs text-zinc-300 leading-snug line-clamp-3">{art.title}</p>
                    </div>
                  ))}
                  {leftArticles.length === 0 && (
                    <span className="text-xs text-zinc-600 italic py-2">No coverage found</span>
                  )}
                </div>

                {/* CENTER */}
                <div className="bg-zinc-900/20 border border-zinc-800 rounded-xl p-4 flex flex-col gap-3">
                  <div className="border-b border-zinc-800 pb-2">
                    <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">Center / Neutral Sources</span>
                  </div>
                  {centerArticles.map((art) => (
                    <div key={art.id} className="bg-zinc-950/40 p-3 rounded border border-zinc-800">
                      <span className="text-[9px] text-zinc-400 font-mono-data uppercase mb-1 block">{art.source.name}</span>
                      <p className="text-xs text-zinc-300 leading-snug line-clamp-3">{art.title}</p>
                    </div>
                  ))}
                  {centerArticles.length === 0 && (
                    <span className="text-xs text-zinc-600 italic py-2">No coverage found</span>
                  )}
                </div>

                {/* RIGHT */}
                <div className="bg-rose-950/10 border border-rose-900/30 rounded-xl p-4 flex flex-col gap-3">
                  <div className="border-b border-rose-900/30 pb-2">
                    <span className="text-[10px] font-bold text-rose-400 uppercase tracking-wider">Right-Leaning Sources</span>
                  </div>
                  {rightArticles.map((art) => (
                    <div key={art.id} className="bg-zinc-950/40 p-3 rounded border border-rose-900/20">
                      <span className="text-[9px] text-rose-300 font-mono-data uppercase mb-1 block">{art.source.name}</span>
                      <p className="text-xs text-zinc-300 leading-snug line-clamp-3">{art.title}</p>
                    </div>
                  ))}
                  {rightArticles.length === 0 && (
                    <span className="text-xs text-zinc-600 italic py-2">No coverage found</span>
                  )}
                </div>
              </div>

              {unclassifiedArticles.length > 0 && (
                <div className="mt-4 bg-zinc-900/10 border border-dashed border-zinc-800 rounded-xl p-4 flex flex-col gap-3">
                  <div className="border-b border-zinc-800/60 pb-2">
                    <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-wider">
                      Unclassified ({unclassifiedArticles.length})
                    </span>
                  </div>
                  {unclassifiedArticles.map((art) => (
                    <div key={art.id} className="bg-zinc-950/40 p-3 rounded border border-zinc-800/60">
                      <span className="text-[9px] text-zinc-500 font-mono-data uppercase mb-1 block">{art.source.name}</span>
                      <p className="text-xs text-zinc-400 leading-snug line-clamp-3">{art.title}</p>
                    </div>
                  ))}
                  <p className="text-[10px] text-zinc-600 leading-relaxed">
                    These publishers have no bias classification on record. They
                    are listed separately rather than counted as centre-aligned,
                    because a missing rating is not evidence of a neutral stance.
                  </p>
                </div>
              )}
            </section>
          )}

        </main>

        {/* Source Intel Sidebar (Right Side, 3 Cols) */}
        <aside className="hidden lg:flex flex-col col-span-3 bg-zinc-950/40 border border-outline-variant/60 shadow-sm sticky top-20 h-[calc(100vh-8rem)] overflow-hidden rounded-lg backdrop-blur-md p-4">
          {/* Header */}
          <div className="border-b border-outline-variant/40 pb-4 mb-4 flex-shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded bg-primary/10 text-primary flex items-center justify-center">
                <Newspaper className="w-4 h-4" />
              </div>
              <div>
                <h2 className="font-body-md text-body-md font-bold text-primary leading-tight">Source Intel</h2>
                <span className="font-label-caps text-label-caps text-on-surface-variant text-[10px]">
                  {visibleArticles.length} Wire{visibleArticles.length !== 1 ? 's' : ''} Corroborated
                </span>
              </div>
            </div>
          </div>

          {/* Event metadata quick-stats */}
          <div className="flex-shrink-0 grid grid-cols-2 gap-2 mb-4">
            <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
              <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">Location</span>
              <span className="text-xs text-zinc-200 font-semibold truncate">{event.country || '—'}</span>
            </div>
            <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
              <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">Importance</span>
              <span className="text-xs text-primary font-bold">{event.importance_score.toFixed(1)} / 10</span>
            </div>
            <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
              <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">Trust Avg</span>
              <span
                className={`text-xs font-bold ${avgTrustScore === null ? 'text-zinc-500' : 'text-emerald-400'}`}
              >
                {avgTrustScore === null ? "Not rated" : `${avgTrustScore}%`}
              </span>
            </div>
            <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
              <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">Lean</span>
              <span className="text-xs text-zinc-200 font-semibold capitalize">{biasLabel.toLowerCase()}</span>
            </div>
          </div>

          {/* Article List */}
          <div className="flex-1 overflow-y-auto pr-1 no-scrollbar space-y-3">
            {visibleArticles.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-center gap-2">
                <Newspaper className="w-6 h-6 text-zinc-700" />
                <p className="text-xs text-zinc-500">No source wires linked to this event.</p>
              </div>
            ) : (
              visibleArticles.map((art, idx) => {
                const value = art.source.credibility_score;
                const percent = value === null || value === undefined ? null : Math.round(value * 100);
                const trustColor =
                  percent === null
                    ? 'text-zinc-500'
                    : percent >= 80
                      ? 'text-emerald-400'
                      : percent >= 60
                        ? 'text-amber-400'
                        : 'text-rose-400';
                return (
                  <a
                    key={art.id}
                    href={art.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group block bg-zinc-900/50 border border-zinc-800/50 hover:border-primary/40 hover:bg-zinc-900/80 rounded-lg p-3 transition-all duration-200"
                  >
                    {/* Source + trust badge row */}
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono-data font-bold uppercase tracking-wide truncate max-w-[55%]">
                        {art.source.name}
                      </span>
                      <span className={`text-[9px] font-bold flex items-center gap-1 ${trustColor}`}>
                        {percent === null ? (
                          <>
                            <CircleSlash className="w-2.5 h-2.5" />
                            n/r
                          </>
                        ) : (
                          <>
                            <CheckCircle className="w-2.5 h-2.5" />
                            {percent}%
                          </>
                        )}
                      </span>
                    </div>
                    {/* Title */}
                    <p className="text-[11px] font-semibold text-zinc-200 group-hover:text-white leading-snug line-clamp-3 mb-2 transition-colors">
                      {art.title}
                    </p>
                    {/* Date + link indicator */}
                    <div className="flex items-center justify-between">
                      <span className="text-[9px] text-zinc-600 font-mono-data">
                        {art.published_at ? new Date(art.published_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' }) : 'N/A'}
                      </span>
                      <span className="text-[9px] text-primary flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        View Wire <ExternalLink className="w-2.5 h-2.5" />
                      </span>
                    </div>
                  </a>
                );
              })
            )}
          </div>
        </aside>

      </div>

      <CommentSection eventId={id} />

      {/* Footer */}
      <footer className="bg-surface-container-low border-t border-outline-variant/50 w-full py-6 px-margin-desktop mt-auto flex flex-col md:flex-row justify-between items-center z-10 relative">
        <div className="mb-4 md:mb-0">
          <span className="font-headline-lg text-headline-lg font-bold text-primary block mb-1">GlobeLens AI</span>
          <p className="font-body-sm text-body-sm text-on-surface-variant">© 2026 GlobeLens AI. Authoritative Editorial Intelligence.</p>
        </div>
        <nav className="flex flex-wrap gap-4 md:gap-6 items-center justify-center text-xs">
          <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Institutional Briefs</a>
          <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">Privacy Guidelines</a>
          <a className="text-on-surface-variant hover:text-primary transition-colors" href="#">System Ethics Policy</a>
          <a className="text-on-surface-variant hover:text-primary transition-colors" href="/settings">Settings</a>
        </nav>
      </footer>
    </div>
  );
}

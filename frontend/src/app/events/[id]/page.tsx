"use client";

import React, { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
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
  ExternalLink,
  Newspaper,
  Share2,
  Send,
  ListPlus,
  Download
} from "lucide-react";
import PrimaryNav from "../../components/PrimaryNav";
import ListenButton from "../../components/ListenButton";
import {
  getSavedEvent,
  isEventSaved,
  removeEventForOffline,
  saveEventForOffline,
} from "../../../lib/offline";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Cached per token so a user flipping bookmarks across several dossiers does
// not re-fetch the profile each time.
let cachedUserId: { token: string; id: string } | null = null;

async function resolveUserId(token: string): Promise<string> {
  if (cachedUserId && cachedUserId.token === token) return cachedUserId.id;
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Profile lookup failed (${res.status})`);
  const profile = await res.json();
  cachedUserId = { token, id: profile.id };
  return profile.id as string;
}

interface Source {
  name: string;
  credibility_score: number;
  bias_lean: string;
}

interface Article {
  id: string;
  // 1-based position in the list the LLM was shown, which only includes
  // articles that had a body. Null means the article cannot be cited.
  citation_index: number | null;
  title: string;
  content: string;
  url: string;
  published_at?: string;
  source: Source;
}

interface Contradiction {
  // Candidate disagreement between sources, not a verified ruling. The
  // detector surfaces pairs a reader should weigh; it does not decide
  // which outlet is right, and the UI must not imply that it does.
  nature: string;
  detail: string;
  claim_a: { text: string; source: string };
  claim_b: { text: string; source: string };
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
  bias_lean: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  articles: Article[];
  contradictions: Contradiction[];
}

interface RelatedEvent {
  id: string;
  title: string;
  topic: string;
  created_at: string;
}

interface FactCheckClaim {
  text: string;
  status: string;
}

interface FactCheckResult {
  credibility_score: number;
  trust_risks: string[];
  claims: FactCheckClaim[];
  summary: string;
}

interface CommentItem {
  id: string;
  content: string;
  created_at: string | null;
  event_id: string;
  user_id: string;
  author: string;
}

export default function EventDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [relatedEvents, setRelatedEvents] = useState<RelatedEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isBookmarked, setIsBookmarked] = useState(false);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<string | null>(null);
  const [bookmarkPending, setBookmarkPending] = useState(false);

  // Device-local offline snapshot. Deliberately independent of the account
  // bookmark: it is about making the dossier readable on this machine without a
  // connection, so it works for signed-out readers too.
  const [savedOffline, setSavedOffline] = useState(false);
  const [offlinePending, setOfflinePending] = useState(false);
  const [offlineSnapshotAt, setOfflineSnapshotAt] = useState<number | null>(null);

  // "Save to List" picker. Kept separate from the quick bookmark toggle: the
  // bookmark is a one-click flat save, a reading list is a deliberate,
  // named placement.
  const [showListModal, setShowListModal] = useState(false);
  const [readingLists, setReadingLists] = useState<
    { id: string; name: string; item_count: number }[]
  >([]);
  const [listModalLoading, setListModalLoading] = useState(false);
  const [listModalError, setListModalError] = useState<string | null>(null);
  const [newListName, setNewListName] = useState("");
  const [listModalBusy, setListModalBusy] = useState(false);
  const [savedToListId, setSavedToListId] = useState<string | null>(null);
  
  const [factCheck, setFactCheck] = useState<FactCheckResult | null>(null);
  const [factCheckLoading, setFactCheckLoading] = useState(false);

  // Comments are public to read; only posting/editing/deleting needs a token.
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [newComment, setNewComment] = useState("");
  const [posting, setPosting] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [commentBusyId, setCommentBusyId] = useState<string | null>(null);
  

  
  const fetchEventDetails = async () => {
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
      
      // Fetch related
      try {
        const relatedRes = await fetch(`${API_BASE_URL}/api/v1/events/${id}/related`);
        if (relatedRes.ok) {
          const relatedData = await relatedRes.json();
          setRelatedEvents(relatedData.events || []);
        }
      } catch (e) {
        console.error("Failed to fetch related events", e);
      }
    } catch (err: any) {
      console.error(err);
      // Network failed. If the reader saved this dossier, show the frozen copy
      // clearly labelled as a snapshot rather than a dead-end error page.
      const saved = await getSavedEvent(id);
      if (saved) {
        setEvent(saved.event);
        setOfflineSnapshotAt(saved.savedAt);
      } else {
        setError(err.message || "Failed to connect to synthesis server");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (id) {
      fetchEventDetails();
    }
  }, [id]);

  // Restore the saved state from the server. localStorage is read in an effect
  // rather than during render because this is a client component and reading
  // storage during render breaks server rendering.
  useEffect(() => {
    let cancelled = false;

    const loadBookmarkState = async () => {
      const token = localStorage.getItem("admin_token");
      if (!token) return;
      if (!cancelled) setAuthToken(token);
      fetch(`${API_BASE_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((me) => {
          if (!cancelled && me) setUserRole(me.role);
        })
        .catch(() => {});

      try {
        const userId = await resolveUserId(token);
        if (!cancelled) setCurrentUserId(userId);
        const res = await fetch(`${API_BASE_URL}/api/v1/users/${userId}/bookmarks`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        const ids = new Set<string>(
          (data.bookmarks || []).map((b: { event_id: string }) => b.event_id)
        );
        setIsBookmarked(ids.has(id));
      } catch (err) {
        // A failed lookup must not block the dossier itself.
        console.error("Could not load bookmark state", err);
      }
    };

    loadBookmarkState();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Reflect whether a device-local snapshot already exists for this dossier.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    isEventSaved(id).then((yes) => {
      if (!cancelled) setSavedOffline(yes);
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleOfflineToggle = async () => {
    if (offlinePending) return;
    setOfflinePending(true);
    try {
      if (savedOffline) {
        await removeEventForOffline(id);
        setSavedOffline(false);
        setOfflineSnapshotAt(null);
      } else if (event) {
        const saved = await saveEventForOffline(event);
        setSavedOffline(true);
        setOfflineSnapshotAt(saved.savedAt);
      }
    } catch (err) {
      console.error("Offline save failed", err);
      alert("Could not save this dossier for offline reading.");
    } finally {
      setOfflinePending(false);
    }
  };

  const handleBookmarkToggle = async () => {
    // Honest failure first: the event page is public, so most visitors have no
    // token. Previously this toggled local state only, so the button claimed
    // "Saved to Dossiers" while nothing was persisted anywhere.
    if (!authToken) {
      alert("Sign in to save dossiers to your account.");
      router.push("/login");
      return;
    }
    if (bookmarkPending) return;

    setBookmarkPending(true);
    const previous = isBookmarked;
    // Optimistic, but reverted below if the request fails.
    setIsBookmarked(!previous);

    try {
      const userId = await resolveUserId(authToken);
      const res = await fetch(
        previous
          ? `${API_BASE_URL}/api/v1/users/${userId}/bookmarks/${id}`
          : `${API_BASE_URL}/api/v1/users/${userId}/bookmarks`,
        {
          method: previous ? "DELETE" : "POST",
          headers: {
            Authorization: `Bearer ${authToken}`,
            "Content-Type": "application/json",
          },
          body: previous ? undefined : JSON.stringify({ event_id: id }),
        }
      );

      if (res.status === 401) {
        // Token expired or was revoked mid-session.
        localStorage.removeItem("admin_token");
        setAuthToken(null);
        setIsBookmarked(false);
        alert("Your session expired. Sign in again to save dossiers.");
        router.push("/login");
        return;
      }

      if (!res.ok) {
        throw new Error(`Bookmark request failed (${res.status})`);
      }
    } catch (err) {
      // Never leave the UI claiming a save that did not happen.
      setIsBookmarked(previous);
      console.error("Bookmark toggle failed", err);
      alert("Could not update bookmarks. Please try again.");
    } finally {
      setBookmarkPending(false);
    }
  };

  const loadReadingLists = async (token: string, userId: string) => {
    const res = await fetch(`${API_BASE_URL}/api/v1/users/${userId}/reading-lists`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error(`Could not load your lists (${res.status})`);
    const data = await res.json();
    setReadingLists(
      (data.reading_lists || []).map((l: { id: string; name: string; item_count: number }) => ({
        id: l.id,
        name: l.name,
        item_count: l.item_count,
      }))
    );
  };

  const openListModal = async () => {
    if (!authToken) {
      alert("Sign in to save dossiers to a reading list.");
      router.push("/login");
      return;
    }
    setShowListModal(true);
    setListModalError(null);
    setSavedToListId(null);
    setListModalLoading(true);
    try {
      const userId = await resolveUserId(authToken);
      await loadReadingLists(authToken, userId);
    } catch (err: any) {
      setListModalError(err.message || "Could not load your reading lists");
    } finally {
      setListModalLoading(false);
    }
  };

  const addToList = async (listId: string) => {
    if (!authToken || listModalBusy) return;
    setListModalBusy(true);
    setListModalError(null);
    try {
      const userId = await resolveUserId(authToken);
      const res = await fetch(
        `${API_BASE_URL}/api/v1/users/${userId}/reading-lists/${listId}/items`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${authToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ event_id: id }),
        }
      );
      if (!res.ok) throw new Error(`Could not save to list (${res.status})`);
      setSavedToListId(listId);
      setReadingLists((prev) =>
        prev.map((l) => (l.id === listId ? { ...l, item_count: l.item_count + 1 } : l))
      );
    } catch (err: any) {
      setListModalError(err.message || "Could not save to list");
    } finally {
      setListModalBusy(false);
    }
  };

  const createListThenAdd = async () => {
    if (!authToken || listModalBusy) return;
    const name = newListName.trim();
    if (!name) {
      setListModalError("Give the list a name first");
      return;
    }
    setListModalBusy(true);
    setListModalError(null);
    try {
      const userId = await resolveUserId(authToken);
      const createRes = await fetch(`${API_BASE_URL}/api/v1/users/${userId}/reading-lists`, {
        method: "POST",
        headers: { Authorization: `Bearer ${authToken}`, "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!createRes.ok) {
        const data = await createRes.json().catch(() => ({}));
        throw new Error(data.detail || `Could not create list (${createRes.status})`);
      }
      const created = await createRes.json();
      setNewListName("");
      setReadingLists((prev) => [{ id: created.id, name: created.name, item_count: 0 }, ...prev]);
      await addToList(created.id);
    } catch (err: any) {
      setListModalError(err.message || "Could not create list");
      setListModalBusy(false);
    }
  };

  const handleRunFactCheck = async () => {
    if (factCheck || factCheckLoading) return;
    setFactCheckLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/events/${id}/fact-check`);
      if (res.ok) {
        const data = await res.json();
        setFactCheck(data);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setFactCheckLoading(false);
    }
  };

  // Comments are readable without a session, so this runs for everyone.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    const loadComments = async () => {
      setCommentsLoading(true);
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/events/${id}/comments`);
        if (res.ok && !cancelled) {
          const data = await res.json();
          setComments(data.comments || []);
        }
      } catch (err) {
        console.error("Could not load comments", err);
      } finally {
        if (!cancelled) setCommentsLoading(false);
      }
    };

    loadComments();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handlePostComment = async () => {
    if (!authToken) {
      router.push("/login");
      return;
    }
    const content = newComment.trim();
    if (!content || posting) return;

    setPosting(true);
    setCommentError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/events/${id}/comments`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${authToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content }),
      });

      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        setAuthToken(null);
        router.push("/login");
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Could not post comment");
      }

      const created = await res.json();
      setComments((prev) => [...prev, created]);
      setNewComment("");
    } catch (err: any) {
      setCommentError(err.message || "Could not post comment");
    } finally {
      setPosting(false);
    }
  };

  const handleUpdateComment = async (commentId: string) => {
    if (!authToken) return;
    const content = editText.trim();
    if (!content) {
      setCommentError("Comment cannot be empty");
      return;
    }

    setCommentBusyId(commentId);
    setCommentError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/comments/${commentId}`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${authToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content }),
      });

      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        setAuthToken(null);
        router.push("/login");
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Could not update comment");
      }

      const updated = await res.json();
      setComments((prev) =>
        prev.map((c) => (c.id === commentId ? { ...c, content: updated.content } : c))
      );
      setEditingId(null);
      setEditText("");
    } catch (err: any) {
      setCommentError(err.message || "Could not update comment");
    } finally {
      setCommentBusyId(null);
    }
  };

  const handleDeleteComment = async (commentId: string) => {
    if (!authToken) return;
    if (!window.confirm("Delete this comment?")) return;

    setCommentBusyId(commentId);
    setCommentError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/comments/${commentId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${authToken}` },
      });

      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        setAuthToken(null);
        router.push("/login");
        return;
      }
      // A 404 means it is already gone, which is the desired end state.
      if (!res.ok && res.status !== 404) {
        throw new Error("Could not delete comment");
      }
      setComments((prev) => prev.filter((c) => c.id !== commentId));
    } catch (err: any) {
      setCommentError(err.message || "Could not delete comment");
    } finally {
      setCommentBusyId(null);
    }
  };

  const handlePrint = () => {
    window.print();
  };

  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: event?.title || 'GlobeLens AI Dossier',
          text: 'Read this intelligence dossier on GlobeLens AI',
          url: window.location.href,
        });
      } catch (err) {
        console.error('Error sharing', err);
      }
    } else {
      navigator.clipboard.writeText(window.location.href);
      alert('Link copied to clipboard!');
    }
  };

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

  const renderCitationsParagraphs = (summaryText: string, articlesList: Article[]) => {
    if (!summaryText) return <p className="text-zinc-500 italic">No summary details compiled yet.</p>;

    const paragraphs = summaryText.split("\n\n").filter(p => p.trim().length > 0);

    // Resolve [N] through the server's citation_index rather than N-1
    // positionally. Enrichment only numbered articles that had a body, so
    // array position and citation number drift apart on any event containing a
    // content-less article, which silently attributed claims to the wrong
    // source.
    const byCitationIndex = new Map<number, Article>();
    for (const art of articlesList) {
      if (art.citation_index != null) {
        byCitationIndex.set(art.citation_index, art);
      }
    }
    const danglingCitations: number[] = [];

    const rendered = paragraphs.map((para, pIdx) => {
      // Parse [1], [2], etc. to inline tooltip citations
      const parts = para.split(/(\[\d+\])/g);

      return (
        <p key={pIdx} className="mb-6 text-on-surface leading-[1.8] text-body-lg font-body-lg">
          {parts.map((part, sIdx) => {
            const match = part.match(/\[(\d+)\]/);
            if (match) {
              const associatedArticle = byCitationIndex.get(parseInt(match[1]));

              if (associatedArticle) {
                const trustPercent = Math.round((associatedArticle.source.credibility_score || 0.85) * 100);
                
                return (
                  <a 
                    key={sIdx}
                    href={associatedArticle.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="has-tooltip inline-flex items-center justify-center bg-zinc-800 hover:bg-primary text-[10px] text-zinc-300 hover:text-white rounded px-1 ml-0.5 align-super transition-colors duration-150 cursor-pointer"
                  >
                    {match[1]}
                    <span className="ai-tooltip glass-panel p-3 rounded text-left shadow-[0_8px_24px_rgba(0,0,0,0.6)] min-w-[250px] z-50">
                      <strong className="block text-primary font-mono-data mb-1.5 flex items-center gap-1.5 text-xs">
                        <span className="material-symbols-outlined text-[14px]">source</span> 
                        {associatedArticle.source.name} ({trustPercent}% trust)
                      </strong>
                      <span className="block text-xs font-semibold text-white mb-1 leading-tight">
                        {associatedArticle.title}
                      </span>
                      <span className="block text-[10px] text-zinc-400 mt-2 flex items-center gap-1">
                        Click to view original source <ExternalLink className="w-3 h-3" />
                      </span>
                    </span>
                  </a>
                );
              }

              // The model cited a source number this event does not have. Show
              // the marker plainly instead of dropping it, so a broken
              // citation is visible rather than silently removed.
              danglingCitations.push(parseInt(match[1]));
              return (
                <span
                  key={sIdx}
                  title={`Citation [${match[1]}] does not match any source on this event`}
                  className="inline-flex items-center justify-center bg-amber-500/10 text-amber-400 text-[10px] rounded px-1 ml-0.5 align-super border border-amber-500/30"
                >
                  {match[1]}?
                </span>
              );
            }
            return <span key={sIdx}>{part}</span>;
          })}
        </p>
      );
    });

    if (danglingCitations.length > 0 && process.env.NODE_ENV === "development") {
      console.warn(
        `Event summary cites sources that do not exist: ${Array.from(new Set(danglingCitations)).join(", ")}`
      );
    }

    return rendered;
  };

  // Admin console is only relevant to administrators
  const showAdmin = userRole === "ADMIN";

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
          <PrimaryNav activeHref="" role={userRole} />
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

  // Calculate high-level credibility score average from sources
  const sourceCerts = event.articles.length;
  const avgTrustScore = event.articles.length > 0 
    ? Math.round((event.articles.reduce((acc, art) => acc + (art.source.credibility_score || 0.85), 0) / event.articles.length) * 100)
    : 88;

  // Bias lean label & value
  const biasLabel = event.bias_lean.replace("_", "-");
  let biasPercentage = 50;
  if (event.bias_lean === "LEFT") biasPercentage = 15;
  else if (event.bias_lean === "CENTER_LEFT") biasPercentage = 35;
  else if (event.bias_lean === "CENTER_RIGHT") biasPercentage = 65;
  else if (event.bias_lean === "RIGHT") biasPercentage = 85;

  return (
    <div className="bg-background text-on-background min-h-screen flex flex-col font-body-md relative overflow-x-hidden">
      {/* TopNavBar — shared PrimaryNav */}
      <header className="flex justify-between items-center px-margin-desktop w-full h-16 sticky top-0 z-50 bg-[#080c16]/80 backdrop-blur-lg border-b border-indigo-950/40 flex-shrink-0">
        <PrimaryNav activeHref="" role={userRole} />
      </header>

      {/* Main Container */}
      <div className="flex-1 max-w-[1440px] mx-auto w-full grid grid-cols-1 lg:grid-cols-12 gap-gutter px-margin-mobile lg:px-margin-desktop py-stack-lg relative">
        
        {/* Main Content Canvas (9 Cols) */}
        <main className="col-span-1 lg:col-span-9 flex flex-col gap-stack-lg pb-stack-lg">
          
          {offlineSnapshotAt && (
            <div className="flex items-center gap-2 rounded border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] font-mono-data text-amber-400 print:hidden">
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
              <span>
                Offline copy — saved {new Date(offlineSnapshotAt).toLocaleString("en-GB", {
                  day: "2-digit", month: "short", year: "numeric",
                  hour: "2-digit", minute: "2-digit",
                })}. Live updates, comments and source links are unavailable.
              </span>
            </div>
          )}

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
                  title="AI Credibility Analysis"
                >
                  <Shield className="w-3.5 h-3.5 text-primary" />
                  <span className="font-mono-data text-mono-data text-primary">{avgTrustScore}% Reliable</span>
                </div>
              </div>

              <div className="flex items-center gap-2 print:hidden">
                {/* Share Toggle */}
                <button 
                  onClick={handleShare}
                  className="flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant text-on-surface hover:border-primary rounded font-body-sm text-body-sm transition-colors"
                  title="Share Dossier"
                >
                  <Share2 className="w-[18px] h-[18px]" />
                  <span>Share</span>
                </button>

                {/* Print/Export Toggle */}
                <button 
                  onClick={handlePrint}
                  className="flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant text-on-surface hover:border-primary rounded font-body-sm text-body-sm transition-colors"
                  title="Export to PDF"
                >
                  <FileText className="w-[18px] h-[18px]" />
                  <span>Export</span>
                </button>

                {/* Bookmark Toggle */}
                <button
                  onClick={handleBookmarkToggle}
                  disabled={bookmarkPending}
                  title={
                    authToken
                      ? isBookmarked
                        ? "Remove this dossier from your saved list"
                        : "Save this dossier to your list"
                      : "Sign in to save dossiers"
                  }
                  className={`flex items-center gap-2 px-3 py-1.5 bg-surface-container border rounded font-body-sm text-body-sm transition-colors disabled:opacity-60 disabled:cursor-wait ${
                    isBookmarked
                      ? "border-primary text-primary"
                      : "border-outline-variant text-on-surface hover:border-primary"
                  }`}
                >
                  <Bookmark className={`w-[18px] h-[18px] ${isBookmarked ? "fill-primary" : ""}`} />
                  <span>
                    {bookmarkPending
                      ? "Saving..."
                      : isBookmarked
                      ? "Saved to Dossiers"
                      : "Bookmark Intel"}
                  </span>
                </button>

                {/* Listen — narration of the editorial synthesis below */}
                <ListenButton
                  resource="events"
                  resourceId={event.id}
                  label="Listen"
                  variant="pill"
                />

                {/* Save to Reading List */}
                <button
                  onClick={openListModal}
                  title={authToken ? "Add this dossier to a named reading list" : "Sign in to use reading lists"}
                  className="flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant text-on-surface hover:border-primary rounded font-body-sm text-body-sm transition-colors"
                >
                  <ListPlus className="w-[18px] h-[18px]" />
                  <span>Save to List</span>
                </button>

                {/* Save offline — device-local snapshot, no account required */}
                <button
                  onClick={handleOfflineToggle}
                  disabled={offlinePending}
                  title={
                    savedOffline
                      ? "Remove the offline copy from this device"
                      : "Save this dossier to read offline on this device"
                  }
                  className={`flex items-center gap-2 px-3 py-1.5 bg-surface-container border rounded font-body-sm text-body-sm transition-colors disabled:opacity-60 disabled:cursor-wait ${
                    savedOffline
                      ? "border-primary text-primary"
                      : "border-outline-variant text-on-surface hover:border-primary"
                  }`}
                >
                  <Download className="w-[18px] h-[18px]" />
                  <span>
                    {offlinePending ? "Saving..." : savedOffline ? "Saved Offline" : "Save Offline"}
                  </span>
                </button>
              </div>
            </div>

            {/* Headline & Summary */}
            <h1 className="font-display-lg text-[28px] md:text-display-lg text-on-surface leading-tight font-bold">
              {event.title}
            </h1>
            
            <p className="font-body-lg text-body-lg text-secondary leading-relaxed border-l-4 border-surface-variant pl-4">
              Detailed analytical synthesis tag-linked to {event.country || "Global Region"} operational updates.
            </p>

            {/* Hero Image */}
            <div className="w-full h-[250px] md:h-[380px] rounded border border-outline-variant bg-surface-container overflow-hidden mt-2 relative">
              <img 
                alt="Analytical map or visualization" 
                className="w-full h-full object-cover opacity-80 mix-blend-luminosity hover:mix-blend-normal transition-all duration-500"
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuDSP9yo5kG__HrAfn-e8URLtZr9SXssOai6gtJj8UcPd6bcFLngSfhOehWcUFJsd562571h35zQDEGg9iBrz_Os1umIt4AtXTKmCceDgigeBNEb_qxdnSqiTbIbaC38gsHa7aBnqFrHUvif3x2swtmTok1k2gUhlBSh_ZiejcOANdOmaDw2DP8Yxslct6LhTJgAGXrUfqgqEEL3Vze0sHsK2lQTsp_OVa8flFH6e1e9K05HwOHhHse3z1ZPqm2qDhmXSOOWx9hworKq"
              />
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-background to-transparent h-24 pointer-events-none"></div>
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
                Aggregated from {sourceCerts} independent source wire{sourceCerts === 1 ? "" : "s"} across global coordinates. High confidence observed on core timeline.
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
                  <div className="absolute top-0 bottom-0 w-3 bg-primary rounded-full transition-all duration-700 shadow-sm" style={{ left: `calc(${biasPercentage}% - 6px)` }}></div>
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
            <div className="flex items-start justify-between gap-4 mb-4">
              <h3 className="font-label-caps text-label-caps text-zinc-500 uppercase tracking-widest text-[10px]">
                EDITORIAL SYNTHESIS REPORT
              </h3>
              <ListenButton
                resource="events"
                resourceId={event.id}
                label="Listen to this event"
                className="-mt-1 shrink-0"
              />
            </div>
            <article className="max-w-3xl">
              {renderCitationsParagraphs(event.summary, event.articles)}
            </article>
          </div>

          {/* Cross-Source Contradictions */}
          {event.contradictions && event.contradictions.length > 0 && (
            <div className="border-b border-outline-variant/40 pb-6">
              <h3 className="font-label-caps text-label-caps text-zinc-500 uppercase tracking-widest text-[10px] mb-4 flex items-center gap-2">
                <span className="material-symbols-outlined text-[14px] text-amber-500">compare_arrows</span>
                Sources Disagree
              </h3>
              <p className="text-xs text-zinc-500 mb-4 max-w-3xl">
                Automated comparison found claims that differ between outlets.
                These are flagged for review, not resolved: read both passages and
                judge which reporting holds up.
              </p>
              <div className="space-y-4 max-w-3xl">
                {event.contradictions.map((c, i) => (
                  <div
                    key={`${c.nature}-${i}`}
                    className="border-l-2 border-amber-500/60 bg-amber-500/5 pl-4 py-3"
                  >
                    <p className="text-[11px] font-mono-data text-amber-400/90 mb-3">
                      {c.detail}
                    </p>
                    <div className="space-y-2">
                      <blockquote className="text-sm text-zinc-300 border-l border-zinc-600 pl-3">
                        <span className="text-[10px] uppercase tracking-wider text-zinc-500 block mb-1">
                          {c.claim_a.source}
                        </span>
                        {c.claim_a.text}
                      </blockquote>
                      <blockquote className="text-sm text-zinc-300 border-l border-zinc-600 pl-3">
                        <span className="text-[10px] uppercase tracking-wider text-zinc-500 block mb-1">
                          {c.claim_b.source}
                        </span>
                        {c.claim_b.text}
                      </blockquote>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Related Events Timeline */}
          {relatedEvents.length > 0 && (
            <div className="pt-2 pb-6 border-b border-outline-variant/40 print:hidden">
              <h3 className="text-[11px] text-zinc-400 font-bold uppercase tracking-widest mb-6 flex items-center gap-2 font-label-caps">
                <span className="material-symbols-outlined text-[16px] text-primary">timeline</span>
                Related Intelligence
              </h3>
              <div className="space-y-4">
                {relatedEvents.map((re) => (
                  <div key={re.id} className="relative pl-6 border-l border-primary/50 py-1">
                    <div className="absolute left-[-5px] top-3 w-2 h-2 rounded-full bg-primary shadow-[0_0_8px_rgba(6,182,212,0.6)]"></div>
                    <span className="text-[10px] text-zinc-500 font-mono-data mb-1 block">
                      {re.created_at ? new Date(re.created_at).toLocaleDateString() : 'N/A'}
                    </span>
                    <a href={`/events/${re.id}`} className="text-sm font-semibold text-zinc-200 hover:text-white hover:underline transition-colors leading-snug">
                      {re.title}
                    </a>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Cross-References & Source List */}
          <section className="space-y-4">
            <h3 className="font-label-caps text-label-caps text-zinc-400 uppercase tracking-widest text-[11px] flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[16px] text-primary">fact_check</span>
              Corroborating Source Documentation ({event.articles.length})
            </h3>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {event.articles.map((art) => {
                const percent = Math.round((art.source.credibility_score || 0.85) * 100);
                return (
                  <div 
                    key={art.id}
                    className="bg-zinc-950/40 border border-zinc-900 rounded-xl p-4 flex flex-col justify-between hover:border-zinc-800 transition-colors"
                  >
                    <div>
                      <div className="flex justify-between items-start mb-2">
                        <span className="font-mono-data text-[10px] px-2 py-0.5 rounded bg-zinc-900 text-zinc-400 font-bold border border-zinc-800 uppercase">
                          {art.source.name}
                        </span>
                        <div className="flex flex-col items-end gap-1">
                          <span className="text-[10px] font-bold text-emerald-400 flex items-center gap-1">
                            <CheckCircle className="w-3 h-3" /> {percent}% Trust
                          </span>
                          <span className={`text-[8px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border ${
                            art.source.bias_lean === 'LEFT' ? 'text-blue-400 bg-blue-500/10 border-blue-500/30' :
                            art.source.bias_lean === 'CENTER_LEFT' ? 'text-blue-200 bg-blue-300/10 border-blue-300/30' :
                            art.source.bias_lean === 'CENTER_RIGHT' ? 'text-red-200 bg-red-300/10 border-red-300/30' :
                            art.source.bias_lean === 'RIGHT' ? 'text-red-400 bg-red-500/10 border-red-500/30' :
                            'text-zinc-400 bg-zinc-500/10 border-zinc-500/30'
                          }`}>
                            {art.source.bias_lean.replace('_', ' ')}
                          </span>
                        </div>
                      </div>
                      <h4 className="text-sm font-semibold text-white leading-snug mb-3">
                        {art.title}
                      </h4>
                    </div>
                    <div className="flex items-center gap-4 mt-4 border-t border-zinc-900/60 pt-2.5">
                      <a 
                        href={art.url} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        className="text-primary hover:text-white text-xs flex items-center gap-1 font-label-caps text-[10px] uppercase w-max"
                      >
                        View Source Wire <ExternalLink className="w-3 h-3" />
                      </a>
                      <ListenButton resource="articles" resourceId={art.id} label="Listen" />
                    </div>
                  </div>
                );
              })}
              {event.articles.length === 0 && (
                <div className="col-span-2 p-6 rounded bg-zinc-950/20 border border-dashed border-zinc-800 text-center">
                  <p className="text-zinc-500 text-sm">No external wire references associated with this event.</p>
                </div>
              )}
            </div>
          </section>

          {/* Discussion — anyone can read, a session is required to post */}
          <section className="space-y-4">
            <h3 className="font-label-caps text-label-caps text-zinc-400 uppercase tracking-widest text-[11px] flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[16px] text-primary">forum</span>
              Discussion ({comments.length})
            </h3>

            {commentsLoading && (
              <div className="flex items-center gap-2 text-primary text-xs font-mono-data animate-pulse py-2">
                <RefreshCw className="w-4 h-4 animate-spin" /> Loading discussion...
              </div>
            )}

            {!commentsLoading && comments.length === 0 && (
              <p className="text-sm text-zinc-500">No comments yet. Start the discussion.</p>
            )}

            {comments.length > 0 && (
              <div className="space-y-3">
                {comments.map((c) => {
                  const mine = !!currentUserId && c.user_id === currentUserId;
                  const editing = editingId === c.id;
                  return (
                    <div key={c.id} className="bg-zinc-950/40 border border-zinc-900 rounded-xl p-4">
                      <div className="flex items-center justify-between gap-3 mb-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <div className="w-6 h-6 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center font-mono-data text-[9px] text-primary font-bold flex-shrink-0">
                            {(c.author || "?").slice(0, 2).toUpperCase()}
                          </div>
                          <span className="text-xs font-semibold text-primary truncate">{c.author}</span>
                          {mine && (
                            <span className="text-[9px] uppercase tracking-wider text-zinc-500 font-mono-data">
                              You
                            </span>
                          )}
                        </div>
                        <span className="text-[10px] text-zinc-500 font-mono-data flex-shrink-0">
                          {c.created_at
                            ? new Date(c.created_at).toLocaleString("en-GB", {
                                day: "2-digit",
                                month: "short",
                                year: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              })
                            : ""}
                        </span>
                      </div>

                      {editing ? (
                        <div className="space-y-2">
                          <textarea
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            rows={3}
                            className="w-full bg-zinc-950/60 border border-outline-variant rounded-lg p-3 text-sm text-zinc-200 focus:outline-none focus:border-primary transition-colors resize-y"
                          />
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleUpdateComment(c.id)}
                              disabled={commentBusyId === c.id}
                              className="px-3 py-1.5 bg-primary/10 hover:bg-primary/20 border border-primary/30 text-primary rounded text-[10px] font-bold uppercase tracking-wider font-mono-data disabled:opacity-60"
                            >
                              {commentBusyId === c.id ? "Saving..." : "Save"}
                            </button>
                            <button
                              onClick={() => {
                                setEditingId(null);
                                setEditText("");
                              }}
                              className="px-3 py-1.5 bg-zinc-900/60 hover:bg-zinc-800/80 border border-zinc-800 text-zinc-300 rounded text-[10px] font-bold uppercase tracking-wider font-mono-data"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                            {c.content}
                          </p>
                          {mine && (
                            <div className="flex gap-3 mt-2 print:hidden">
                              <button
                                onClick={() => {
                                  setEditingId(c.id);
                                  setEditText(c.content);
                                  setCommentError(null);
                                }}
                                className="text-[10px] uppercase tracking-wider font-mono-data text-zinc-500 hover:text-primary transition-colors"
                              >
                                Edit
                              </button>
                              <button
                                onClick={() => handleDeleteComment(c.id)}
                                disabled={commentBusyId === c.id}
                                className="text-[10px] uppercase tracking-wider font-mono-data text-zinc-500 hover:text-cyber-rose transition-colors disabled:opacity-60"
                              >
                                Delete
                              </button>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {authToken ? (
              <div className="space-y-2">
                <textarea
                  value={newComment}
                  onChange={(e) => setNewComment(e.target.value)}
                  rows={3}
                  maxLength={4000}
                  placeholder="Share your analysis..."
                  className="w-full bg-zinc-950/40 border border-zinc-800 rounded-lg p-3 text-sm text-zinc-200 focus:outline-none focus:border-primary transition-colors resize-y placeholder:text-zinc-700"
                />
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[10px] text-zinc-600 font-mono-data">
                    {newComment.length}/4000
                  </span>
                  <button
                    onClick={handlePostComment}
                    disabled={posting || !newComment.trim()}
                    className="flex items-center gap-2 px-4 py-2 bg-primary/10 hover:bg-primary/20 border border-primary/30 text-primary rounded text-xs font-bold uppercase tracking-wider font-mono-data disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {posting ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Send className="w-3.5 h-3.5" />
                    )}
                    {posting ? "Posting..." : "Post Comment"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="p-4 bg-zinc-950/40 border border-dashed border-zinc-800 rounded-xl flex flex-wrap items-center justify-between gap-3">
                <span className="text-sm text-zinc-400">Sign in to join the discussion.</span>
                <button
                  onClick={() => router.push("/login")}
                  className="px-4 py-2 bg-primary/10 hover:bg-primary/20 border border-primary/30 text-primary rounded text-xs font-bold uppercase tracking-wider font-mono-data"
                >
                  Sign In
                </button>
              </div>
            )}

            {commentError && (
              <div className="p-3 rounded-lg bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs font-mono-data uppercase tracking-wider">
                {commentError}
              </div>
            )}
          </section>

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
                  {event.articles.length} Wire{event.articles.length !== 1 ? 's' : ''} Corroborated
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
              <span className="text-xs text-emerald-400 font-bold">{avgTrustScore}%</span>
            </div>
            <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-lg p-2.5 flex flex-col gap-0.5">
              <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-mono-data">Lean</span>
              <span className="text-xs text-zinc-200 font-semibold capitalize">{biasLabel.toLowerCase()}</span>
            </div>
          </div>

          {/* Transparency Panel */}
          <div className="bg-zinc-900/60 border border-zinc-800/60 rounded-lg p-3 mb-4 flex flex-col gap-2">
            <h3 className="text-xs text-zinc-400 font-bold uppercase tracking-widest flex items-center gap-1.5 mb-1">
              <span className="material-symbols-outlined text-[14px]">visibility</span>
              Transparency Panel
            </h3>
            <div className="flex justify-between items-center text-xs">
              <span className="text-zinc-500">Last Updated</span>
              <span className="text-zinc-200">{event.updated_at ? new Date(event.updated_at).toLocaleDateString() : 'N/A'}</span>
            </div>
            <div className="flex justify-between items-center text-xs">
              <span className="text-zinc-500">Sources Used</span>
              <span className="text-zinc-200">{event.articles.length} verified</span>
            </div>
            <div className="flex justify-between items-center text-xs">
              <span className="text-zinc-500">Contradictions</span>
              <span className="text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded">0 detected</span>
            </div>
            <div className="flex justify-between items-center text-xs">
              <span className="text-zinc-500">Editor Confidence</span>
              <span className="text-zinc-200">{avgTrustScore}%</span>
            </div>
          </div>

          {/* AI Fact Check Module */}
          <div className="bg-zinc-950/40 border border-zinc-800/60 rounded-lg p-3 mb-4 print:hidden">
            <h3 className="text-xs text-zinc-400 font-bold uppercase tracking-widest flex items-center gap-1.5 mb-2">
              <span className="material-symbols-outlined text-[14px]">policy</span>
              AI Fact Check
            </h3>
            
            {!factCheck && !factCheckLoading && (
              <button 
                onClick={handleRunFactCheck}
                className="w-full py-2 bg-primary/10 hover:bg-primary/20 text-primary border border-primary/30 rounded text-xs font-bold transition-colors"
              >
                Run Credibility Analysis
              </button>
            )}

            {factCheckLoading && (
              <div className="flex items-center justify-center py-4 gap-2 text-primary text-xs font-mono-data animate-pulse">
                <RefreshCw className="w-4 h-4 animate-spin" /> Analyzing claims...
              </div>
            )}

            {factCheck && (
              <div className="flex flex-col gap-3 mt-2">
                <div className="flex justify-between items-center bg-zinc-900 p-2 rounded border border-zinc-800 text-xs">
                  <span className="text-zinc-400 font-mono-data">Credibility Score</span>
                  <span className={`font-bold ${factCheck.credibility_score > 75 ? 'text-emerald-400' : factCheck.credibility_score > 50 ? 'text-amber-400' : 'text-rose-400'}`}>
                    {factCheck.credibility_score}%
                  </span>
                </div>
                
                <p className="text-xs text-zinc-300 leading-relaxed border-l-2 border-primary/50 pl-2">
                  {factCheck.summary}
                </p>

                {factCheck.claims && factCheck.claims.length > 0 && (
                  <div className="flex flex-col gap-2 mt-2">
                    <span className="text-[10px] text-zinc-500 uppercase tracking-widest font-mono-data">Claim Review</span>
                    {factCheck.claims.map((claim, idx) => (
                      <div key={idx} className="bg-zinc-900/50 p-2 rounded text-xs border border-zinc-800/50">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] uppercase font-bold mb-1 ${
                          claim.status.toUpperCase() === 'CORROBORATED' ? 'bg-emerald-500/10 text-emerald-400' :
                          claim.status.toUpperCase() === 'DISPUTED' ? 'bg-rose-500/10 text-rose-400' :
                          'bg-amber-500/10 text-amber-400'
                        }`}>
                          {claim.status}
                        </span>
                        <p className="text-zinc-300 line-clamp-2" title={claim.text}>{claim.text}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Article List */}
          <div className="flex-1 overflow-y-auto pr-1 no-scrollbar space-y-3">
            {event.articles.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-center gap-2">
                <Newspaper className="w-6 h-6 text-zinc-700" />
                <p className="text-xs text-zinc-500">No source wires linked to this event.</p>
              </div>
            ) : (
              event.articles.map((art, idx) => {
                const percent = Math.round((art.source.credibility_score || 0.85) * 100);
                const trustColor = percent >= 80 ? 'text-emerald-400' : percent >= 60 ? 'text-amber-400' : 'text-rose-400';
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
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono-data font-bold uppercase tracking-wide truncate max-w-[50%]">
                        {art.source.name}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <span className={`text-[8px] font-bold uppercase tracking-wider px-1 py-0.5 rounded border ${
                            art.source.bias_lean === 'LEFT' ? 'text-blue-400 bg-blue-500/10 border-blue-500/30' :
                            art.source.bias_lean === 'CENTER_LEFT' ? 'text-blue-200 bg-blue-300/10 border-blue-300/30' :
                            art.source.bias_lean === 'CENTER_RIGHT' ? 'text-red-200 bg-red-300/10 border-red-300/30' :
                            art.source.bias_lean === 'RIGHT' ? 'text-red-400 bg-red-500/10 border-red-500/30' :
                            'text-zinc-400 bg-zinc-500/10 border-zinc-500/30'
                        }`}>
                            {art.source.bias_lean.replace('_', ' ')}
                        </span>
                        <span className={`text-[9px] font-bold flex items-center gap-1 ${trustColor}`}>
                          <CheckCircle className="w-2.5 h-2.5" />
                          {percent}%
                        </span>
                      </div>
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
        </nav>
      </footer>

      {/* Save to Reading List modal */}
      {showListModal && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
          onClick={() => !listModalBusy && setShowListModal(false)}
        >
          <div
            className="w-full max-w-md bg-[#0c101b] border border-indigo-500/20 rounded-2xl shadow-[0_0_50px_rgba(6,182,212,0.15)] p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="text-lg font-bold text-white">Save to Reading List</h3>
                <p className="text-xs text-zinc-500 mt-0.5">{event.title}</p>
              </div>
              <button
                onClick={() => !listModalBusy && setShowListModal(false)}
                className="text-zinc-500 hover:text-white transition-colors"
              >
                ✕
              </button>
            </div>

            {listModalError && (
              <div className="mb-4 p-2.5 rounded-lg bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs font-mono-data uppercase tracking-wider">
                {listModalError}
              </div>
            )}

            <div className="max-h-64 overflow-y-auto no-scrollbar space-y-1.5 mb-4">
              {listModalLoading ? (
                <p className="text-xs text-zinc-500 py-4 text-center">Loading your lists…</p>
              ) : readingLists.length === 0 ? (
                <p className="text-xs text-zinc-500 py-4 text-center">
                  No lists yet — create one below.
                </p>
              ) : (
                readingLists.map((l) => (
                  <button
                    key={l.id}
                    onClick={() => addToList(l.id)}
                    disabled={listModalBusy}
                    className={`w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-xl border transition-all text-left disabled:opacity-60 ${
                      savedToListId === l.id
                        ? "border-primary/50 bg-primary/10"
                        : "border-outline-variant hover:border-primary/40 hover:bg-white/[0.03]"
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="block text-sm text-zinc-200 truncate">{l.name}</span>
                      <span className="block font-mono-data text-[10px] uppercase tracking-wider text-zinc-500">
                        {l.item_count} {l.item_count === 1 ? "item" : "items"}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono-data text-[10px] uppercase tracking-wider text-primary">
                      {savedToListId === l.id ? "Saved" : "Add"}
                    </span>
                  </button>
                ))
              )}
            </div>

            <div className="border-t border-indigo-950/40 pt-4">
              <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold mb-2">
                Create new list
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={newListName}
                  onChange={(e) => setNewListName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") createListThenAdd();
                  }}
                  placeholder="e.g. Iran watch"
                  className="flex-1 bg-zinc-950/60 border border-outline-variant rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-primary transition-all"
                />
                <button
                  onClick={createListThenAdd}
                  disabled={listModalBusy}
                  className="shrink-0 px-3 py-2 bg-primary/10 hover:bg-primary/20 border border-primary/30 text-primary font-mono-data text-[11px] font-bold tracking-widest uppercase rounded-xl transition-all disabled:opacity-60"
                >
                  Create &amp; Add
                </button>
              </div>
            </div>

            <div className="mt-4 flex justify-end">
              <button
                onClick={() => router.push("/reading-lists")}
                className="text-xs text-zinc-500 hover:text-primary font-mono-data uppercase tracking-wider transition-colors"
              >
                View all lists →
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

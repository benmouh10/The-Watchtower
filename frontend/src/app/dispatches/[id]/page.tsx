"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Calendar,
  ExternalLink,
  FileText,
  MessageSquare,
  Pencil,
  RefreshCw,
  Send,
  Trash2,
  User,
} from "lucide-react";
import PrimaryNav from "../../components/PrimaryNav";
import ListenButton from "../../components/ListenButton";
import EventIllustrationImage, {
  EventIllustration,
} from "../../components/EventIllustrationImage";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API = `${API_BASE_URL}/api/v1`;

interface Article {
  id: string;
  title: string;
  content: string;
  url: string | null;
  published_at: string | null;
  created_at: string | null;
  publication_status: string;
  author: { id: string; name: string } | null;
  illustration?: EventIllustration | null;
}

interface CommentItem {
  id: string;
  content: string;
  created_at: string | null;
  article_id: string | null;
  user_id: string;
  author: string;
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DispatchDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = Array.isArray(params?.id) ? params.id[0] : (params?.id as string | undefined);

  const [article, setArticle] = useState<Article | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Comments are public to read; a session is required to post.
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<string | null>(null);
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [newComment, setNewComment] = useState("");
  const [posting, setPosting] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [commentBusyId, setCommentBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/articles/${id}`);
      if (res.status === 404) throw new Error("This dispatch is not available");
      if (!res.ok) throw new Error(`Could not load this dispatch (${res.status})`);
      setArticle(await res.json());
    } catch (err: any) {
      setError(err.message || "Could not load this dispatch");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!id) return;
    const saved = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    setAuthToken(saved);
    if (saved) {
      fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${saved}` } })
        .then((r) => (r.ok ? r.json() : null))
        .then((me) => {
          if (me) {
            setCurrentUserId(me.id);
            setUserRole(me.role);
          }
        })
        .catch(() => {});
    }

    const loadComments = async () => {
      setCommentsLoading(true);
      try {
        const res = await fetch(`${API}/articles/${id}/comments`);
        if (res.ok) {
          const data = await res.json();
          setComments(data.comments || []);
        }
      } catch {
        /* the discussion is best-effort; the article itself already loaded */
      } finally {
        setCommentsLoading(false);
      }
    };
    loadComments();
  }, [id]);

  const handlePostComment = async () => {
    if (!id) return;
    if (!authToken) {
      router.push("/login");
      return;
    }
    const content = newComment.trim();
    if (!content) return;
    setPosting(true);
    setCommentError(null);
    try {
      const res = await fetch(`${API}/articles/${id}/comments`, {
        method: "POST",
        headers: { Authorization: `Bearer ${authToken}`, "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        router.push("/login");
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not post comment");
      setComments((prev) => [...prev, data]);
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
      const res = await fetch(`${API}/comments/${commentId}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${authToken}`, "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not update comment");
      setComments((prev) => prev.map((c) => (c.id === commentId ? { ...c, content: data.content } : c)));
      setEditingCommentId(null);
      setEditText("");
    } catch (err: any) {
      setCommentError(err.message || "Could not update comment");
    } finally {
      setCommentBusyId(null);
    }
  };

  const handleDeleteComment = async (commentId: string) => {
    if (!authToken) return;
    if (typeof window !== "undefined" && !window.confirm("Delete this comment?")) return;
    setCommentBusyId(commentId);
    setCommentError(null);
    try {
      const res = await fetch(`${API}/comments/${commentId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!res.ok && res.status !== 204) throw new Error("Could not delete comment");
      setComments((prev) => prev.filter((c) => c.id !== commentId));
    } catch (err: any) {
      setCommentError(err.message || "Could not delete comment");
    } finally {
      setCommentBusyId(null);
    }
  };

  const showAdmin = userRole === "ADMIN";

  return (
    <div className="bg-background text-on-background min-h-screen flex flex-col font-body-md relative overflow-x-hidden">
      <header className="flex justify-between items-center px-margin-desktop w-full h-16 sticky top-0 z-50 bg-[#080c16]/80 backdrop-blur-lg border-b border-indigo-950/40 flex-shrink-0">
        <PrimaryNav activeHref="/dispatches" role={userRole} />
      </header>

      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(69,70,77,0.06)_1px,transparent_1px),linear-gradient(to_bottom,rgba(69,70,77,0.06)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none z-0"></div>

      <main className="flex-grow w-full max-w-3xl mx-auto px-margin-mobile md:px-margin-desktop py-stack-lg z-10 relative">
        <button
          onClick={() => router.push("/dispatches")}
          className="flex items-center gap-1.5 text-zinc-500 hover:text-primary transition-colors font-label-caps text-[10px] uppercase tracking-wider font-bold mb-6"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Dispatches
        </button>

        {loading && (
          <div className="flex items-center justify-center gap-2 py-24 text-primary text-sm font-mono-data animate-pulse">
            <RefreshCw className="w-4 h-4 animate-spin" /> Loading dispatch...
          </div>
        )}

        {!loading && error && (
          <div className="max-w-lg mx-auto text-center bg-surface-container border border-outline-variant rounded-lg p-8">
            <div className="w-12 h-12 mx-auto rounded-full bg-zinc-900 border border-outline-variant flex items-center justify-center mb-4">
              <FileText className="w-5 h-5 text-zinc-500" />
            </div>
            <h2 className="font-headline-lg text-[20px] text-on-surface font-bold">Dispatch Unavailable</h2>
            <p className="text-on-surface-variant text-sm mt-2">{error}</p>
            <Link
              href="/dispatches"
              className="mt-6 inline-flex items-center gap-2 px-5 py-2.5 bg-primary/10 hover:bg-primary/20 border border-primary/30 text-primary font-mono-data text-xs font-bold tracking-widest uppercase rounded-xl transition-all"
            >
              Back to Dispatches
            </Link>
          </div>
        )}

        {!loading && !error && article && (
          <>
          <article className="bg-surface-container border border-outline-variant rounded-lg p-stack-md md:p-8">
            <h1 className="font-display-lg text-[26px] md:text-display-lg text-on-surface leading-tight font-bold">
              {article.title}
            </h1>

            {/* Artwork belongs to the parent event, so it is inherited here
                rather than generated per article. */}
            {article.illustration ? (
              <div className="mt-5">
                <EventIllustrationImage
                  illustration={article.illustration}
                  variant="hero"
                  className="max-h-[420px]"
                />
              </div>
            ) : null}

            <div className="flex items-center gap-3 mt-4 pb-5 border-b border-outline-variant/60 flex-wrap">
              <span className="inline-flex items-center gap-1.5 text-[10px] text-zinc-400 font-mono-data uppercase tracking-wider">
                <User className="w-3.5 h-3.5 text-primary" />
                {article.author?.name || "Newsroom"}
              </span>
              <span className="inline-flex items-center gap-1.5 text-[10px] text-zinc-500 font-mono-data uppercase tracking-wider">
                <Calendar className="w-3.5 h-3.5" />
                {formatDateTime(article.published_at || article.created_at)}
              </span>
              {article.url && (
                <a
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[10px] text-cyber-cyan hover:text-cyber-cyan/80 font-mono-data uppercase tracking-wider transition-colors"
                >
                  <ExternalLink className="w-3.5 h-3.5" /> View Source
                </a>
              )}
              <ListenButton resource="articles" resourceId={article.id} label="Listen to this dispatch" className="ml-auto" />
            </div>

            <div className="mt-6 text-zinc-200 text-[15px] leading-relaxed whitespace-pre-wrap">
              {article.content}
            </div>
          </article>

          {/* Discussion — anyone can read; a session is required to post */}
          <section className="mt-stack-md bg-surface-container border border-outline-variant rounded-lg p-stack-md">
            <h3 className="font-label-caps text-label-caps text-zinc-400 uppercase tracking-widest text-[11px] flex items-center gap-1.5 mb-4">
              <MessageSquare className="w-4 h-4 text-primary" />
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
                  const editing = editingCommentId === c.id;
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
                          {c.created_at ? formatDateTime(c.created_at) : ""}
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
                                setEditingCommentId(null);
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
                          <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">{c.content}</p>
                          {mine && (
                            <div className="flex gap-3 mt-2">
                              <button
                                onClick={() => {
                                  setEditingCommentId(c.id);
                                  setEditText(c.content);
                                  setCommentError(null);
                                }}
                                className="flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono-data text-zinc-500 hover:text-primary transition-colors"
                              >
                                <Pencil className="w-3 h-3" /> Edit
                              </button>
                              <button
                                onClick={() => handleDeleteComment(c.id)}
                                disabled={commentBusyId === c.id}
                                className="flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono-data text-zinc-500 hover:text-cyber-rose transition-colors disabled:opacity-60"
                              >
                                <Trash2 className="w-3 h-3" /> Delete
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
              <div className="space-y-2 mt-4">
                <textarea
                  value={newComment}
                  onChange={(e) => setNewComment(e.target.value)}
                  rows={3}
                  maxLength={4000}
                  placeholder="Share your analysis..."
                  className="w-full bg-zinc-950/40 border border-zinc-800 rounded-lg p-3 text-sm text-zinc-200 focus:outline-none focus:border-primary transition-colors resize-y placeholder:text-zinc-700"
                />
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[10px] text-zinc-600 font-mono-data">{newComment.length}/4000</span>
                  <button
                    onClick={handlePostComment}
                    disabled={posting || !newComment.trim()}
                    className="flex items-center gap-2 px-4 py-2 bg-primary/10 hover:bg-primary/20 border border-primary/30 text-primary rounded text-xs font-bold uppercase tracking-wider font-mono-data disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {posting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                    {posting ? "Posting..." : "Post Comment"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-4 p-4 bg-zinc-950/40 border border-dashed border-zinc-800 rounded-xl flex flex-wrap items-center justify-between gap-3">
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
              <div className="mt-3 p-3 rounded-lg bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs font-mono-data uppercase tracking-wider">
                {commentError}
              </div>
            )}
          </section>
          </>
        )}
      </main>
    </div>
  );
}

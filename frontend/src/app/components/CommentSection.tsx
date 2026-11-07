'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowBigDown, ArrowBigUp, CornerDownRight, Loader2, MessageSquare, Pencil, Send, Trash2, X } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const TOKEN_KEY = 'admin_token';

interface CommentBadge {
  label: string;
  tone: string;
  detail: string;
}

interface CommentItem {
  id: string;
  content: string;
  created_at: string | null;
  updated_at: string | null;
  is_edited: boolean;
  is_hidden: boolean;
  parent_id: string | null;
  author: { id: string | null; name: string; is_author_self: boolean };
  badge: CommentBadge | null;
  expert_badge: CommentBadge | null;
  votes: { upvotes: number; downvotes: number; score: number };
  viewer_vote: number;
  can_edit: boolean;
  can_delete: boolean;
  replies: CommentItem[];
}

interface CommentsResponse {
  event_id: string;
  total: number;
  comments: CommentItem[];
}

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem(TOKEN_KEY) : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function timeAgo(iso: string | null): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

const BADGE_TONES: Record<string, string> = {
  cyan: 'text-cyber-cyan border-cyber-cyan/30 bg-cyber-cyan/10',
  amber: 'text-cyber-amber border-cyber-amber/30 bg-cyber-amber/10',
  violet: 'text-cyber-purple border-cyber-purple/40 bg-cyber-purple/10',
};

export default function CommentSection({ eventId }: { eventId: string }) {
  const [data, setData] = useState<CommentsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [replyDraft, setReplyDraft] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState('');

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/comments/events/${eventId}`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (res.status === 404) {
        setError('Discussion unavailable for this dossier.');
        setData(null);
        return;
      }
      if (!res.ok) throw new Error('Failed to load discussion');
      setData(await res.json());
    } catch {
      setError('Failed to load discussion.');
    } finally {
      setLoading(false);
    }
  }, [eventId]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const run = async (fn: () => Promise<Response>, successMsg?: string) => {
    setNotice(null);
    const res = await fn();
    if (res.status === 401) {
      setNotice('Sign in to join the discussion.');
      return false;
    }
    if (res.status === 403) {
      const body = await res.json().catch(() => ({}));
      setNotice(body.detail || 'You do not have permission to do that.');
      return false;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setNotice(body.detail || 'Something went wrong. Please try again.');
      return false;
    }
    if (successMsg) setNotice(successMsg);
    await load();
    return true;
  };

  const postComment = async (parentId: string | null, body: string) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const ok = await run(
        () =>
          fetch(`${API_BASE_URL}/api/v1/comments/events/${eventId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify({ content: body, parent_id: parentId }),
          }),
        parentId ? 'Reply posted.' : 'Comment posted.'
      );
      if (ok) {
        setDraft('');
        setReplyDraft('');
        setReplyTo(null);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const vote = (commentId: string, value: 1 | -1) =>
    run(() =>
      fetch(`${API_BASE_URL}/api/v1/comments/${commentId}/vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ value }),
      })
    );

  const retractVote = (commentId: string) =>
    run(() =>
      fetch(`${API_BASE_URL}/api/v1/comments/${commentId}/vote`, {
        method: 'DELETE',
        headers: authHeaders(),
      })
    );

  const saveEdit = (commentId: string) =>
    run(
      () =>
        fetch(`${API_BASE_URL}/api/v1/comments/${commentId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ content: editDraft }),
        }),
      'Comment updated.'
    ).then((ok) => {
      if (ok) {
        setEditingId(null);
        setEditDraft('');
      }
    });

  const remove = (commentId: string) =>
    run(
      () =>
        fetch(`${API_BASE_URL}/api/v1/comments/${commentId}`, {
          method: 'DELETE',
          headers: authHeaders(),
        }),
      'Comment deleted.'
    );

  const renderComment = (comment: CommentItem, isReply = false) => {
    const badgeTone = comment.badge ? BADGE_TONES[comment.badge.tone] || BADGE_TONES.cyan : null;
    const expertTone = comment.expert_badge
      ? BADGE_TONES[comment.expert_badge.tone] || BADGE_TONES.violet
      : null;
    const voted = comment.viewer_vote;

    return (
      <div
        key={comment.id}
        className={`border border-cyber-line/40 bg-cyber-panel/30 ${isReply ? 'ml-5 sm:ml-8 border-l-2 border-l-cyber-cyan/30' : ''}`}
      >
        <div className="p-3 sm:p-4">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="text-[11px] font-mono-data font-bold text-cyber-text">
              {comment.author.name}
            </span>
            {comment.badge && badgeTone && (
              <span
                title={comment.badge.detail}
                className={`inline-flex items-center gap-1 text-[9px] font-mono-data font-bold border px-2 py-0.5 rounded uppercase ${badgeTone}`}
              >
                {comment.badge.label}
              </span>
            )}
            {comment.expert_badge && expertTone && (
              <span
                title={comment.expert_badge.detail}
                className={`inline-flex items-center gap-1 text-[9px] font-mono-data font-bold border px-2 py-0.5 rounded uppercase ${expertTone}`}
              >
                {comment.expert_badge.label}
              </span>
            )}
            <span className="text-[10px] text-cyber-dim">{timeAgo(comment.created_at)}</span>
            {comment.is_edited && (
              <span className="text-[9px] text-cyber-dim italic">edited</span>
            )}
          </div>

          {editingId === comment.id ? (
            <div className="space-y-2">
              <textarea
                value={editDraft}
                onChange={(e) => setEditDraft(e.target.value)}
                rows={3}
                maxLength={5000}
                className="w-full bg-cyber-bg border border-cyber-line/60 px-2 py-1.5 text-[12px] text-cyber-text focus:border-cyber-cyan/50 outline-none"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => saveEdit(comment.id)}
                  className="text-[10px] font-mono-data font-bold text-cyber-cyan border border-cyber-cyan/30 bg-cyber-cyan/10 px-2 py-1 hover:bg-cyber-cyan/20"
                >
                  Save
                </button>
                <button
                  onClick={() => {
                    setEditingId(null);
                    setEditDraft('');
                  }}
                  className="text-[10px] font-mono-data text-cyber-dim px-2 py-1 hover:text-cyber-text"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <p className="text-[12px] leading-relaxed text-cyber-text/90 whitespace-pre-wrap break-words">
              {comment.content}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3 mt-3">
            <div className="flex items-center gap-1">
              <button
                onClick={() => (voted === 1 ? retractVote(comment.id) : vote(comment.id, 1))}
                title={voted === 1 ? 'Remove your upvote' : 'Upvote'}
                aria-pressed={voted === 1}
                className={`p-1 border transition-colors ${
                  voted === 1
                    ? 'text-cyber-cyan border-cyber-cyan/50 bg-cyber-cyan/15'
                    : 'text-cyber-dim border-cyber-line/50 hover:text-cyber-cyan'
                }`}
              >
                <ArrowBigUp size={13} />
              </button>
              <span
                className={`text-[11px] font-mono-data font-bold min-w-[2ch] text-center ${
                  comment.votes.score > 0
                    ? 'text-cyber-cyan'
                    : comment.votes.score < 0
                      ? 'text-cyber-red'
                      : 'text-cyber-dim'
                }`}
              >
                {comment.votes.score}
              </span>
              <button
                onClick={() => (voted === -1 ? retractVote(comment.id) : vote(comment.id, -1))}
                title={voted === -1 ? 'Remove your downvote' : 'Downvote'}
                aria-pressed={voted === -1}
                className={`p-1 border transition-colors ${
                  voted === -1
                    ? 'text-cyber-red border-cyber-red/50 bg-cyber-red/15'
                    : 'text-cyber-dim border-cyber-line/50 hover:text-cyber-red'
                }`}
              >
                <ArrowBigDown size={13} />
              </button>
            </div>

            {!isReply && (
              <button
                onClick={() => {
                  setReplyTo(replyTo === comment.id ? null : comment.id);
                  setReplyDraft('');
                }}
                className="inline-flex items-center gap-1 text-[10px] font-mono-data text-cyber-dim hover:text-cyber-cyan transition-colors"
              >
                <CornerDownRight size={11} />
                Reply
              </button>
            )}

            {comment.can_edit && editingId !== comment.id && (
              <button
                onClick={() => {
                  setEditingId(comment.id);
                  setEditDraft(comment.content);
                }}
                className="inline-flex items-center gap-1 text-[10px] font-mono-data text-cyber-dim hover:text-cyber-cyan transition-colors"
              >
                <Pencil size={10} />
                Edit
              </button>
            )}

            {comment.can_delete && (
              <button
                onClick={() => remove(comment.id)}
                className="inline-flex items-center gap-1 text-[10px] font-mono-data text-cyber-dim hover:text-cyber-red transition-colors"
              >
                <Trash2 size={10} />
                Delete
              </button>
            )}
          </div>

          {replyTo === comment.id && (
            <div className="mt-3 space-y-2">
              <textarea
                value={replyDraft}
                onChange={(e) => setReplyDraft(e.target.value)}
                placeholder="Add a reply…"
                rows={2}
                maxLength={2000}
                className="w-full bg-cyber-bg border border-cyber-line/60 px-2 py-1.5 text-[12px] text-cyber-text focus:border-cyber-cyan/50 outline-none"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => postComment(comment.id, replyDraft.trim())}
                  disabled={!replyDraft.trim() || submitting}
                  className="inline-flex items-center gap-1 text-[10px] font-mono-data font-bold text-cyber-cyan border border-cyber-cyan/30 bg-cyber-cyan/10 px-2 py-1 hover:bg-cyber-cyan/20 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Send size={10} />
                  Reply
                </button>
                <button
                  onClick={() => setReplyTo(null)}
                  className="inline-flex items-center gap-1 text-[10px] font-mono-data text-cyber-dim px-2 py-1 hover:text-cyber-text"
                >
                  <X size={10} />
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        {comment.replies.length > 0 && (
          <div className="space-y-2 p-2 sm:p-3 pt-0">
            {comment.replies.map((reply) => renderComment(reply, true))}
          </div>
        )}
      </div>
    );
  };

  return (
    <section className="mt-8 border border-cyber-line/40 bg-cyber-panel/20">
      <header className="flex items-center justify-between px-4 py-3 border-b border-cyber-line/40">
        <h2 className="flex items-center gap-2 text-[11px] font-mono-data font-bold uppercase tracking-widest text-cyber-text">
          <MessageSquare size={13} className="text-cyber-cyan" />
          Discussion
        </h2>
        {data && (
          <span className="text-[10px] font-mono-data text-cyber-dim">
            {data.total} {data.total === 1 ? 'comment' : 'comments'}
          </span>
        )}
      </header>

      <div className="p-4">
        {notice && (
          <p className="mb-3 text-[10px] font-mono-data text-cyber-amber border border-cyber-amber/30 bg-cyber-amber/10 px-2 py-1.5">
            {notice}
          </p>
        )}

        <div className="mb-5 space-y-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add to the discussion…"
            rows={2}
            maxLength={5000}
            className="w-full bg-cyber-bg border border-cyber-line/60 px-2 py-1.5 text-[12px] text-cyber-text focus:border-cyber-cyan/50 outline-none"
          />
          <button
            onClick={() => postComment(null, draft.trim())}
            disabled={!draft.trim() || submitting}
            className="inline-flex items-center gap-1 text-[10px] font-mono-data font-bold text-cyber-cyan border border-cyber-cyan/30 bg-cyber-cyan/10 px-3 py-1.5 hover:bg-cyber-cyan/20 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
            Post comment
          </button>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-[11px] font-mono-data text-cyber-dim py-4">
            <Loader2 size={12} className="animate-spin" />
            Loading discussion…
          </div>
        ) : error ? (
          <p className="text-[11px] font-mono-data text-cyber-dim py-4">{error}</p>
        ) : data && data.comments.length === 0 ? (
          <p className="text-[11px] font-mono-data text-cyber-dim py-4">
            No comments yet. Start the discussion.
          </p>
        ) : (
          <div className="space-y-3">
            {data?.comments.map((comment) => renderComment(comment))}
          </div>
        )}
      </div>
    </section>
  );
}

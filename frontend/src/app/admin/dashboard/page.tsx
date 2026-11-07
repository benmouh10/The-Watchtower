"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { 
  Shield, 
  Activity, 
  Database, 
  Server, 
  TrendingUp, 
  AlertTriangle, 
  RefreshCw, 
  Play, 
  FileText, 
  Cpu, 
  Network, 
  Layers, 
  Globe, 
  UserCheck, 
  MessageSquare,
  Newspaper,
  LogOut 
} from "lucide-react";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  Legend as RechartsLegend
} from "recharts";

// Dynamically import the Map component to prevent window undefined SSR issues
const Map = dynamic(() => import("../../components/Map"), { ssr: false });

// API Client configuration
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
import GlobelensLogo from "../../components/GlobelensLogo";


interface HealthState {
  status: "healthy" | "degraded" | "loading" | "error";
  backend: "UP" | "DOWN";
  postgres: "UP" | "DOWN";
  redis: "UP" | "DOWN";
  elasticsearch: "UP" | "DOWN";
  frontend: "UP" | "DOWN";
}

interface StatsState {
  funnel: {
    scraped: number;
    vectorized: number;
    clustered: number;
    enriched: number;
  };
  media_split: Record<string, number>;
  topic_distribution: Record<string, number>;
  bias_distribution: Record<string, number>;
  window?: { from: string | null; to: string | null };
}

interface SelectedEvent {
  id: string;
  title: string;
  summary: string;
  topic: string;
  country: string;
  latitude: number;
  longitude: number;
  importance_score: number;
  source_count: number;
}

interface ModerationComment {
  id: string;
  event_id: string;
  content: string;
  is_hidden: boolean;
  parent_id: string | null;
  reply_count: number;
  author: string | null;
  author_role: string | null;
  // Same badge vocabulary as the public comment thread, so the queue can show
  // "Journalist"/"Moderator" instead of a raw enum value.
  author_badge?: { label: string; tone: string; detail: string } | null;
  created_at: string | null;
}

interface AdminUser {
  id: string;
  name: string;
  email: string;
  role: string;
  plan: string;
  is_blocked: boolean;
  created_at: string | null;
}

// Role badge colours mirror CommentSection's BADGE_TONES.
const USER_MGMT_ROLE_TONES: Record<string, string> = {
  JOURNALIST: "text-cyber-cyan border-cyber-cyan/30 bg-cyber-cyan/10",
  ADMIN: "text-cyber-amber border-cyber-amber/30 bg-cyber-amber/10",
};

interface ModerationArticle {
  id: string;
  title: string;
  url: string;
  is_hidden: boolean;
  event_id: string | null;
  event_title: string | null;
  source_id: string | null;
  source_name: string | null;
  processing_status: string | null;
  created_at: string | null;
}

export default function AdminDashboardPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [isMounted, setIsMounted] = useState(false);

  // Dashboard Stats & Health States
  const [health, setHealth] = useState<HealthState>({
    status: "loading",
    backend: "DOWN",
    postgres: "DOWN",
    redis: "DOWN",
    elasticsearch: "DOWN",
    frontend: "UP"
  });

  const [stats, setStats] = useState<StatsState>({
    funnel: { scraped: 0, vectorized: 0, clustered: 0, enriched: 0 },
    media_split: { "BBC News": 0, "CNN": 0, "Al Jazeera": 0 },
    topic_distribution: {},
    bias_distribution: {}
  });

  const [mapEvents, setMapEvents] = useState<SelectedEvent[]>([]);

  // Comment moderation
  const [modComments, setModComments] = useState<ModerationComment[]>([]);
  const [showHiddenOnly, setShowHiddenOnly] = useState(false);
  const [modLoading, setModLoading] = useState(false);
  const [modError, setModError] = useState<string | null>(null);
  const [busyCommentId, setBusyCommentId] = useState<string | null>(null);

  // Article moderation
  const [modArticles, setModArticles] = useState<ModerationArticle[]>([]);
  const [showHiddenArticlesOnly, setShowHiddenArticlesOnly] = useState(false);
  const [modArticlesLoading, setModArticlesLoading] = useState(false);
  const [modArticlesError, setModArticlesError] = useState<string | null>(null);
  const [busyArticleId, setBusyArticleId] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<SelectedEvent | null>(null);
  const [isStatsLoading, setIsStatsLoading] = useState(true);
  const [statsFrom, setStatsFrom] = useState<string>("");
  const [statsTo, setStatsTo] = useState<string>("");

  // User management (roles drive the comment expert badges)
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [userFilter, setUserFilter] = useState("");
  const [selfUserId, setSelfUserId] = useState<string | null>(null);

  // Administrative Trigger States
  const [triggerStatus, setTriggerStatus] = useState<{
    running: boolean;
    name: string;
    progress: number;
    message: string;
  }>({
    running: false,
    name: "",
    progress: 0,
    message: ""
  });

  // Handle client-side mount check
  useEffect(() => {
    setIsMounted(true);
    const savedToken = localStorage.getItem("admin_token");
    if (!savedToken) {
      router.push("/login");
    } else {
      setToken(savedToken);
    }
  }, [router]);

  const handleLogout = useCallback(async () => {
    // Sign out server-side first, so the refresh token stops working even if
    // this browser's storage is later restored or copied elsewhere. The
    // access token cannot be revoked: it is stateless and lives out its
    // 30-minute lifetime, which is why the refresh token is the one that matters.
    const token = localStorage.getItem("admin_token");
    const refreshToken = sessionStorage.getItem("admin_refresh_token");
    if (token) {
      try {
        await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(
            refreshToken ? { refresh_token: refreshToken } : {}
          ),
        });
      } catch {
        // A network failure must not trap the user in the dashboard; the local
        // state is cleared either way, and an unrevoked refresh token simply
        // expires on its own.
      }
    }

    localStorage.removeItem("admin_token");
    sessionStorage.removeItem("admin_refresh_token");
    // Login sets this cookie for middleware, so logout has to clear it or the
    // guard would still see a token and wave the next page load through.
    document.cookie = "admin_token=; path=/; max-age=0; SameSite=Strict";
    setToken(null);
    router.push("/login");
  }, [router, setToken]);

  // Live Checkup Polling
  const checkHealth = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/health`);
      if (!response.ok) throw new Error("Backend degraded");

      const data = await response.json();
      setHealth({
        status: data.status === "healthy" ? "healthy" : "degraded",
        backend: "UP",
        postgres: data.database === "ok" ? "UP" : "DOWN",
        redis: data.redis === "ok" ? "UP" : "DOWN",
        elasticsearch: data.elasticsearch === "ok" ? "UP" : "DOWN",
        frontend: "UP"
      });
    } catch (err) {
      setHealth((prev) => ({
        ...prev,
        status: "error",
        backend: "DOWN",
        postgres: "DOWN",
        redis: "DOWN",
        elasticsearch: "DOWN"
      }));
    }
  }, []);

  // Fetch Dashboard Stats & Map Events
  const fetchStatsAndMap = useCallback(async () => {
    if (!token) return;
    setIsStatsLoading(true);

    try {
      // 1. Fetch Stats (optionally bounded to a from/to window)
      const params = new URLSearchParams();
      if (statsFrom) {
        params.set("from", new Date(`${statsFrom}T00:00:00Z`).toISOString());
      }
      if (statsTo) {
        params.set("to", new Date(`${statsTo}T23:59:59Z`).toISOString());
      }
      const qs = params.toString();
      const statsResp = await fetch(
        `${API_BASE_URL}/api/v1/admin/stats${qs ? `?${qs}` : ""}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (statsResp.ok) {
        const statsData = await statsResp.json();
        setStats(statsData);
      } else if (statsResp.status === 401 || statsResp.status === 403) {
        handleLogout();
        return;
      }

      // 2. Fetch Map Events
      const mapResp = await fetch(`${API_BASE_URL}/api/v1/events/map`);
      if (mapResp.ok) {
        const mapData = await mapResp.json();
        setMapEvents(mapData.events || []);
      }
    } catch (err) {
      console.error("Failed to fetch statistics", err);
    } finally {
      setIsStatsLoading(false);
    }
  }, [token, handleLogout, statsFrom, statsTo]);

  // Fetch the comment moderation queue
  const fetchModerationQueue = useCallback(async () => {
    if (!token) return;
    setModLoading(true);
    setModError(null);

    try {
      // Always ask for hidden rows too, so the two views are served from one
      // request and a hidden comment can be restored from either filter.
      const resp = await fetch(
        `${API_BASE_URL}/api/v1/admin/moderation/comments?include_hidden=true`,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setModError(detail.detail || `Could not load the queue (HTTP ${resp.status}).`);
        return;
      }

      const data = await resp.json();
      setModComments(data.comments || []);
    } catch (err) {
      console.error("Failed to fetch moderation queue", err);
      setModError("Failed to reach the moderation API.");
    } finally {
      setModLoading(false);
    }
  }, [token, handleLogout]);

  // Hide or restore a comment, then refresh so the row reflects stored state
  const setCommentHidden = async (commentId: string, isHidden: boolean) => {
    if (!token) return;
    setBusyCommentId(commentId);
    setModError(null);

    try {
      const resp = await fetch(
        `${API_BASE_URL}/api/v1/admin/comments/${commentId}/visibility`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`
          },
          body: JSON.stringify({ is_hidden: isHidden })
        }
      );

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setModError(detail.detail || `Update failed (HTTP ${resp.status}).`);
        return;
      }

      // Trust the server response rather than assuming the write succeeded.
      const saved = await resp.json();
      setModComments((prev) =>
        prev.map((c) => (c.id === commentId ? { ...c, is_hidden: saved.is_hidden } : c))
      );
    } catch (err) {
      console.error("Failed to update comment visibility", err);
      setModError("Failed to reach the moderation API.");
    } finally {
      setBusyCommentId(null);
    }
  };

  // Fetch the article moderation queue
  const fetchArticleModerationQueue = useCallback(async () => {
    if (!token) return;
    setModArticlesLoading(true);
    setModArticlesError(null);

    try {
      // Always request hidden rows too, so hidden-only and all-views share one
      // request and a hidden article stays restorable from either filter.
      const resp = await fetch(
        `${API_BASE_URL}/api/v1/admin/moderation/articles?include_hidden=true`,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setModArticlesError(detail.detail || `Could not load articles (HTTP ${resp.status}).`);
        return;
      }

      const data = await resp.json();
      setModArticles(data.articles || []);
    } catch (err) {
      console.error("Failed to fetch article moderation queue", err);
      setModArticlesError("Failed to reach the moderation API.");
    } finally {
      setModArticlesLoading(false);
    }
  }, [token, handleLogout]);

  // Hide or restore an article, then trust the server's stored state
  const setArticleHidden = async (articleId: string, isHidden: boolean) => {
    if (!token) return;
    setBusyArticleId(articleId);
    setModArticlesError(null);

    try {
      const resp = await fetch(
        `${API_BASE_URL}/api/v1/admin/articles/${articleId}/hide`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`
          },
          body: JSON.stringify({ is_hidden: isHidden })
        }
      );

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setModArticlesError(detail.detail || `Update failed (HTTP ${resp.status}).`);
        return;
      }

      const saved = await resp.json();
      setModArticles((prev) =>
        prev.map((a) => (a.id === articleId ? { ...a, is_hidden: saved.is_hidden } : a))
      );
    } catch (err) {
      console.error("Failed to update article visibility", err);
      setModArticlesError("Failed to reach the moderation API.");
    } finally {
      setBusyArticleId(null);
    }
  };

  // Fetch the user list (roles here drive the comment expert badges)
  const fetchUsers = useCallback(async () => {
    if (!token) return;
    setUsersLoading(true);
    setUsersError(null);

    try {
      // Learn the signed-in user's own id once, so the grid can guard the
      // self-demotion / self-block rules the backend enforces.
      if (selfUserId === null) {
        const meRes = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (meRes.status === 401 || meRes.status === 403) {
          handleLogout();
          return;
        }
        if (meRes.ok) {
          const me = await meRes.json();
          setSelfUserId(me.id || null);
        }
      }

      const resp = await fetch(`${API_BASE_URL}/api/v1/admin/users`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setUsersError(detail.detail || `Could not load users (HTTP ${resp.status}).`);
        return;
      }
      const data = await resp.json();
      setUsers(data.users || []);
    } catch (err) {
      console.error("Failed to fetch users", err);
      setUsersError("Failed to reach the user management API.");
    } finally {
      setUsersLoading(false);
    }
  }, [token, selfUserId, handleLogout]);

  // Change a user's role; trust the server's stored state
  const changeUserRole = async (user: AdminUser, role: string) => {
    if (!token) return;
    setBusyUserId(user.id);
    setUsersError(null);

    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/admin/users/${user.id}/role`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ role })
      });

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setUsersError(detail.detail || `Role change failed (HTTP ${resp.status}).`);
        return;
      }

      const saved = await resp.json();
      setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, role: saved.role } : u)));
    } catch (err) {
      console.error("Failed to change user role", err);
      setUsersError("Failed to reach the user management API.");
    } finally {
      setBusyUserId(null);
    }
  };

  // Assign a plan; trust the server's stored state. Plans gate usage quotas
  // (they are not self-serve: no payment provider is wired, so this is the only
  // way a plan changes) and every move is audited server-side.
  const changeUserPlan = async (user: AdminUser, plan: string) => {
    if (!token) return;
    setBusyUserId(user.id);
    setUsersError(null);

    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/admin/users/${user.id}/plan`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ plan })
      });

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setUsersError(detail.detail || `Plan change failed (HTTP ${resp.status}).`);
        return;
      }

      const saved = await resp.json();
      setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, plan: saved.plan } : u)));
    } catch (err) {
      console.error("Failed to change user plan", err);
      setUsersError("Failed to reach the user management API.");
    } finally {
      setBusyUserId(null);
    }
  };

  // Block / unblock a user; trust the server's stored state
  const toggleUserBlock = async (user: AdminUser) => {
    if (!token) return;
    setBusyUserId(user.id);
    setUsersError(null);

    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/admin/users/${user.id}/block`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}` }
      });

      if (resp.status === 401 || resp.status === 403) {
        handleLogout();
        return;
      }
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setUsersError(detail.detail || `Update failed (HTTP ${resp.status}).`);
        return;
      }

      const saved = await resp.json();
      setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, is_blocked: saved.is_blocked } : u)));
    } catch (err) {
      console.error("Failed to toggle user block", err);
      setUsersError("Failed to reach the user management API.");
    } finally {
      setBusyUserId(null);
    }
  };

  // Sync health checks and analytics polling loops
  useEffect(() => {
    checkHealth();
    const healthInterval = setInterval(checkHealth, 10000);
    return () => clearInterval(healthInterval);
  }, [checkHealth]);

  useEffect(() => {
    if (token) {
      fetchStatsAndMap();
      const statsInterval = setInterval(fetchStatsAndMap, 15000);
      return () => clearInterval(statsInterval);
    }
  }, [token, fetchStatsAndMap]);

  // Load the moderation queues once authenticated
  useEffect(() => {
    if (token) {
      fetchModerationQueue();
      fetchArticleModerationQueue();
      fetchUsers();
    }
  }, [token, fetchModerationQueue, fetchArticleModerationQueue, fetchUsers]);

  const shownComments = showHiddenOnly
    ? modComments.filter((c) => c.is_hidden)
    : modComments;

  const shownArticles = showHiddenArticlesOnly
    ? modArticles.filter((a) => a.is_hidden)
    : modArticles;

  // Administrative Trigger overrides
  const triggerWorker = async (name: string, endpoint: string) => {
    if (!token || triggerStatus.running) return;

    setTriggerStatus({
      running: true,
      name,
      progress: 5,
      message: "Initializing background container worker..."
    });

    // Simulate progress bar increase
    const progressInterval = setInterval(() => {
      setTriggerStatus((prev) => {
        if (prev.progress >= 95) return prev;
        return {
          ...prev,
          progress: prev.progress + Math.floor(Math.random() * 8) + 2,
          message: prev.progress > 70 ? "Synthesizing intelligence feed databases..." : "Executing pipeline steps..."
        };
      });
    }, 450);

    try {
      const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` }
      });

      if (!response.ok) {
        throw new Error(`Worker execution failed with status: ${response.status}`);
      }

      // Completed successfully
      clearInterval(progressInterval);
      setTriggerStatus((prev) => ({
        ...prev,
        progress: 100,
        message: "Worker task initiated successfully (202 Accepted)!"
      }));

      setTimeout(() => {
        setTriggerStatus({ running: false, name: "", progress: 0, message: "" });
        fetchStatsAndMap();
      }, 1500);

    } catch (err: any) {
      clearInterval(progressInterval);
      setTriggerStatus({
        running: true,
        name,
        progress: 100,
        message: `Error: ${err.message || "Failed to trigger worker"}`
      });
      setTimeout(() => {
        setTriggerStatus({ running: false, name: "", progress: 0, message: "" });
      }, 3500);
    }
  };

  // NOTE: the four funnel cards used to render a sparkline built from invented
  // constants, e.g. [5, 8, 4, 15, 12, count + 2, count + 1, count]. Nothing
  // measured those values; the middle points were literals and the tail was
  // arithmetic on the current total, so the line implied a history that has
  // never been recorded. (When a count was 0 the expression even produced -1.)
  //
  // A sparkline needs per-tick history, and there is no history table in this
  // schema, so there is no honest trend to draw. The cards now show the real
  // current count only. Adding a genuine time series means recording snapshots.


  // Calculate Average Ideological Spectrum value (LEFT: 1 to RIGHT: 5)
  const calculateBiasScore = () => {
    const dist = stats.bias_distribution;
    const weights: Record<string, number> = {
      LEFT: 1,
      CENTER_LEFT: 2,
      CENTER: 3,
      CENTER_RIGHT: 4,
      RIGHT: 5
    };

    let totalWeight = 0;
    let totalCount = 0;
    Object.entries(dist).forEach(([bias, count]) => {
      const countNum = count as number;
      if (weights[bias]) {
        totalWeight += weights[bias] * countNum;
        totalCount += countNum;
      }
    });

    // With no classified events there is no average to compute. Returning 3.0
    // (dead centre) here would assert a neutral lean that nobody assessed,
    // which is the same fabrication the nullable bias columns exist to prevent.
    // null is propagated to the UI, which shows "Not rated" instead.
    return totalCount > 0 ? totalWeight / totalCount : null;
  };

  const biasScore = calculateBiasScore();
  const biasPercentage = biasScore !== null ? ((biasScore - 1) / 4) * 100 : null;

  // Render chart data formats
  const pieData: { name: string; value: number }[] = Object.entries(stats.media_split).map(([name, value]) => ({
    name,
    value: value as number
  }));
  const mediaColors = ["#7c3aed", "#3b82f6", "#10b981", "#dec29a"];

  const barData = Object.entries(stats.topic_distribution).map(([topic, count]) => ({
    name: topic,
    count
  }));

  if (!token) {
    return (
      <main className="min-h-screen bg-[#030712] flex flex-col items-center justify-center p-4 relative overflow-hidden">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-cyber-indigo/5 rounded-full blur-[120px] pointer-events-none"></div>
        <div className="z-10 text-center">
          <Cpu className="w-12 h-12 text-cyber-indigo animate-spin mx-auto mb-4" />
          <div className="text-zinc-400 font-mono-data text-xs tracking-widest uppercase">Redirecting to clearance gateway...</div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#030712] text-zinc-100 font-sans p-6 overflow-x-hidden relative">
      {/* Background gradients */}
      <div className="absolute top-0 right-0 w-[600px] h-[600px] bg-cyber-cyan/5 rounded-full blur-[150px] pointer-events-none"></div>
      <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-cyber-indigo/5 rounded-full blur-[150px] pointer-events-none"></div>
      
      {/* Grid Pattern overlay */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.02)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.02)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none z-0"></div>

      {/* HEADER SECTION */}
      <header className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-indigo-950/40 pb-6 mb-8 relative z-20">
        <div>
          <div className="flex items-center gap-2 text-cyber-cyan font-bold tracking-wider text-xs uppercase mb-1 font-mono-data">
            <Activity className="w-4 h-4 animate-pulse" />
            <span>Admin Live Monitoring</span>
          </div>
          <h1 className="text-3xl font-extrabold text-white tracking-tight bg-gradient-to-r from-white via-zinc-200 to-indigo-300 bg-clip-text text-transparent">
            System Administrative Dashboard
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/")}
            className="flex items-center gap-2 bg-cyber-cyan/10 border border-cyber-cyan/30 hover:bg-cyber-cyan/20 text-cyber-cyan text-xs font-bold tracking-wider uppercase font-mono-data px-4 py-2.5 rounded-xl transition-all shadow-[0_0_15px_rgba(6,182,212,0.1)] hover:scale-[1.02] active:scale-[0.98]"
          >
            <Globe className="w-4 h-4" />
            Operational Console
          </button>
          <button
            onClick={fetchStatsAndMap}
            className="flex items-center gap-2 bg-zinc-900/60 border border-zinc-800/80 hover:bg-zinc-800/85 text-xs font-bold tracking-wider uppercase font-mono-data px-4 py-2.5 rounded-xl transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <RefreshCw className={`w-4 h-4 ${isStatsLoading ? "animate-spin" : ""}`} />
            Refresh State
          </button>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 bg-cyber-rose/10 border border-cyber-rose/30 hover:bg-cyber-rose/20 text-cyber-rose text-xs font-bold tracking-wider uppercase font-mono-data px-4 py-2.5 rounded-xl transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 relative z-10">
        
        {/* 1. HEALTH AND INFRASTRUCTURE CHECKS */}
        <section className="lg:col-span-4 grid grid-cols-2 md:grid-cols-5 gap-4">
          {[
            { name: "Backend Core", status: health.backend, icon: Server },
            { name: "PostgreSQL", status: health.postgres, icon: Database },
            { name: "Redis Cache", status: health.redis, icon: Cpu },
            { name: "Elasticsearch", status: health.elasticsearch, icon: Network },
            { name: "Web Frontend", status: health.frontend, icon: Globe }
          ].map((srv) => (
            <div
              key={srv.name}
              className="glass-panel rounded-2xl p-4 flex items-center justify-between shadow-lg hover:border-indigo-500/30 hover:shadow-[0_0_15px_rgba(99,102,241,0.05)] transition-all duration-300"
            >
              <div className="flex items-center gap-3">
                <div className="p-2 bg-zinc-950/60 border border-zinc-800/80 rounded-xl text-zinc-400">
                  <srv.icon className="w-5 h-5 text-zinc-400" />
                </div>
                <div>
                  <h3 className="text-xs text-zinc-300 font-semibold">{srv.name}</h3>
                  <span className="text-[9px] font-semibold text-zinc-500 uppercase font-mono-data">Status</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {srv.status === "UP" ? (
                  <>
                    <span className="h-2.5 w-2.5 rounded-full bg-cyber-emerald shadow-[0_0_10px_rgba(16,185,129,0.8)] animate-pulse"></span>
                    <span className="text-[10px] text-cyber-emerald font-bold font-mono-data">ONLINE</span>
                  </>
                ) : (
                  <>
                    <span className="h-2.5 w-2.5 rounded-full bg-cyber-rose shadow-[0_0_10px_rgba(244,63,94,0.8)] animate-pulse"></span>
                    <span className="text-[10px] text-cyber-rose font-bold font-mono-data">DOWN</span>
                  </>
                )}
              </div>
            </div>
          ))}
        </section>

        {/* 2. LIVE DATA FUNNEL METRICS */}
        <section className="lg:col-span-4 space-y-4">
          <div className="glass-panel rounded-2xl p-4 flex flex-wrap items-center gap-3">
            <span className="text-[11px] font-mono-data text-zinc-400 uppercase tracking-wider">
              Analytics window
            </span>
            <input
              type="date"
              value={statsFrom}
              onChange={(e) => setStatsFrom(e.target.value)}
              aria-label="Analytics from date"
              className="bg-background border border-outline-variant rounded px-2 py-1.5 text-[12px] font-mono-data text-on-surface focus:outline-none focus:border-primary"
            />
            <span className="text-zinc-500 text-[11px] font-mono-data">→</span>
            <input
              type="date"
              value={statsTo}
              onChange={(e) => setStatsTo(e.target.value)}
              aria-label="Analytics to date"
              className="bg-background border border-outline-variant rounded px-2 py-1.5 text-[12px] font-mono-data text-on-surface focus:outline-none focus:border-primary"
            />
            <button
              onClick={() => {
                setStatsFrom("");
                setStatsTo("");
              }}
              className="text-[11px] font-mono-data text-zinc-400 hover:text-primary uppercase px-2 py-1 border border-outline-variant rounded"
            >
              All time
            </button>
            {stats.window && (stats.window.from || stats.window.to) ? (
              <span className="text-[10px] font-mono-data text-cyber-cyan">
                {stats.window.from ? new Date(stats.window.from).toLocaleDateString() : "start"}
                {" → "}
                {stats.window.to ? new Date(stats.window.to).toLocaleDateString() : "now"}
              </span>
            ) : null}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-6">
          {[
            {
              title: "Scraped Articles",
              count: stats.funnel.scraped,
              status: "SCRAPED",
              color: "text-cyber-amber bg-cyber-amber/10 border-cyber-amber/20",
              glowStyle: "hover:shadow-[0_0_20px_rgba(245,158,11,0.15)] hover:border-cyber-amber/30"
            },
            {
              title: "Vectorized Articles",
              count: stats.funnel.vectorized,
              status: "EMBEDDED",
              color: "text-cyber-cyan bg-cyber-cyan/10 border-cyber-cyan/20",
              glowStyle: "hover:shadow-[0_0_20px_rgba(6,182,212,0.15)] hover:border-cyber-cyan/30"
            },
            {
              title: "Clustered Articles",
              count: stats.funnel.clustered,
              status: "CLUSTERED",
              color: "text-cyber-indigo bg-cyber-indigo/10 border-cyber-indigo/20",
              glowStyle: "hover:shadow-[0_0_20px_rgba(99,102,241,0.15)] hover:border-cyber-indigo/30"
            },
            {
              title: "Enriched Events",
              count: stats.funnel.enriched,
              status: "PROCESSED",
              color: "text-cyber-emerald bg-cyber-emerald/10 border-cyber-emerald/20",
              glowStyle: "hover:shadow-[0_0_20px_rgba(16,185,129,0.15)] hover:border-cyber-emerald/30"
            }
          ].map((gauge) => (
            <div
              key={gauge.title}
              className={`glass-panel rounded-2xl p-5 shadow-lg relative overflow-hidden transition-all duration-300 hover:scale-[1.02] ${gauge.glowStyle}`}
            >
              <div className="flex justify-between items-start mb-2">
                <span className="text-xs text-zinc-400 font-semibold uppercase tracking-wider">{gauge.title}</span>
                <span className={`text-[10px] font-bold px-2.5 py-0.5 rounded-full border ${gauge.color} font-mono-data`}>
                  {gauge.status}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-4xl font-extrabold text-white tracking-tight bg-gradient-to-r from-white via-zinc-150 to-indigo-100 bg-clip-text text-transparent drop-shadow-[0_0_10px_rgba(255,255,255,0.05)]">
                  {gauge.count}
                </span>
                <span className="text-xs text-zinc-500 font-mono-data">items</span>
              </div>
              {/*
                The "Ingestion velocity" sparkline was removed rather than
                relabelled. It plotted invented numbers, and there is no
                recorded history to plot instead.
              */}
              <div className="flex justify-between items-center mt-3 pt-3 border-t border-indigo-950/40">
                <span className="text-[10px] text-zinc-500 font-mono-data">
                  Current total &mdash; no history recorded
                </span>
              </div>
            </div>
          ))}
          </div>
        </section>

        {/* 3. LEAFLET MAP SECTION */}
        <section className="lg:col-span-3 h-[500px] relative rounded-2xl overflow-hidden border border-indigo-950/50 shadow-2xl">
          <div className="absolute top-4 left-4 z-20 pointer-events-none">
            <div className="bg-[#0b0f19]/90 backdrop-blur-md border border-indigo-950/50 rounded-xl px-4 py-2 text-xs flex items-center gap-2 shadow-lg">
              <span className="h-2 w-2 rounded-full bg-cyber-cyan animate-pulse shadow-[0_0_8px_rgba(6,182,212,0.8)]"></span>
              <span className="text-white font-semibold font-mono-data uppercase tracking-wider">Event Cartography</span>
            </div>
          </div>
          <Map events={mapEvents} selectedEvent={selectedEvent} onSelectEvent={setSelectedEvent} />
        </section>

        {/* 4. ACTIVE ADMINISTRATIVE TRIGGER CONSOLE */}
        <section className="lg:col-span-1 glass-panel rounded-2xl p-5 flex flex-col justify-between shadow-lg h-[500px] border border-indigo-950/50 hover:border-cyber-indigo/35 transition-all duration-300">
          <div>
            <h2 className="text-lg font-bold text-white mb-2 flex items-center gap-2">
              <Cpu className="w-5 h-5 text-cyber-indigo animate-pulse" />
              Administrative Overrides
            </h2>
            <p className="text-xs text-zinc-400 mb-6 leading-relaxed">
              Manually trigger background pipeline services in our container stack. Executions return 202 Accepted instantly.
            </p>

            <div className="space-y-3">
              {[
                {
                  label: "Run Ingestion Scraper",
                  desc: "Pull new articles via BBC/CNN/Al-Jazeera RSS feeds.",
                  endpoint: "/api/v1/articles/sync",
                  glowColor: "group-hover:border-cyber-amber/40 hover:shadow-[0_0_15px_rgba(245,158,11,0.1)]",
                  activeColor: "group-hover:text-cyber-amber"
                },
                {
                  label: "Run Vector Inversion",
                  desc: "Compute offline Sentence-Transformer float embeddings.",
                  endpoint: "/api/v1/admin/embed/process",
                  glowColor: "group-hover:border-cyber-cyan/40 hover:shadow-[0_0_15px_rgba(6,182,212,0.1)]",
                  activeColor: "group-hover:text-cyber-cyan"
                },
                {
                  label: "Run Cluster Partitioning",
                  desc: "Cosine similarity grouping into event clusters.",
                  endpoint: "/api/v1/admin/cluster/process",
                  glowColor: "group-hover:border-cyber-indigo/40 hover:shadow-[0_0_15px_rgba(99,102,241,0.1)]",
                  activeColor: "group-hover:text-cyber-indigo"
                },
                {
                  label: "Run LLM Enrichment",
                  desc: "Synthesize clustered articles using Gemini.",
                  endpoint: "/api/v1/admin/llm/process",
                  glowColor: "group-hover:border-cyber-emerald/40 hover:shadow-[0_0_15px_rgba(16,185,129,0.1)]",
                  activeColor: "group-hover:text-cyber-emerald"
                }
              ].map((btn) => (
                <button
                  key={btn.label}
                  disabled={triggerStatus.running}
                  onClick={() => triggerWorker(btn.label, btn.endpoint)}
                  className={`w-full text-left bg-zinc-950/60 border border-zinc-850/80 hover:bg-zinc-900/60 disabled:opacity-50 p-3.5 rounded-xl transition-all duration-300 group flex items-start justify-between ${btn.glowColor}`}
                >
                  <div className="pr-4">
                    <h4 className={`text-xs font-bold text-white transition-colors ${btn.activeColor}`}>
                      {btn.label}
                    </h4>
                    <p className="text-[10px] text-zinc-500 mt-1 leading-normal">{btn.desc}</p>
                  </div>
                  <Play className="w-4 h-4 text-zinc-600 group-hover:text-cyber-cyan flex-shrink-0 transition-colors mt-0.5" />
                </button>
              ))}
            </div>
          </div>

          <div className="text-center text-[10px] text-zinc-500 border-t border-indigo-950/40 pt-4 mt-4 font-mono-data tracking-wider uppercase">
            Authorized admin override console.
          </div>
        </section>

        {/* 5. COMMENT MODERATION QUEUE */}
        <section className="lg:col-span-8 glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <MessageSquare className="w-5 h-5 text-cyber-cyan" />
                Comment Moderation
              </h2>
              <p className="text-[11px] text-zinc-500 mt-1">
                Hidden comments stay in the database and can be restored. They are
                excluded from the public thread until an admin un-hides them.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowHiddenOnly((v) => !v)}
                className={`px-3 py-2 rounded-lg border text-[10px] font-mono-data font-bold uppercase tracking-wider transition-all ${
                  showHiddenOnly
                    ? "bg-amber-950/40 border-amber-700/60 text-amber-300"
                    : "bg-zinc-950/60 border-zinc-800 text-zinc-400 hover:border-cyber-cyan/40"
                }`}
              >
                {showHiddenOnly ? "Hidden only" : "All comments"}
              </button>
              <button
                onClick={() => fetchModerationQueue()}
                disabled={modLoading}
                className="px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-950/60 text-zinc-400 text-[10px] font-mono-data font-bold uppercase tracking-wider hover:border-cyber-cyan/40 hover:text-cyber-cyan disabled:opacity-50"
              >
                {modLoading ? "Loading..." : "Refresh"}
              </button>
            </div>
          </div>

          {modError && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2.5">
              <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0 mt-0.5" />
              <p className="text-[12px] text-rose-200/80 leading-relaxed">{modError}</p>
            </div>
          )}

          {!modLoading && shownComments.length === 0 && !modError && (
            <p className="text-[12px] text-zinc-600 py-6 text-center font-mono-data">
              {showHiddenOnly ? "No hidden comments." : "No comments yet."}
            </p>
          )}

          <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
            {shownComments.map((c) => (
              <div
                key={c.id}
                className={`rounded-xl border px-3 py-2.5 transition-colors ${
                  c.is_hidden
                    ? "border-amber-900/50 bg-amber-950/20"
                    : "border-zinc-800/80 bg-zinc-950/40"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className="text-[11px] font-bold text-zinc-200">
                        {c.author || "Unknown author"}
                      </span>
                      {c.author_badge ? (
                        <span
                          title={c.author_badge.detail}
                          className={`text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded border ${
                            c.author_badge.tone === "amber"
                              ? "text-cyber-amber border-cyber-amber/30 bg-cyber-amber/10"
                              : "text-cyber-cyan border-cyber-cyan/30 bg-cyber-cyan/10"
                          }`}
                        >
                          {c.author_badge.label}
                        </span>
                      ) : c.author_role ? (
                        <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
                          {c.author_role}
                        </span>
                      ) : null}
                      {c.parent_id && (
                        <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">
                          Reply
                        </span>
                      )}
                      {c.reply_count > 0 && (
                        <span className="text-[9px] font-mono-data text-zinc-500">
                          {c.reply_count} {c.reply_count === 1 ? "reply" : "replies"}
                        </span>
                      )}
                      {c.is_hidden && (
                        <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-900/60">
                          Hidden
                        </span>
                      )}
                    </div>
                    <p className="text-[12px] text-zinc-400 leading-relaxed break-words">
                      {c.content}
                    </p>
                  </div>

                  <button
                    onClick={() => setCommentHidden(c.id, !c.is_hidden)}
                    disabled={busyCommentId === c.id}
                    className={`flex-shrink-0 px-3 py-1.5 rounded-lg border text-[10px] font-mono-data font-bold uppercase tracking-wider transition-all disabled:opacity-50 ${
                      c.is_hidden
                        ? "border-emerald-800/60 bg-emerald-950/30 text-emerald-400 hover:bg-emerald-950/50"
                        : "border-zinc-700 bg-zinc-900/60 text-zinc-400 hover:border-amber-700/60 hover:text-amber-400"
                    }`}
                  >
                    {busyCommentId === c.id
                      ? "..."
                      : c.is_hidden
                        ? "Restore"
                        : "Hide"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 6. ARTICLE MODERATION QUEUE */}
        <section className="lg:col-span-8 glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Newspaper className="w-5 h-5 text-cyber-cyan" />
                Article Moderation
              </h2>
              <p className="text-[11px] text-zinc-500 mt-1">
                Hidden articles are kept for audit and drop out of public event
                dossiers. Restoring one puts it back immediately.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowHiddenArticlesOnly((v) => !v)}
                className={`px-3 py-2 rounded-lg border text-[10px] font-mono-data font-bold uppercase tracking-wider transition-all ${
                  showHiddenArticlesOnly
                    ? "bg-amber-950/40 border-amber-700/60 text-amber-300"
                    : "bg-zinc-950/60 border-zinc-800 text-zinc-400 hover:border-cyber-cyan/40"
                }`}
              >
                {showHiddenArticlesOnly ? "Hidden only" : "All articles"}
              </button>
              <button
                onClick={() => fetchArticleModerationQueue()}
                disabled={modArticlesLoading}
                className="px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-950/60 text-zinc-400 text-[10px] font-mono-data font-bold uppercase tracking-wider hover:border-cyber-cyan/40 hover:text-cyber-cyan disabled:opacity-50"
              >
                {modArticlesLoading ? "Loading..." : "Refresh"}
              </button>
            </div>
          </div>

          {modArticlesError && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2.5">
              <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0 mt-0.5" />
              <p className="text-[12px] text-rose-200/80 leading-relaxed">{modArticlesError}</p>
            </div>
          )}

          {!modArticlesLoading && shownArticles.length === 0 && !modArticlesError && (
            <p className="text-[12px] text-zinc-600 py-6 text-center font-mono-data">
              {showHiddenArticlesOnly
                ? "No hidden articles."
                : "No articles yet. Articles appear here once the scraper ingests them."}
            </p>
          )}

          <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
            {shownArticles.map((a) => (
              <div
                key={a.id}
                className={`rounded-xl border px-3 py-2.5 transition-colors ${
                  a.is_hidden
                    ? "border-amber-900/50 bg-amber-950/20"
                    : "border-zinc-800/80 bg-zinc-950/40"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className="text-[11px] font-bold text-zinc-200 break-words">
                        {a.title}
                      </span>
                      {a.source_name && (
                        <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
                          {a.source_name}
                        </span>
                      )}
                      {a.processing_status && (
                        <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">
                          {a.processing_status}
                        </span>
                      )}
                      {a.is_hidden && (
                        <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-900/60">
                          Hidden
                        </span>
                      )}
                    </div>
                    <a
                      href={a.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] text-cyber-cyan/70 hover:text-cyber-cyan break-all"
                    >
                      {a.url}
                    </a>
                    {a.event_title && (
                      <p className="text-[10px] text-zinc-600 mt-1 font-mono-data">
                        Event: {a.event_title}
                      </p>
                    )}
                  </div>

                  <button
                    onClick={() => setArticleHidden(a.id, !a.is_hidden)}
                    disabled={busyArticleId === a.id}
                    className={`flex-shrink-0 px-3 py-1.5 rounded-lg border text-[10px] font-mono-data font-bold uppercase tracking-wider transition-all disabled:opacity-50 ${
                      a.is_hidden
                        ? "border-emerald-800/60 bg-emerald-950/30 text-emerald-400 hover:bg-emerald-950/50"
                        : "border-zinc-700 bg-zinc-900/60 text-zinc-400 hover:border-amber-700/60 hover:text-amber-400"
                    }`}
                  >
                    {busyArticleId === a.id
                      ? "..."
                      : a.is_hidden
                        ? "Restore"
                        : "Hide"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 7. USER MANAGEMENT — roles drive the comment expert badges */}
        <section className="lg:col-span-8 glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <UserCheck className="w-5 h-5 text-cyber-cyan" />
                User Management
              </h2>
              <p className="text-[11px] text-zinc-500 mt-1">
                Roles are never self-asserted: only an administrator can grant
                them here. <span className="text-cyber-cyan/80">Journalist</span> accounts
                carry an expert badge on their comments. Plans gate usage
                quotas and are assigned here too &mdash; no payment provider is
                wired, so there is no self-serve upgrade, and every change is
                audited.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="text"
                value={userFilter}
                onChange={(e) => setUserFilter(e.target.value)}
                placeholder="Filter by name or email"
                className="px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-950/60 text-zinc-300 text-[10px] font-mono-data placeholder:text-zinc-600 focus:border-cyber-cyan/40 focus:outline-none w-52"
              />
              <button
                onClick={() => fetchUsers()}
                disabled={usersLoading}
                className="px-3 py-2 rounded-lg border border-zinc-800 bg-zinc-950/60 text-zinc-400 text-[10px] font-mono-data font-bold uppercase tracking-wider hover:border-cyber-cyan/40 hover:text-cyber-cyan disabled:opacity-50"
              >
                {usersLoading ? "Loading..." : "Refresh"}
              </button>
            </div>
          </div>

          {usersError && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2.5">
              <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0 mt-0.5" />
              <p className="text-[12px] text-rose-200/80 leading-relaxed">{usersError}</p>
            </div>
          )}

          {!usersLoading && users.length === 0 && !usersError && (
            <p className="text-[12px] text-zinc-600 py-6 text-center font-mono-data">
              No users found.
            </p>
          )}

          <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
            {users
              .filter((u) =>
                !userFilter.trim() ||
                u.name.toLowerCase().includes(userFilter.toLowerCase()) ||
                u.email.toLowerCase().includes(userFilter.toLowerCase())
              )
              .map((u) => {
                const isSelf = selfUserId === u.id;
                const roleTone = USER_MGMT_ROLE_TONES[u.role] || "text-zinc-400 border-zinc-700 bg-zinc-800/60";
                const busy = busyUserId === u.id;
                return (
                  <div
                    key={u.id}
                    className={`rounded-xl border px-3 py-2.5 transition-colors ${
                      u.is_blocked
                        ? "border-amber-900/50 bg-amber-950/20"
                        : "border-zinc-800/80 bg-zinc-950/40"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          <span className="text-[11px] font-bold text-zinc-200">
                            {u.name}
                            {isSelf && <span className="text-zinc-500 font-normal"> (you)</span>}
                          </span>
                          <span className={`text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded border ${roleTone}`}>
                            {u.role}
                          </span>
                          {u.is_blocked && (
                            <span className="text-[9px] font-mono-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-900/60">
                              Blocked
                            </span>
                          )}
                        </div>
                        <p className="text-[10px] text-zinc-500 font-mono-data break-all">
                          {u.email} · {u.plan} plan
                          {u.created_at ? ` · joined ${new Date(u.created_at).toLocaleDateString()}` : ""}
                        </p>
                      </div>

                      <div className="flex items-center gap-2 flex-shrink-0">
                        <select
                          value={u.role}
                          disabled={busy || isSelf}
                          onChange={(e) => changeUserRole(u, e.target.value)}
                          className="px-2 py-1.5 rounded-lg border border-zinc-800 bg-zinc-950/70 text-zinc-300 text-[10px] font-mono-data font-bold disabled:opacity-40 focus:border-cyber-cyan/40 focus:outline-none"
                        >
                          {["AUTH_USER", "JOURNALIST", "ADMIN"].map((role) => (
                            <option key={role} value={role}>{role}</option>
                          ))}
                        </select>
                        <select
                          value={u.plan}
                          disabled={busy}
                          title="Assign a plan: gates usage quotas and is audited"
                          onChange={(e) => changeUserPlan(u, e.target.value)}
                          className="px-2 py-1.5 rounded-lg border border-zinc-800 bg-zinc-950/70 text-cyber-cyan text-[10px] font-mono-data font-bold disabled:opacity-40 focus:border-cyber-cyan/40 focus:outline-none"
                        >
                          {["FREE", "PRO", "ENTERPRISE"].map((plan) => (
                            <option key={plan} value={plan}>{plan}</option>
                          ))}
                        </select>
                        <button
                          onClick={() => toggleUserBlock(u)}
                          disabled={busy || isSelf}
                          className={`px-3 py-1.5 rounded-lg border text-[10px] font-mono-data font-bold uppercase tracking-wider transition-all disabled:opacity-40 ${
                            u.is_blocked
                              ? "border-emerald-800/60 bg-emerald-950/30 text-emerald-400 hover:bg-emerald-950/50"
                              : "border-zinc-700 bg-zinc-900/60 text-zinc-400 hover:border-amber-700/60 hover:text-amber-400"
                          }`}
                        >
                          {busy ? "..." : u.is_blocked ? "Unblock" : "Block"}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
          </div>
        </section>

        {/* 8. MEDIA SOURCE & SPECTRUM CLASSIFICATIONS */}
        <section className="lg:col-span-4 grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Chart A: Media Split Donut Chart (via Recharts) */}
          <div className="glass-panel rounded-2xl p-5 shadow-lg flex flex-col justify-between min-h-[320px] border border-indigo-950/50">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-300 mb-4 font-mono-data">Media Split Breakdown</h3>
              <div className="flex items-center justify-center h-44 relative">
                {isMounted && pieData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={pieData}
                        cx="50%"
                        cy="50%"
                        innerRadius={50}
                        outerRadius={70}
                        paddingAngle={4}
                        dataKey="value"
                      >
                        {pieData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={mediaColors[index % mediaColors.length]} />
                        ))}
                      </Pie>
                      <RechartsTooltip 
                        contentStyle={{ background: "#0b0f19", borderColor: "rgba(99,102,241,0.2)", color: "#d4e4fa", fontSize: "11px", borderRadius: "8px" }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="text-zinc-500 text-xs font-mono-data">No media data available</div>
                )}
                {/* Core Total overlay */}
                <div className="absolute text-center pointer-events-none">
                  <span className="text-[9px] text-zinc-500 block uppercase font-bold tracking-wider font-mono-data">Total</span>
                  <span className="text-2xl font-black text-white bg-gradient-to-r from-white to-zinc-400 bg-clip-text text-transparent">
                    {pieData.reduce((acc, curr) => acc + curr.value, 0)}
                  </span>
                </div>
              </div>
            </div>

            {/* Legend */}
            <div className="grid grid-cols-3 gap-2 text-center text-xs mt-4 pt-4 border-t border-indigo-950/40 font-body-sm">
              {pieData.map((item, index) => (
                <div key={item.name} className="flex flex-col items-center">
                  <span 
                    className="inline-block w-2.5 h-2.5 rounded-full mb-1 shadow-[0_0_8px_rgba(255,255,255,0.15)]" 
                    style={{ backgroundColor: mediaColors[index % mediaColors.length] }}
                  ></span>
                  <span className="text-zinc-400 text-[9px] truncate max-w-[80px] font-mono-data font-semibold">{item.name}</span>
                  <span className="font-bold text-white mt-0.5">{item.value}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Chart B: Topic Distribution Histogram (via Recharts) */}
          <div className="glass-panel rounded-2xl p-5 shadow-lg flex flex-col justify-between min-h-[320px] border border-indigo-950/50">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-300 mb-4 font-mono-data">Topic Category Distribution</h3>
              <div className="h-44 w-full">
                {isMounted && barData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={barData} layout="vertical" margin={{ left: -10, right: 10, top: 0, bottom: 0 }}>
                      <XAxis type="number" stroke="#909097" fontSize={9} hide />
                      <YAxis dataKey="name" type="category" stroke="#909097" fontSize={9} width={75} axisLine={false} tickLine={false} />
                      <RechartsTooltip 
                        contentStyle={{ background: "#0b0f19", borderColor: "rgba(99,102,241,0.2)", color: "#d4e4fa", fontSize: "11px", borderRadius: "8px" }}
                      />
                      <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                        {barData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill="url(#barGradient)" />
                        ))}
                      </Bar>
                      {/* Define gradient colors */}
                      <defs>
                        <linearGradient id="barGradient" x1="0" y1="0" x2="1" y2="0">
                          <stop offset="0%" stopColor="#6366f1" />
                          <stop offset="100%" stopColor="#06b6d4" />
                        </linearGradient>
                      </defs>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="text-zinc-500 text-xs flex items-center justify-center h-full font-mono-data">No topics classified yet</div>
                )}
              </div>
            </div>
            <div className="text-center text-[9px] text-zinc-500 border-t border-indigo-950/40 pt-4 mt-4 uppercase font-bold tracking-wider font-mono-data">
              Event Category Frequency breakdown
            </div>
          </div>

          {/* Chart C: Collective Ideological Bias Spectrum */}
          <div className="glass-panel rounded-2xl p-5 shadow-lg flex flex-col justify-between min-h-[320px] border border-indigo-950/50">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-300 mb-4 font-mono-data">Collective Ideological Lean</h3>
              
              <div className="space-y-6 py-6">
                {/* Horizontal Spectrum Gradient */}
                <div className="relative">
                  <div className="h-4 w-full rounded-full bg-gradient-to-r from-cyber-rose via-zinc-400 to-cyber-emerald border border-zinc-800/80"></div>
                  
                  {/* Slider cursor showing calculated average score */}
                  {biasPercentage !== null && (
                    <div
                      className="absolute -top-1.5 flex flex-col items-center transition-all duration-500"
                      style={{ left: `calc(${biasPercentage}% - 8px)` }}
                    >
                      <div className="h-7 w-4 bg-white border border-slate-900 rounded-md shadow-[0_0_12px_rgba(255,255,255,0.4)] flex items-center justify-center cursor-default">
                        <div className="w-1.5 h-1.5 rounded-full bg-cyber-indigo"></div>
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex justify-between text-[9px] text-zinc-400 font-bold uppercase px-1 font-mono-data">
                  <span>Left Lean</span>
                  <span>Center</span>
                  <span>Right Lean</span>
                </div>
              </div>

              {/* Summary of lean scores */}
              <div className="bg-zinc-950/60 border border-zinc-850/80 rounded-xl p-3 text-xs flex items-center justify-between">
                <div>
                  <span className="text-zinc-500 font-bold block text-[9px] uppercase font-mono-data">Aggregated Index</span>
                  <span className="font-extrabold text-white">
                    {biasScore !== null ? `${biasScore.toFixed(2)} / 5.0` : "Not rated"}
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-zinc-500 font-bold block text-[9px] uppercase font-mono-data">General Bias</span>
                  <span className="font-bold text-cyber-cyan shadow-sm">
                    {biasScore === null
                      ? "No classified events"
                      : biasScore < 2.2
                        ? "LEFT BIAS"
                        : biasScore < 2.8
                          ? "CENTER-LEFT"
                          : biasScore < 3.2
                            ? "CENTERED"
                            : biasScore < 3.8
                              ? "CENTER-RIGHT"
                              : "RIGHT BIAS"}
                  </span>
                </div>
              </div>
            </div>
            
            <div className="text-center text-[9px] text-zinc-500 border-t border-indigo-950/40 pt-4 mt-4 uppercase font-bold tracking-wider font-mono-data">
              Ideological spectrum weighted calculation
            </div>
          </div>
        </section>

      </div>

        {/* 8. EVENT DETAILS SLIDE OVER GLASS PANEL */}
      {selectedEvent && (
        <div className="fixed inset-y-0 right-0 w-full sm:w-[450px] glass-dossier shadow-2xl z-50 transition-all duration-300 p-6 flex flex-col justify-between border-l border-indigo-950/80">
          <div>
            <div className="flex justify-between items-start mb-6">
              <div>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-cyber-cyan/10 border border-cyber-cyan/30 text-cyber-cyan text-[10px] font-bold uppercase mb-2">
                  <Globe className="w-3.5 h-3.5" />
                  {selectedEvent.country}
                </span>
                <h3 className="text-xl font-extrabold text-white leading-snug">{selectedEvent.title}</h3>
              </div>
              <button
                onClick={() => setSelectedEvent(null)}
                className="text-zinc-400 hover:text-white bg-zinc-900 border border-zinc-800/80 p-2 rounded-xl transition-all"
              >
                ✕
              </button>
            </div>

            <div className="space-y-6">
              <div>
                <h4 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-2 font-mono-data">AI-Generated Intelligence Summary</h4>
                <div className="text-xs text-zinc-300 space-y-3 leading-relaxed whitespace-pre-line font-body-sm bg-zinc-950/40 border border-zinc-850/80 p-4 rounded-xl">
                  {selectedEvent.summary || "No intelligence summary generated yet for this event."}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 border-t border-indigo-950/40 pt-6 font-body-sm">
                <div>
                  <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider">Geographic Coordinates</h5>
                  <span className="text-xs text-white font-mono-data font-semibold">
                    {selectedEvent.latitude.toFixed(4)}, {selectedEvent.longitude.toFixed(4)}
                  </span>
                </div>
                <div>
                  <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider">Media Source Count</h5>
                  <span className="text-xs text-white font-semibold">
                    {selectedEvent.source_count} reporting publishers
                  </span>
                </div>
                <div>
                  <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider">Classification Topic</h5>
                  <span className="text-xs text-cyber-cyan font-bold uppercase font-mono-data">
                    {selectedEvent.topic}
                  </span>
                </div>
                <div>
                  <h5 className="text-[9px] font-bold text-zinc-500 uppercase tracking-wider">Importance Level</h5>
                  <span className="text-xs text-cyber-emerald font-bold">
                    {selectedEvent.importance_score.toFixed(1)} / 10.0
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="border-t border-indigo-950/40 pt-6">
            <button
              onClick={() => setSelectedEvent(null)}
              className="w-full py-3 bg-zinc-900/60 border border-zinc-850/80 hover:bg-zinc-800 text-xs font-bold transition-all text-center text-zinc-400 rounded-xl"
            >
              Close Detailed Panel
            </button>
          </div>
        </div>
      )}

        {/* 9. FULL-SCREEN SKELETON SYNTHESIS OVERLAY */}
      {triggerStatus.running && (
        <div className="fixed inset-0 bg-[#030712] z-[10000] flex flex-col antialiased relative overflow-hidden">
          {/* Animated Sweeping Scanning Line */}
          <div className="absolute inset-0 bg-gradient-to-b from-transparent via-cyber-cyan/10 to-transparent h-1/3 w-full animate-scan pointer-events-none z-10"></div>
          
          {/* Subtle Grid Layout */}
          <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.02)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.02)_1px,transparent_1px)] bg-[size:32px_32px] pointer-events-none z-0"></div>

          {/* Top Navbar Skeleton */}
          <nav className="flex justify-between items-center px-margin-desktop w-full h-16 bg-[#080c16]/80 border-b border-indigo-950/40 z-20">
            <div className="flex items-center gap-8">
              <span className="font-extrabold text-lg text-cyber-cyan drop-shadow-[0_0_10px_rgba(6,182,212,0.4)]">GlobeLens AI</span>
              <div className="hidden md:flex items-center gap-6">
                <span className="text-cyber-indigo font-bold border-b-2 border-cyber-indigo pb-1 text-xs uppercase tracking-wider font-mono-data">
                  Pipeline Ingestion Active
                </span>
              </div>
            </div>
            <div className="w-8 h-8 rounded-full bg-zinc-900 border border-indigo-950/40 animate-pulse"></div>
          </nav>

          {/* Skeleton Content */}
          <div className="flex-1 flex flex-col justify-center max-w-4xl mx-auto w-full px-6 py-10 relative z-20">
            <div className="space-y-6">
              {/* Dynamic Status card (Pulsing and Glassmorphic) */}
              <div className="glass-panel rounded-2xl p-6 border border-cyber-cyan/45 shadow-[0_0_30px_rgba(6,182,212,0.15)] relative overflow-hidden group">
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-indigo via-cyber-cyan to-cyber-emerald"></div>
                <div className="flex items-center gap-4 mb-4">
                  <Cpu className="w-8 h-8 text-cyber-cyan animate-spin" />
                  <div>
                    <h2 className="text-lg font-bold text-white font-mono-data">System Worker Override Active</h2>
                    <span className="text-xs text-cyber-indigo font-bold tracking-wider font-mono-data uppercase">{triggerStatus.name}</span>
                  </div>
                </div>
                <p className="text-xs text-zinc-300 font-mono-data mb-4 bg-zinc-950/85 p-3.5 border border-indigo-950/50 rounded-lg">
                  {triggerStatus.message}
                </p>

                {/* Progress bar container */}
                <div className="w-full bg-zinc-950 rounded-full h-4 border border-indigo-950/50 overflow-hidden relative shadow-[inset_0_1px_4px_rgba(0,0,0,0.6)]">
                  <div
                    className="bg-gradient-to-r from-cyber-indigo to-cyber-cyan h-full rounded-full transition-all duration-300 shadow-[0_0_10px_rgba(6,182,212,0.8)]"
                    style={{ width: `${triggerStatus.progress}%` }}
                  ></div>
                  <span className="absolute inset-0 flex items-center justify-center text-[10px] font-extrabold text-white font-mono-data select-none">
                    {triggerStatus.progress}% SYNTHESIZED
                  </span>
                </div>
              </div>

              {/* Shimmer skeleton lines */}
              <div className="space-y-3 pt-6 border-t border-indigo-950/40">
                <div className="w-full h-4 rounded bg-zinc-900/60 border border-zinc-850/60 relative overflow-hidden">
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-zinc-850/20 to-transparent -translate-x-full animate-[shimmer_2s_infinite]"></div>
                </div>
                <div className="w-11/12 h-4 rounded bg-zinc-900/60 border border-zinc-850/60 relative overflow-hidden">
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-zinc-850/20 to-transparent -translate-x-full animate-[shimmer_2s_infinite]"></div>
                </div>
                <div className="w-4/5 h-4 rounded bg-zinc-900/60 border border-zinc-850/60 relative overflow-hidden">
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-zinc-850/20 to-transparent -translate-x-full animate-[shimmer_2s_infinite]"></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bell, BellOff, Check, Globe, KeyRound, Link2, Mail, Settings as SettingsIcon } from "lucide-react";
import { MAP_TOPICS } from "../components/MapFilterPanel";
import {
  Device,
  PushConfig,
  disablePush,
  enablePush,
  fetchPushConfig,
  fetchPreferences,
  followTopic,
  getFollowing,
  getToken,
  listSubscriptions,
  Preferences,
  unregisterDevice,
  unfollowTopic,
} from "../../lib/push";
import {
  NewsletterConfig,
  NewsletterStatus,
  fetchNewsletterConfig,
  fetchNewsletterStatus,
  subscribeToNewsletter,
  unsubscribeFromNewsletter,
} from "../../lib/newsletter";
import {
  ApiKey,
  ApiKeyCreated,
  Webhook,
  createApiKey,
  deleteWebhook,
  listApiKeys,
  listWebhooks,
  registerWebhook,
  revokeApiKey,
} from "../../lib/integrations";
import {
  MyUsage,
  Subscription,
  fetchMyUsage,
  fetchSubscription,
} from "../../lib/billing";

export default function SettingsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  const [following, setFollowing] = useState<string[]>([]);
  const [topicsBusy, setTopicsBusy] = useState<string | null>(null);
  const [topicsError, setTopicsError] = useState<string | null>(null);

  const [devices, setDevices] = useState<Device[]>([]);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushNotice, setPushNotice] = useState<string | null>(null);

  const [prefs, setPrefs] = useState<Preferences | null>(null);

  const [nlConfig, setNlConfig] = useState<NewsletterConfig | null>(null);
  const [nlStatus, setNlStatus] = useState<NewsletterStatus | null>(null);
  const [nlBusy, setNlBusy] = useState(false);
  const [nlError, setNlError] = useState<string | null>(null);
  const [nlNotice, setNlNotice] = useState<string | null>(null);

  const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
  const [freshKey, setFreshKey] = useState<ApiKeyCreated | null>(null);
  const [keyName, setKeyName] = useState("");
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyNotice, setKeyNotice] = useState<string | null>(null);

  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [maxEndpoints, setMaxEndpoints] = useState(0);
  const [hookUrl, setHookUrl] = useState("");
  const [hookError, setHookError] = useState<string | null>(null);
  const [hookNotice, setHookNotice] = useState<string | null>(null);
  const [hookBusy, setHookBusy] = useState(false);

  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usage, setUsage] = useState<MyUsage | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);

  useEffect(() => {
    const t = getToken();
    setToken(t);
    fetchPushConfig().then(setConfig).catch((e) => setConfigError(e.message));
    fetchNewsletterConfig().then(setNlConfig).catch((e) => setNlError(e.message));
  }, []);

  const reloadSubscriptions = useCallback(async (t: string) => {
    try {
      setDevices(await listSubscriptions(t));
    } catch (err: any) {
      setPushNotice(err.message || "Could not load alert devices.");
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    getFollowing(token)
      .then(setFollowing)
      .catch((e) => setTopicsError(e.message));
    reloadSubscriptions(token);
    fetchPreferences(token)
      .then(setPrefs)
      .catch(() => undefined);
    fetchNewsletterStatus(token)
      .then(setNlStatus)
      .catch(() => undefined);
    refreshIntegrations(token).catch(() => undefined);
    Promise.all([fetchSubscription(token), fetchMyUsage(token)])
      .then(([sub, usg]) => {
        setSubscription(sub);
        setUsage(usg);
      })
      .catch((e) => setUsageError(e.message));
  }, [token, reloadSubscriptions]);

  const refreshIntegrations = useCallback(async (t: string) => {
    setApiKeys(await listApiKeys(t));
    const hooks = await listWebhooks(t);
    setWebhooks(hooks.items);
    setMaxEndpoints(hooks.max_endpoints);
  }, []);

  const handleCreateKey = async () => {
    if (!token || !keyName.trim()) return;
    setKeyError(null);
    setKeyNotice(null);
    try {
      const created = await createApiKey(token, keyName.trim());
      setFreshKey(created);
      setKeyName("");
      setApiKeys((prev) => [...prev.filter((k) => k.id !== created.id), created]);
      setKeyNotice(`Key "${created.name}" created.`);
    } catch (err: any) {
      setKeyError(err.message || "Could not create the API key.");
    }
  };

  const handleRevokeKey = async (id: string) => {
    if (!token) return;
    setKeyError(null);
    try {
      await revokeApiKey(token, id);
      setApiKeys((prev) => prev.map((k) => (k.id === id ? { ...k, revoked_at: new Date().toISOString(), is_active: false } : k)));
    } catch (err: any) {
      setKeyError(err.message || "Could not revoke the API key.");
    }
  };

  const handleAddWebhook = async () => {
    if (!token || !hookUrl.trim()) return;
    setHookBusy(true);
    setHookError(null);
    setHookNotice(null);
    try {
      const created = await registerWebhook(token, hookUrl.trim(), ["event.created"]);
      setWebhooks((prev) => [...prev, created]);
      setHookUrl("");
      setHookNotice(`Endpoint registered. Signing secret shown once: ${created.secret}`);
    } catch (err: any) {
      setHookError(err.message || "Could not register the endpoint.");
    } finally {
      setHookBusy(false);
    }
  };

  const handleRemoveWebhook = async (id: string) => {
    if (!token) return;
    setHookError(null);
    try {
      await deleteWebhook(token, id);
      setWebhooks((prev) => prev.map((w) => (w.id === id ? { ...w, is_active: false } : w)));
      setHookNotice("Endpoint deactivated.");
    } catch (err: any) {
      setHookError(err.message || "Could not deactivate the endpoint.");
    }
  };

  const handleFollowToggle = async (topic: string) => {
    if (!token) return;
    const active = following.includes(topic);
    setTopicsBusy(topic);
    setTopicsError(null);
    try {
      if (active) {
        await unfollowTopic(token, topic);
        setFollowing((prev) => prev.filter((t) => t !== topic));
      } else {
        await followTopic(token, topic);
        setFollowing((prev) => [...prev.filter((t) => t !== topic), topic]);
      }
    } catch (err: any) {
      setTopicsError(err.message || "Could not update topic alerts.");
    } finally {
      setTopicsBusy(null);
    }
  };

  const handleEnablePush = async () => {
    if (!token || !config) return;
    setPushBusy(true);
    setPushNotice(null);
    try {
      await enablePush(token, config);
      setPushNotice("Alerts enabled on this browser.");
      await reloadSubscriptions(token);
    } catch (err: any) {
      setPushNotice(err.message || "Could not enable alerts.");
    } finally {
      setPushBusy(false);
    }
  };

  const handleRemoveDevice = async (deviceId: string) => {
    if (!token) return;
    setPushBusy(true);
    setPushNotice(null);
    try {
      await unregisterDevice(token, deviceId);
      setDevices((prev) => prev.filter((d) => d.id !== deviceId));
      setPushNotice("Device removed.");
    } catch (err: any) {
      setPushNotice(err.message || "Could not remove the device.");
    } finally {
      setPushBusy(false);
    }
  };

  const handleDisableAll = async () => {
    if (!token) return;
    setPushBusy(true);
    setPushNotice(null);
    try {
      await disablePush(token);
      setDevices([]);
      setPushNotice("Alerts disabled everywhere.");
    } catch (err: any) {
      setPushNotice(err.message || "Could not disable alerts.");
    } finally {
      setPushBusy(false);
    }
  };

  const handleNewsletterToggle = async () => {
    if (!token) return;
    setNlBusy(true);
    setNlError(null);
    setNlNotice(null);
    try {
      if (nlStatus && nlStatus.subscribed) {
        await unsubscribeFromNewsletter(token);
        setNlStatus((prev) =>
          prev ? { ...prev, subscribed: false, unsubscribed_at: new Date().toISOString() } : prev
        );
        setNlNotice("Unsubscribed. Pending email sends were cancelled.");
      } else {
        setNlStatus(await subscribeToNewsletter(token));
        setNlNotice("Subscribed to the daily comprehensive brief.");
      }
    } catch (err: any) {
      setNlError(err.message || "Could not update the newsletter subscription.");
    } finally {
      setNlBusy(false);
    }
  };

  const signedOut = !token;

  return (
    <main className="bg-[#030712] text-zinc-150 min-h-screen selection:bg-cyber-indigo/30 selection:text-white font-body-md">
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.01)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.01)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none"></div>

      {/* TopNavBar */}
      <header className="flex justify-between items-center px-margin-desktop w-full h-16 bg-[#080c16]/80 backdrop-blur-md border-b border-indigo-950/40 z-50 relative">
        <div className="flex items-center gap-3">
          <Link href="/" className="inline-flex items-center gap-2 font-mono-data text-[10px] text-zinc-400 hover:text-cyber-cyan uppercase tracking-wider font-bold transition-colors">
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Operations
          </Link>
        </div>
        <div className="flex items-center gap-2 font-mono-data text-[10px] text-zinc-300 uppercase tracking-wider font-bold">
          <SettingsIcon className="w-3.5 h-3.5 text-cyber-cyan" />
          Alert Settings
        </div>
      </header>

      <div className="relative z-10 max-w-2xl mx-auto px-6 py-10 space-y-6">
        {/* Plan & usage */}
        <section className="glass-panel rounded-2xl p-6 border border-indigo-950/60 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-cyan to-cyber-indigo"></div>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-extrabold text-white tracking-tight">
                <KeyRound className="w-5 h-5 text-cyber-cyan" />
                Plan &amp; Usage
              </h2>
              <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-1">
                Monthly AI tooling allowance against your plan
              </p>
            </div>
          </div>

          {usageError ? (
            <p className="mt-4 p-3 rounded-xl bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs font-mono-data">
              {usageError}
            </p>
          ) : null}

          {signedOut ? (
            <div className="mt-5 p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60">
              <p className="text-sm text-zinc-300">
                Sign in to view your plan, monthly allowance and upgrade options.
              </p>
              <Link
                href="/login"
                className="inline-flex items-center gap-2 mt-3 px-4 py-2 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/25 border border-cyber-cyan/35 text-cyber-cyan font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors"
              >
                Sign in
              </Link>
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              <div className="flex items-center justify-between gap-3 p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60">
                <div>
                  <p className="text-sm text-zinc-300">
                    Current plan:{" "}
                    <span className="font-bold text-white">
                      {(subscription?.plan ?? usage?.plan ?? "FREE").toUpperCase()}
                    </span>
                  </p>
                  <p className="font-mono-data text-[10px] text-zinc-500 mt-0.5 uppercase tracking-wider">
                    {subscription?.has_payment_method ? "payment method on file" : "no payment method"}
                  </p>
                </div>
                <Link
                  href="/pricing"
                  className="shrink-0 px-4 py-2 rounded-lg bg-cyber-purple/15 hover:bg-cyber-purple/25 border border-cyber-purple/35 text-cyber-purple font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors"
                >
                  Change plan
                </Link>
              </div>

              {usage && Object.keys(usage.meters).length > 0 ? (
                <ul className="space-y-3">
                  {Object.entries(usage.meters).map(([meter, m]) => {
                    const pct = m.limit ? Math.min(100, Math.round((m.used / m.limit) * 100)) : 0;
                    return (
                      <li key={meter} className="p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60">
                        <div className="flex justify-between text-sm">
                          <span className="text-zinc-400 capitalize">{meter.replace(/_/g, " ")}</span>
                          <span className="text-zinc-200 font-semibold">
                            {m.used.toLocaleString()} /{" "}
                            {m.limit === null ? "unlimited" : m.limit.toLocaleString()}
                          </span>
                        </div>
                        <div className="mt-2 h-2 rounded-full bg-zinc-800 overflow-hidden">
                          <div
                            className={`h-full transition-all ${pct >= 100 ? "bg-cyber-rose" : "bg-cyber-cyan"}`}
                            style={{ width: `${m.limit === null ? 100 : pct}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-zinc-400">
                  No metered usage this cycle yet.
                </p>
              )}
            </div>
          )}
        </section>

        {/* Push device management */}
        <section className="glass-panel rounded-2xl p-6 border border-indigo-950/60 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-cyan to-cyber-indigo"></div>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-extrabold text-white tracking-tight">
                {config && config.enabled ? <Bell className="w-5 h-5 text-cyber-cyan" /> : <BellOff className="w-5 h-5 text-zinc-500" />}
                Browser Alerts
              </h2>
              <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-1">
                Digest briefs are pushed to registered devices
              </p>
            </div>
          </div>

          {configError ? (
            <p className="mt-4 p-3 rounded-xl bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs font-mono-data">
              {configError}
            </p>
          ) : null}

          {signedOut ? (
            <div className="mt-5 p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60">
              <p className="text-sm text-zinc-300">
                Alert management requires a signed-in session.
              </p>
              <Link
                href="/login"
                className="inline-flex items-center gap-2 mt-3 px-4 py-2 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/25 border border-cyber-cyan/35 text-cyber-cyan font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors"
              >
                <Globe className="w-3.5 h-3.5" />
                Sign in
              </Link>
            </div>
          ) : (
            <div className="mt-5 space-y-5">
              {config && !config.enabled ? (
                <div className="p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60">
                  <p className="text-sm text-zinc-300">
                    In-app alerts are not configured on this deployment, so no
                    push messages can be sent. Device registration is still
                    available and will work the moment the operator enables
                    push + VAPID keys.
                  </p>
                </div>
              ) : null}

              {devices.length === 0 ? (
                <div className="p-4 rounded-xl bg-surface-container/50 border border-outline-variant/40 text-sm text-zinc-300 flex items-center gap-3">
                  <Bell className="w-4 h-4 text-zinc-500" />
                  No devices registered for alerts yet.
                  <button
                    onClick={handleEnablePush}
                    disabled={pushBusy || (!!config && !config.enabled)}
                    className="ml-auto inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyber-indigo/15 hover:bg-cyber-indigo/25 border border-cyber-indigo/35 text-cyber-indigo font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Bell className="w-3.5 h-3.5" />
                    {pushBusy ? "Working…" : "Enable on this browser"}
                  </button>
                </div>
              ) : (
                <div className="space-y-3">
                  <ul className="space-y-2">
                    {devices.map((d) => (
                      <li
                        key={d.id}
                        className="flex items-center justify-between gap-3 p-3 rounded-xl bg-surface-container/50 border border-outline-variant/40"
                      >
                        <div className="min-w-0">
                          <p className="text-sm text-on-surface truncate">{d.endpoint}</p>
                          <p className="font-mono-data text-[10px] text-zinc-500 mt-0.5 uppercase tracking-wider">
                            {d.user_agent || "browser"} · {d.created_at ? new Date(d.created_at).toLocaleDateString() : "unknown"}
                          </p>
                        </div>
                        <button
                          onClick={() => handleRemoveDevice(d.id)}
                          disabled={pushBusy}
                          className="shrink-0 px-3 py-1.5 rounded-lg border border-outline-variant text-[10px] font-mono-data font-bold uppercase tracking-wider text-on-surface-variant hover:border-cyber-rose/50 hover:text-cyber-rose transition-colors disabled:opacity-50"
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                  <button
                    onClick={handleDisableAll}
                    disabled={pushBusy}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-outline-variant text-zinc-400 font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors hover:border-cyber-rose/50 hover:text-cyber-rose disabled:opacity-50"
                  >
                    <BellOff className="w-3.5 h-3.5" />
                    Disable everywhere
                  </button>
                </div>
              )}

              {pushNotice ? (
                <p className="text-[11px] font-mono-data text-zinc-400" role="status">
                  {pushNotice}
                </p>
              ) : null}
            </div>
          )}
        </section>

        {/* Topic following */}
        <section className="glass-panel rounded-2xl p-6 border border-indigo-950/60 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-indigo to-cyber-cyan"></div>
          <h2 className="text-lg font-extrabold text-white tracking-tight">Topic Alerts</h2>
          <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-1">
            Follow topics to narrow which digest briefs you receive
          </p>

          {signedOut ? (
            <p className="mt-5 text-sm text-zinc-400">
              Sign in to manage your followed topics. Without topics, you receive
              every brief; settings always narrow, never widen.
            </p>
          ) : (
            <div className="mt-5">
              <div className="flex flex-wrap gap-2">
                {MAP_TOPICS.map((t) => {
                  const active = following.includes(t.key);
                  return (
                    <button
                      key={t.key}
                      onClick={() => handleFollowToggle(t.key)}
                      disabled={topicsBusy === t.key}
                      className={`inline-flex items-center gap-2 px-3 py-2 rounded-full border text-xs font-mono-data font-bold uppercase tracking-wider transition-colors disabled:opacity-60 ${
                        active ? "border-cyber-cyan text-cyber-cyan" : "border-outline-variant text-zinc-400 hover:border-cyber-cyan/50"
                      }`}
                      style={active ? { background: t.bg } : undefined}
                      aria-pressed={active}
                    >
                      {topicsBusy === t.key ? (
                        <span className="w-3 h-3 inline-block rounded-full border border-current border-t-transparent animate-spin" />
                      ) : (
                        <Check className="w-3 h-3" style={{ opacity: active ? 1 : 0 }} />
                      )}
                      {t.label}
                    </button>
                  );
                })}
              </div>
              {prefs ? (
                <p className="font-mono-data text-[9px] text-zinc-500 mt-4 uppercase tracking-wider">
                  {prefs.preferred_topics.length === 0
                    ? "No topics followed: you will receive every brief."
                    : `${prefs.preferred_topics.length} topic${prefs.preferred_topics.length === 1 ? "" : "s"} followed: you only receive briefs covering one of them.`}
                </p>
              ) : null}
              {topicsError ? (
                <p className="text-[11px] font-mono-data text-cyber-rose mt-3">{topicsError}</p>
              ) : null}
            </div>
          )}
        </section>

        {/* Newsletter */}
        <section className="glass-panel rounded-2xl p-6 border border-indigo-950/60 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-cyan to-cyber-indigo"></div>
          <h2 className="flex items-center gap-2 text-lg font-extrabold text-white tracking-tight">
            <Mail className="w-5 h-5 text-cyber-cyan" />
            Daily Newsletter
          </h2>
          <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-1">
            Email delivery of the comprehensive brief
          </p>

          {nlConfig ? (
            <p className="font-mono-data text-[9px] text-zinc-500 mt-2 uppercase tracking-wider">
              {nlConfig.delivered_via === "smtp"
                ? `Delivered via SMTP each time a digest is generated`
                : `Log mode: sends are captured to the audit log, never emailed`}
            </p>
          ) : null}

          {nlError ? (
            <p className="mt-4 p-3 rounded-xl bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs font-mono-data">
              {nlError}
            </p>
          ) : null}

          {signedOut ? (
            <div className="mt-5 p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60">
              <p className="text-sm text-zinc-300">
                Newsletter subscription requires a signed-in session.
              </p>
              <Link
                href="/login"
                className="inline-flex items-center gap-2 mt-3 px-4 py-2 rounded-lg bg-cyber-cyan/15 hover:bg-cyber-cyan/25 border border-cyber-cyan/35 text-cyber-cyan font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors"
              >
                <Globe className="w-3.5 h-3.5" />
                Sign in
              </Link>
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              <div className="p-4 rounded-xl bg-zinc-950/60 border border-indigo-950/60 flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm text-zinc-300">
                    {nlStatus && nlStatus.subscribed
                      ? "You are subscribed to the daily comprehensive brief."
                      : "You are not subscribed to the daily comprehensive brief."}
                  </p>
                  {nlStatus && nlStatus.last_digest ? (
                    <p className="font-mono-data text-[10px] text-zinc-500 mt-1 uppercase tracking-wider">
                      Last brief: {nlStatus.last_digest.title}
                    </p>
                  ) : null}
                </div>
                <button
                  onClick={handleNewsletterToggle}
                  disabled={nlBusy}
                  className={`shrink-0 inline-flex items-center gap-2 px-4 py-2 rounded-lg border font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                    nlStatus && nlStatus.subscribed
                      ? "border-outline-variant text-zinc-400 hover:border-cyber-rose/50 hover:text-cyber-rose"
                      : "bg-cyber-indigo/15 hover:bg-cyber-indigo/25 border-cyber-indigo/35 text-cyber-indigo"
                  }`}
                >
                  <Mail className="w-3.5 h-3.5" />
                  {nlBusy
                    ? "Working…"
                    : nlStatus && nlStatus.subscribed
                      ? "Unsubscribe"
                      : "Subscribe"}
                </button>
              </div>
              {nlNotice ? (
                <p className="text-[11px] font-mono-data text-zinc-400" role="status">
                  {nlNotice}
                </p>
              ) : null}
            </div>
          )}
        </section>

        {/* API Keys + Webhooks */}
        <section className="glass-panel rounded-2xl p-6 border border-indigo-950/60 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-purple to-cyber-cyan"></div>
          <h2 className="flex items-center gap-2 text-lg font-extrabold text-white tracking-tight">
            <KeyRound className="w-5 h-5 text-cyber-purple" />
            API Keys &amp; Webhooks
          </h2>
          <p className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider mt-1">
            Machine credentials and signed event sinks
          </p>

          {signedOut ? (
            <p className="mt-5 text-sm text-zinc-400">
              Sign in to manage API keys and webhook endpoints.
            </p>
          ) : (
            <div className="mt-5 space-y-5">
              <div>
                <p className="text-sm text-zinc-300">API Keys</p>
                <p className="font-mono-data text-[9px] text-zinc-500 mt-1 uppercase tracking-wider">
                  Bearer credentials; the raw key is shown exactly once
                </p>
                <div className="mt-3 flex gap-2">
                  <input
                    value={keyName}
                    onChange={(e) => setKeyName(e.target.value)}
                    placeholder="Key name e.g. CI nightly export"
                    className="flex-1 px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-cyber-purple/50"
                  />
                  <button
                    onClick={handleCreateKey}
                    disabled={!keyName.trim()}
                    className="shrink-0 px-4 py-2 rounded-lg bg-cyber-purple/15 hover:bg-cyber-purple/25 border border-cyber-purple/35 text-cyber-purple font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Create
                  </button>
                </div>
                {freshKey ? (
                  <div className="mt-3 p-3 rounded-xl bg-zinc-950/70 border border-cyber-purple/30">
                    <p className="font-mono-data text-[9px] text-cyber-purple uppercase tracking-wider">
                      Raw key (copy it now — it is not stored)
                    </p>
                    <p className="mt-1 text-sm font-mono-data text-cyber-cyan break-all">{freshKey.raw_key}</p>
                    <p className="mt-2 text-[10px] font-mono-data text-zinc-400">{freshKey.warning}</p>
                  </div>
                ) : null}
                {apiKeys.length > 0 ? (
                  <ul className="mt-3 space-y-2">
                    {apiKeys.map((k) => (
                      <li
                        key={k.id}
                        className="flex items-center justify-between gap-3 p-3 rounded-xl bg-surface-container/50 border border-outline-variant/40"
                      >
                        <div className="min-w-0">
                          <p className="text-sm text-on-surface truncate">{k.name}</p>
                          <p className="font-mono-data text-[10px] text-zinc-500 mt-0.5 uppercase tracking-wider">
                            gl_live_{k.prefix}… · {k.scopes.join(", ")} ·{" "}
                            {k.is_active ? "active" : "revoked"}
                          </p>
                        </div>
                        {k.is_active ? (
                          <button
                            onClick={() => handleRevokeKey(k.id)}
                            className="shrink-0 px-3 py-1.5 rounded-lg border border-outline-variant text-[10px] font-mono-data font-bold uppercase tracking-wider text-on-surface-variant hover:border-cyber-rose/50 hover:text-cyber-rose transition-colors"
                          >
                            Revoke
                          </button>
                        ) : (
                          <span className="shrink-0 px-3 py-1.5 rounded-lg border border-outline-variant text-[10px] font-mono-data font-bold uppercase tracking-wider text-zinc-500">
                            Revoked
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-3 text-xs text-zinc-400">
                    No API keys yet. Create one to call the read endpoints programmatically.
                  </p>
                )}
              </div>

              <div className="border-t border-indigo-950/60 pt-5">
                <p className="flex items-center gap-2 text-sm text-zinc-300">
                  <Link2 className="w-4 h-4 text-cyber-purple" />
                  Webhook Endpoints
                </p>
                <p className="font-mono-data text-[9px] text-zinc-500 mt-1 uppercase tracking-wider">
                  POST /event.created payloads, HMAC-SHA256 signed · {webhooks.length}/{maxEndpoints} used
                </p>
                <div className="mt-3 flex gap-2">
                  <input
                    value={hookUrl}
                    onChange={(e) => setHookUrl(e.target.value)}
                    placeholder="https://your-service.example/hook"
                    className="flex-1 px-3 py-2 rounded-lg bg-zinc-950/60 border border-indigo-950/60 text-sm text-on-surface placeholder:text-zinc-600 focus:outline-none focus:border-cyber-purple/50"
                  />
                  <button
                    onClick={handleAddWebhook}
                    disabled={hookBusy || !hookUrl.trim() || webhooks.length >= maxEndpoints}
                    className="shrink-0 px-4 py-2 rounded-lg bg-cyber-purple/15 hover:bg-cyber-purple/25 border border-cyber-purple/35 text-cyber-purple font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {hookBusy ? "Working…" : "Add"}
                  </button>
                </div>
                {webhooks.length > 0 ? (
                  <ul className="mt-3 space-y-2">
                    {webhooks.map((w) => (
                      <li
                        key={w.id}
                        className="flex items-start justify-between gap-3 p-3 rounded-xl bg-surface-container/50 border border-outline-variant/40"
                      >
                        <div className="min-w-0">
                          <p className="text-sm text-on-surface break-all">{w.url}</p>
                          <p className="font-mono-data text-[10px] text-zinc-500 mt-0.5 uppercase tracking-wider">
                            {w.event_types.length ? w.event_types.join(", ") : "all events"} ·{" "}
                            {w.is_active ? "active" : "deactivated"}
                          </p>
                          {w.deliveries[0] ? (
                            <p className="font-mono-data text-[9px] text-zinc-500 mt-0.5 uppercase tracking-wider">
                              last attempt: {w.deliveries[0].status}
                              {w.deliveries[0].last_error ? ` — ${w.deliveries[0].last_error}` : ""}
                            </p>
                          ) : null}
                        </div>
                        {w.is_active ? (
                          <button
                            onClick={() => handleRemoveWebhook(w.id)}
                            className="shrink-0 px-3 py-1.5 rounded-lg border border-outline-variant text-[10px] font-mono-data font-bold uppercase tracking-wider text-on-surface-variant hover:border-cyber-rose/50 hover:text-cyber-rose transition-colors"
                          >
                            Remove
                          </button>
                        ) : (
                          <span className="shrink-0 px-3 py-1.5 rounded-lg border border-outline-variant text-[10px] font-mono-data font-bold uppercase tracking-wider text-zinc-500">
                            Off
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-3 text-xs text-zinc-400">
                    No endpoints registered. Every processed event will be POSTed to endpoints you add.
                  </p>
                )}
              </div>

              {keyError || hookError ? (
                <p className="text-[11px] font-mono-data text-cyber-rose">
                  {keyError || hookError}
                </p>
              ) : null}
              {keyNotice || hookNotice ? (
                <p className="text-[11px] font-mono-data text-zinc-400" role="status">
                  {keyNotice || hookNotice}
                </p>
              ) : null}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
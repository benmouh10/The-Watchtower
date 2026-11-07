"use client";

import React, { useState, useEffect } from "react";
import { Check, Info, ShieldAlert, Loader2 } from "lucide-react";
import GlobelensLogo from "../components/GlobelensLogo";
import {
  MyUsage,
  Subscription,
  createCheckout,
  fetchMyUsage,
  fetchSubscription,
} from "../../lib/billing";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Tier {
  plan: string;
  name: string;
  tagline: string;
  features: string[];
  quotas: Record<string, number | null>;
  quota_labels: Record<string, string>;
}

interface PlansResponse {
  tiers: Tier[];
  enforcement: { enabled: boolean; note: string };
  billing: { stripe_configured: boolean; self_serve_upgrade: boolean; note: string };
}

const displayOrder = ["free", "pro", "enterprise"];

function quotaText(value: number | null): string {
  if (value === null) return "Unlimited";
  return value.toLocaleString();
}

export default function PricingPage() {
  const [plans, setPlans] = useState<PlansResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(false);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usage, setUsage] = useState<MyUsage | null>(null);
  const [checkoutBusy, setCheckoutBusy] = useState<string | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/plans`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Could not load plans");
        if (!cancelled) setPlans(data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load plans");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let active = true;
    try {
      const token = localStorage.getItem("admin_token");
      if (!token) return;
      setSignedIn(true);
      Promise.all([fetchSubscription(token), fetchMyUsage(token)])
        .then(([sub, mysub]) => {
          if (!active) return;
          setSubscription(sub);
          setUsage(mysub);
        })
        .catch(() => undefined);
    } catch {
      /* storage unavailable */
    }
    return () => {
      active = false;
    };
  }, []);

  const handleUpgrade = async (planKey: string) => {
    if (!subscription || subscription.plan === planKey.toUpperCase()) return;
    setCheckoutBusy(planKey);
    setCheckoutError(null);
    try {
      const token = localStorage.getItem("admin_token");
      if (!token) {
        window.location.href = "/login";
        return;
      }
      const result = await createCheckout(token, planKey);
      if (result.url) {
        window.location.href = result.url;
      } else {
        setCheckoutError("Checkout started but no redirect URL was returned.");
      }
    } catch (e: any) {
      setCheckoutError(e.message || "Could not start checkout.");
    } finally {
      setCheckoutBusy(null);
    }
  };

  const currentPlan = subscription?.plan ?? (usage?.plan ?? null);

  return (
    <main className="min-h-screen bg-background text-on-surface">
      {/* Header */}
      <header className="border-b border-outline-variant bg-[#0a0d16]/80 backdrop-blur-md sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <a href="/" className="flex items-center gap-3">
            <GlobelensLogo size="2rem" hideWordmark />
            <div>
              <div className="font-display text-lg font-bold tracking-tight text-white">
                GlobeLens AI
              </div>
              <div className="text-[10px] font-mono-data text-cyber-cyan uppercase tracking-widest">
                Pricing
              </div>
            </div>
          </a>
          <nav className="flex items-center gap-4 text-sm">
            <a href="/login" className="text-zinc-400 hover:text-white transition-colors">
              Sign in
            </a>
            <a
              href="/login"
              className="px-4 py-2 rounded-lg bg-gradient-to-r from-cyan-500 to-emerald-400 text-black font-semibold text-sm hover:opacity-90 transition-opacity"
            >
              Get started free
            </a>
          </nav>
        </div>
      </header>

      <section className="max-w-6xl mx-auto px-6 py-16">
        <div className="text-center mb-12">
          <h1 className="font-display text-4xl md:text-5xl font-extrabold tracking-tight text-white">
            Plans that scale with your{" "}
            <span className="bg-gradient-to-r from-cyan-400 to-emerald-300 bg-clip-text text-transparent">
              intelligence workflow
            </span>
          </h1>
          <p className="mt-4 text-zinc-400 max-w-2xl mx-auto">
            GlobeLens turns a noisy world into synthesized, cited dossiers. Start
            free and upgrade when you outgrow your monthly AI tooling allowance.
          </p>
        </div>

        {error ? (
          <div className="max-w-md mx-auto text-center text-red-400 border border-red-500/30 rounded-lg px-4 py-6">
            {error}
          </div>
        ) : !plans ? (
          <div className="flex justify-center py-20">
            <Loader2 className="w-8 h-8 animate-spin text-cyber-cyan" />
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {displayOrder.map((planKey, idx) => {
                const tier = plans.tiers.find((t) => t.plan === planKey)!;
                const featured = planKey === "pro";
                return (
                  <div
                    key={tier.plan}
                    className={`relative rounded-2xl border p-6 flex flex-col ${
                      featured
                        ? "border-cyan-400/50 bg-gradient-to-b from-cyan-500/10 to-transparent shadow-[0_0_40px_rgba(6,182,212,0.15)]"
                        : "border-outline-variant bg-[#0d1120]/70"
                    }`}
                  >
                    {featured && (
                      <span className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 text-[10px] font-mono-data uppercase tracking-widest bg-gradient-to-r from-cyan-500 to-emerald-400 text-black rounded-full">
                        Most popular
                      </span>
                    )}
                    <h2 className="font-display text-xl font-bold text-white">{tier.name}</h2>
                    <p className="mt-1 text-sm text-zinc-400 min-h-[40px]">{tier.tagline}</p>

                    <ul className="mt-6 space-y-2.5 text-sm flex-1">
                      {tier.features.map((f) => (
                        <li key={f} className="flex items-start gap-2.5">
                          <Check className="w-4 h-4 mt-0.5 shrink-0 text-emerald-400" />
                          <span className="text-zinc-300">{f}</span>
                        </li>
                      ))}
                    </ul>

                    <div className="mt-6 pt-4 border-t border-outline-variant">
                      <div className="text-[11px] font-mono-data text-zinc-500 uppercase tracking-wider">
                        Monthly AI tooling allowance
                      </div>
                      <dl className="mt-2 space-y-1 text-sm">
                        {Object.entries(tier.quotas).map(([meter, value]) => (
                          <div key={meter} className="flex justify-between">
                            <dt className="text-zinc-500">{tier.quota_labels[meter] || meter}</dt>
                            <dd className="text-zinc-200 font-semibold">{quotaText(value)}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>

                    {currentPlan === tier.plan.toUpperCase() ? (
                      <div className="mt-5 px-4 py-2.5 rounded-lg border border-emerald-400/30 bg-emerald-400/10 text-emerald-300 text-sm font-medium text-center">
                        Your current plan
                      </div>
                    ) : signedIn ? (
                      <button
                        onClick={() => handleUpgrade(tier.plan)}
                        disabled={checkoutBusy !== null}
                        className={`mt-5 px-4 py-2.5 rounded-lg text-sm font-semibold text-center transition-colors disabled:opacity-50 ${
                          featured
                            ? "bg-gradient-to-r from-cyan-500 to-emerald-400 text-black hover:opacity-90"
                            : "bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
                        }`}
                      >
                        {checkoutBusy === tier.plan ? "Opening checkout…" : "Upgrade"}
                      </button>
                    ) : (
                      <a
                        href="/login"
                        className="mt-5 px-4 py-2.5 rounded-lg text-sm font-semibold text-center transition-colors bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
                      >
                        Sign in to upgrade
                      </a>
                    )}
                  </div>
                );
              })}
            </div>

            {checkoutError && (
              <div className="mt-6 max-w-xl mx-auto text-center text-sm text-red-400 border border-red-500/30 rounded-lg px-4 py-3">
                {checkoutError}
              </div>
            )}

            <div className="mt-8 flex flex-col sm:flex-row gap-4 items-center justify-center">
              <div className="text-sm text-zinc-500 max-w-xl flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 shrink-0 text-amber-400" />
                <span>{plans.enforcement.note}</span>
              </div>
              <div className="text-sm text-zinc-500 max-w-xl flex items-center gap-2">
                <Info className="w-4 h-4 shrink-0 text-cyber-cyan" />
                <span>{plans.billing.note}</span>
              </div>
            </div>
          </>
        )}
      </section>

      <footer className="border-t border-outline-variant py-8 text-center text-xs text-zinc-600">
        GlobeLens AI — synthesized intelligence, honestly sourced. Quotas shown are the
        deployment&apos;s configured allowances.
      </footer>
    </main>
  );
}
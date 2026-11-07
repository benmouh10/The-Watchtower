"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Check, Mail, Settings as SettingsIcon, X } from "lucide-react";
import { unsubscribeWithToken } from "../../../lib/newsletter";

type UnsubState = "working" | "ok" | "error";

function UnsubscribeBody() {
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<UnsubState>("working");
  const [detail, setDetail] = useState<string>("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setDetail(
        "This unsubscribe link is missing its token. Sign in and toggle the digest subscription in Settings instead."
      );
      return;
    }
    unsubscribeWithToken(token)
      .then((res) => {
        setState("ok");
        setDetail(
          res.reason === "already_unsubscribed"
            ? "You were already unsubscribed — no emails are being sent to this address."
            : "You have been unsubscribed from the daily comprehensive brief. Pending emails were cancelled."
        );
      })
      .catch((err: any) => {
        setState("error");
        setDetail(err.message || "This unsubscribe link is invalid or has expired.");
      });
  }, [token]);

  const loading = state === "working";
  const failed = state === "error";

  return (
    <div className="relative z-10 max-w-lg mx-auto px-6 py-16">
      <div
        className={`glass-panel rounded-2xl p-8 border shadow-2xl text-center ${
          failed ? "border-cyber-rose/40" : "border-indigo-950/60"
        }`}
      >
        <div
          className={`mx-auto w-14 h-14 rounded-full flex items-center justify-center ${
            failed ? "bg-cyber-rose/10 text-cyber-rose" : "bg-cyber-cyan/15 text-cyber-cyan"
          }`}
        >
          {loading ? (
            <span className="w-5 h-5 inline-block rounded-full border-2 border-current border-t-transparent animate-spin" />
          ) : failed ? (
            <X className="w-6 h-6" />
          ) : (
            <Check className="w-6 h-6" />
          )}
        </div>
        <h1 className="mt-5 text-xl font-extrabold text-white tracking-tight">
          {loading ? "Working…" : failed ? "Unsubscribe link not valid" : "Unsubscribed"}
        </h1>
        <p className="mt-3 text-sm text-zinc-300 leading-relaxed">{detail}</p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link
            href="/settings"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyber-indigo/15 hover:bg-cyber-indigo/25 border border-cyber-indigo/35 text-cyber-indigo font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors"
          >
            <SettingsIcon className="w-3.5 h-3.5" />
            Alert settings
          </Link>
          <Link
            href="/"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-outline-variant text-zinc-400 font-mono-data text-[10px] font-bold tracking-widest uppercase transition-colors hover:border-cyber-cyan/50 hover:text-cyber-cyan"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Operations
          </Link>
        </div>
      </div>
      <p className="mt-6 text-center font-mono-data text-[10px] text-zinc-500 uppercase tracking-wider">
        GlobeLens AI comprehensive brief
      </p>
    </div>
  );
}

export default function NewsletterUnsubscribePage() {
  return (
    <main className="bg-[#030712] text-zinc-150 min-h-screen selection:bg-cyber-indigo/30 selection:text-white font-body-md">
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.01)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.01)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none"></div>
      <header className="flex justify-between items-center px-margin-desktop w-full h-16 bg-[#080c16]/80 backdrop-blur-md border-b border-indigo-950/40 z-50 relative">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="inline-flex items-center gap-2 font-mono-data text-[10px] text-zinc-400 hover:text-cyber-cyan uppercase tracking-wider font-bold transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Operations
          </Link>
        </div>
        <div className="flex items-center gap-2 font-mono-data text-[10px] text-zinc-300 uppercase tracking-wider font-bold">
          <Mail className="w-3.5 h-3.5 text-cyber-cyan" />
          Newsletter
        </div>
      </header>
      <Suspense
        fallback={
          <div className="relative z-10 max-w-lg mx-auto px-6 py-16 text-zinc-400 text-sm">
            Loading…
          </div>
        }
      >
        <UnsubscribeBody />
      </Suspense>
    </main>
  );
}
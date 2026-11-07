"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { 
  Globe, 
  Link as LinkIcon, 
  CheckCircle, 
  XCircle, 
  AlertTriangle,
  History,
  RefreshCw,
  Award,
  BookOpen,
  CornerDownRight,
  UserCheck
} from "lucide-react";

import GlobelensLogo from "../components/GlobelensLogo";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";


interface ClaimItem {
  text: string;
  status: string; // "Corroborated" | "Disputed" | "Unverified"
}

interface HistoricalMatchItem {
  title: string;
  last_active: string;
  match_percentage: number;
}

interface FactCheckResult {
  id: string;
  input: string;
  result: string;
  created_at: string;
  explanation: {
    credibility_score: number;
    trust_risks: string[];
    independent_cross_references: number;
    claims: ClaimItem[];
    historical_matches: HistoricalMatchItem[];
    summary?: string;
  };
}

export default function FactCheckerPage() {
  const router = useRouter();
  const [urlInput, setUrlInput] = useState("https://example-news-source.com/article-123");
  const [loading, setLoading] = useState(false);
  
  // Active report being displayed
  const [currentReport, setCurrentReport] = useState<FactCheckResult | null>(null);
  
  // Analysis History list
  const [history, setHistory] = useState<FactCheckResult[]>([]);
  const [userProfile, setUserProfile] = useState<any>(null);
  const [authError, setAuthError] = useState(false);
const [submitError, setSubmitError] = useState<string | null>(null);

  // Authenticate user & load history
  useEffect(() => {
    const token = localStorage.getItem("admin_token");
    if (!token) {
      setAuthError(true);
      router.push("/login");
      return;
    }

    const loadUserData = async () => {
      try {
        // Fetch current user details
        const meResponse = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        
        if (!meResponse.ok) {
          throw new Error("Authentication failed");
        }
        
        const profile = await meResponse.json();
        setUserProfile(profile);

        // Fetch user's fact check history
        const historyResponse = await fetch(`${API_BASE_URL}/api/v1/fact-check/users/${profile.id}`, {
          headers: { Authorization: `Bearer ${token}` }
        });

        if (historyResponse.ok) {
          const historyData = await historyResponse.json();
          setHistory(historyData.requests || []);
          if (historyData.requests && historyData.requests.length > 0) {
            setCurrentReport(historyData.requests[0]);
          }
        }
      } catch (err) {
        console.error("Auth error in fact-checker", err);
        setAuthError(true);
        router.push("/login");
      }
    };

    loadUserData();
  }, [router]);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!urlInput.trim()) return;

    setLoading(true);
    setSubmitError(null);
    const token = localStorage.getItem("admin_token");
    
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/fact-check`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ input_text_url: urlInput })
      });

      if (response.ok) {
        const newReport = await response.json();
        setCurrentReport(newReport);
        
        // Refresh history
        if (userProfile) {
          const historyResponse = await fetch(`${API_BASE_URL}/api/v1/fact-check/users/${userProfile.id}`, {
            headers: { Authorization: `Bearer ${token}` }
          });
          if (historyResponse.ok) {
            const historyData = await historyResponse.json();
            setHistory(historyData.requests || []);
          }
        }
      } else {
        // 422 means the page could not be read, 503 means the model is down.
        // Neither is a crash, and the backend explains what to do instead, so
        // it is shown inline rather than as a blocking alert.
        const errData = await response.json().catch(() => ({}));
        setSubmitError(
          errData.detail ||
            (response.status === 422
              ? "That page could not be read, so there was nothing to analyse. Paste the article text instead."
              : `Analysis failed (HTTP ${response.status}).`)
        );
      }
    } catch (err) {
      console.error("Analysis request failed", err);
      setSubmitError("Failed to connect to the analysis pipeline.");
    } finally {
      setLoading(false);
    }
  };

  if (authError || !userProfile) {
    return (
      <main className="min-h-screen bg-[#030712] flex flex-col items-center justify-center p-4 relative overflow-hidden">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-cyber-indigo/5 rounded-full blur-[120px] pointer-events-none"></div>
        <div className="z-10 text-center">
          <RefreshCw className="w-12 h-12 text-cyber-indigo animate-spin mx-auto mb-4" />
          <div className="text-zinc-400 font-mono-data text-xs tracking-widest uppercase">Authenticating clearance credentials...</div>
        </div>
      </main>
    );
  }

  return (
    <div className="bg-[#030712] text-zinc-150 min-h-screen flex flex-col font-body-md overflow-x-hidden selection:bg-cyber-indigo/30 selection:text-white relative">
      {/* Background gradients */}
      <div className="absolute top-0 right-0 w-[600px] h-[600px] bg-cyber-cyan/5 rounded-full blur-[150px] pointer-events-none"></div>
      <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-cyber-indigo/5 rounded-full blur-[150px] pointer-events-none"></div>
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.02)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.02)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none z-0"></div>

      {/* TopNavBar */}
      <nav className="flex justify-between items-center px-margin-desktop w-full h-16 sticky top-0 z-50 bg-[#080c16]/80 backdrop-blur-lg border-b border-indigo-950/40 flex-shrink-0">
        <div className="flex items-center gap-stack-lg">
          <div className="flex items-center gap-2 cursor-pointer" onClick={() => router.push("/")}>
            <GlobelensLogo size="36px" hideWordmark />
            <span className="font-extrabold text-headline-lg bg-gradient-to-r from-cyber-cyan via-indigo-300 to-cyber-indigo bg-clip-text text-transparent drop-shadow-[0_0_15px_rgba(6,182,212,0.25)] tracking-tight">
              GlobeLens AI
            </span>
          </div>


          <div className="hidden md:flex gap-stack-lg items-center ml-8 pt-1">
            <a className="font-body-md text-body-md text-on-surface-variant font-medium hover:text-cyber-cyan transition-colors" href="/?view=standard">Standard</a>
            <a className="font-body-md text-body-md text-on-surface-variant font-medium hover:text-cyber-cyan transition-colors" href="/?view=map">Map</a>
            <a className="font-body-md text-body-md text-on-surface-variant font-medium hover:text-cyber-cyan transition-colors" href="/admin/dashboard">Admin</a>
            <a className="font-body-md text-body-md text-cyber-cyan font-bold border-b-2 border-cyber-cyan pb-1" href="/fact-checker">Fact Checker</a>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="w-8 h-8 rounded-full bg-zinc-900 border border-indigo-950/50 flex items-center justify-center font-mono-data text-xs text-cyber-cyan font-bold shadow-[0_0_10px_rgba(6,182,212,0.15)]">
            {userProfile.name.slice(0, 2).toUpperCase()}
          </div>
        </div>
      </nav>

      {/* Main Content Layout */}
      <div className="flex-1 flex w-full max-w-container-max-width mx-auto relative px-margin-mobile md:px-margin-desktop py-stack-lg z-10 gap-6">
        
        {/* Left Column (Fact Checker Utilities) */}
        <main className="flex-grow w-full lg:w-3/4 pb-32 flex flex-col gap-6">
          <header className="mb-2">
            <div className="flex items-center gap-2 text-cyber-cyan font-bold tracking-wider text-xs uppercase mb-1 font-mono-data">
              <CornerDownRight className="w-4 h-4 animate-pulse" />
              <span>Intelligence Analysis Toolkit</span>
            </div>
            <h1 className="font-display-lg text-headline-xl md:text-3xl font-extrabold text-white mb-2 bg-gradient-to-r from-white via-zinc-200 to-indigo-300 bg-clip-text text-transparent">
              Fact-Checker Utility
            </h1>
            <p className="text-zinc-400 text-xs max-w-3xl leading-relaxed">
              Analyze cross-reference metrics, trust risks, and matching historical topics for any source URL or textual narrative.
            </p>
          </header>

          {/* URL / Text Input Area */}
          <section className="glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg relative overflow-hidden">
            <form onSubmit={handleAnalyze} className="flex flex-col md:flex-row gap-4 items-stretch md:items-center">
              <div className="flex-1 bg-zinc-950/60 border border-zinc-800/80 rounded-xl flex items-center px-4 py-3 focus-within:border-cyber-indigo/60 transition-all duration-300 focus-within:shadow-[0_0_15px_rgba(99,102,241,0.15)]">
                <LinkIcon className="w-5 h-5 text-cyber-cyan mr-3 flex-shrink-0" />
                <input 
                  type="text" 
                  value={urlInput}
                  onChange={(e) => setUrlInput(e.target.value)}
                  placeholder="Paste URL or claim statement to analyze..."
                  className="w-full bg-transparent border-none text-sm text-zinc-200 focus:ring-0 focus:outline-none placeholder-zinc-600"
                  required
                />
              </div>
              <button 
                type="submit"
                disabled={loading}
                className="bg-cyber-cyan/15 hover:bg-cyber-cyan/25 border border-cyber-cyan/35 text-cyber-cyan font-mono-data text-xs font-bold tracking-widest px-6 py-4 rounded-xl uppercase transition-all duration-300 flex items-center justify-center gap-2 whitespace-nowrap shadow-[0_0_15px_rgba(6,182,212,0.1)] disabled:opacity-50"
              >
                {loading ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Scanning Source...
                  </>
                ) : (
                  <>
                    <Award className="w-4 h-4 animate-pulse" />
                    Verify Integrity
                  </>
                )}
              </button>
            </form>
            {submitError && (
              <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-900/50 bg-amber-950/30 px-3 py-2.5">
                <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-[11px] font-mono-data font-bold uppercase tracking-wider text-amber-400">
                    No analysis produced
                  </p>
                  <p className="text-[12px] text-amber-200/80 leading-relaxed mt-1">
                    {submitError}
                  </p>
                </div>
              </div>
            )}
          </section>

          {/* Radar Scanning HUD Sweep Overlay (Active during load) */}
          {loading && (
            <div className="glass-panel rounded-2xl p-8 border border-cyber-cyan/45 shadow-[0_0_30px_rgba(6,182,212,0.15)] relative overflow-hidden flex flex-col items-center justify-center min-h-[300px] animate-pulse">
              <div className="absolute inset-0 bg-gradient-to-b from-transparent via-cyber-cyan/15 to-transparent h-1/3 w-full animate-scan pointer-events-none z-10"></div>
              <div className="relative mb-6">
                <div className="w-24 h-24 rounded-full border border-cyber-cyan/20 flex items-center justify-center relative shadow-[0_0_20px_rgba(6,182,212,0.1)]">
                  <div className="w-20 h-20 rounded-full border border-cyber-cyan/40 flex items-center justify-center animate-spin-slow">
                    <div className="w-16 h-16 rounded-full border-t border-t-cyber-cyan/80 flex items-center justify-center"></div>
                  </div>
                  <Globe className="w-8 h-8 text-cyber-cyan absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
                </div>
              </div>
              <h3 className="text-sm font-bold text-white font-mono-data uppercase tracking-widest mb-2">Analyzing Verification Vectors</h3>
              <div className="space-y-1.5 text-center text-xs text-zinc-500 font-mono-data">
                <p className="animate-pulse">CRAWLING TARGET SOURCE DOMAIN METADATA...</p>
                <p className="text-[10px] text-cyber-indigo">COMPUTING CROSS-REFERENCE GRAPH WEIGHTS...</p>
              </div>
            </div>
          )}

          {/* Results Area */}
          {!loading && currentReport && (
            <div className="space-y-6 animate-in fade-in duration-500">
              
              {/* High-Level Metrics Bento Row */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                
                {/* 1. Credibility Score */}
                <div className="glass-panel rounded-2xl p-5 flex flex-col justify-between shadow-lg border border-indigo-950/50 hover:border-cyber-indigo/35 transition-all duration-300">
                  <div className="flex justify-between items-start mb-4">
                    <span className="font-bold text-zinc-500 uppercase tracking-widest text-[10px] font-mono-data">Credibility Score</span>
                    <span className="material-symbols-outlined text-cyber-cyan text-[20px] shadow-sm">verified_user</span>
                  </div>
                  <div className="flex items-end gap-1 mb-2">
                    <span className="text-4xl font-extrabold text-white font-mono-data drop-shadow-[0_0_10px_rgba(255,255,255,0.1)]">
                      {currentReport.explanation.credibility_score}
                    </span>
                    <span className="text-zinc-500 text-xs font-mono-data mb-1.5">/ 100</span>
                  </div>
                  <div className="w-full bg-zinc-950 h-2 rounded-full overflow-hidden mb-3 border border-indigo-950/50 shadow-[inset_0_1px_3px_rgba(0,0,0,0.5)]">
                    <div 
                      className={`h-full transition-all duration-1000 shadow-[0_0_8px_rgba(255,255,255,0.4)] ${
                        currentReport.explanation.credibility_score >= 70 
                          ? "bg-emerald-500" 
                          : currentReport.explanation.credibility_score <= 40 
                            ? "bg-rose-500" 
                            : "bg-amber-500"
                      }`}
                      style={{ width: `${currentReport.explanation.credibility_score}%` }}
                    ></div>
                  </div>
                  <p className={`font-mono-data text-[10px] uppercase tracking-wider font-bold ${
                    currentReport.explanation.credibility_score >= 70 
                      ? "text-emerald-500" 
                      : currentReport.explanation.credibility_score <= 40 
                        ? "text-rose-500" 
                        : "text-amber-500"
                  }`}>
                    {currentReport.explanation.credibility_score >= 70 
                      ? "HIGH TRUST CLASSIFICATION" 
                      : currentReport.explanation.credibility_score <= 40 
                        ? "RISK WARNING THRESHOLD" 
                        : "MODERATE VERIFICATION LEVEL"}
                  </p>
                </div>

                {/* 2. Trust Risks */}
                <div className="glass-panel rounded-2xl p-5 flex flex-col justify-between shadow-lg border border-indigo-950/50 hover:border-cyber-indigo/35 transition-all duration-300">
                  <div className="flex justify-between items-start mb-4">
                    <span className="font-bold text-zinc-500 uppercase tracking-widest text-[10px] font-mono-data">Identified Risks</span>
                    <AlertTriangle className={`w-5 h-5 ${currentReport.explanation.trust_risks.length > 0 ? "text-amber-400 animate-pulse" : "text-emerald-500"}`} />
                  </div>
                  <div className="flex-grow">
                    {currentReport.explanation.trust_risks.length > 0 ? (
                      <ul className="space-y-2 text-xs text-zinc-400 leading-relaxed font-body-sm">
                        {currentReport.explanation.trust_risks.map((risk, idx) => (
                          <li key={idx} className="flex items-start gap-2">
                            <span className="text-rose-400 font-bold mt-0.5">•</span>
                            <span>{risk}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-emerald-400 font-semibold leading-relaxed">
                        No metadata discrepancies or structural risks detected.
                      </p>
                    )}
                  </div>
                </div>

                {/* 3. Cross-References */}
                <div className="glass-panel rounded-2xl p-5 flex flex-col justify-between shadow-lg border border-indigo-950/50 hover:border-cyber-indigo/35 transition-all duration-300">
                  <div className="flex justify-between items-start mb-4">
                    <span className="font-bold text-zinc-500 uppercase tracking-widest text-[10px] font-mono-data">Cross-References</span>
                    <Globe className="w-5 h-5 text-cyber-indigo" />
                  </div>
                  <div className="mb-2">
                    <span className="text-4xl font-extrabold text-white font-mono-data drop-shadow-[0_0_10px_rgba(255,255,255,0.1)]">
                      {currentReport.explanation.independent_cross_references}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-500 leading-normal mb-3">Independent wire agencies reporting matching corroborating details.</p>
                  <div className="flex -space-x-2">
                    {["AP", "BBC", "RTR", "AJ"].map((pub) => (
                      <div key={pub} className="w-6 h-6 rounded-full bg-zinc-950 border border-zinc-800/80 flex items-center justify-center text-[9px] font-mono-data text-zinc-400 font-bold uppercase shadow-sm">
                        {pub}
                      </div>
                    ))}
                    <div className="w-6 h-6 rounded-full bg-cyber-indigo/20 border border-cyber-indigo/30 flex items-center justify-center text-[8px] font-mono-data text-cyber-indigo font-bold shadow-sm">
                      +{Math.max(0, currentReport.explanation.independent_cross_references - 4)}
                    </div>
                  </div>
                </div>

              </div>

              {/* AI Verification Report Summary */}
              {currentReport.explanation.summary && (
                <div className="glass-panel rounded-2xl p-6 shadow-lg border border-indigo-950/50 border-l-4 border-l-cyber-cyan relative overflow-hidden group">
                  <div className="absolute inset-0 bg-gradient-to-r from-cyber-cyan/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500"></div>
                  <h3 className="text-xs font-bold text-cyber-cyan mb-3 flex items-center gap-2 tracking-wider uppercase font-mono-data">
                    <span className="material-symbols-outlined text-cyber-cyan text-[18px]">summarize</span>
                    AI Verification Analysis
                  </h3>
                  <p className="text-zinc-300 text-xs leading-relaxed font-body-sm bg-zinc-950/30 p-4 border border-zinc-850/60 rounded-xl">
                    {currentReport.explanation.summary}
                  </p>
                </div>
              )}

              {/* Deep Dive Row (Claims + Historical Matching) */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                
                {/* Extracted Claims */}
                <div className="glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg">
                  <h3 className="text-sm font-bold text-white mb-4 flex items-center gap-2 font-mono-data uppercase tracking-wider">
                    <span className="material-symbols-outlined text-cyber-cyan text-[20px]">fact_check</span>
                    Extracted Claims Validation
                  </h3>
                  <div className="space-y-3">
                    {currentReport.explanation.claims.map((claim, idx) => (
                      <div key={idx} className="bg-zinc-950/50 border border-zinc-850/60 rounded-xl p-3.5 flex flex-col gap-2 relative transition-all duration-300 hover:border-zinc-800/80">
                        <p className="text-xs text-zinc-300 leading-relaxed italic">&ldquo;{claim.text}&rdquo;</p>
                        <div className="flex items-center justify-between mt-1 pt-2 border-t border-zinc-900/60">
                          <span className="font-mono-data text-[9px] font-bold text-zinc-500 uppercase">Verification Status</span>
                          <span className={`inline-flex items-center gap-1.5 text-[9px] font-extrabold uppercase tracking-widest ${
                            claim.status.toLowerCase() === "corroborated" 
                              ? "text-emerald-400" 
                              : claim.status.toLowerCase() === "disputed" 
                                ? "text-rose-400" 
                                : "text-amber-400"
                          }`}>
                            {claim.status.toLowerCase() === "corroborated" && <CheckCircle className="w-3.5 h-3.5" />}
                            {claim.status.toLowerCase() === "disputed" && <XCircle className="w-3.5 h-3.5" />}
                            {claim.status.toLowerCase() === "unverified" && <AlertTriangle className="w-3.5 h-3.5 animate-pulse" />}
                            {claim.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Historical Event Matches */}
                <div className="glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg flex flex-col justify-between">
                  <div>
                    <h3 className="text-sm font-bold text-white mb-4 flex items-center gap-2 font-mono-data uppercase tracking-wider">
                      <span className="material-symbols-outlined text-cyber-cyan text-[20px]">history</span>
                      Historical Alignments
                    </h3>
                    <p className="text-xs text-zinc-500 mb-4 font-mono-data">Matches database event clusters matching the narrative thread.</p>
                    <div className="space-y-3">
                      {currentReport.explanation.historical_matches.length > 0 ? (
                        currentReport.explanation.historical_matches.map((match, idx) => (
                          <div key={idx} className="flex justify-between items-center border-b border-indigo-950/30 pb-3 last:border-b-0">
                            <div className="space-y-1">
                              <h4 className="text-xs text-zinc-200 font-bold leading-snug">{match.title}</h4>
                              <p className="text-[10px] text-zinc-500 font-mono-data flex items-center gap-1.5">
                                <Clock className="w-3 h-3 text-zinc-600" /> Active: {match.last_active}
                              </p>
                            </div>
                            <span className="font-mono-data text-xs text-cyber-cyan font-bold ml-4 whitespace-nowrap">
                              {match.match_percentage}% Match
                            </span>
                          </div>
                        ))
                      ) : (
                        <p className="text-xs text-zinc-600 italic font-mono-data">No corresponding historical events found.</p>
                      )}
                    </div>
                  </div>
                </div>

              </div>

              {/* Analysis History Archive Table */}
              <div className="glass-panel border border-indigo-950/50 rounded-2xl p-5 shadow-lg">
                <div className="flex justify-between items-center mb-4 border-b border-indigo-950/40 pb-3">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2 font-mono-data uppercase tracking-wider">
                    <History className="w-5 h-5 text-cyber-cyan" />
                    Analysis History Archive
                  </h3>
                  <span className="font-mono-data text-[10px] text-zinc-500 uppercase tracking-widest font-semibold">Logged: {history.length} runs</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-indigo-950/40 font-mono-data text-zinc-500 uppercase text-[9px] tracking-widest">
                        <th className="pb-3 w-3/5 font-bold">Target Claim / URL Source</th>
                        <th className="pb-3 px-4 text-center font-bold">Verification Date</th>
                        <th className="pb-3 text-right font-bold">Trust Metric</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-indigo-950/20">
                      {history.map((hItem) => (
                        <tr 
                          key={hItem.id}
                          onClick={() => setCurrentReport(hItem)}
                          className={`hover:bg-cyber-indigo/5 cursor-pointer transition-colors duration-200 ${currentReport.id === hItem.id ? "bg-cyber-indigo/10" : ""}`}
                        >
                          <td className="py-3 pr-4">
                            <div className="font-semibold text-zinc-300 max-w-lg truncate leading-normal flex items-center gap-1.5">
                              <CornerDownRight className="w-3.5 h-3.5 text-cyber-cyan flex-shrink-0" />
                              {hItem.input}
                            </div>
                          </td>
                          <td className="py-3 px-4 font-mono-data text-zinc-500 text-center">
                            {hItem.created_at ? new Date(hItem.created_at).toLocaleDateString() : "Unknown"}
                          </td>
                          <td className="py-3 text-right font-mono-data font-bold">
                            <span className={
                              hItem.explanation.credibility_score >= 70 
                                ? "text-emerald-500" 
                                : hItem.explanation.credibility_score <= 40 
                                  ? "text-rose-500" 
                                  : "text-amber-500"
                            }>
                              {hItem.explanation.credibility_score}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

            </div>
          )}
        </main>

        {/* Dynamic Social Flux Aside Sidebar */}
        <aside className="hidden lg:flex flex-col w-80 bg-zinc-950/40 border border-indigo-950/50 rounded-2xl p-4 h-[calc(100vh-8rem)] sticky top-6 overflow-hidden z-10 flex-shrink-0 backdrop-blur-md">
          <div className="mb-6 flex flex-col border-b border-indigo-950/40 pb-4">
            <span className="font-mono-data text-[10px] text-zinc-500 mb-1 uppercase tracking-widest font-bold">Real-Time Radar</span>
            <div className="flex items-center justify-between">
              <h2 className="font-mono-data text-xs font-bold text-white flex items-center gap-2 uppercase tracking-wider">
                <span className="material-symbols-outlined text-[18px] text-cyber-cyan animate-pulse">radar</span>
                Social Flux
              </h2>
              <button 
                onClick={() => router.push("/")}
                className="text-zinc-400 hover:text-cyber-cyan transition-colors flex items-center text-[10px] font-bold tracking-wider uppercase gap-1 font-mono-data"
              >
                Operational Map
              </button>
            </div>
          </div>

          <div className="flex-grow flex flex-col gap-3 overflow-y-auto pr-1 no-scrollbar">
            {/*
              This panel previously rendered two invented "wire" cards attributed
              to Associated Press, claiming the pipeline was "fully connected"
              and quoting a "verification latency" of 182ms. Neither number came
              from anywhere, and attributing them to a real newsroom made a
              fabrication look like a sourced report.

              There is no real-time ingestion in this build (see feature 11: RSS
              only, pulled on demand), so there is nothing truthful to stream
              here. The panel now says so.
            */}
            <div className="p-3 bg-zinc-950/60 rounded-xl border border-indigo-950/40">
              <div className="flex justify-between items-start mb-1 text-[9px] text-zinc-500 font-mono-data">
                <span>Ingestion Status</span>
                <span>Live query</span>
              </div>
              <p className="text-[11px] text-zinc-300 leading-relaxed">
                No live wire is connected. This build ingests articles on demand
                from configured publisher RSS feeds; it does not stream breaking
                news.
              </p>
            </div>

            <div className="p-3 bg-zinc-950/60 rounded-xl border border-indigo-950/40">
              <div className="flex justify-between items-start mb-1 text-[9px] text-zinc-500 font-mono-data">
                <span>External Fact-Checkers</span>
                <span>Not connected</span>
              </div>
              <p className="text-[11px] text-zinc-300 leading-relaxed">
                No third-party fact-checker feed is connected, so no
                &quot;reviewed by&quot; badge is shown. Verdicts below come from
                this system&apos;s own analysis and are labelled as such.
              </p>
            </div>
          </div>
        </aside>

      </div>
    </div>
  );
}

// Simple clock icon replacement
function Clock({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}

"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Mail, Key, ArrowRight, Shield, Globe, Send, ArrowLeft } from "lucide-react";
import WatchtowerLogo from "../components/WatchtowerLogo";


const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type ViewState = "login" | "register" | "forgot";

export default function LoginPage() {
  const router = useRouter();
  const [view, setView] = useState<ViewState>("login");
  
  // Login fields
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  // Register fields
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regStatus, setRegStatus] = useState<string | null>(null);

  // Recovery fields
  const [recEmail, setRecEmail] = useState("");
  const [recSent, setRecSent] = useState(false);

  // Handle Login submission
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginLoading(true);
    setLoginError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Invalid corporate credentials");
      }

      const data = await response.json();
      const token = data.access_token;
      
      // Store token in localStorage and cookies for middleware access
      localStorage.setItem("admin_token", token);
      document.cookie = `admin_token=${token}; path=/; max-age=1800; SameSite=Strict; Secure`;

      // Fetch user profile to verify role
      const profileResponse = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (profileResponse.ok) {
        const profile = await profileResponse.json();
        // Redirect to admin dashboard if Admin, otherwise home map
        if (profile.role === "ADMIN") {
          router.push("/admin/dashboard");
        } else {
          router.push("/");
        }
      } else {
        router.push("/");
      }
    } catch (err: any) {
      setLoginError(err.message || "An authentication error occurred.");
    } finally {
      setLoginLoading(false);
    }
  };

  // Handle registration dispatch
  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegStatus("Submitting credentials...");
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `${firstName} ${lastName}`,
          email: regEmail,
          password: regPassword,
        }),
      });

      if (response.ok) {
        setRegStatus(" Clearance requested. Redirecting to login...");
        setTimeout(() => {
          setView("login");
          setRegStatus(null);
        }, 2000);
      } else {
        const err = await response.json().catch(() => ({}));
        setRegStatus(`Error: ${err.detail || "Clearance failed"}`);
      }
    } catch (err) {
      setRegStatus("Failed to submit enrollment request.");
    }
  };

  return (
    <main className="bg-[#030712] text-zinc-150 min-h-screen flex selection:bg-cyber-indigo/30 selection:text-white relative overflow-hidden font-body-md">
      {/* Ambient Grid overlay */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.01)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.01)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none z-0"></div>

      {/* Left Column (Concept Art) - Hidden on Mobile */}
      <div className="hidden lg:flex flex-1 relative flex-col justify-between p-12 overflow-hidden border-r border-indigo-950/40">
        {/* Background Asset */}
        <div 
          className="absolute inset-0 bg-cover bg-center z-0" 
          style={{ 
            backgroundImage: "url('https://lh3.googleusercontent.com/aida-public/AB6AXuDmZKJlShKNDhogzgHf8kGMTnELrPGIBgtDZDBQIoq4WkZf4e8_Zl1bJB7P0NjWPHN3KFk6T6B6WGVcWCG7qRceARj0TLrBLDf2m1MSCoL5dLhuKhr4blPrcQxDgAGVhpT5XcSOdfTV-z8aWWkeOExUIWwLq2sfeps2Ppykxed2EPcagEIqRTQ6F7u41ZEu80wzslf4EvLWa7vfn3bfjUpKAWmmEAzvkb9nC_ijOspMVkSZX5ygpVN8on-effC0CgBw6vHCYsZ8GPAf')",
            opacity: 0.15
          }}
        ></div>
        <div className="absolute inset-0 bg-[#080c16]/80 backdrop-blur-md z-0"></div>
        <div className="absolute inset-0 bg-gradient-to-t from-[#030712] via-transparent to-[#030712]/40 z-0"></div>

        {/* Ambient Glows */}
        <div className="absolute top-1/4 left-1/4 w-[400px] h-[400px] bg-cyber-cyan/5 rounded-full blur-[100px] pointer-events-none"></div>
        <div className="absolute bottom-1/4 right-1/4 w-[400px] h-[400px] bg-cyber-indigo/5 rounded-full blur-[100px] pointer-events-none"></div>

        {/* Top Branding */}
        <div className="relative z-10 flex items-center gap-3 text-white">
          <WatchtowerLogo size="36px" hideWordmark={true} />
          <span className="font-headline-lg text-headline-lg font-extrabold tracking-tight bg-gradient-to-r from-cyber-cyan via-indigo-200 to-cyber-indigo bg-clip-text text-transparent drop-shadow-[0_0_10px_rgba(6,182,212,0.15)]">
            The Watchtower
          </span>
        </div>


        {/* Bottom Editorial Anchor */}
        <div className="relative z-10 max-w-xl space-y-4">
          <div className="w-12 h-1 bg-gradient-to-r from-cyber-cyan to-cyber-indigo rounded-full mb-6"></div>
          <h2 className="text-3xl font-extrabold text-white leading-tight">The Informed Edge.</h2>
          <p className="text-zinc-400 text-sm leading-relaxed max-w-md">
            Synthesizing global events into actionable intelligence. Merging traditional newsroom credibility with high-velocity precision data science.
          </p>
        </div>
      </div>

      {/* Right Interaction Column */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-12 lg:p-24 bg-[#030712] relative overflow-y-auto z-10">
        
        {/* Background Gradients for Right Side */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[350px] h-[350px] bg-cyber-indigo/5 rounded-full blur-[120px] pointer-events-none"></div>

        {/* Mobile Logo */}
        <div className="absolute top-8 left-8 flex lg:hidden items-center gap-2 text-white">
          <WatchtowerLogo size="28px" hideWordmark={true} />
          <span className="font-extrabold tracking-tight bg-gradient-to-r from-cyber-cyan to-cyber-indigo bg-clip-text text-transparent">
            The Watchtower
          </span>
        </div>


        {/* Interaction Form Container (Glass panel HUD) */}
        <div className="w-full max-w-[420px] relative glass-panel rounded-2xl p-6 sm:p-8 border border-indigo-950/60 shadow-2xl transition-all duration-300 hover:border-cyber-indigo/25">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-cyber-cyan to-cyber-indigo"></div>

          {/* 1. LOGIN VIEW */}
          {view === "login" && (
            <div className="space-y-6 transition-all duration-300 animate-in fade-in">
              <div className="space-y-1">
                <h1 className="text-2xl font-extrabold text-white tracking-tight">Authenticate</h1>
                <p className="text-zinc-400 text-xs font-mono-data uppercase tracking-wider">Access secure operational console.</p>
              </div>

              <div className="space-y-4">
                {/* OAuth Button */}
                <button 
                  onClick={() => alert("Google Single Sign-On simulation.")}
                  className="w-full flex items-center justify-center gap-3 py-3 px-4 border border-zinc-800/80 rounded-xl bg-zinc-950/40 hover:bg-zinc-900/40 transition-all text-zinc-300 font-semibold text-xs font-mono-data tracking-wider uppercase group"
                >
                  <svg className="w-4.5 h-4.5 text-on-surface group-hover:scale-105 transition-transform" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"></path>
                    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"></path>
                    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"></path>
                    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"></path>
                  </svg>
                  Continue with Google
                </button>

                {/* Divider */}
                <div className="relative flex items-center py-2">
                  <div className="flex-grow border-t border-indigo-950/60"></div>
                  <span className="flex-shrink-0 mx-4 text-zinc-500 font-mono-data text-[9px] uppercase tracking-widest font-bold">Or Secure Uplink</span>
                  <div className="flex-grow border-t border-indigo-950/60"></div>
                </div>

                {/* Credentials Form */}
                <form onSubmit={handleLogin} className="space-y-4">
                  <div className="space-y-1.5">
                    <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold" htmlFor="email">Clearance Email</label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-zinc-500">
                        <Mail className="w-4 h-4 text-cyber-cyan" />
                      </span>
                      <input 
                        type="email"
                        id="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="analyst@globelens.ai"
                        className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl pl-10 pr-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all placeholder:text-zinc-700"
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <div className="flex justify-between items-center">
                      <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold" htmlFor="password">Passphrase</label>
                      <button 
                        type="button" 
                        onClick={() => setViewState("forgot")}
                        className="font-mono-data text-[9px] text-cyber-indigo hover:text-cyber-cyan transition-colors uppercase font-bold tracking-wider"
                      >
                        Recover Access
                      </button>
                    </div>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-zinc-500">
                        <Key className="w-4 h-4 text-cyber-cyan" />
                      </span>
                      <input 
                        type="password"
                        id="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="••••••••••••"
                        className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl pl-10 pr-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all placeholder:text-zinc-700"
                        required
                      />
                    </div>
                  </div>

                  <div className="flex items-center pt-2 select-none">
                    <input 
                      type="checkbox"
                      id="remember"
                      checked={remember}
                      onChange={(e) => setRemember(e.target.checked)}
                      className="w-4 h-4 rounded border-zinc-800/80 bg-zinc-950/80 text-cyber-cyan focus:ring-cyber-cyan focus:ring-offset-[#030712] cursor-pointer"
                    />
                    <label className="ml-3 font-mono-data text-[10px] text-zinc-400 cursor-pointer" htmlFor="remember">
                      Maintain secure session parameters
                    </label>
                  </div>

                  {loginError && (
                    <div className="p-3.5 bg-cyber-rose/10 border border-cyber-rose/30 text-cyber-rose text-xs rounded-xl font-mono-data uppercase tracking-wider">
                      {loginError}
                    </div>
                  )}

                  <button 
                    type="submit"
                    disabled={loginLoading}
                    className="w-full mt-4 py-3 px-4 bg-cyber-cyan/15 hover:bg-cyber-cyan/25 border border-cyber-cyan/35 text-cyber-cyan font-mono-data text-xs font-bold tracking-widest rounded-xl transition-all shadow-[0_0_15px_rgba(6,182,212,0.1)] hover:scale-[1.02] active:scale-[0.98] flex items-center justify-center gap-2 group"
                  >
                    <span>
                      {loginLoading ? "Uplink active..." : "Initialize Gateway"}
                    </span>
                    <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                  </button>
                </form>
              </div>

              <p className="text-center font-mono-data text-[10px] text-zinc-500 pt-4 border-t border-indigo-950/40 font-semibold uppercase tracking-wider">
                No active clearance? 
                <button 
                  type="button" 
                  onClick={() => setViewState("register")}
                  className="text-cyber-cyan hover:text-white transition-colors ml-1 font-bold"
                >
                  Request Enrollment
                </button>
              </p>
            </div>
          )}

          {/* 2. REGISTER VIEW */}
          {view === "register" && (
            <div className="space-y-6 transition-all duration-300 animate-in fade-in">
              <div className="space-y-1">
                <button 
                  type="button" 
                  onClick={() => setViewState("login")}
                  className="flex items-center gap-1.5 text-zinc-500 hover:text-cyber-cyan transition-colors font-mono-data text-[9px] uppercase tracking-wider font-bold mb-2"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Gateway
                </button>
                <h1 className="text-2xl font-extrabold text-white tracking-tight">Enrollment</h1>
                <p className="text-zinc-400 text-xs font-mono-data uppercase tracking-wider">Submit credentials for clearance review.</p>
              </div>

              <form onSubmit={handleRegister} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold">First Name</label>
                    <input 
                      type="text" 
                      value={firstName}
                      onChange={(e) => setFirstName(e.target.value)}
                      className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all"
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold">Last Name</label>
                    <input 
                      type="text" 
                      value={lastName}
                      onChange={(e) => setLastName(e.target.value)}
                      className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all"
                      required
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold">Corporate Email</label>
                  <input 
                    type="email" 
                    value={regEmail}
                    onChange={(e) => setRegEmail(e.target.value)}
                    placeholder="analyst@globelens.ai"
                    className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all placeholder:text-zinc-700"
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold">Security Passphrase</label>
                  <input 
                    type="password" 
                    value={regPassword}
                    onChange={(e) => setRegPassword(e.target.value)}
                    placeholder="••••••••••••"
                    className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all"
                    required
                  />
                  <p className="font-mono-data text-[9px] text-zinc-500 mt-1 uppercase font-bold tracking-wider">Requires 12+ characters, alphanumeric.</p>
                </div>

                {regStatus && (
                  <div className="p-3.5 bg-zinc-950/80 border border-indigo-950/80 text-xs rounded-xl text-cyber-cyan font-mono-data uppercase tracking-wider text-center">
                    {regStatus}
                  </div>
                )}

                <button 
                  type="submit"
                  className="w-full mt-6 py-3 px-4 bg-cyber-indigo/15 hover:bg-cyber-indigo/25 border border-cyber-indigo/35 text-cyber-indigo font-mono-data text-xs font-bold tracking-widest rounded-xl transition-all shadow-[0_0_15px_rgba(99,102,241,0.1)] hover:scale-[1.02] active:scale-[0.98] flex items-center justify-center gap-2"
                >
                  Submit Dossier
                  <Send className="w-4 h-4" />
                </button>
              </form>
            </div>
          )}

          {/* 3. RECOVERY VIEW */}
          {view === "forgot" && (
            <div className="space-y-6 transition-all duration-300 animate-in fade-in">
              <div className="space-y-1">
                <button 
                  type="button" 
                  onClick={() => setViewState("login")}
                  className="flex items-center gap-1.5 text-zinc-500 hover:text-cyber-cyan transition-colors font-mono-data text-[9px] uppercase tracking-wider font-bold mb-2"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Gateway
                </button>
                <h1 className="text-2xl font-extrabold text-white tracking-tight">Recover Access</h1>
                <p className="text-zinc-400 text-xs font-mono-data uppercase tracking-wider">Secure recovery protocol dispatch.</p>
              </div>

              <form onSubmit={(e) => { e.preventDefault(); setRecSent(true); }} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="block font-mono-data text-[10px] text-zinc-400 uppercase tracking-widest font-bold">Clearance Email</label>
                  <input 
                    type="email" 
                    value={recEmail}
                    onChange={(e) => setRecEmail(e.target.value)}
                    placeholder="analyst@globelens.ai"
                    className="w-full bg-zinc-950/60 border border-zinc-850/80 rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-cyber-cyan focus:shadow-[0_0_15px_rgba(6,182,212,0.15)] transition-all placeholder:text-zinc-700"
                    required
                  />
                </div>

                <button 
                  type="submit"
                  className="w-full mt-4 py-3 px-4 bg-zinc-900/60 border border-zinc-800/80 hover:bg-zinc-850/20 text-zinc-300 font-mono-data text-xs font-bold tracking-widest rounded-xl transition-all shadow-md"
                >
                  Dispatch Protocol
                </button>

                {recSent && (
                  <div className="mt-4 p-4 rounded-xl bg-zinc-950/80 border border-indigo-950/60 flex items-start gap-3 animate-in fade-in">
                    <div className="mt-0.5">
                      <Shield className="w-5 h-5 text-cyber-cyan animate-pulse" />
                    </div>
                    <div>
                      <p className="font-mono-data text-xs font-bold text-white uppercase tracking-wider">Protocol Dispatched</p>
                      <p className="text-[11px] text-zinc-400 mt-1 leading-relaxed">
                        If credentials match active records, encrypted recovery instructions have been transmitted.
                      </p>
                    </div>
                  </div>
                )}
              </form>
            </div>
          )}

        </div>
      </div>
    </main>
  );

  // Helper helper to switch views with smooth fade-in
  function setViewState(target: ViewState) {
    setView(target);
    setRecSent(false);
    setLoginError(null);
  }
}

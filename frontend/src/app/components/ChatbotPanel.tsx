"use client";

import React, { useState, useRef, useEffect } from "react";
import { 
  Bot, 
  X, 
  Send, 
  Database, 
  Globe, 
  Sparkles, 
  RefreshCw, 
  CornerDownRight, 
  Mic 
} from "lucide-react";

interface Message {
  sender: "user" | "bot";
  text: string;
  source?: "database" | "web";
  in_database?: boolean;
}

interface ChatbotPanelProps {
  onClose: () => void;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function ChatbotPanel({ onClose }: ChatbotPanelProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      sender: "bot",
      text: "Hello Analyst. I am AI News-Scout, your news intelligence assistant. Ask me anything about the global events in our database, or ask me to retrieve the latest news from the web.",
    }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Suggestions listed at the bottom
  const suggestions = [
    "What are the latest geopolitics events in Europe?",
    "Summarize tech events in Asia.",
    "Explain the market valuation milestones of Nvidia."
  ];

  // Auto-scroll to bottom of messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleSend = async (textToSend: string) => {
    if (!textToSend.trim() || loading) return;

    // Add user message to state
    const userMessage: Message = { sender: "user", text: textToSend };
    setMessages(prev => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    const token = localStorage.getItem("admin_token");

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/chatbot`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : ""
        },
        body: JSON.stringify({ message: textToSend })
      });

      if (response.ok) {
        const data = await response.json();
        setMessages(prev => [...prev, {
          sender: "bot",
          text: data.reply,
          source: data.source,
          in_database: data.in_database
        }]);
      } else {
        const errData = await response.json().catch(() => ({}));
        setMessages(prev => [...prev, {
          sender: "bot",
          text: `Error: ${errData.detail || "Failed to get a response from AI News-Scout."}`,
        }]);
      }
    } catch (err) {
      console.error("Chatbot request failed", err);
      setMessages(prev => [...prev, {
        sender: "bot",
        text: "Failed to connect to the analysis pipeline. Please try again.",
      }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full w-full bg-[#0d1117]/85 backdrop-blur-xl border border-white/[0.07] rounded-2xl shadow-[0_8px_32px_rgba(0,0,0,0.5)] overflow-hidden">
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-3 bg-[#0a0d14] border-b border-indigo-950/40">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-cyber-indigo/15 border border-cyber-indigo/35 flex items-center justify-center text-cyber-indigo shadow-[0_0_10px_rgba(99,102,241,0.15)]">
            <Bot className="w-4 h-4" />
          </div>
          <div>
            <div className="font-mono-data text-xs font-bold text-white leading-none">AI News-Scout</div>
            <div className="text-[10px] text-zinc-500 font-mono-data mt-0.5">Tactical Analyst Assistant</div>
          </div>
        </div>
        <button 
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-300 transition-colors p-1 rounded-md hover:bg-zinc-800/20"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 no-scrollbar">
        {messages.map((msg, index) => (
          <div 
            key={index}
            className={`flex flex-col ${msg.sender === "user" ? "items-end" : "items-start"} space-y-1`}
          >
            <div 
              className={`text-sm px-4 py-2.5 rounded-2xl break-words whitespace-pre-wrap leading-relaxed ${
                msg.sender === "user" 
                  ? "bg-cyber-indigo text-white rounded-tr-none max-w-[85%] shadow-[0_4px_12px_rgba(99,102,241,0.2)]" 
                  : "bg-zinc-900/80 border border-zinc-800/40 text-zinc-200 rounded-tl-none max-w-[90%]"
              }`}
            >
              {msg.text}
            </div>
            
            {/* Metadata (source badge) */}
            {msg.sender === "bot" && msg.source && (
              <div className="flex items-center gap-1.5 px-2 mt-0.5 font-mono-data text-[10px]">
                {msg.source === "database" ? (
                  <span className="flex items-center gap-1 text-cyber-cyan bg-cyber-cyan/10 px-1.5 py-0.5 rounded border border-cyber-cyan/20">
                    <Database className="w-2.5 h-2.5" />
                    The Watchtower Database
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-amber-400 bg-amber-400/10 px-1.5 py-0.5 rounded border border-amber-400/20">
                    <Globe className="w-2.5 h-2.5" />
                    Live Web Crawl
                  </span>
                )}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex items-start gap-2">
            <div className="bg-zinc-900/80 border border-zinc-800/40 text-zinc-400 rounded-2xl rounded-tl-none px-4 py-3 flex items-center gap-2 max-w-[85%] font-mono-data text-xs">
              <RefreshCw className="w-3.5 h-3.5 animate-spin text-cyber-indigo" />
              <span>Scanning sources...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Suggestions Overlay if chat has only intro message */}
      {messages.length === 1 && !loading && (
        <div className="px-4 py-2 border-t border-indigo-950/20 bg-[#0a0d14]/30 space-y-1.5">
          <div className="flex items-center gap-1 text-zinc-500 font-mono-data text-[10px] uppercase tracking-wider mb-1">
            <Sparkles className="w-3 h-3 text-cyber-cyan" />
            <span>Suggested Inquiries</span>
          </div>
          <div className="flex flex-col gap-1.5">
            {suggestions.map((s, i) => (
              <button
                key={i}
                onClick={() => handleSend(s)}
                className="text-left text-xs bg-zinc-900/60 hover:bg-zinc-800/50 border border-zinc-850 rounded-xl px-3 py-2 text-zinc-400 hover:text-zinc-200 transition-all font-body-sm flex items-start gap-2"
              >
                <CornerDownRight className="w-3.5 h-3.5 text-cyber-indigo flex-shrink-0 mt-0.5" />
                <span>{s}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input */}
      <div className="p-3 bg-[#0a0d14] border-t border-indigo-950/40">
        <form 
          onSubmit={(e) => {
            e.preventDefault();
            handleSend(input);
          }} 
          className="flex items-center gap-2"
        >
          <div className="flex-1 bg-zinc-950/60 border border-zinc-800/80 rounded-xl flex items-center px-3 py-2 focus-within:border-cyber-indigo/60 transition-all duration-300">
            <input 
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Instruct AI News-Scout..."
              className="w-full bg-transparent border-none text-xs text-zinc-200 focus:ring-0 focus:outline-none placeholder-zinc-600"
              disabled={loading}
            />
            <button 
              type="button" 
              className="text-zinc-600 hover:text-zinc-400 p-1 flex-shrink-0 transition-colors"
            >
              <Mic className="w-4 h-4" />
            </button>
          </div>
          <button 
            type="submit"
            disabled={!input.trim() || loading}
            className="bg-cyber-indigo hover:bg-indigo-600 text-white rounded-xl p-2.5 transition-all shadow-[0_0_15px_rgba(99,102,241,0.2)] disabled:opacity-40 disabled:shadow-none flex-shrink-0"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </form>
      </div>
    </div>
  );
}

"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Search, X, TrendingUp, Globe, ShieldAlert } from "lucide-react";

interface SearchBarProps {
  onSelectEvent?: (event: any) => void;
  onSearchResults?: (results: any[] | null) => void;
  onClearSearch?: () => void;
  // When provided, Enter / the ANALYZE button delegate execution to the caller
  // (e.g. navigation to a dedicated /search results page) instead of fetching
  // here and flying to the first hit. Homepage behaviour is unchanged because
  // it does not pass either optional handler.
  onSubmit?: (query: string) => void;
  onSuggestionSelect?: (event: any) => void;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function SearchBar({ onSelectEvent, onSearchResults, onClearSearch, onSubmit, onSuggestionSelect }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [loading, setLoading] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Fetch Autocomplete Suggestions with Debouncing
  useEffect(() => {
    if (query.trim().length < 1) {
      setSuggestions([]);
      return;
    }

    const delayDebounceFn = setTimeout(async () => {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/v1/search/autocomplete?prefix=${encodeURIComponent(query)}`
        );
        if (response.ok) {
          const data = await response.json();
          setSuggestions(data);
          setShowDropdown(true);
        }
      } catch (err) {
        console.error("Autocomplete fetch failed", err);
      }
    }, 250);

    return () => clearTimeout(delayDebounceFn);
  }, [query]);

  // Execute full-text query
  const handleSearch = useCallback(async (searchQuery: string) => {
    if (!searchQuery.trim()) return;
    if (onSubmit) {
      onSubmit(searchQuery);
      return;
    }
    setLoading(true);
    setShowDropdown(false);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/search/query?q=${encodeURIComponent(searchQuery)}`
      );
      if (response.ok) {
        const data = await response.json();
        onSearchResults?.(data);
        if (data && data.length > 0) {
          // If search yielded results, fly to the first matching event
          onSelectEvent?.(data[0]);
        }
      } else {
        onSearchResults?.([]);
      }
    } catch (err) {
      console.error("Search query failed", err);
      onSearchResults?.([]);
    } finally {
      setLoading(false);
    }
  }, [onSearchResults, onSelectEvent, onSubmit]);

  const handleSuggestionClick = (event: any) => {
    setQuery(event.title);
    setShowDropdown(false);
    if (onSuggestionSelect) {
      onSuggestionSelect(event);
      return;
    }
    onSelectEvent?.(event);
    onSearchResults?.([event]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleSearch(query);
    }
  };

  const handleClear = () => {
    setQuery("");
    setSuggestions([]);
    setShowDropdown(false);
    onClearSearch?.();
  };

  return (
    <div className="relative w-full max-w-2xl z-[1000]" ref={dropdownRef}>
      <div className="relative w-full">
        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
          <Search className="w-5 h-5 text-outline" />
        </div>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (suggestions.length > 0) setShowDropdown(true);
          }}
          className="w-full bg-primary-container border border-outline-variant text-on-background font-body-md text-body-md rounded-lg pl-12 pr-[144px] py-3.5 shadow-[inset_0_2px_4px_rgba(0,0,0,0.2)] focus:ring-2 focus:ring-primary focus:border-primary transition-all duration-200 outline-none placeholder:text-outline/60"
          placeholder="Search intelligence, topics..."
        />
        {query && (
          <button
            onClick={handleClear}
            className="absolute inset-y-0 right-24 px-3 flex items-center text-outline hover:text-primary transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        )}
        <button
          onClick={() => handleSearch(query)}
          disabled={loading}
          className="absolute right-2 top-2 bottom-2 bg-primary text-primary-container hover:bg-surface-tint hover:scale-98 active:scale-95 px-4 rounded font-label-caps text-[10px] tracking-wider uppercase font-bold transition-all"
        >
          {loading ? "ANALYZE..." : "ANALYZE"}
        </button>
      </div>

      {/* Dropdown Suggestions */}
      {showDropdown && suggestions.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-surface-container-high/95 backdrop-blur-md border border-outline-variant rounded-xl shadow-2xl overflow-hidden max-h-80 overflow-y-auto no-scrollbar">
          <div className="p-3 border-b border-outline-variant/50 flex items-center justify-between">
            <span className="font-label-caps text-[10px] text-on-surface-variant tracking-wider uppercase font-bold flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5 text-primary" />
              Suggested Intelligence Dossiers
            </span>
            <span className="text-[10px] text-outline">Press Enter to Search</span>
          </div>
          <div className="divide-y divide-outline-variant/40">
            {suggestions.map((item) => (
              <button
                key={item.id}
                onClick={() => handleSuggestionClick(item)}
                className="w-full text-left px-4 py-3 hover:bg-primary-container/85 flex items-start gap-3 transition-colors duration-150 group"
              >
                <div className="mt-0.5 p-1 bg-surface-container-highest border border-outline-variant rounded-lg group-hover:border-primary transition-colors">
                  <Globe className="w-4 h-4 text-on-surface-variant group-hover:text-primary transition-colors" />
                </div>
                <div className="flex-1 overflow-hidden">
                  <h4 className="text-xs font-semibold text-on-surface group-hover:text-primary transition-colors truncate">
                    {item.title}
                  </h4>
                  <p className="text-[10px] text-on-surface-variant truncate mt-0.5">
                    {item.location_country || item.country || "Global"} · {item.topic || "WORLD"}
                  </p>
                </div>
                {item.importance_score && (
                  <span className="text-[9px] font-mono-data bg-surface-container-highest px-2 py-0.5 border border-outline-variant/60 rounded text-outline group-hover:text-primary group-hover:border-primary/50 transition-all self-center">
                    IMP: {parseFloat(item.importance_score).toFixed(1)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

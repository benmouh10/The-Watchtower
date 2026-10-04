"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Pause, Volume2 } from "lucide-react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Status = "idle" | "loading" | "playing" | "paused";

const LINK_CLASS =
  "font-label-caps text-[10px] uppercase flex items-center gap-1 w-max " +
  "text-primary hover:text-white disabled:text-zinc-600 disabled:hover:text-zinc-600 disabled:cursor-not-allowed";

// Matches the action pills in the event header (Bookmark / Save to List).
const PILL_CLASS =
  "flex items-center gap-2 px-3 py-1.5 bg-surface-container border border-outline-variant " +
  "text-on-surface hover:text-primary rounded font-body-sm text-body-sm transition-colors " +
  "disabled:opacity-60 disabled:cursor-wait";

/**
 * Plays an article's or event's narration in place.
 *
 * Playback is handed to a media element pointed at a short-lived signed URL rather
 * than a Blob built from a complete response. That matters because narration of an
 * uncached story takes real time to synthesise: with a Blob the listener waited for
 * every byte before hearing anything, while a streamed URL starts on the first
 * chunk. The signed token is what lets the element load the stream at all, since it
 * cannot carry the Authorization header — the blob fetch stays as a fallback for
 * when minting one is not possible.
 */
export default function ListenButton({
  resource,
  resourceId,
  label = "Listen",
  variant = "link",
  className = "",
}: {
  resource: "articles" | "events";
  resourceId: string;
  label?: string;
  variant?: "link" | "pill";
  className?: string;
}) {
  const router = useRouter();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const recoveringRef = useRef(false);
  const [status, setStatus] = useState<Status>("idle");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    };
  }, []);

  function discard() {
    audioRef.current?.pause();
    audioRef.current = null;
  }

  function attach(audio: HTMLAudioElement) {
    audio.onended = () => setStatus("idle");
    audio.onplaying = () => {
      recoveringRef.current = false;
      setNotice(null);
      setStatus("playing");
    };
    audio.onpause = () => setStatus((current) => (current === "playing" ? "paused" : current));
    audio.onerror = () => {
      discard();
      // A stream that was left paused long enough may have been dropped. Retrying
      // once with a fresh token gets playback going again from the start, which is
      // better than a button that does nothing.
      if (!recoveringRef.current) {
        recoveringRef.current = true;
        start();
        return;
      }
      setNotice("Narration could not be played.");
      setStatus("idle");
    };
    audioRef.current = audio;
    return audio;
  }

  async function playStreamed(token: string) {
    const audio = attach(new Audio());
    audio.src = `${API_BASE_URL}/api/v1/${resource}/${resourceId}/audio?sig=${encodeURIComponent(
      token
    )}`;
    await audio.play();
  }

  /** Fallback for when a signed link is refused: fetch the whole clip and play it. */
  async function playBlob(authToken: string) {
    const response = await fetch(`${API_BASE_URL}/api/v1/${resource}/${resourceId}/audio`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });

    if (response.status === 401) {
      localStorage.removeItem("admin_token");
      router.push("/login");
      return false;
    }
    if (response.status === 501) {
      setNotice("Narration is not enabled on this deployment.");
      setStatus("idle");
      return false;
    }
    if (!response.ok) {
      throw new Error(`narration request failed: ${response.status}`);
    }

    const blob = await response.blob();
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    const url = URL.createObjectURL(blob);
    blobUrlRef.current = url;
    await attach(new Audio(url)).play();
    return true;
  }

  async function start() {
    const authToken = localStorage.getItem("admin_token");
    if (!authToken) {
      router.push("/login");
      return;
    }

    discard();
    setNotice(null);
    setStatus("loading");
    try {
      const minted = await fetch(`${API_BASE_URL}/api/v1/${resource}/${resourceId}/audio/url`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });

      if (minted.status === 401) {
        localStorage.removeItem("admin_token");
        router.push("/login");
        return;
      }

      if (minted.ok) {
        const { token } = await minted.json();
        try {
          await playStreamed(token);
          return;
        } catch {
          // Autoplay can be refused before any audio has played on the page; the
          // blob path gives the element something to retry against.
          discard();
        }
      }

      await playBlob(authToken);
    } catch {
      setNotice("Narration could not be prepared. Try again.");
      setStatus("idle");
    }
  }

  function pause() {
    audioRef.current?.pause();
  }

  function resume() {
    setNotice(null);
    const audio = audioRef.current;
    if (!audio) {
      // Nothing buffered to resume from, so there is only one thing left to try.
      recoveringRef.current = true;
      start();
      return;
    }
    // Resumes from the element's own position, reusing what it already buffered.
    audio.play().catch(() => {
      recoveringRef.current = true;
      start();
    });
  }

  const busy = status === "loading";
  const playing = status === "playing";
  const paused = status === "paused";

  return (
    <div>
      <button
        type="button"
        onClick={playing ? pause : paused ? resume : start}
        disabled={busy}
        aria-label={playing ? `Pause narration of ${label}` : `Listen to ${label}`}
        className={`${variant === "pill" ? PILL_CLASS : LINK_CLASS} ${className}`}
      >
        {busy ? (
          <Loader2 className="w-3 h-3 animate-spin" />
        ) : playing ? (
          <Pause className="w-3 h-3" />
        ) : (
          <Volume2 className="w-3 h-3" />
        )}
        {busy ? "Preparing" : playing ? "Pause" : paused ? "Resume" : label}
      </button>
      {notice && <p className="text-[10px] text-zinc-500 mt-1.5">{notice}</p>}
    </div>
  );
}
"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import PillNav from "./PillNav";
import UserDock from "./UserDock";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface PrimaryNavProps {
  /** Href of the current page, used to highlight the active pill. */
  activeHref: string;
  initialLoadAnimation?: boolean;
  /**
   * When provided, this role drives the Admin link instead of fetching
   * /auth/me. Pass `null` while the profile is still loading.
   */
  role?: string | null;
  /** Override the Standard / Map click handlers (the home page toggles in place). */
  onStandard?: (e: React.MouseEvent<HTMLAnchorElement>) => void;
  onMap?: (e: React.MouseEvent<HTMLAnchorElement>) => void;
}

/**
 * Single source of truth for the primary top navigation. Every page renders
 * this so the menu can never drift between routes; the Admin entry appears
 * only for administrators.
 */
export default function PrimaryNav({
  activeHref,
  initialLoadAnimation = false,
  role: roleProp,
  onStandard,
  onMap,
}: PrimaryNavProps) {
  const router = useRouter();
  const [fetchedRole, setFetchedRole] = useState<string | null>(null);

  useEffect(() => {
    if (roleProp !== undefined) return;
    if (typeof window === "undefined") return;
    const token = localStorage.getItem("admin_token");
    if (!token) return;
    fetch(`${API_BASE_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((me) => setFetchedRole(me?.role ?? "AUTH_USER"))
      .catch(() => setFetchedRole("AUTH_USER"));
  }, [roleProp]);

  const role = roleProp !== undefined ? roleProp : fetchedRole;
  const showAdmin = role === "ADMIN";

  const handleStandard =
    onStandard ??
    ((e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      router.push("/?view=standard");
    });
  const handleMap =
    onMap ??
    ((e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      router.push("/?view=map");
    });

  return (
    <>
    <PillNav
      logo="/logo.svg"
      logoAlt="The Watchtower Logo"
      items={[
        { label: "Standard", href: "/?view=standard", onClick: handleStandard },
        { label: "Map", href: "/?view=map", onClick: handleMap },
        { label: "Dispatches", href: "/dispatches" },
        { label: "Reading Lists", href: "/reading-lists" },
        { label: "Newsletter", href: "/newsletter" },
        ...(showAdmin ? [] : [{ label: "Profile", href: "/profile" }]),
        ...(showAdmin ? [{ label: "Admin", href: "/admin/dashboard" }] : []),
        { label: "Fact Checker", href: "/fact-checker" },
      ]}
      activeHref={activeHref}
      baseColor="#080c16"
      pillColor="#0c101b"
      hoveredPillTextColor="#22d3ee"
      pillTextColor="#94a3b8"
      initialLoadAnimation={initialLoadAnimation}
    />
    <UserDock />
    </>
  );
}

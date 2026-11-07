"""
GlobeLens AI — ApiKeyService
============================
Raw-key generation, SHA-256 hashing, and the scope vocabulary for long-lived
bearer API keys.

Security model mirrors refresh tokens: only the SHA-256 of a key is stored.
SHA-256 (not bcrypt/argon2) is the correct primitive because the input is a
high-entropy server-generated secret; a slow KDF would defend a search space
that does not exist. The raw key is returned exactly once, at creation.

Scopes: a key is created with a subset of the vocabulary below and can only
enjoy the endpoints whose gate it satisfies. Access-token sessions bypass
scope gates entirely (a signed JWT for the account is already the whole
account), which is what keeps existing behavior intact.
"""
import hashlib
import secrets
from typing import FrozenSet, List, Optional

API_KEY_PREFIX = "glb_"


# ── Scope vocabulary ──────────────────────────────────────────────────────────
# Machine-facing scopes. read:events / read:sources gate the otherwise-
# anonymous read surface (/events, /sources, /digests, /search); the rest are
# wired onto already-authenticated endpoints where a scripted consumer is the
# natural caller. See controllers/public_read_guard.py for the read mapping.
KEY_SCOPE_READ_EVENTS = "read:events"
KEY_SCOPE_READ_SOURCES = "read:sources"
KEY_SCOPE_BOOKMARKS = "bookmarks"
KEY_SCOPE_READING_LISTS = "reading-lists"
KEY_SCOPE_FACT_CHECK = "fact-check"
KEY_SCOPE_USAGE = "usage"
KEY_SCOPE_ENGAGEMENT = "engagement"
KEY_SCOPE_ADMIN = "admin"

ALL_KEY_SCOPES: FrozenSet[str] = frozenset({
    KEY_SCOPE_READ_EVENTS,
    KEY_SCOPE_READ_SOURCES,
    KEY_SCOPE_BOOKMARKS,
    KEY_SCOPE_READING_LISTS,
    KEY_SCOPE_FACT_CHECK,
    KEY_SCOPE_USAGE,
    KEY_SCOPE_ENGAGEMENT,
    KEY_SCOPE_ADMIN,
})

# Everything an access-token session may do. Used to render scope gates as
# always-satisfied for JWT principals, never stored on a key row.
ALL_SCOPES: FrozenSet[str] = ALL_KEY_SCOPES


def generate_raw_key() -> str:
    """A fresh `glb_`-prefixed secret. Never logged, never stored raw."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw_key: str) -> str:
    """Hex SHA-256 of the raw key; what the database stores."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def display_prefix(raw_key: str) -> str:
    """`glb_` plus the first 8 chars: enough to recognise, too little to use."""
    return raw_key[: len(API_KEY_PREFIX) + 8]


def parse_scopes(raw: Optional[List[str]]) -> tuple:
    """
    Normalize a requested scope list.
    Returns (scopes, error): scopes is a sorted unique list when valid,
    otherwise error is a human message. Unknown strings are rejected outright
    so a typo cannot mint a key that silently gates nothing meaningfully.
    """
    if not raw:
        return [], None
    seen: List[str] = []
    for scope in raw:
        scope = str(scope).strip()
        if not scope:
            continue
        if scope not in ALL_KEY_SCOPES:
            return [], f"Unknown scope '{scope}'. Valid: {sorted(ALL_KEY_SCOPES)}"
        if scope not in seen:
            seen.append(scope)
    return sorted(seen), None


def requires(key_scopes: FrozenSet[str], *required: str) -> bool:
    """True when a key's scope set covers every required scope."""
    return required <= key_scopes


__all__ = [
    "ALL_KEY_SCOPES",
    "ALL_SCOPES",
    "API_KEY_PREFIX",
    "KEY_SCOPE_ADMIN",
    "KEY_SCOPE_BOOKMARKS",
    "KEY_SCOPE_ENGAGEMENT",
    "KEY_SCOPE_FACT_CHECK",
    "KEY_SCOPE_READ_EVENTS",
    "KEY_SCOPE_READ_SOURCES",
    "KEY_SCOPE_READING_LISTS",
    "KEY_SCOPE_USAGE",
    "display_prefix",
    "generate_raw_key",
    "hash_key",
    "parse_scopes",
    "requires",
]
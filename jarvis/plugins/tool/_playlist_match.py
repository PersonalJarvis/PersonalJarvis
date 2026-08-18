"""Shared "which of the user's playlists did they mean" matcher.

Used by every music connector (Spotify, YouTube Music): a spoken playlist name
arrives with filler ("my running playlist", "meine Lauf-Wiedergabeliste") and
inflection, and the provider only knows exact titles. One matcher, one set of
rules, so "play my running playlist" resolves the same way whichever service
is connected. Pure functions, stdlib only.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

# Names people use for the liked-songs list; matched after normalisation.
LIKED_NAMES: frozenset[str] = frozenset({
    "liked", "likes", "liked songs", "liked music", "my likes", "favorites", "favourites",
    # i18n-allow: spoken-input vocabulary (de/es), not prose
    "gelikte songs", "gelikte lieder", "geliked", "meine likes", "favoriten",
    "lieblingslieder", "lieblingssongs",
    "canciones que me gustan", "me gusta", "favoritos", "mis me gusta",
})

# Filler around a playlist name ("my running playlist" → "running").
_PLAYLIST_NOISE_RE = re.compile(
    r"\b(my|the|playlist"
    r"|mein|meine|meiner|meinen|wiedergabeliste|die|der|das"  # i18n-allow: spoken-input vocabulary
    r"|mi|mis|lista|el|la)\b",  # i18n-allow: spoken-input vocabulary
    re.IGNORECASE,
)

# Below this similarity a "closest" title is a guess, not a match.
_FUZZY_FLOOR = 0.6


def normalize_name(name: str) -> str:
    cleaned = _PLAYLIST_NOISE_RE.sub(" ", (name or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def is_liked_name(name: str) -> bool:
    """Does ``name`` mean the user's liked songs rather than a named list?"""
    return normalize_name(name) in {normalize_name(n) for n in LIKED_NAMES} or (
        (name or "").strip().lower() in LIKED_NAMES
    )


def match_playlist(
    name: str, playlists: list[dict[str, Any]], *, title_key: str = "title"
) -> dict[str, Any] | None:
    """The playlist a spoken name most plausibly means: exact, then contains,
    then a fuzzy ratio — never a wild guess below the floor."""
    wanted = normalize_name(name)
    if not wanted or not playlists:
        return None
    titled = [(p, normalize_name(str(p.get(title_key) or ""))) for p in playlists]
    for playlist, title in titled:
        if title == wanted:
            return playlist
    for playlist, title in titled:
        if wanted in title or (title and title in wanted):
            return playlist
    scored = sorted(
        ((difflib.SequenceMatcher(None, wanted, title).ratio(), p) for p, title in titled),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= _FUZZY_FLOOR:
        return scored[0][1]
    return None


__all__ = ["LIKED_NAMES", "is_liked_name", "match_playlist", "normalize_name"]

"""Which music connector a request goes to — Spotify or YouTube Music.

Two connectors, one domain: "spiel Musik" is a valid request to either. This
module is the one place that decides, pure and unit-testable:

1. A service the user NAMES wins ("… auf YouTube Music", "… on Spotify").
2. Otherwise the ``[music] preferred_service`` setting, when that service is
   connected.
3. Otherwise ``auto``: the only connected service, else the skill that matched
   the turn (Spotify by catalog order — the behaviour before the setting).

The brain applies the answer to the deterministic skill capture. The two
music tools put the same sentence at the START of their descriptions (hybrid
live models compact to 450 characters, so a trailing hint was being cut).
Execute-time reroute (``reroute_music_tool``) is the correctness boundary:
prompt compliance is not one (hybrid live 2026-08-19: preference YouTube
Music, Spotify not connected, the live model still called ``spotify``).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterable

from jarvis.core.music_constants import (
    MUSIC_PLUGIN_IDS,
    MUSIC_SERVICE_AUTO,
    MUSIC_SERVICE_SPOTIFY,
    MUSIC_SERVICE_YOUTUBE_MUSIC,
)

# The brand mentions that name a service outright. Word-boundaried; "youtube"
# alone counts for YouTube Music because in a MUSIC request that is what the
# user means (spoken input rarely says the full brand).
_EXPLICIT: tuple[tuple[str, re.Pattern[str]], ...] = (
    (MUSIC_SERVICE_SPOTIFY, re.compile(r"\bspotify\b", re.IGNORECASE)),
    (
        MUSIC_SERVICE_YOUTUBE_MUSIC,
        re.compile(r"\b(?:youtube(?:\s*music)?|yt\s*music|ytmusic|yt)\b", re.IGNORECASE),
    ),
)

_LABELS = {MUSIC_SERVICE_SPOTIFY: "Spotify", MUSIC_SERVICE_YOUTUBE_MUSIC: "YouTube Music"}

# "play something I like" is liked songs, not a title search.
# Word-boundaried so a song title that happens to contain "like" stays a title.
_TASTE_RE = re.compile(
    r"(?:"
    r"was\s+mir\s+gef[aä]llt|"  # i18n-allow: spoken-input vocabulary
    r"something\s+i\s+(?:like|love)|"
    r"songs?\s+i\s+(?:like|love)|"
    r"liked\s+songs?|"
    r"meine\s+likes|"  # i18n-allow: spoken-input vocabulary
    r"lo\s+que\s+me\s+gusta"
    r")",
    re.IGNORECASE,
)

# Spotify `type=track` vs YouTube Music `type=song`.
_TYPE_MAP: dict[tuple[str, str], dict[str, str]] = {
    (MUSIC_SERVICE_SPOTIFY, MUSIC_SERVICE_YOUTUBE_MUSIC): {"track": "song"},
    (MUSIC_SERVICE_YOUTUBE_MUSIC, MUSIC_SERVICE_SPOTIFY): {
        "song": "track",
        "liked": "playlist",
    },
}

log = logging.getLogger(__name__)

# Which music connectors hold a usable credential is a keyring read; the tool
# descriptions ask on every turn, so the answer is remembered briefly.
_CONNECTED_TTL_S = 15.0
_connected_cache: tuple[float, tuple[str, ...]] | None = None
_connected_lock = threading.Lock()


def connected_music_services(*, store: object | None = None) -> tuple[str, ...]:
    """Music plugin ids with a usable credential (access token, not flagged
    for re-auth). Cached for a few seconds; a store fault answers the last
    known value or nothing — never raises. ``store`` is injectable for tests."""
    global _connected_cache
    now = time.monotonic()
    if store is None:
        with _connected_lock:
            if _connected_cache is not None and _connected_cache[0] > now:
                return _connected_cache[1]
    live: list[str] = []
    try:
        if store is None:
            from jarvis.marketplace.token_store import TokenStore

            store = TokenStore()
        for plugin_id in MUSIC_PLUGIN_IDS:
            try:
                tokens = store.load(plugin_id)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 — one broken credential stays isolated
                log.debug("music connection probe: %s unreadable: %s", plugin_id, exc)
                continue
            if (
                tokens is not None
                and getattr(tokens, "access", None)
                and not getattr(tokens, "needs_reauth", False)
            ):
                live.append(plugin_id)
    except Exception as exc:  # noqa: BLE001 — a store fault must not decide a turn
        log.debug("music connection probe failed: %s", exc)
        return _connected_cache[1] if _connected_cache else ()
    result = tuple(live)
    if store is None:  # an injected (test) store must never poison the process cache
        with _connected_lock:
            _connected_cache = (now + _CONNECTED_TTL_S, result)
    return result


def forget_connected_music_services() -> None:
    """Drop the cached connection answer (a connect/disconnect just happened)."""
    global _connected_cache, _preferred_cache
    with _connected_lock:
        _connected_cache = None
        _preferred_cache = None


# The preferred service is read from jarvis.toml; a tool description is built
# on every router turn, so the value is remembered for the same few seconds
# rather than re-parsing the config each time. The Settings route drops it on
# write (via forget_connected_music_services), so a change shows at once.
_preferred_cache: tuple[float, str] | None = None


def preferred_music_service() -> str:
    """``[music] preferred_service`` — cached briefly; a config fault reads as
    ``auto``. Never raises."""
    global _preferred_cache
    now = time.monotonic()
    with _connected_lock:
        if _preferred_cache is not None and _preferred_cache[0] > now:
            return _preferred_cache[1]
    try:
        from jarvis.core.config import load_config

        value = str(load_config().music.preferred_service or MUSIC_SERVICE_AUTO)
    except Exception as exc:  # noqa: BLE001 — a config fault means "no preference"
        log.debug("preferred music service read failed: %s", exc)
        value = MUSIC_SERVICE_AUTO
    with _connected_lock:
        _preferred_cache = (now + _CONNECTED_TTL_S, value)
    return value


def description_hint(service_id: str) -> str:
    """The sentence a music tool appends to its description (see
    :func:`preference_hint`), built from the cached preference and connection
    state. Never raises — a description must not."""
    try:
        return preference_hint(
            service_id,
            preferred=preferred_music_service(),
            connected=connected_music_services(),
        )
    except Exception as exc:  # noqa: BLE001 — a fault means no hint, not no tool
        log.debug("music description hint failed: %s", exc)
        return ""


def service_label(service_id: str) -> str:
    return _LABELS.get(service_id, service_id)


def explicit_music_service(text: str) -> str | None:
    """The service the utterance names, or None when it names none (or both)."""
    hits = [service for service, pattern in _EXPLICIT if pattern.search(text or "")]
    if len(hits) == 1:
        return hits[0]
    return None


def resolve_music_service(
    text: str,
    *,
    preferred: str,
    connected: Iterable[str],
    matched: str | None = None,
) -> str | None:
    """The connector this request should go to (see the module docstring).

    ``connected`` are the music plugin ids holding a usable credential;
    ``matched`` is the plugin id of the skill the deterministic matcher picked,
    if any. Returns None only when nothing is connected and nothing matched.
    """
    live = [pid for pid in MUSIC_PLUGIN_IDS if pid in set(connected)]
    named = explicit_music_service(text)
    if named:
        return named
    pref = (preferred or MUSIC_SERVICE_AUTO).strip().lower()
    if pref != MUSIC_SERVICE_AUTO and pref in live:
        return pref
    if len(live) == 1:
        return live[0]
    if matched in MUSIC_PLUGIN_IDS:
        return matched
    return live[0] if live else None


def preference_hint(service_id: str, *, preferred: str, connected: Iterable[str]) -> str:
    """One sentence for a music tool's description, so the LLM router honours
    the same preference the skill capture does.

    With only one service connected, the sentence names that service (and
    tells the disconnected sibling not to take unnamed requests) — otherwise
    a live model still sees both declarations and picks the disconnected
    one. Empty only when nothing is connected, or two are connected with no
    named preference (``auto``).
    """
    live = [pid for pid in MUSIC_PLUGIN_IDS if pid in set(connected)]
    if len(live) == 1:
        only = live[0]
        if service_id == only:
            return (
                f" {service_label(only)} is the connected music service: use this tool "
                "for any music request that does not name another service."
            )
        if service_id in MUSIC_PLUGIN_IDS:
            return (
                f" {service_label(service_id)} is not connected. Use "
                f"{service_label(only)} for music unless the user names "
                f"{service_label(service_id)} explicitly."
            )
        return ""
    pref = (preferred or MUSIC_SERVICE_AUTO).strip().lower()
    if len(live) < 2 or pref == MUSIC_SERVICE_AUTO or pref not in live:
        return ""
    if service_id == pref:
        return (
            f" The user prefers {service_label(pref)} for music: use this tool for any music "
            "request that does not name another service."
        )
    return (
        f" The user prefers {service_label(pref)} for music: use this tool only when the "
        f"request names {service_label(service_id)} explicitly."
    )


def compose_tool_description(service_id: str, base: str) -> str:
    """Put the preference/connection sentence FIRST so a 450-character compact
    live-model description still carries it."""
    hint = description_hint(service_id).strip()
    body = str(base or "").strip()
    if not hint:
        return body
    return f"{hint} {body}" if body else hint


def reroute_music_tool(called: str, text: str) -> str:
    """The music tool this call should actually run (see module docstring).

    ``called`` is the tool the model named. Unnamed requests go to the
    preferred connected service, else the only connected one. A named
    service still wins. Never raises — a fault keeps ``called``.
    """
    if called not in MUSIC_PLUGIN_IDS:
        return called
    try:
        target = resolve_music_service(
            text,
            preferred=preferred_music_service(),
            connected=connected_music_services(),
            matched=called,
        )
    except Exception as exc:  # noqa: BLE001 — a routing nicety must never break a turn
        log.debug("music tool reroute skipped: %s", exc)
        return called
    return target if target in MUSIC_PLUGIN_IDS else called


def adapt_music_arguments(
    text: str,
    *,
    source: str,
    target: str,
    args: dict[str, object] | None,
) -> dict[str, object]:
    """Map arguments across the two music tools and, for a taste request
    ("something I like"), play liked songs instead of searching the words
    as a title."""
    out: dict[str, object] = dict(args or {})
    if source != target:
        item_type = str(out.get("type") or "")
        mapped = _TYPE_MAP.get((source, target), {}).get(item_type)
        if mapped:
            out["type"] = mapped
        action = str(out.get("action") or "").strip()
        if target == MUSIC_SERVICE_YOUTUBE_MUSIC and action in {
            "list_devices",
            "queue",
        }:
            out["action"] = "play"
        elif target == MUSIC_SERVICE_SPOTIFY and action in {
            "liked_songs",
            "like",
            "open",
            "hide_player",
            "add_to_playlist",
            "create_playlist",
        }:
            if action in {"liked_songs", "like"}:
                out["action"] = "play"
                out["type"] = "playlist"
                out.setdefault("query", "Liked Songs")
            elif action in {"open", "hide_player"}:
                out["action"] = "now_playing"
            else:
                out["action"] = "play"
    return _apply_taste(text, target, out)


def _apply_taste(
    text: str, target: str, args: dict[str, object]
) -> dict[str, object]:
    if not _TASTE_RE.search(text or ""):
        return args
    action = str(args.get("action") or "play").strip() or "play"
    if action not in {"play", "search", "now_playing"}:
        return args
    query = str(args.get("query") or "").strip()
    if query and not _TASTE_RE.search(query):
        # A concrete title in `query` still wins over the taste phrasing.
        return args
    args["action"] = "play"
    if query:
        args.pop("query", None)
    if target == MUSIC_SERVICE_YOUTUBE_MUSIC:
        args["type"] = "liked"
    else:
        args["type"] = "playlist"
        args["query"] = "Liked Songs"
    return args


__all__ = [
    "adapt_music_arguments",
    "compose_tool_description",
    "connected_music_services",
    "description_hint",
    "explicit_music_service",
    "forget_connected_music_services",
    "preference_hint",
    "preferred_music_service",
    "reroute_music_tool",
    "resolve_music_service",
    "service_label",
]

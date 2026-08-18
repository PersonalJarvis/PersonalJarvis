"""Music-plugin vocabulary — the single source of truth (five-layer pattern L0).

Two music connectors (Spotify, YouTube Music) share one domain, so two
settings arbitrate them: which service a request that names no service goes
to, and where YouTube Music playback happens. Every layer imports THESE
symbols — the config ``Literal`` is asserted against them at import time, the
REST route derives its accepted set from them, the frontend mirrors them as a
TypeScript union (``src/lib/musicSettings.ts``) pinned by a parity test — so a
value can never be spelled differently in one place than in another (BUG-008).
"""
from __future__ import annotations

# `preferred_service`: the connector a music request goes to when the user
# does not name one. "auto" = the only connected service, else the first
# music skill by catalog order (Spotify) — the same behaviour as before the
# setting existed.
MUSIC_SERVICE_AUTO = "auto"
MUSIC_SERVICE_SPOTIFY = "spotify"
MUSIC_SERVICE_YOUTUBE_MUSIC = "youtube_music"
MUSIC_SERVICES: tuple[str, ...] = (
    MUSIC_SERVICE_AUTO,
    MUSIC_SERVICE_SPOTIFY,
    MUSIC_SERVICE_YOUTUBE_MUSIC,
)

# `playback`: where YouTube Music plays. "background" = the hidden in-app
# player window (no browser tab, no focus steal); "browser" = the system
# browser, the pre-2026-08-18 behaviour and the fallback wherever the player
# cannot run.
MUSIC_PLAYBACK_BACKGROUND = "background"
MUSIC_PLAYBACK_BROWSER = "browser"
MUSIC_PLAYBACK_MODES: tuple[str, ...] = (MUSIC_PLAYBACK_BACKGROUND, MUSIC_PLAYBACK_BROWSER)

# The catalog plugin ids that count as music services (their paired skills are
# ``plugin-<id>``). Order = the "auto" tie-break order.
MUSIC_PLUGIN_IDS: tuple[str, ...] = (MUSIC_SERVICE_SPOTIFY, MUSIC_SERVICE_YOUTUBE_MUSIC)

__all__ = [
    "MUSIC_PLAYBACK_BACKGROUND",
    "MUSIC_PLAYBACK_BROWSER",
    "MUSIC_PLAYBACK_MODES",
    "MUSIC_PLUGIN_IDS",
    "MUSIC_SERVICES",
    "MUSIC_SERVICE_AUTO",
    "MUSIC_SERVICE_SPOTIFY",
    "MUSIC_SERVICE_YOUTUBE_MUSIC",
]

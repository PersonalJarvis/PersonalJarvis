"""Spawn-acknowledgement language must mirror the user's spoken language.

Live bug 2026-06-14 (voice transcript 15:00): an English travel question
("Could you please tell me which city you would recommend me if I would like
to book a trip to Australia? It's a really interesting country for me.") was
force-spawned to a worker, and the spoken ACK came back in German
("Ich lasse meine Reiseexperten ... sondieren") even though Reply Language was
"Automatic".

Root cause: ``_spawn_ack_language`` used the weak ``_looks_german`` stop-word
heuristic, which scored the English sentence 0-0 and broke the tie to German
(``score_de >= score_en``). The fix routes the spawn ACK through the canonical
``jarvis.core.turn_language.detect_text_language`` — the same single source of
truth the pipeline already uses for the turn language — instead of a private,
weaker detector.

The spawn-announcement composer supports de/en only (ack-brain convention), so
Spanish/unknown text under "auto" collapses to English (never silently German).
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager, _looks_german

# The exact English utterance from the 2026-06-14 transcript that regressed to
# a German spoken ACK.
AUSTRALIA_EN = (
    "Could you please tell me which city you would recommend me if I would "
    "like to book a trip to Australia? It's a really interesting country for me."
)


def _manager(reply_language: str) -> BrainManager:
    """A BrainManager with __init__ bypassed — only ``_reply_language`` matters."""
    m = BrainManager.__new__(BrainManager)
    m._reply_language = reply_language
    return m


def test_english_force_spawn_utterance_acks_in_english() -> None:
    """THE live bug: an English force-spawn must not be acknowledged in German."""
    assert _manager("auto")._spawn_ack_language(AUSTRALIA_EN) == "en"


def test_german_force_spawn_utterance_acks_in_german() -> None:
    text = "Oeffne bitte den Browser"  # i18n-allow: German voice fixture
    assert _manager("auto")._spawn_ack_language(text) == "de"


def test_pinned_german_always_wins_over_english_text() -> None:
    assert _manager("de")._spawn_ack_language(AUSTRALIA_EN) == "de"


def test_pinned_english_always_wins_over_german_text() -> None:
    text = "Mach bitte das Licht im Wohnzimmer an"  # i18n-allow: German voice fixture
    assert _manager("en")._spawn_ack_language(text) == "en"


def test_spanish_auto_collapses_to_english_for_de_en_composer() -> None:
    # The composer speaks de/en only; Spanish must degrade to English, not German.
    assert _manager("auto")._spawn_ack_language("¿Qué ciudad me recomiendas?") == "en"


def test_ambiguous_utterance_does_not_default_to_german() -> None:
    # Regression guard for the tie-to-German bug class: zero-signal text must
    # fall to the English default (canonical module default), never German.
    assert _manager("auto")._spawn_ack_language("Spotify.") == "en"


# ---------------------------------------------------------------------------
# The shared root function. ``_looks_german`` backs FOUR language decisions
# (spawn ACK, the "nothing found" fallback, and two ResponseGenerated language
# tags). Pinning it directly guards the whole bug class, not just one site.
# ---------------------------------------------------------------------------


def test_looks_german_rejects_clear_english() -> None:
    """The 0-0 tie-to-German trap: a clean English sentence is NOT German."""
    assert _looks_german(AUSTRALIA_EN) is False


def test_looks_german_accepts_clear_german() -> None:
    assert _looks_german("Mach bitte das Licht an") is True  # i18n-allow: German fixture


def test_looks_german_rejects_spanish() -> None:
    assert _looks_german("¿Qué tiempo hace hoy en Madrid?") is False


def test_looks_german_rejects_ambiguous_zero_signal_text() -> None:
    # "Spotify." has no language signal — the old stop-word heuristic scored it
    # 0-0 and returned True (German). It must now be False.
    assert _looks_german("Spotify.") is False

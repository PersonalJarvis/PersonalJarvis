"""Unit tests for the mission-inject directive composer."""
from __future__ import annotations

from jarvis.ui.web.mission_inject import MISSION_INJECT_CAP, compose_mission_inject_text


def test_compose_uses_utterance_status_and_summary() -> None:
    text = compose_mission_inject_text(
        {
            "slug": "20260615__recherchiere__abc123",
            "utterance": "recherchiere AI-News",
            "status": "success",
            "summary": "Found three reports on model releases.",
        }
    )
    assert text is not None
    assert "recherchiere AI-News" in text
    assert "success" in text
    assert "Found three reports" in text
    # Emoji-prefixed so it reads as a deliberate "pulled in" turn.
    assert text.startswith("\U0001F4CE")


def test_compose_includes_error_when_present() -> None:
    text = compose_mission_inject_text(
        {"utterance": "build the thing", "status": "error", "error": "boom: exit 2"}
    )
    assert text is not None
    assert "boom: exit 2" in text


def test_compose_returns_none_for_empty_payload() -> None:
    assert compose_mission_inject_text({}) is None
    assert compose_mission_inject_text({"utterance": "  ", "slug": ""}) is None


def test_compose_avoids_router_spawn_trigger_words() -> None:
    """The injected turn must read as conversation, not a spawn order.

    The router force-spawns on "sub-agent"/"spawn"/"delegate"/"openclaw" and on
    action verbs. A dropped mission must be *discussed*, never re-dispatched
    (spec AP-5/AP-14) — so the directive must not contain those triggers.
    """
    text = compose_mission_inject_text(
        {
            "slug": "s",
            "utterance": "Write a 200-word origin story for a lighthouse keeper named Bo.",
            "status": "success",
            "summary": "A 200-word story about Bo.",
        }
    )
    assert text is not None
    lowered = text.lower()
    for trigger in ("sub-agent", "subagent", "spawn", "delegate", "openclaw"):
        assert trigger not in lowered, f"directive contains spawn trigger {trigger!r}"


def test_compose_caps_length() -> None:
    text = compose_mission_inject_text(
        {"utterance": "x", "status": "success", "summary": "y" * 10_000}
    )
    assert text is not None
    assert len(text) <= MISSION_INJECT_CAP

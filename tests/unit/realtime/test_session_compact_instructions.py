"""The compact instruction profile for small self-hosted realtime brains.

A 7B brain prefills the ENTIRE instruction block every turn; the full
~24k-char profile cost 7.8 s of LLM time per answer (live 2026-08-07,
qwen2.5:7b behind the local-realtime server). What these tests pin:

- compact is a real reduction, not a cosmetic one;
- the assembly is static-first / dynamic-last so a prefix-caching server
  (Ollama) only re-reads the per-turn tail;
- cloud providers keep the exact historical text (flag absent = byte-equal
  to the pre-compact behavior);
- the session captures the capability at accept and uses it per turn.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.brain.persona_loader import invalidate_cache
from jarvis.realtime.session import RealtimeVoiceSession, _session_instructions

from .test_session import FakeProvider, _cfg


@pytest.fixture(autouse=True)
def _fresh_persona_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _build(compact: bool) -> str:
    return _session_instructions(
        "de",
        input_language="auto",
        provider="p",
        model="m",
        language_is_pinned=True,
        tool_directive="TOOL DIRECTIVE",
        preferences="PREFS",
        workspace_directive="WORKSPACE ROSTER",
        skill_directive="SKILL BODY",
        compact=compact,
    )


def test_compact_is_a_real_reduction() -> None:
    full = _build(compact=False)
    compact = _build(compact=True)
    assert len(compact) < len(full) * 0.6
    # The load-bearing pieces survive.
    assert "TOOL DIRECTIVE" in compact
    assert "PREFS" in compact
    assert "Reply only in German" in compact


def test_compact_orders_static_first_dynamic_last() -> None:
    """The per-turn dynamic pieces must sit BEHIND every static block, so
    the unchanged head stays a byte-stable prefix across session updates."""
    compact = _build(compact=True)
    static_markers = ["voice companion", "TOOL DIRECTIVE", "Runtime identity"]
    dynamic_markers = [
        "WORKSPACE ROSTER",
        "SKILL BODY",
        "Current local date and time",
        "Reply only in German",
    ]
    last_static = max(compact.index(marker) for marker in static_markers)
    first_dynamic = min(compact.index(marker) for marker in dynamic_markers)
    assert last_static < first_dynamic


def test_compact_head_is_stable_across_turn_rebuilds() -> None:
    """Two rebuilds that differ only in per-turn dynamics share their whole
    static head — the property Ollama's prefix cache actually exploits."""
    first = _session_instructions(
        "de",
        tool_directive="TOOL DIRECTIVE",
        preferences="PREFS",
        workspace_directive="ROSTER ONE",
        compact=True,
    )
    second = _session_instructions(
        "en",
        tool_directive="TOOL DIRECTIVE",
        preferences="PREFS",
        workspace_directive="ROSTER TWO",
        compact=True,
    )
    head = first[: first.index("ROSTER ONE")]
    assert second.startswith(head)


def test_flag_absent_means_the_historical_profile() -> None:
    """``compact`` defaults off: providers that never declared the
    capability get a byte-identical prompt to the explicit False path."""
    assert _build(compact=False) == _session_instructions(
        "de",
        input_language="auto",
        provider="p",
        model="m",
        language_is_pinned=True,
        tool_directive="TOOL DIRECTIVE",
        preferences="PREFS",
        workspace_directive="WORKSPACE ROSTER",
        skill_directive="SKILL BODY",
    )


class _CompactProvider(FakeProvider):
    name = "compact-family"
    prefers_compact_instructions = True


@pytest.mark.asyncio
async def test_session_captures_the_capability_at_accept():
    provider = _CompactProvider([])
    sess = RealtimeVoiceSession(
        session_id="compact-capture",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[provider],
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.end(reason="test")
    assert sess._compact_instructions is True
    # The provider actually received the compact block at open.
    opened = provider.opened_with
    assert len(opened.instructions) < 15_000


@pytest.mark.asyncio
async def test_session_defaults_to_the_full_profile():
    provider = FakeProvider([])
    sess = RealtimeVoiceSession(
        session_id="full-capture",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[provider],
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.end(reason="test")
    assert sess._compact_instructions is False

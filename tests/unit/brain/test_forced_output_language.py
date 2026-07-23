"""Realtime-delegate output-language override (live forensic 2026-07-23).

Root cause this guards against: a realtime voice conversation resolved to
English (the realtime session's own model reply and the recorded ``jarvis_lang``
both said ``en``), but a ``jarvis_action`` turn was delegated to the classic
BrainManager, which RE-DERIVED the language from the transcript. On a memory-
save turn the German-only router prompt then made it answer "Notiert ..." in
German — two layers, one turn, two languages (the "no layer re-derives the
language" doctrine violation).

The fix: the realtime session hands the delegate its own resolved output
language via ``generate(..., force_output_language=...)``; the manager pins it
so ``_reply_language_directive`` hard-locks that language instead of the
transcript's detected one. An explicit ``brain.reply_language`` pin still wins.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainDelta, BrainRequest

# A statement whose text alone detects as German, and one that detects as
# English — the two sides of the code-switch that made the delegate diverge.
_GERMAN_FACT = "Mein Bruder heisst Tom und wohnt schon lange in Berlin"  # i18n-allow: German test fixture — the German half of the code-switch under test
_ENGLISH_FACT = "My brother is a doctor and he lives here with me now"


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(self, user_text, *, images=(), history=None, **_kwargs):
        self.calls.append({"user_text": user_text})
        agg = StreamingAggregate()
        agg.text = "reply"
        agg.finish_reason = "stop"
        return agg


def _manager(reply_language: str = "auto") -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    cfg.brain.reply_language = reply_language
    m = BrainManager(config=cfg, bus=EventBus(), tools={})
    m._build_fallback_chain = lambda _l: [("fake", "fake-model")]  # type: ignore[method-assign]
    m._get_brain = lambda _n, _mo: _FakeBrain()  # type: ignore[method-assign]
    m._build_dispatcher = lambda _b, *, tools_override=None: _RecordingDispatcher()  # type: ignore[method-assign]
    return m


@pytest.mark.asyncio
async def test_auto_mode_redetects_german_without_force() -> None:
    """Baseline (the bug): auto mode pins the transcript's own language."""
    m = _manager("auto")
    await m.generate(_GERMAN_FACT, trace_id=uuid4(), use_history=False)
    assert m._turn_detected_lang == "de"
    assert "German" in m._reply_language_directive()


@pytest.mark.asyncio
async def test_force_output_language_overrides_detected_language() -> None:
    """The fix: a forced language beats the transcript's detected one."""
    m = _manager("auto")
    await m.generate(
        _GERMAN_FACT,
        trace_id=uuid4(),
        use_history=False,
        force_output_language="en",
    )
    assert m._turn_detected_lang == "en"
    directive = m._reply_language_directive()
    assert "English" in directive
    assert "MANDATORY" in directive


@pytest.mark.asyncio
async def test_explicit_pin_wins_over_forced_language() -> None:
    """A user's explicit reply-language pin outranks the realtime force."""
    m = _manager("de")
    await m.generate(
        _ENGLISH_FACT,
        trace_id=uuid4(),
        use_history=False,
        force_output_language="en",
    )
    # Pinned mode leaves _turn_detected_lang empty; the directive uses the pin.
    assert "German" in m._reply_language_directive()


@pytest.mark.asyncio
async def test_invalid_force_language_is_ignored() -> None:
    """Garbage force value falls through to normal per-turn detection."""
    m = _manager("auto")
    await m.generate(
        _ENGLISH_FACT,
        trace_id=uuid4(),
        use_history=False,
        force_output_language="klingon",
    )
    assert m._turn_detected_lang == "en"


def test_router_note_prefix_is_language_relative() -> None:
    """The memory-save prefix must follow the reply language, not the prompt.

    A hardcoded German "Notiert" prefix forced German replies on English
    memory-save turns (the other half of the 2026-07-23 bug). The router
    instruction now names the acknowledgement word in every supported language.
    """
    from jarvis.brain.router import SYSTEM_PROMPT

    assert "Noted" in SYSTEM_PROMPT
    assert "Anotado" in SYSTEM_PROMPT
    # German stays present — it is just no longer the only mandated word.
    assert "Notiert" in SYSTEM_PROMPT


def test_ack_keywords_cover_all_supported_languages() -> None:
    """The memory-signal detector must fire on the ack word in any language.

    The wiki ack path keys off the brain's acknowledgement word; if the reply
    is English/Spanish the word is not "notiert", so the detector must know the
    other languages' words too, or a non-German memory-save would be missed.
    """
    from jarvis.memory.wiki.voice_bridge import _ACK_KEYWORDS

    assert "notiert" in _ACK_KEYWORDS  # de
    assert "noted" in _ACK_KEYWORDS  # en
    assert "anotado" in _ACK_KEYWORDS  # es

"""Spoken fallback phrases must match the user's language (live bug 2026-06-10 23:13).

Both German replies the user heard on English turns were HARDCODED German
fallback strings, not LLM output (data/jarvis_desktop.log):

- 23:13:24 the tool_use_loop anti-silence fallback (tool not in the router
  tool set) spoke the German anti-silence phrase on the English turn
  "Hey, what's the weather like today?".
- 23:12:56 leaked search_web tool-call recovery → spoke the German
  leak-recovery phrase on "What's weather like tomorrow?".

Contract: a pinned reply language (brain.reply_language = de/en/es) wins; in
``auto`` mode the phrase mirrors the language detected from the user's text;
ambiguous text keeps the historical German default.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.brain.tool_use_loop import (
    _anti_silence_phrase,
    _meta_debug_ack_phrase,
)
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult

_EN_ANTI_SILENCE = "I can't do that right now — I'm missing the right tool for it."
_DE_ANTI_SILENCE_MARKER = "mir fehlt dafuer das passende Werkzeug"  # i18n-allow: German TTS phrase
_DE_ACTION_FAILED = "Ich habe die Aktion erkannt, konnte sie aber nicht ausfuehren."  # i18n-allow
_EN_ACTION_FAILED = "I recognized the action but couldn't execute it."
_ES_ACTION_FAILED = "Reconocí la acción, pero no pude ejecutarla."

_DE_OPEN_EDITOR = "Oeffne bitte den Editor fuer mich"  # i18n-allow: German voice fixture
_DE_CLOSE_WINDOW = "Mach bitte das Fenster zu"  # i18n-allow: German voice fixture
_DE_META_DEBUG = "Warum hat der Provider-Fallback gegriffen?"  # i18n-allow: German voice fixture


class _FakeTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _NullExecutor:
    async def execute(self, *a: Any, **kw: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


def _manager(reply_language: str = "auto") -> BrainManager:
    config = JarvisConfig()
    config.brain.reply_language = reply_language
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=_NullExecutor(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# tool_use_loop anti-silence fallback
# ---------------------------------------------------------------------------

def test_anti_silence_phrase_english_for_english_turn() -> None:
    phrase = _anti_silence_phrase("Hey, what's the weather like today?", "auto")
    assert phrase == _EN_ANTI_SILENCE


def test_anti_silence_phrase_german_for_german_turn() -> None:
    phrase = _anti_silence_phrase(_DE_OPEN_EDITOR, "auto")
    assert _DE_ANTI_SILENCE_MARKER in phrase


def test_anti_silence_phrase_pin_beats_text_detection() -> None:
    phrase = _anti_silence_phrase("Hey, what's the weather like today?", "de")
    assert _DE_ANTI_SILENCE_MARKER in phrase


def test_anti_silence_phrase_ambiguous_text_defaults_german() -> None:
    phrase = _anti_silence_phrase("", "auto")
    assert _DE_ANTI_SILENCE_MARKER in phrase


def test_meta_debug_ack_phrase_localized() -> None:
    en = _meta_debug_ack_phrase("Why did the provider fallback trigger?", "auto")
    de = _meta_debug_ack_phrase(_DE_META_DEBUG, "auto")
    assert en != de
    assert en.startswith("Understood")


# ---------------------------------------------------------------------------
# BrainManager leak-recovery fallback (generate_stream)
# ---------------------------------------------------------------------------

def test_action_failed_phrase_english_for_english_turn() -> None:
    manager = _manager()
    phrase = manager._action_failed_phrase("What's weather like tomorrow?")
    assert phrase == _EN_ACTION_FAILED


def test_action_failed_phrase_german_for_german_turn() -> None:
    manager = _manager()
    phrase = manager._action_failed_phrase(_DE_CLOSE_WINDOW)
    assert phrase == _DE_ACTION_FAILED


def test_action_failed_phrase_respects_reply_language_pin() -> None:
    manager = _manager(reply_language="de")
    phrase = manager._action_failed_phrase("What's weather like tomorrow?")
    assert phrase == _DE_ACTION_FAILED


def test_action_failed_phrase_spanish_pin() -> None:
    manager = _manager(reply_language="es")
    phrase = manager._action_failed_phrase("What's weather like tomorrow?")
    assert phrase == _ES_ACTION_FAILED

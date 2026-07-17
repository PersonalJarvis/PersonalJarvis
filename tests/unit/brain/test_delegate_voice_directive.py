"""Delegated-voice speed contract in the dispatcher system prompt.

Live forensic 2026-07-17 (turn af736681): a delegated voice turn burned five
sequential tool-loop rounds — three near-identical wiki-recall calls, then
wiki-list, then wiki-page-read — into the 20 s deadline. Round count is the
dominant latency on the delegated path, so delegated turns get an explicit
static directive: batch independent lookups into one round, never repeat a
call, answer as soon as the evidence suffices.

Contract under test:
  1. ``_build_dispatcher(delegated_voice=True)`` appends the directive.
  2. Classic dispatchers (default) stay byte-identical — no directive.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.manager import _DELEGATE_VOICE_DIRECTIVE, BrainManager


def _bare_manager() -> Any:
    """A BrainManager shell sufficient for _build_dispatcher."""
    manager = BrainManager.__new__(BrainManager)
    manager._tools = {}
    manager._tool_executor = None
    manager._config = type(
        "Cfg", (), {"brain": type("BrainCfg", (), {"max_tokens": 1024})()}
    )()
    manager._build_system_prompt = lambda: "BASE PROMPT"  # type: ignore[method-assign]
    manager._plugin_usage_cards_block = lambda tools: ""  # type: ignore[method-assign]
    return manager


def test_delegated_voice_dispatcher_carries_the_speed_contract() -> None:
    manager = _bare_manager()

    dispatcher = manager._build_dispatcher(object(), delegated_voice=True)

    assert dispatcher._system_prompt.startswith("BASE PROMPT")
    assert _DELEGATE_VOICE_DIRECTIVE in dispatcher._system_prompt


def test_classic_dispatcher_prompt_stays_unchanged() -> None:
    manager = _bare_manager()

    dispatcher = manager._build_dispatcher(object())

    assert dispatcher._system_prompt == "BASE PROMPT"
    assert _DELEGATE_VOICE_DIRECTIVE not in dispatcher._system_prompt

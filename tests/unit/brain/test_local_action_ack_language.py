"""The DIRECT local-action fast path must acknowledge in the turn's language.

Live bug 2026-06-15: an English voice turn ("Could you please open the Explorer
for me?") was answered "Gestartet: explorer" — German — even with the desktop
"Languages" pin set to English. Root cause: the DIRECT fast path
(``BrainManager._run_local_action_fast_path``) surfaces the tool's ``output``
VERBATIM to the user, with no LLM re-render. ``open_app`` hardcodes its success
string as German (``f"Gestartet: {app_name}"``), so it bypasses the entire
language-pin machinery (``_reply_language_directive`` / ``_turn_detected_lang``)
that only governs LLM-generated replies. The acknowledgement must therefore be
localized at the surfacing point.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.local_action_gate import (
    LocalActionMode,
    LocalActionPlan,
    LocalToolCall,
)
from jarvis.brain.manager import BrainManager


class _FakeBus:
    async def publish(self, event) -> None:  # noqa: ANN001
        pass


class _OpenAppExecutor:
    """tool_executor stand-in mimicking open_app's hardcoded German output."""

    def __init__(self, *, app: str = "explorer") -> None:
        self.app = app
        self.called_with: dict | None = None

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        self.called_with = dict(args)
        # Byte-identical to jarvis/plugins/tool/open_app.py success output.
        return SimpleNamespace(
            success=True, output=f"Gestartet: {args.get('app_name')}", error=None
        )


def _make_direct_manager(*, reply_language: str = "auto", turn_lang: str = ""):
    mgr = BrainManager.__new__(BrainManager)
    mgr._config = SimpleNamespace(
        local_action=SimpleNamespace(
            enabled=True, harness_timeout_s=30.0, direct_timeout_s=3.0
        )
    )
    mgr._bus = _FakeBus()
    mgr._tool_executor = _OpenAppExecutor()
    mgr._local_action_tools = {"open_app": object()}
    mgr._cost_meter = None
    mgr._reply_language = reply_language
    mgr._turn_detected_lang = turn_lang
    return mgr


def _direct_open_explorer_plan(_text: str) -> LocalActionPlan:
    return LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(LocalToolCall(name="open_app", args={"app_name": "explorer"}),),
    )


@pytest.mark.asyncio
async def test_english_pin_acknowledges_in_english(monkeypatch) -> None:
    # Desktop "Languages" view set to English (brain.reply_language="en").
    mgr = _make_direct_manager(reply_language="en")
    monkeypatch.setattr(
        "jarvis.brain.manager.match_local_action", _direct_open_explorer_plan
    )

    reply = await mgr._run_local_action_fast_path(
        "Could you please open the Explorer for me?"
    )

    assert reply is not None
    assert "Gestartet" not in reply, f"English turn got a German ack: {reply!r}"
    assert "explorer" in reply.lower()


@pytest.mark.asyncio
async def test_auto_mode_mirrors_english_text(monkeypatch) -> None:
    # No explicit pin: the turn's English text must drive the ack language.
    mgr = _make_direct_manager(reply_language="auto")
    monkeypatch.setattr(
        "jarvis.brain.manager.match_local_action", _direct_open_explorer_plan
    )

    reply = await mgr._run_local_action_fast_path(
        "Can you open for me my explorer and find my newest video?"
    )

    assert reply is not None
    assert "Gestartet" not in reply, f"English turn got a German ack: {reply!r}"


@pytest.mark.asyncio
async def test_german_stays_german(monkeypatch) -> None:
    # Regression guard: a German turn must keep the exact historical ack.
    mgr = _make_direct_manager(reply_language="auto")
    monkeypatch.setattr(
        "jarvis.brain.manager.match_local_action", _direct_open_explorer_plan
    )

    reply = await mgr._run_local_action_fast_path("öffne den Explorer")

    assert reply == "Gestartet: explorer"


@pytest.mark.asyncio
async def test_german_pin_stays_german(monkeypatch) -> None:
    # An explicit German pin keeps German even for English text.
    mgr = _make_direct_manager(reply_language="de")
    monkeypatch.setattr(
        "jarvis.brain.manager.match_local_action", _direct_open_explorer_plan
    )

    reply = await mgr._run_local_action_fast_path("open the explorer please")

    assert reply == "Gestartet: explorer"

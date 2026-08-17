"""GT-19: the deterministic Computer-Use fast path never promises what it cannot do.

The fast path used to ACK ("Mach ich") and start the harness in the background
*before* checking that Computer-Use is actually wired. It is wired only when
``[computer_use].enabled`` is true AND a vision engine could be built
(``brain/factory.py`` calls ``set_computer_use_context``). On every other
machine — the shipped default among them — each harness step then died deep
inside the loop, so the user heard a commitment, then silence, then a failure up
to 180 s later.

The LLM tool path has guarded this since 2026-07-06
(``plugins/tool/computer_use_tool.py::execute``). These tests pin the same guard
on the deterministic path: no dispatch, and an immediate honest sentence in the
turn's own language.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.local_action_gate import LocalActionMode, LocalActionPlan
from jarvis.brain.manager import BrainManager
from jarvis.harness.computer_use_context import set_computer_use_context
from jarvis.voice.action_phrases import action_phrase


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:  # noqa: ANN001
        self.published.append(event)


class _RecordingExecutor:
    """A tool executor that must never be reached on an unwired machine."""

    def __init__(self) -> None:
        self.calls: list = []

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        self.calls.append(args)
        return SimpleNamespace(success=True, output={}, error=None)


@pytest.fixture(autouse=True)
def _unwired_computer_use_context():
    """No context — Computer-Use is OFF on this machine."""
    set_computer_use_context(None)
    yield
    set_computer_use_context(None)


def _make_manager(executor, bus, *, reply_language: str = "auto"):
    mgr = BrainManager.__new__(BrainManager)
    mgr._config = SimpleNamespace(
        local_action=SimpleNamespace(enabled=True, harness_timeout_s=30.0, direct_timeout_s=3.0)
    )
    mgr._bus = bus
    mgr._tool_executor = executor
    mgr._local_action_tools = {"dispatch_to_harness": object()}
    mgr._cost_meter = None
    mgr._reply_language = reply_language
    mgr._history = []
    return mgr


def _pin_cu_plan(monkeypatch) -> None:
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open chrome"
    )
    monkeypatch.setattr(
        "jarvis.brain.manager.match_local_action", lambda _t, **_kw: plan
    )


@pytest.mark.asyncio
async def test_unwired_computer_use_never_dispatches(monkeypatch) -> None:
    executor = _RecordingExecutor()
    mgr = _make_manager(executor, _FakeBus())
    _pin_cu_plan(monkeypatch)

    await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow: DE voice fixture

    assert not executor.calls, "dispatched to the harness although Computer-Use is off"
    assert not getattr(mgr, "_cu_background_tasks", set()), (
        "started a background mission although Computer-Use is off"
    )


@pytest.mark.asyncio
async def test_unwired_computer_use_speaks_instead_of_acking(monkeypatch) -> None:
    """The user hears an honest refusal, not silence and not "Mach ich"."""
    mgr = _make_manager(_RecordingExecutor(), _FakeBus())
    _pin_cu_plan(monkeypatch)

    reply = await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow: DE voice fixture

    assert reply, "returned nothing — the user would hear silence"
    assert reply == action_phrase("cu_not_wired", "de")
    assert reply != action_phrase("cu_dispatch_ack", "de"), "still promises to do it"
    # The refusal must be ACTIONABLE, not just honest: it names the switch the
    # user can actually flip. A generic "can't do that right now" would leave
    # them with no way forward.
    assert "computer_use.enabled" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance,lang", [
    ("öffne chrome", "de"),  # i18n-allow: DE language-detection fixture
    ("open chrome and search for a flight", "en"),
])
async def test_unwired_refusal_follows_the_turn_language(
    monkeypatch, utterance: str, lang: str,
) -> None:
    """The refusal is localized by the same resolver as the ACK it replaces."""
    mgr = _make_manager(_RecordingExecutor(), _FakeBus())
    _pin_cu_plan(monkeypatch)

    reply = await mgr._run_local_action_fast_path(utterance)

    assert reply == action_phrase("cu_not_wired", lang)


def test_cu_not_wired_is_translated_in_every_locale() -> None:
    """All locales rank equally (contract §1) — none may fall back to German.

    ``action_phrase`` silently serves the German column for a missing language,
    so a forgotten translation would ship as an untranslated German sentence to
    an English or Spanish speaker instead of failing loudly here.
    """
    from jarvis.voice.action_phrases import _PHRASES

    variants = _PHRASES["cu_not_wired"]
    assert set(variants) == {"de", "en", "es"}
    assert len(set(variants.values())) == 3, f"a locale was left untranslated: {variants}"
    # The actionable half must survive translation in every locale.
    assert all("computer_use.enabled" in text for text in variants.values())


@pytest.mark.asyncio
async def test_missing_dispatch_tool_is_logged_not_swallowed(monkeypatch, caplog) -> None:
    """No dispatcher registered: the turn falls through to the brain, but audibly.

    ``None`` is the correct return (the ``computer_use`` router tool carries its
    own preflight), yet the drop must leave a trace instead of vanishing.
    """
    mgr = _make_manager(_RecordingExecutor(), _FakeBus())
    mgr._local_action_tools = {}
    _pin_cu_plan(monkeypatch)

    with caplog.at_level("WARNING", logger="jarvis.brain.manager"):
        reply = await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow: DE fixture

    assert reply is None
    assert any("dispatch_to_harness" in record.message for record in caplog.records), (
        "the dropped fast path left no log line"
    )

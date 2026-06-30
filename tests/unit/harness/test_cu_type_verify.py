"""Type read-back verification (audit 🔴 #1, the user's #1 complaint).

After a `type`, the loop re-reads the editable field's value from the accessibility
tree and confirms the text actually landed — instead of returning success the moment
it dispatched (which let "typed into the wrong field, didn't notice" through). On a
confirmed miss it FAILS the action so the loop re-focuses and retries (and a blind
focus->type->Enter batch stops at the type).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from jarvis.harness import screenshot_only_loop as sol
from jarvis.harness.computer_use_context import ComputerUseContext


def _node(role: str, value: str, name: str = "f") -> Any:
    return SimpleNamespace(role=role, value=value, name=name, bounds=(0, 0, 10, 10), enabled=True)


# --- pure helper: _typed_text_landed (tri-state) -----------------------------


def test_landed_true_when_editable_field_contains_text():
    nodes = (_node("Edit", "hello world", name="search"),)
    assert sol._typed_text_landed(nodes, "hello world") is True


def test_landed_true_on_substring_after_existing_text():
    # field had pre-existing text and the typed text was appended.
    nodes = (_node("Edit", "see: hello world", name="box"),)
    assert sol._typed_text_landed(nodes, "hello world") is True


def test_landed_false_when_editable_present_but_text_absent():
    nodes = (_node("Edit", "something totally different", name="search"),)
    assert sol._typed_text_landed(nodes, "hello world") is False


def test_landed_none_when_no_editable_node():
    nodes = (_node("Button", "Submit"),)
    assert sol._typed_text_landed(nodes, "hello world") is None


def test_landed_none_when_text_too_short():
    nodes = (_node("Edit", "x"),)
    assert sol._typed_text_landed(nodes, "ab") is None  # < _TYPE_VERIFY_MIN_CHARS


# --- integration: the type action gate in _execute_action --------------------


class _FakeResult:
    success = True
    output = "ok"
    error = ""


class _FakeExecutor:
    async def execute(self, tool: Any, args: dict, *, user_utterance: str = "",
                      trace_id: Any = None) -> Any:
        return _FakeResult()


class _FakeTree:
    def __init__(self, nodes: tuple) -> None:
        self._nodes = nodes

    async def observe(self, **_kw) -> Any:
        return SimpleNamespace(nodes=self._nodes)


def _ctx(*, strict: bool = True) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=_FakeExecutor(),
        tools={"type_text": object(), "hotkey": object()},
        settle_scale=0.0, strict_verify=strict,
    )


async def _type(ctx: ComputerUseContext, text: str) -> tuple:
    return await sol._execute_action(
        {"action": "type", "text": text}, ctx,
        trace_id=None, user_goal="x", monitor_geom=(0, 0, 1000, 1000), observation=None,
    )


async def test_type_confirmed_when_text_lands(monkeypatch):
    monkeypatch.setattr(sol, "_get_ui_tree_source",
                        lambda: _FakeTree((_node("Edit", "hello world", name="search"),)))
    ok, msg = await _type(_ctx(), "hello world")
    assert ok is True
    assert "confirmed" in msg.lower()


async def test_type_fails_when_text_did_not_land(monkeypatch):
    # editable field readable, but it holds something else -> confirmed miss -> FAIL,
    # so the loop re-focuses (and a focus->type->Enter batch stops here).
    monkeypatch.setattr(sol, "_get_ui_tree_source",
                        lambda: _FakeTree((_node("Edit", "unrelated", name="search"),)))
    ok, msg = await _type(_ctx(), "hello world")
    assert ok is False
    assert "did not land" in msg.lower() and "field" in msg.lower()


async def test_type_passes_when_tree_unreadable(monkeypatch):
    # no editable node -> can't tell -> keep the legacy success (no false failure).
    monkeypatch.setattr(sol, "_get_ui_tree_source",
                        lambda: _FakeTree((_node("Button", "Submit"),)))
    ok, msg = await _type(_ctx(), "hello world")
    assert ok is True
    assert msg == "ok"


async def test_short_text_skips_the_gate(monkeypatch):
    called: list = []
    monkeypatch.setattr(sol, "_get_ui_tree_source",
                        lambda: called.append(1) or _FakeTree(()))
    ok, msg = await _type(_ctx(), "ab")  # < min chars
    assert ok is True and msg == "ok"
    assert called == []  # gate skipped -> no tree fetch


async def test_strict_verify_off_skips_the_gate(monkeypatch):
    called: list = []
    monkeypatch.setattr(sol, "_get_ui_tree_source",
                        lambda: called.append(1) or _FakeTree((_node("Edit", "unrelated"),)))
    ok, msg = await _type(_ctx(strict=False), "hello world")
    assert ok is True and msg == "ok"   # legacy path despite a non-matching field
    assert called == []

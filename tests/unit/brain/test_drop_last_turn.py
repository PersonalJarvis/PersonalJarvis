"""Unit tests for BrainManager.drop_last_turn (continuation recombine, Unit C)."""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.protocols import BrainMessage


def _mgr_with_history(messages):
    mgr = BrainManager.__new__(BrainManager)
    mgr._history = list(messages)
    return mgr


def test_drops_matching_user_assistant_pair():
    mgr = _mgr_with_history([
        BrainMessage(role="user", content="hello"),
        BrainMessage(role="assistant", content="hi"),
        BrainMessage(role="user", content="ich moechte nach"),
        BrainMessage(role="assistant", content="Wohin genau?"),
    ])
    assert mgr.drop_last_turn("ich moechte nach") is True
    assert len(mgr._history) == 2
    assert mgr._history[-1].content == "hi"


def test_noop_when_tail_user_text_differs():
    mgr = _mgr_with_history([
        BrainMessage(role="user", content="something else"),
        BrainMessage(role="assistant", content="ok"),
    ])
    assert mgr.drop_last_turn("ich moechte nach") is False
    assert len(mgr._history) == 2


def test_noop_on_short_history():
    mgr = _mgr_with_history([BrainMessage(role="user", content="x")])
    assert mgr.drop_last_turn("x") is False
    assert len(mgr._history) == 1


def test_noop_when_tail_is_not_user_assistant_pair():
    mgr = _mgr_with_history([
        BrainMessage(role="assistant", content="a"),
        BrainMessage(role="assistant", content="b"),
    ])
    assert mgr.drop_last_turn("a") is False


def test_match_is_whitespace_insensitive():
    mgr = _mgr_with_history([
        BrainMessage(role="user", content="  ich moechte nach  "),
        BrainMessage(role="assistant", content="?"),
    ])
    assert mgr.drop_last_turn("ich moechte nach") is True
    assert mgr._history == []

"""Unit tests for BrainManager.seed_history (Chats conversation manager, Slice 1).

`seed_history` preseeds the brain's conversation buffer with prior turns so a
re-opened chat (text continuation) or a "Speak in this conversation" voice
session continues coherently. It is the single primitive behind both paths.

Invariants:
- Replaces (does not append to) `_history`.
- Accepts (role, text) tuples, dicts, and BrainMessage objects.
- Drops roles outside the BrainMessage vocabulary (e.g. UI-only `preamble`).
- Caps to the same window the auto-append paths use, keeping the LAST turns.
- Empty input behaves like clear_history().
- The seeded turns are visible to the real consumer (`_build_history_hints`).
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainMessage


def _bare_manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(config=cfg, bus=EventBus(), tools={})


def test_seed_history_replaces_with_tuples() -> None:
    m = _bare_manager()
    m._history.append(BrainMessage(role="user", content="stale"))

    m.seed_history([("user", "Hi"), ("assistant", "Hello there")])

    assert [(x.role, x.content) for x in m._history] == [
        ("user", "Hi"),
        ("assistant", "Hello there"),
    ]


def test_seed_history_accepts_brain_messages_and_dicts() -> None:
    m = _bare_manager()
    m.seed_history(
        [
            BrainMessage(role="user", content="from-object"),
            {"role": "assistant", "content": "from-dict"},
            {"role": "user", "text": "text-key-also-ok"},
        ]
    )
    assert [(x.role, x.content) for x in m._history] == [
        ("user", "from-object"),
        ("assistant", "from-dict"),
        ("user", "text-key-also-ok"),
    ]


def test_seed_history_drops_unknown_roles_and_empty_text() -> None:
    m = _bare_manager()
    m.seed_history(
        [
            ("preamble", "ui only"),  # not in BrainMessage vocabulary
            ("user", ""),  # empty → dropped
            ("assistant", "kept"),
        ]
    )
    assert [(x.role, x.content) for x in m._history] == [("assistant", "kept")]


def test_seed_history_caps_to_last_turns() -> None:
    m = _bare_manager()
    turns = [("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(120)]
    m.seed_history(turns)
    # Capped to the same 40-message window the auto-append paths enforce.
    assert len(m._history) == 40
    assert m._history[-1].content == "m119"
    assert m._history[0].content == "m80"


def test_seed_history_empty_clears() -> None:
    m = _bare_manager()
    m._history.append(BrainMessage(role="user", content="stale"))
    m.seed_history([])
    assert m._history == []


def test_seeded_turns_visible_to_history_hints() -> None:
    m = _bare_manager()
    m.seed_history([("user", "What is the capital of France?"), ("assistant", "Paris.")])
    hints = m._build_history_hints()
    joined = "\n".join(hints)
    assert "Paris." in joined
    assert "capital of France" in joined


@pytest.mark.asyncio
async def test_generate_history_override_is_context_local(monkeypatch) -> None:
    manager = _bare_manager()
    manager.seed_history(
        [("user", "stale unrelated request"), ("assistant", "stale answer")]
    )
    realtime_history = (
        BrainMessage(role="user", content="The launch code name is Aurora."),
        BrainMessage(role="assistant", content="Understood."),
    )

    async def observe_context(_user_text, **_kwargs):
        assert "Aurora" in (manager._last_exchange_text() or "")
        assert "stale" not in "\n".join(manager._build_history_hints())
        return "observed"

    monkeypatch.setattr(manager, "_generate", observe_context)

    result = await manager.generate(
        "Write that to the wiki.",
        use_history=False,
        history_override=realtime_history,
    )

    assert result == "observed"
    assert manager._last_exchange_text() == "user: stale unrelated request\nassistant: stale answer"

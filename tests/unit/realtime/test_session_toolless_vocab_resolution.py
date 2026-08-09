"""The tool-less delegation tie-break degrades instead of killing the pump.

``_toolless_ambiguous_action`` reuses FIVE private names from
``jarvis.brain.turn_planner`` (``_normalize`` plus four suppressor regexes) so
no second, drifting copy of that vocabulary lives in the session. Private
names are another module's internals: a rename there used to raise
``AttributeError`` at import time — or, once resolved lazily, inside the event
pump at the first user final, which kills the live call.

Pinned here: the names are resolved defensively per call, a missing one
disables the tie-break (ambiguous finals stay on the plain planner path) and
warns exactly ONCE, and the fully-present vocabulary still decides as before.
"""

from __future__ import annotations

import logging

import pytest

from jarvis.realtime import session as session_module

# A tasking phrase whose verb sits OUTSIDE the planner's action vocabulary —
# exactly the shape the tie-break exists to delegate.
AMBIGUOUS_ACTION = "Can you take care of that for me"


@pytest.fixture(autouse=True)
def _reset_vocab_warning() -> None:
    """The one-shot warning latch is module state — unlatch it per test."""
    session_module._toolless_vocab_warning_emitted = False
    yield
    session_module._toolless_vocab_warning_emitted = False


def test_the_full_vocabulary_still_decides() -> None:
    assert session_module._toolless_ambiguous_action(AMBIGUOUS_ACTION) is True
    assert session_module._resolve_toolless_vocab() is not None


@pytest.mark.parametrize("missing", session_module._TOOLLESS_VOCAB_NAMES)
def test_a_missing_planner_name_degrades_to_the_planner_path(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any renamed private name: no raise, no delegation, plain path."""
    monkeypatch.delattr(session_module._planner_vocab, missing)

    assert session_module._resolve_toolless_vocab() is None
    assert session_module._toolless_ambiguous_action(AMBIGUOUS_ACTION) is False


def test_the_missing_name_warning_is_emitted_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delattr(session_module._planner_vocab, "_OPINION_RE")

    with caplog.at_level(logging.WARNING, logger=session_module.log.name):
        for _ in range(3):
            assert (
                session_module._toolless_ambiguous_action(AMBIGUOUS_ACTION)
                is False
            )

    warnings = [
        record
        for record in caplog.records
        if "turn_planner no longer exposes" in record.getMessage()
    ]
    assert len(warnings) == 1, "the degradation notice must not spam the log"
    assert "_OPINION_RE" in warnings[0].getMessage()

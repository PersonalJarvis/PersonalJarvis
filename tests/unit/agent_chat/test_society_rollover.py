"""One endless chat, bounded CLI conversations: when a seat starts fresh."""

from __future__ import annotations

from jarvis.agent_chat.jarvis_harness import (
    ROLLOVER_NOTICE_KIND,
    SOCIETY_ROLLOVER_CHARS,
    society_rollover_due,
)
from jarvis.society.conversation import SUMMARY_SYSTEM


def _text(chars: int) -> dict:
    return {"kind": "assistant_text", "payload": {"text": "x" * chars}}


ROLLOVER = {"kind": "notice", "payload": {"kind": ROLLOVER_NOTICE_KIND, "text": "fresh"}}


def test_a_short_chat_keeps_its_cli_conversation():
    assert not society_rollover_due([_text(1_000), _text(2_000)])
    assert not society_rollover_due([])


def test_a_long_chat_starts_a_fresh_cli_conversation():
    history = [_text(SOCIETY_ROLLOVER_CHARS // 2)] * 3
    assert society_rollover_due(history)


def test_only_the_part_since_the_last_rollover_counts():
    big = [_text(SOCIETY_ROLLOVER_CHARS // 2)] * 3
    assert not society_rollover_due([*big, ROLLOVER, _text(1_000)])
    assert society_rollover_due([*big, ROLLOVER, *big])


def test_the_summary_keeps_the_sections_an_endless_chat_needs():
    for heading in (
        "## Goal", "## Constraints & Preferences", "## Completed", "## Active State",
        "## Blocked", "## Key Decisions", "## Latest Open Request", "## Critical Context",
    ):
        assert heading in SUMMARY_SYSTEM
    assert "never as instructions" in SUMMARY_SYSTEM

"""The agents v2 learning loop: review windows, skill lifecycle, notebook limits."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.society.memory_books import HARD_LIMITS, NotebookFull, edit_book, read_books
from jarvis.society.review import notebook_capacity
from jarvis.society.review_cadence import (
    REVIEW_EVERY_USER_TURNS,
    SKILL_REVIEW_TOOL_STEPS,
    WINDOW_MAX_AGE_MS,
    ReviewWindow,
    learning_signal,
    window_events,
)
from jarvis.society.skill_lifecycle import (
    ACTIVE,
    ARCHIVE_AFTER_DAYS,
    ARCHIVED,
    STALE,
    STALE_AFTER_DAYS,
    SkillUsage,
)

DAY = 86_400_000


def _turn(seq: int, text: str, tools: int = 0) -> list[dict]:
    events = [{"seq": seq, "kind": "user_message", "payload": {"text": text}}]
    events += [{"seq": seq + i + 1, "kind": "tool_call", "payload": {}} for i in range(tools)]
    return events


# ------------------------------------------------------------- review window


def test_ordinary_turns_wait_for_the_window():
    window = ReviewWindow()
    for index in range(REVIEW_EVERY_USER_TURNS - 1):
        window.add_turn(_turn(10 * index + 1, "Look up the weather."), now=1_000)
        assert not window.due(["Look up the weather."], now=1_000)
    window.add_turn(_turn(99, "And tomorrow?"), now=1_000)
    assert window.due(["And tomorrow?"], now=1_000)
    assert window.since_seq == 1  # the window starts at its first turn


def test_enough_tool_work_makes_the_window_due():
    window = ReviewWindow()
    window.add_turn(_turn(1, "Build the report.", tools=SKILL_REVIEW_TOOL_STEPS), now=0)
    assert window.due(["Build the report."], now=0)


def test_an_old_window_is_reviewed_on_the_next_turn():
    window = ReviewWindow()
    window.add_turn(_turn(1, "Hello."), now=1)
    assert not window.due(["Hello."], now=2)
    assert window.due(["Hello."], now=1 + WINDOW_MAX_AGE_MS)


@pytest.mark.parametrize(
    "text",
    [
        "Remember that my invoices go to accounting.",
        "No, not like that. Use bullet points.",
        "Next time keep it shorter.",
        "Merk dir, dass ich Rechnungen als PDF will.",  # i18n-allow: input vocabulary
        "Das ist falsch, hab ich dir doch gesagt.",  # i18n-allow: input vocabulary
        "La próxima vez, más corto.",  # i18n-allow: input vocabulary
    ],
)
def test_a_learning_signal_reviews_at_once(text):
    assert learning_signal([text])
    window = ReviewWindow()
    window.add_turn(_turn(1, text), now=0)
    assert window.due([text], now=0)


def test_plain_requests_are_no_learning_signal():
    assert not learning_signal(["Summarize today's mail.", "Wie immer bitte."])  # i18n-allow


def test_board_messages_never_pose_as_the_person():
    events = [
        {"seq": 1, "kind": "user_message", "payload": {"text": "[assignment from jarvis]\nDo it"}},
        {"seq": 2, "kind": "user_message", "payload": {"text": "[query from nova]\nWhere?"}},
        {"seq": 3, "kind": "user_message", "payload": {"text": "My own words"}},
        {"seq": 4, "kind": "assistant_text", "payload": {"text": "Done"}},
    ]
    assert [e["seq"] for e in window_events(events)] == [3, 4]


def test_the_window_survives_a_restart_as_json():
    window = ReviewWindow()
    window.add_turn(_turn(5, "Hi", tools=2), now=7)
    restored = ReviewWindow.parse(window.dump())
    assert restored == window
    assert ReviewWindow.parse("") == ReviewWindow()
    assert ReviewWindow.parse("not json") == ReviewWindow()


# ------------------------------------------------------------- skill lifecycle


def test_skills_age_from_active_to_stale_to_archived(tmp_path: Path):
    usage = SkillUsage(tmp_path)
    usage.record("weekly-report", kind="write", now=0)
    assert usage.transitions(["weekly-report"], now=(STALE_AFTER_DAYS - 1) * DAY)["stale"] == 0
    assert usage.transitions(["weekly-report"], now=(STALE_AFTER_DAYS + 1) * DAY)["stale"] == 1
    assert usage.rows()["weekly-report"]["state"] == STALE
    usage.transitions(["weekly-report"], now=(ARCHIVE_AFTER_DAYS + 1) * DAY)
    assert usage.rows()["weekly-report"]["state"] == ARCHIVED


def test_using_a_skill_brings_it_back(tmp_path: Path):
    usage = SkillUsage(tmp_path)
    usage.record("mail-triage", kind="write", now=0)
    usage.transitions(["mail-triage"], now=(ARCHIVE_AFTER_DAYS + 1) * DAY)
    usage.record("mail-triage", now=(ARCHIVE_AFTER_DAYS + 2) * DAY)
    row = usage.rows()["mail-triage"]
    assert row["state"] == ACTIVE and row["uses"] == 1


def test_a_skill_seen_first_starts_its_clock_then(tmp_path: Path):
    usage = SkillUsage(tmp_path)
    counts = usage.transitions(["found-on-disk"], now=100 * DAY)
    assert counts["seeded"] == 1
    usage.transitions(["found-on-disk"], now=(100 + STALE_AFTER_DAYS - 1) * DAY)
    assert usage.rows()["found-on-disk"]["state"] == ACTIVE


def test_a_broken_usage_file_starts_over(tmp_path: Path):
    (tmp_path / ".usage.json").write_text("{broken", encoding="utf-8")
    usage = SkillUsage(tmp_path)
    usage.record("x", now=1)
    assert usage.rows()["x"]["uses"] == 1


# ------------------------------------------------------------- notebook limits


def _agent() -> SimpleNamespace:
    return SimpleNamespace(agent_id="scout", name="Scout")


def test_a_claimed_user_origin_does_not_pass_the_limit(tmp_path: Path):
    agent = _agent()
    with pytest.raises(NotebookFull):
        for index in range(HARD_LIMITS["user"] // 900 + 2):
            edit_book(tmp_path, agent, f"{index} " + "z" * 900, target="user", origin="user")


def test_a_full_notebook_asks_the_agent_to_consolidate(tmp_path: Path):
    agent = _agent()
    filler = "x" * 900
    added = 0
    with pytest.raises(NotebookFull, match="Consolidate first"):
        while True:
            edit_book(tmp_path, agent, f"{added} {filler}", target="user", origin="agent")
            added += 1
    assert 0 < added * 900 <= HARD_LIMITS["user"]
    # A replace still works and frees room.
    edit_book(
        tmp_path, agent, "merged", target="user", operation="replace", old_text=f"0 {filler}"
    )
    edit_book(tmp_path, agent, "one more", target="user", origin="agent")


def test_the_persons_own_request_is_never_refused(tmp_path: Path):
    agent = _agent()
    for index in range(HARD_LIMITS["user"] // 900 + 2):
        edit_book(
            tmp_path, agent, f"{index} " + "y" * 900, target="user", allow_over_limit=True
        )
    assert len(read_books(tmp_path, agent)["user"]) == HARD_LIMITS["user"] // 900 + 2


def test_the_review_sees_how_full_each_notebook_is(tmp_path: Path):
    agent = _agent()
    edit_book(tmp_path, agent, "The person lives in Berlin.", target="user")
    capacity = notebook_capacity(read_books(tmp_path, agent))
    assert capacity["user"].endswith(f"/{HARD_LIMITS['user']} characters")
    assert capacity["memory"] == f"0/{HARD_LIMITS['memory']} characters"

"""Pane titles come from the coding CLI itself, not from a paid summary.

Claude Code records an ``ai-title`` (and ``custom-title`` on ``/rename``) in
its transcript and shows it as the window title; Codex keeps a ``thread_name``
in ``session_index.jsonl``. These tests pin that the header takes those words,
that a title naming only the program or the folder is ignored, and that no
model summary is scheduled for a pane its CLI has already named.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.agentic_ide import cli_title, recap_engine
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.screen import ScreenBuffer
from jarvis.agentic_ide.session import Terminal


@pytest.fixture(autouse=True)
def _clean():
    recap_engine.reset_for_tests()
    yield
    recap_engine.reset_for_tests()


def _pane(agent: str = "claude", display_name: str = "Claude Code", **changes: object) -> Terminal:
    term = Terminal(
        key="t1",
        name="T1",
        agent=agent,
        display_name=display_name,
        index=0,
        status="live",
    )
    for name, value in changes.items():
        setattr(term, name, value)
    return term


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


# ------------------------------------------------------------ window title


def test_the_screen_keeps_the_last_window_title_in_both_terminators() -> None:
    screen = ScreenBuffer()
    screen.feed("\x1b]0;✳ Claude Code\x07hello")
    assert screen.title == "✳ Claude Code"
    screen.feed("\x1b]2;⠋ Fix login test\x1b\\")
    assert screen.title == "⠋ Fix login test"
    # A hyperlink is an OSC too, and must not touch the title.
    screen.feed("\x1b]8;;https://example.com\x07link\x1b]8;;\x07")
    assert screen.title == "⠋ Fix login test"


def test_a_title_split_across_two_reads_is_still_read() -> None:
    screen = ScreenBuffer()
    screen.feed("\x1b]0;✳ Pane ti")
    screen.feed("tles from the CLI\x07")
    assert screen.title == "✳ Pane titles from the CLI"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("✳ Terminal titles in the IDE", "Terminal titles in the IDE"),
        ("⠹ Fix login test", "Fix login test"),
        ("✳ Claude Code", ""),
        ("codex", ""),
        ("⠇ Personal Jarvis", ""),
        ("C:\\windows\\system32\\cmd.exe ", ""),
        ("/usr/bin/zsh", ""),
        ("", ""),
    ],
)
def test_a_window_title_is_cleaned_and_program_names_are_dropped(raw: str, expected: str) -> None:
    names = ("claude", "Claude Code", "Personal Jarvis")
    assert cli_title.clean_window_title(raw, names=names) == expected


def test_a_pane_takes_its_live_window_title() -> None:
    term = _pane(folder="/work/Personal Jarvis")
    term.transcript.feed("\x1b]0;⠐ Terminal titles from the CLI\x07")

    assert cli_title.title_for(term) == "Terminal titles from the CLI"


def test_codex_naming_only_its_folder_is_not_a_title() -> None:
    term = _pane(agent="codex", display_name="Codex", folder="/work/Personal Jarvis")
    term.transcript.feed("\x1b]0;⠋ Personal Jarvis\x07")

    assert cli_title.title_for(term) == ""


# ------------------------------------------------------------ CLI records


def test_claude_s_own_title_record_wins_and_a_rename_outranks_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_title, "RECHECK_S", 0.0)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    session_id = "11111111-2222-3333-4444-555555555555"
    record = tmp_path / "projects" / "proj" / f"{session_id}.jsonl"
    _write_jsonl(
        record,
        [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "ai-title", "aiTitle": "Terminal titles in the IDE", "sessionId": session_id},
        ],
    )
    term = _pane(resume=ResumeHandle(kind="claude_session", id=session_id, captured_at=0.0))
    term.transcript.feed("\x1b]0;✳ Something older\x07")

    assert cli_title.title_for(term) == "Terminal titles in the IDE"

    # Only the appended bytes are read next time, and /rename wins.
    _write_jsonl(record, [{"type": "custom-title", "customTitle": "Demo branch"}])
    assert cli_title.title_for(term) == "Demo branch"
    _write_jsonl(record, [{"type": "ai-title", "aiTitle": "A newer automatic title"}])
    assert cli_title.title_for(term) == "Demo branch"


def test_codex_s_thread_name_is_the_title(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_title, "RECHECK_S", 0.0)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    index = tmp_path / "session_index.jsonl"
    _write_jsonl(
        index,
        [
            {"id": "other", "thread_name": "Someone else"},
            {"id": "abc", "thread_name": "Analyse the paste bug"},
        ],
    )
    term = _pane(
        agent="codex",
        display_name="Codex",
        resume=ResumeHandle(kind="codex_rollout", id="abc", captured_at=0.0),
    )

    assert cli_title.title_for(term) == "Analyse the paste bug"

    _write_jsonl(index, [{"id": "abc", "thread_name": "Paste bug — fixed"}])
    assert cli_title.title_for(term) == "Paste bug — fixed"


# ------------------------------------------------------------ recap engine


def test_the_header_shows_the_cli_title_over_the_floor() -> None:
    term = _pane(last_prompt="please look at the thing")
    term.transcript.feed("\x1b]0;✳ Terminal titles in the IDE\x07")

    answer = recap_engine.recap_for(term, lines=term.transcript.lines())

    assert answer.source == recap_engine.BY_CLI
    assert answer.reason == recap_engine.WHY_CLI_TITLE
    assert answer.headline == "Terminal titles in the IDE"
    assert answer.writer == "Claude Code"
    assert recap_engine.known_headline(term) == "Terminal titles in the IDE"


def test_a_pinned_title_still_outranks_the_cli() -> None:
    term = _pane()
    term.transcript.feed("\x1b]0;✳ Terminal titles in the IDE\x07")
    recap_engine.pin(recap_engine.pane_id(term), "Leave this one alone")

    assert recap_engine.recap_for(term, lines=[]).headline == "Leave this one alone"


def test_a_pane_its_cli_named_is_never_summarized(monkeypatch) -> None:
    monkeypatch.setattr(recap_engine, "_enabled", lambda: True)
    spawned: list[str] = []
    monkeypatch.setattr(recap_engine, "_spawn", lambda term, key, rows, folder: spawned.append(key))
    term = _pane()
    term.transcript.feed("\x1b]0;✳ Terminal titles in the IDE\x07")

    recap_engine.refresh_soon(term, lines=[f"row {n}" for n in range(80)])

    assert spawned == []


def test_model_recaps_are_off_unless_switched_on(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "jarvis.core.config.load_config", lambda: SimpleNamespace(agentic_ide=SimpleNamespace())
    )
    assert recap_engine._enabled() is False  # noqa: SLF001

    from jarvis.core.config import AgenticIdeConfig

    assert AgenticIdeConfig().smart_recaps is False

"""The per-agent resume contract: minting, spending and finding a handle.

The point of these tests is that several coding panes in ONE folder must never
collide. Codex's own ``--last`` shortcut would give all of them the same
conversation, so discovery has to tell them apart — and it has to do so on a
machine in any timezone, which is where the interesting failure lives.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from jarvis.agentic_ide import agent_sessions as sessions


def _epoch(iso: str) -> float:
    """A UTC ISO string as a POSIX timestamp, computed rather than hardcoded."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ------------------------------------------------------------------- minting
def test_claude_mints_a_session_id_at_launch() -> None:
    argv, handle = sessions.launch_extra("claude")
    assert argv[0] == "--session-id"
    assert handle is not None and handle.id == argv[1]
    # Claude Code rejects anything that is not a UUID.
    assert len(handle.id) == 36 and handle.id.count("-") == 4


def test_claude_resumes_exactly_that_id() -> None:
    _, handle = sessions.launch_extra("claude")
    assert handle is not None
    assert sessions.resume_argv("claude", handle) == ("--resume", handle.id)


def test_two_panes_never_share_a_minted_id() -> None:
    """Otherwise the second pane would overwrite the first one's conversation."""
    _, first = sessions.launch_extra("claude")
    _, second = sessions.launch_extra("claude")
    assert first is not None and second is not None and first.id != second.id


def test_codex_cannot_be_told_an_id_but_can_resume_one() -> None:
    argv, handle = sessions.launch_extra("codex")
    assert argv == () and handle is None
    stored = sessions.ResumeHandle(kind="codex_rollout", id="abc-123", captured_at=1.0)
    assert sessions.resume_argv("codex", stored) == ("resume", "abc-123")


# ------------------------------------------------------------------ refusals
def test_an_unknown_agent_never_claims_it_can_resume() -> None:
    """A coding CLI added later degrades to a fresh start, never to a crash."""
    assert sessions.can_resume("some-future-cli") is False
    assert sessions.launch_extra("some-future-cli") == ((), None)
    handle = sessions.ResumeHandle(kind="claude_session", id="x", captured_at=0.0)
    assert sessions.resume_argv("some-future-cli", handle) is None


def test_a_handle_of_the_wrong_kind_is_refused() -> None:
    """A Codex id must never be spent on Claude Code."""
    codex = sessions.ResumeHandle(kind="codex_rollout", id="x", captured_at=0.0)
    assert sessions.resume_argv("claude", codex) is None


def test_no_handle_means_no_resume_arguments() -> None:
    assert sessions.resume_argv("claude", None) is None


# ------------------------------------------------------------- codex lookup
def _write_rollout(
    root: Path,
    *,
    day: str,
    name: str,
    cwd: str,
    utc: str,
    session_id: str,
    extra: dict | None = None,
) -> Path:
    folder = root / "sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    payload = {
        "id": session_id,
        "session_id": session_id,
        "timestamp": utc,
        "cwd": cwd,
        "originator": "codex-tui",
    }
    payload.update(extra or {})
    record = {"timestamp": utc, "type": "session_meta", "payload": payload}
    # A real rollout has thousands of lines after the header; only the first
    # one may ever be read.
    target.write_text(
        json.dumps(record) + "\n" + '{"type":"turn"}\n' * 50, encoding="utf-8"
    )
    return target


def test_codex_discovery_picks_the_pane_s_own_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three panes in one folder must not all resume the same conversation."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cwd = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    for stamp, ident in (
        ("2026-07-25T12:00:00Z", "first"),
        ("2026-07-25T12:00:10Z", "second"),
        ("2026-07-25T12:00:20Z", "third"),
    ):
        _write_rollout(
            tmp_path,
            day="2026/07/25",
            name=f"rollout-{ident}.jsonl",
            cwd=cwd,
            utc=stamp,
            session_id=ident,
        )
    found = sessions.discover("codex", cwd, _epoch("2026-07-25T12:00:05Z"))
    assert found is not None and found.id == "second"


def test_panes_opened_in_one_batch_do_not_claim_the_same_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collision that time alone cannot resolve.

    Codex needs a beat to write its session file, and opening five panes takes
    less time than that — so every pane sees every file as "the first one after
    I started". Only the claim check keeps them apart.
    """
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cwd = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    for stamp, ident in (
        ("2026-07-25T12:00:02Z", "pane-one-session"),
        ("2026-07-25T12:00:03Z", "pane-two-session"),
    ):
        _write_rollout(
            tmp_path,
            day="2026/07/25",
            name=f"rollout-{ident}.jsonl",
            cwd=cwd,
            utc=stamp,
            session_id=ident,
        )
    # Both panes launched within the same second, before either file existed.
    first = sessions.discover("codex", cwd, _epoch("2026-07-25T12:00:00Z"))
    assert first is not None and first.id == "pane-one-session"
    second = sessions.discover(
        "codex", cwd, _epoch("2026-07-25T12:00:00Z"), taken={first.id}
    )
    assert second is not None and second.id == "pane-two-session"


def test_codex_discovery_ignores_a_session_from_another_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "repo").mkdir()
    (tmp_path / "elsewhere").mkdir()
    _write_rollout(
        tmp_path,
        day="2026/07/25",
        name="rollout-x.jsonl",
        cwd=str(tmp_path / "elsewhere"),
        utc="2026-07-25T12:00:10Z",
        session_id="stranger",
    )
    assert (
        sessions.discover(
            "codex", str(tmp_path / "repo"), _epoch("2026-07-25T12:00:05Z")
        )
        is None
    )


def test_codex_discovery_ignores_a_session_that_predates_the_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yesterday's conversation in this folder is not this pane's."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cwd = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    _write_rollout(
        tmp_path,
        day="2026/07/24",
        name="rollout-old.jsonl",
        cwd=cwd,
        utc="2026-07-24T09:00:00Z",
        session_id="yesterday",
    )
    assert sessions.discover("codex", cwd, _epoch("2026-07-25T12:00:05Z")) is None


def test_codex_discovery_survives_a_filename_in_a_foreign_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trap this test exists for.

    Codex stamps the FILENAME in local time and the record in UTC. A machine at
    UTC+10 writes ``rollout-2026-07-25T22-00-10-...`` for a session that
    happened at 12:00:10Z, and files it under the local day. Matching on the
    filename would place that session ten hours in the future and never find it;
    matching on the record works in every timezone.
    """
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cwd = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    _write_rollout(
        tmp_path,
        day="2026/07/26",  # local date is already the next day
        name="rollout-2026-07-25T22-00-10-tz.jsonl",
        cwd=cwd,
        utc="2026-07-25T12:00:10Z",
        session_id="right-one",
    )
    found = sessions.discover("codex", cwd, _epoch("2026-07-25T12:00:05Z"))
    assert found is not None and found.id == "right-one"


def test_codex_discovery_skips_a_subagent_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer subagent writes its own rollout; the pane is the parent."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cwd = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    _write_rollout(
        tmp_path,
        day="2026/07/25",
        name="rollout-sub.jsonl",
        cwd=cwd,
        utc="2026-07-25T12:00:08Z",
        session_id="subagent-thread",
        extra={"thread_source": "subagent"},
    )
    _write_rollout(
        tmp_path,
        day="2026/07/25",
        name="rollout-main.jsonl",
        cwd=cwd,
        utc="2026-07-25T12:00:12Z",
        session_id="the-pane",
    )
    found = sessions.discover("codex", cwd, _epoch("2026-07-25T12:00:05Z"))
    assert found is not None and found.id == "the-pane"


def test_a_corrupt_rollout_file_is_skipped_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    folder = tmp_path / "sessions" / "2026" / "07" / "25"
    folder.mkdir(parents=True)
    (folder / "rollout-broken.jsonl").write_text("{not json", encoding="utf-8")
    (tmp_path / "repo").mkdir()
    assert (
        sessions.discover(
            "codex", str(tmp_path / "repo"), _epoch("2026-07-25T12:00:05Z")
        )
        is None
    )


def test_discovery_without_a_home_directory_returns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh machine that has never run Codex — not an error."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "does-not-exist"))
    assert sessions.discover("codex", str(tmp_path), 0.0) is None


def test_claude_needs_no_discovery(tmp_path: Path) -> None:
    """It was told its id at launch, so there is nothing to look for."""
    assert sessions.discover("claude", str(tmp_path), 0.0) is None


# ------------------------------------------------------- cross-platform paths
def test_a_folder_matches_itself_however_it_was_spelled(tmp_path: Path) -> None:
    """The one line in this module whose behaviour differs between systems.

    A pane's folder comes from the user's own selection and the CLI's record
    comes from its process, so the two can be spelled differently: forward
    slashes against backslashes, a trailing separator, a relative segment. On
    Windows the drive letter's case can differ too, and there a mismatch would
    silently hide a pane's own conversation from it.
    """
    from jarvis.agentic_ide.agent_sessions import _same_folder

    native = str(tmp_path)
    assert _same_folder(native, native)
    assert _same_folder(native, native.replace("\\", "/"))
    assert _same_folder(native, native + os.sep)
    assert _same_folder(native, str(tmp_path / "sub" / ".."))
    assert not _same_folder(native, str(tmp_path / "elsewhere"))


def test_case_differences_only_matter_where_the_system_says_so(
    tmp_path: Path,
) -> None:
    """Windows treats paths case-insensitively; POSIX does not. Follow the host."""
    from jarvis.agentic_ide.agent_sessions import _same_folder

    swapped = str(tmp_path).upper()
    expected = os.path.normcase(swapped) == os.path.normcase(str(tmp_path))
    assert _same_folder(str(tmp_path), swapped) is expected


def test_each_cli_home_follows_its_own_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A managed install can move either history; both must be followed."""
    from jarvis.agentic_ide.agent_sessions import _claude_home, _codex_home

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-elsewhere"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-elsewhere"))
    assert _claude_home() == tmp_path / "claude-elsewhere"
    assert _codex_home() == tmp_path / "codex-elsewhere"

    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.delenv("CODEX_HOME")
    assert _claude_home() == Path.home() / ".claude"
    assert _codex_home() == Path.home() / ".codex"


# ------------------------------------------------------------------ storage
def test_handle_round_trips_through_a_dict() -> None:
    handle = sessions.ResumeHandle(kind="claude_session", id="u", captured_at=3.5)
    assert sessions.ResumeHandle.from_dict(handle.to_dict()) == handle


def test_a_damaged_stored_handle_reads_as_no_handle() -> None:
    """The file it comes from survived a crash or a hand edit."""
    assert sessions.ResumeHandle.from_dict({"kind": "x"}) is None
    assert sessions.ResumeHandle.from_dict({"id": "x"}) is None
    assert sessions.ResumeHandle.from_dict("nonsense") is None
    assert sessions.ResumeHandle.from_dict(None) is None
    salvaged = sessions.ResumeHandle.from_dict(
        {"kind": "claude_session", "id": "u", "captured_at": "not a number"}
    )
    assert salvaged is not None and salvaged.captured_at == 0.0

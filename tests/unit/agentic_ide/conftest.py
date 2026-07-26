"""Shared safety net for the Agentic-IDE tests.

Opening a workspace touches two things that live OUTSIDE the repository, and a
test must reach neither of them. Both are redirected here for the whole package,
whether or not an individual test knows they exist — a per-test opt-in is a
guarantee that only holds until somebody writes the next test.

1. **The resume snapshot.** Every workspace change now records one. Left
   unredirected, a test would write into the developer's real data directory and
   leave the app offering to reopen a workspace that pytest invented in a
   temporary folder.
2. **The trust pre-seed.** Opening a workspace marks its folder as trusted in
   Claude Code's and Codex's own config files so no pane stops on a "do you
   trust this directory?" dialog. Against a real home that means every test run
   permanently added its throwaway folders to the developer's CLI configs. That
   is not hypothetical: it is why one such config had grown to 71 KB of dead
   entries, which the trust check then had to parse before every workspace open.
   The trust logic itself is covered where it belongs, in
   ``tests/unit/workspace/test_trust.py``, against a temporary home.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import resume_store


@pytest.fixture(autouse=True)
def _resume_store_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the resume snapshot at a throwaway file for the duration of a test."""
    target = tmp_path_factory.mktemp("resume") / "last_session.json"
    monkeypatch.setattr(resume_store, "_store_path", lambda: target)
    return target


@pytest.fixture(autouse=True)
def _never_touch_real_agent_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the trust pre-seed away from the developer's own CLI configuration."""
    from jarvis.workspace import trust

    def _skip(*_args: Any, **_kwargs: Any) -> list[trust.TrustResult]:
        return []

    monkeypatch.setattr(trust, "ensure_trusted", _skip)


@pytest.fixture(autouse=True)
def _agent_history_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Give each test an empty stand-in for the coding CLIs' own histories.

    Resuming asks the CLI whether a conversation really exists, so tests read
    that history. Pointed at the real one they would inherit whatever the
    developer happens to have on disk — a test could pass because an unrelated
    conversation was lying around, or fail on a fresh machine.
    """
    home = tmp_path_factory.mktemp("agent-history")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / "codex"))
    return home


@pytest.fixture
def existing_conversation() -> Any:
    """Create the on-disk conversation a resume handle points at.

    Real files rather than a patched check: the difference between "a handle
    exists" and "a conversation exists" is the bug this guards, and stubbing the
    lookup would stub out exactly the thing under test.
    """
    from jarvis.agentic_ide import agent_sessions

    def _make(session_id: str, *, agent: str = "claude") -> None:
        if agent == "claude":
            folder = agent_sessions._claude_home() / "projects" / "a-project"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{session_id}.jsonl").write_text("{}\n", encoding="utf-8")
        else:
            folder = agent_sessions._codex_home() / "sessions" / "2026" / "07" / "26"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"rollout-2026-07-26T00-00-00-{session_id}.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )

    return _make

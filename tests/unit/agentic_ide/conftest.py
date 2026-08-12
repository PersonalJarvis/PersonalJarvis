"""Shared safety net for the Agentic-IDE tests.

Opening a workspace touches two things that live OUTSIDE the repository, and a
test must reach neither of them. Both are redirected here for the whole package,
whether or not an individual test knows they exist — a per-test opt-in is a
guarantee that only holds until somebody writes the next test.

1. **The resume snapshot.** Every workspace change now records one. Left
   unredirected, a test would write into the developer's real data directory and
   leave the app offering to reopen a workspace that pytest invented in a
   temporary folder.
2. **The recent-folder history.** Same store, same failure, one page earlier in
   the wizard — and it happened: a run against the real store filled seven of
   the eight slots on the picker's front page with pytest folders and pushed the
   one folder the developer had actually opened to the bottom. ``recents``
   declines throwaway paths on its own now, but the redirect stays, because a
   test must not depend on a production filter to keep its hands off real data.
3. **The trust pre-seed.** Opening a workspace marks its folder as trusted in
   Claude Code's and Codex's own config files so no pane stops on a "do you
   trust this directory?" dialog. Against a real home that means every test run
   permanently added its throwaway folders to the developer's CLI configs. That
   is not hypothetical: it is why one such config had grown to 71 KB of dead
   entries, which the trust check then had to parse before every workspace open.
   The trust logic itself is covered where it belongs, in
   ``tests/unit/workspace/test_trust.py``, against a temporary home.
4. **The agent-account store.** Panes now record which subscription they run on,
   which means opening one reads (and resuming one could write) the developer's
   real account list. Same store, same failure class as 1 and 2.
5. **The workspace display preferences.** The remembered terminal text size is
   kept outside the repo too, so an unredirected test would resize the panes of
   whoever ran it.
6. **The project/chat library.** The newest of the five, and the one with the
   most to lose: it holds the user's projects and their chat history, so a test
   left pointing at it would both pollute the sidebar and give a delete test a
   real project to remove.
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
def _ui_prefs_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the workspace display preferences at a throwaway file.

    Same store, same failure class as 1 and 2 above: a test that writes a text
    size would change the size of the developer's own terminals.
    """
    from jarvis.agentic_ide import ui_prefs

    target = tmp_path_factory.mktemp("ui-prefs") / "ui_prefs.json"
    monkeypatch.setattr(ui_prefs, "_store_path", lambda: target)
    return target


@pytest.fixture(autouse=True)
def _chat_library_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the project/chat library at a throwaway directory.

    Same store, same failure class as 1 and 2 above, one level up: this one
    holds the user's PROJECTS and their chat history, so an unredirected test
    would file its temporary folders into the sidebar of whoever ran it — and,
    worse, could delete a real project on the way through a delete test.
    """
    from jarvis.agentic_ide import library

    root = tmp_path_factory.mktemp("chat-library")
    monkeypatch.setattr(library, "_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def _never_touch_real_agent_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the trust pre-seed away from the developer's own CLI configuration."""
    from jarvis.workspace import trust

    def _skip(*_args: Any, **_kwargs: Any) -> list[trust.TrustResult]:
        return []

    monkeypatch.setattr(trust, "ensure_trusted", _skip)


@pytest.fixture(autouse=True)
def _fresh_writer_cache() -> None:
    """Start every test without a remembered brief writer.

    ``writer.resolve_writer`` answers repeats from a short-lived cache in
    production. Between tests that cache is a leak: the first test to resolve a
    writer would hand its monkeypatched brain to every later test in the run.
    """
    from jarvis.agentic_ide import writer

    writer.invalidate_writer_cache()


@pytest.fixture(autouse=True)
def _agent_history_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Give each test an empty stand-in for the coding CLIs' own histories.

    Resuming asks the CLI whether a conversation really exists, so tests read
    that history. Pointed at the real one they would inherit whatever the
    developer happens to have on disk — a test could pass because an unrelated
    conversation was lying around, or fail on a fresh machine.

    The per-CLI variables are read from the registry rather than listed here, so
    a CLI registered later is redirected without anybody remembering to add it.
    The two that resolve their history from the home directory instead of a
    variable are redirected at their own seam, for the same reason: a test that
    can reach the developer's real history is a test that will, eventually.
    """
    from jarvis.agentic_ide import agent_sessions
    from jarvis.workspace import agents as workspace_agents

    home = tmp_path_factory.mktemp("agent-history")
    for entry in workspace_agents.coding_agents():
        for variable, template in (entry.account.env if entry.account else ()):
            monkeypatch.setenv(variable, template.format(dir=home / entry.name))
    # This CLI follows the XDG data convention on every OS, so one variable
    # moves its whole session database.
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    # And this one derives its root from the home directory with no variable at
    # all, so the seam is the only place to intercept it.
    kimi_root = home / "kimi"
    monkeypatch.setattr(
        agent_sessions,
        "_kimi_root",
        lambda override=None: Path(override) if override else kimi_root,
    )
    return home


@pytest.fixture(autouse=True)
def _agent_accounts_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Give each test its own empty account list and account folder root."""
    from jarvis import agent_accounts

    root = tmp_path_factory.mktemp("agent-accounts")
    monkeypatch.setattr(agent_accounts, "_store_path", lambda: root / "accounts.json")
    monkeypatch.setattr(agent_accounts, "_accounts_root", lambda: root / "dirs")
    return root


@pytest.fixture
def existing_conversation() -> Any:
    """Create the on-disk conversation a resume handle points at.

    Real files rather than a patched check: the difference between "a handle
    exists" and "a conversation exists" is the bug this guards, and stubbing the
    lookup would stub out exactly the thing under test.
    """
    from jarvis.agentic_ide import agent_sessions

    def _claude(session_id: str) -> None:
        folder = agent_sessions._claude_home() / "projects" / "a-project"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{session_id}.jsonl").write_text("{}\n", encoding="utf-8")

    def _codex(session_id: str) -> None:
        folder = agent_sessions._codex_home() / "sessions" / "2026" / "07" / "26"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"rollout-2026-07-26T00-00-00-{session_id}.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )

    def _kimi(session_id: str) -> None:
        # One directory per session, named after the id, under a folder named
        # after the working directory — the layout measured on a live install.
        folder = agent_sessions._kimi_root() / "sessions" / "a-project" / session_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "context.jsonl").write_text("{}\n", encoding="utf-8")

    # One writer per session STORE. A CLI whose store this table does not know
    # falls to the last entry, which would quietly build a conversation the CLI
    # under test never reads — so an unknown agent raises instead.
    writers = {"claude": _claude, "codex": _codex, "kimi": _kimi}

    def _make(session_id: str, *, agent: str = "claude") -> None:
        writer = writers.get(agent)
        if writer is None:
            raise AssertionError(
                f"No conversation writer for {agent!r} — add one here rather "
                "than letting the test read another CLI's history."
            )
        writer(session_id)

    return _make

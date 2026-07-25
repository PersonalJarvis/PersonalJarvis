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

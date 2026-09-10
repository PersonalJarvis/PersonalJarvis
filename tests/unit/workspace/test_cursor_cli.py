"""A CLI named agent must not route Cursor work to Grok or another vendor."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.workspace import cursor_cli


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    clear = cursor_cli._supports_cursor_cli.cache_clear
    clear()
    monkeypatch.setattr("jarvis.core.path_augment.ensure_cli_paths", lambda: None)
    yield
    clear()


@pytest.mark.parametrize(
    "output",
    [
        "Grok Build --single --output-format --resume",
        "Claude Code --print --output-format --resume",
        "",
    ],
)
def test_another_agents_help_does_not_authorize_cursor(monkeypatch, output):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output)
    )
    assert not cursor_cli._supports_cursor_cli("agent", 0)


def test_verified_cursor_is_cached_per_binary_revision(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["creationflags"] == NO_WINDOW_CREATIONFLAGS
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == 8
        return SimpleNamespace(
            returncode=0, stdout="--print --output-format --resume CURSOR_API_KEY"
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert cursor_cli._supports_cursor_cli("agent", 1)
    assert cursor_cli._supports_cursor_cli("agent", 1)
    assert len(calls) == 1
    assert cursor_cli._supports_cursor_cli("agent", 2)
    assert len(calls) == 2


def test_a_shadowing_agent_does_not_hide_cursor_later_on_path(monkeypatch, tmp_path):
    grok = tmp_path / "grok-agent"
    cursor = tmp_path / "cursor-agent"
    grok.touch()
    cursor.touch()
    monkeypatch.setenv("PATH", os.pathsep.join(["first", "second"]))
    monkeypatch.setattr(
        cursor_cli.shutil,
        "which",
        lambda name, path: str(grok if path == "first" else cursor) if name == "agent" else None,
    )
    monkeypatch.setattr(
        cursor_cli, "_supports_cursor_cli", lambda path, revision: path == str(cursor)
    )
    assert cursor_cli.resolve_cursor_binary() == str(cursor)


async def test_missing_cursor_agrees_across_picker_pane_and_recheck(monkeypatch):
    from jarvis.agent_chat import runner_cli
    from jarvis.agentic_ide.session import agent_argv
    from jarvis.workspace.agents import recheck_agent

    monkeypatch.setattr(cursor_cli, "resolve_cursor_binary", lambda: None)
    assert not runner_cli.cli_installed("cursor-cli")
    with pytest.raises(runner_cli.CliUnavailable):
        runner_cli.cursor_argv_prefix()
    assert agent_argv("cursor") is None
    status = await recheck_agent("cursor")
    assert status is not None and not status.installed

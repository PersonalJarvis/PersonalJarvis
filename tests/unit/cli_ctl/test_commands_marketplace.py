"""`jarvis marketplace install` — the command a downloader copies off a page.

Its whole job is that nobody has to guess whether anything happened, so these
tests pin the REPORTING as tightly as the request: what the terminal is told
after a skill, a plugin, an unknown name, and a broken install, plus the exit
code that a script keys on.
"""
from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()

_INDEX = {
    "status": "fresh",
    "plugins": [
        {
            "name": "todo-fox",
            "display_name": "TodoFox",
            "publisher": "octocat",
            "version": "1.2.0",
            "description": "Tasks and reminders",
            "source_url": "https://github.com/PersonalJarvis/marketplace",
            "installed": False,
        }
    ],
    "skills": [
        {
            "name": "three-point-check",
            "title": "Three Point Check",
            "publisher": "octocat",
            "version": "1.0.0",
            "description": "Three bullets plus a takeaway",
            "source_url": "https://github.com/PersonalJarvis/marketplace",
            "installed": False,
        }
    ],
}

_SKILL_RESULT = {
    "ok": True,
    "kind": "skill",
    "id": "three-point-check",
    "title": "Three Point Check",
    "publisher": "octocat",
    "version": "1.0.0",
    "location": r"C:\Users\x\AppData\Local\Jarvis\skills\three-point-check\SKILL.md",
    "state": "validated",
    "ready": True,
    "problem": None,
    "next_action": "none",
}


@pytest.fixture()
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stdout is an interactive terminal, so the prose path runs."""
    monkeypatch.setattr("jarvis.cli_ctl.render._stdout_isatty", lambda: True)


def _out(res: Any) -> str:
    return (res.stdout + res.stderr).lower()


# ----------------------------------------------------------------------
# Non-interactive (pipe / agent / --json): payload straight through
# ----------------------------------------------------------------------


def test_piped_install_sends_one_post_and_prints_the_payload(capture_api) -> None:
    capture_api["routes"][
        ("POST", "/api/marketplace/community/install/three-point-check")
    ] = (200, _SKILL_RESULT)
    res = runner.invoke(app, ["marketplace", "install", "three-point-check"])
    assert res.exit_code == 0
    # No index lookup, no prompt — exactly one request, the install itself.
    assert [c["path"] for c in capture_api["calls"]] == [
        "/api/marketplace/community/install/three-point-check"
    ]
    assert '"kind": "skill"' in res.stdout


def test_dry_run_previews_without_sending(capture_api) -> None:
    res = runner.invoke(app, ["marketplace", "install", "todo-fox", "--dry-run"])
    assert res.exit_code == 0
    assert capture_api["calls"] == []
    assert "dry_run" in res.stdout
    assert "/api/marketplace/community/install/todo-fox" in res.stdout


# ----------------------------------------------------------------------
# Terminal: say what happened
# ----------------------------------------------------------------------


def test_skill_install_reports_it_as_ready(capture_api, terminal) -> None:
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, _INDEX)
    capture_api["routes"][
        ("POST", "/api/marketplace/community/install/three-point-check")
    ] = (200, _SKILL_RESULT)
    res = runner.invoke(
        app, ["marketplace", "install", "three-point-check", "--yes"]
    )
    assert res.exit_code == 0
    out = _out(res)
    assert "three point check" in out
    assert "installed" in out
    assert "ready to use" in out
    assert "no restart" in out


def test_plugin_install_says_it_is_not_connected_yet(capture_api, terminal) -> None:
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, _INDEX)
    capture_api["routes"][
        ("POST", "/api/marketplace/community/install/todo-fox")
    ] = (
        200,
        {
            "ok": True,
            "kind": "plugin",
            "id": "todo-fox",
            "title": "TodoFox",
            "state": "not_connected",
            "ready": False,
            "problem": None,
            "next_action": "connect",
            "location": "",
        },
    )
    res = runner.invoke(app, ["marketplace", "install", "todo-fox", "--yes"])
    # Needing a connect step is the normal outcome, not a failure.
    assert res.exit_code == 0
    out = _out(res)
    assert "not connected" in out
    assert "connect-start" in out


def test_broken_skill_install_exits_nonzero_and_names_the_problem(
    capture_api, terminal
) -> None:
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, _INDEX)
    capture_api["routes"][
        ("POST", "/api/marketplace/community/install/three-point-check")
    ] = (
        200,
        {
            **_SKILL_RESULT,
            "state": "draft",
            "ready": False,
            "problem": "voice trigger needs 'pattern'",
            "next_action": "repair",
        },
    )
    res = runner.invoke(
        app, ["marketplace", "install", "three-point-check", "--yes"]
    )
    assert res.exit_code == 1
    out = _out(res)
    assert "not usable" in out
    assert "pattern" in out


def test_unknown_name_suggests_the_closest_and_installs_nothing(
    capture_api, terminal
) -> None:
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, _INDEX)
    res = runner.invoke(app, ["marketplace", "install", "three-point-chek", "--yes"])
    assert res.exit_code == 1
    assert [c["method"] for c in capture_api["calls"]] == ["GET"]
    out = _out(res)
    assert "three-point-check" in out
    assert "browse" in out


def test_already_installed_skill_reports_its_current_state(
    capture_api, terminal
) -> None:
    index = {
        **_INDEX,
        "skills": [{**_INDEX["skills"][0], "installed": True}],
    }
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, index)
    capture_api["routes"][("GET", "/api/skills/three-point-check")] = (
        200,
        {"name": "three-point-check", "state": "active", "error": None},
    )
    res = runner.invoke(
        app, ["marketplace", "install", "three-point-check", "--yes"]
    )
    assert res.exit_code == 0
    assert all(c["method"] == "GET" for c in capture_api["calls"])
    out = _out(res)
    assert "already installed" in out
    assert "ready to use" in out


def test_declining_the_prompt_installs_nothing(capture_api, terminal) -> None:
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, _INDEX)
    res = runner.invoke(
        app, ["marketplace", "install", "three-point-check"], input="n\n"
    )
    assert res.exit_code == 1
    assert all(c["method"] == "GET" for c in capture_api["calls"])
    assert "cancelled" in _out(res)


def test_unreachable_server_explains_instead_of_stack_tracing(
    monkeypatch: pytest.MonkeyPatch, terminal
) -> None:
    import httpx

    import jarvis.cli_ctl.client as client_mod

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no listener", request=request)

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(boom)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    res = runner.invoke(app, ["marketplace", "install", "todo-fox", "--yes"])
    assert res.exit_code == 1
    out = _out(res)
    assert "unreachable" not in out or "jarvis" in out
    assert "run.bat" in out or "still starting" in out or "target" in out


# ----------------------------------------------------------------------
# browse
# ----------------------------------------------------------------------


def test_browse_lists_both_kinds_with_install_state(capture_api, terminal) -> None:
    index = {
        **_INDEX,
        "skills": [{**_INDEX["skills"][0], "installed": True}],
    }
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, index)
    res = runner.invoke(app, ["marketplace", "browse"])
    assert res.exit_code == 0
    out = _out(res)
    assert "three-point-check" in out
    assert "installed" in out
    assert "todo-fox" in out
    assert "available" in out


def test_browse_piped_emits_the_raw_index(capture_api) -> None:
    capture_api["routes"][("GET", "/api/marketplace/community")] = (200, _INDEX)
    res = runner.invoke(app, ["marketplace", "browse"])
    assert res.exit_code == 0
    assert '"status": "fresh"' in res.stdout

"""Tests for `jarvis marketplace install/uninstall` — the CLI surface of the
store's three-way install standard (resolve by name, then run the same routes
the store's buttons use)."""

from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()

COMMUNITY = "/api/marketplace/community"


def _payload(
    *,
    plugin_overrides: dict[str, Any] | None = None,
    skill_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plugin: dict[str, Any] = {
        "name": "todo-fox",
        "valid": True,
        "installed": False,
        "seed_conflict": False,
        "publisher": "octocat",
        "version": "1.2.0",
        "mcp_server": {"transport": "http", "url": "https://mcp.todofox.example/mcp"},
    }
    skill: dict[str, Any] = {
        "name": "three-point-check",
        "title": "Three Point Check",
        "installed": False,
        "installable": True,
        "embedded": False,
        "publisher": "octocat",
        "raw_url": "https://raw.example/skills/three-point-check/SKILL.md",
        "source_url": "https://github.com/PersonalJarvis/marketplace",
    }
    plugin.update(plugin_overrides or {})
    skill.update(skill_overrides or {})
    return {"status": "fresh", "plugins": [plugin], "skills": [skill]}


def test_install_resolves_a_plugin_to_the_community_route(capture_api):
    capture_api["routes"][("GET", COMMUNITY)] = (200, _payload())
    res = runner.invoke(app, ["marketplace", "install", "todo-fox", "--yes"])
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/marketplace/community/plugins/todo-fox/install"
    # The consent facts print BEFORE the request, like the store's dialog.
    assert "not reviewed" in res.output
    assert "https://mcp.todofox.example/mcp" in res.output


def test_install_resolves_a_skill_to_the_community_route(capture_api):
    # The name is the ONLY input. `/api/skills/catalog/install` would take the
    # download URL from this caller — and it reports no install to the store.
    capture_api["routes"][("GET", COMMUNITY)] = (200, _payload())
    res = runner.invoke(app, ["marketplace", "install", "three-point-check", "--yes"])
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/marketplace/community/skills/three-point-check/install"
    assert call["body"] is None
    assert "https://raw.example/skills/three-point-check/SKILL.md" in res.output


def test_install_embedded_skill_says_nothing_is_downloaded(capture_api):
    # An index carrying the SKILL.md itself installs without any download —
    # the consent line must not claim a fetch that never happens.
    capture_api["routes"][("GET", COMMUNITY)] = (
        200,
        _payload(skill_overrides={"embedded": True, "raw_url": None}),
    )
    res = runner.invoke(app, ["marketplace", "install", "three-point-check", "--yes"])
    assert res.exit_code == 0, res.output
    assert capture_api["calls"][-1]["method"] == "POST"
    assert "nothing is downloaded" in res.output


def test_install_unknown_name_is_a_clean_error(capture_api):
    capture_api["routes"][("GET", COMMUNITY)] = (200, _payload())
    res = runner.invoke(app, ["marketplace", "install", "nope", "--yes"])
    assert res.exit_code == 1
    assert [c["method"] for c in capture_api["calls"]] == ["GET"]


def test_install_seed_conflict_refuses_before_any_request(capture_api):
    capture_api["routes"][("GET", COMMUNITY)] = (
        200,
        _payload(plugin_overrides={"seed_conflict": True}),
    )
    res = runner.invoke(app, ["marketplace", "install", "todo-fox", "--yes"])
    assert res.exit_code == 1
    assert [c["method"] for c in capture_api["calls"]] == ["GET"]


def test_install_contentless_skill_points_at_the_source(capture_api):
    # Neither an embedded body nor a download link: the registry published an
    # entry it cannot deliver, and no request may leave for it.
    capture_api["routes"][("GET", COMMUNITY)] = (
        200,
        _payload(skill_overrides={"installable": False, "raw_url": None}),
    )
    res = runner.invoke(app, ["marketplace", "install", "three-point-check", "--yes"])
    assert res.exit_code == 1
    assert "carries no content" in res.output
    assert [c["method"] for c in capture_api["calls"]] == ["GET"]


def test_install_refresh_flag_refetches_the_index(capture_api):
    capture_api["routes"][("POST", COMMUNITY + "/refresh")] = (200, _payload())
    res = runner.invoke(app, ["marketplace", "install", "todo-fox", "--refresh", "--yes"])
    assert res.exit_code == 0, res.output
    assert capture_api["calls"][0]["path"] == COMMUNITY + "/refresh"


LISTING = "/api/marketplace/plugins"


def test_uninstall_installed_plugin_resolves_via_the_local_catalog(capture_api):
    # No community route registered on purpose: an installed plugin must be
    # removable even when the index is unavailable or the entry was delisted.
    capture_api["routes"][("GET", LISTING)] = (
        200,
        {"plugins": [{"id": "todo-fox", "source": "community", "status": "not_connected"}]},
    )
    res = runner.invoke(app, ["marketplace", "uninstall", "todo-fox", "--yes"])
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "DELETE"
    assert call["path"] == "/api/marketplace/community/plugins/todo-fox"


def test_uninstall_builtin_plugin_points_at_disconnect(capture_api):
    capture_api["routes"][("GET", LISTING)] = (
        200,
        {"plugins": [{"id": "github", "source": "seed", "status": "connected"}]},
    )
    res = runner.invoke(app, ["marketplace", "uninstall", "github", "--yes"])
    assert res.exit_code == 1
    assert "disconnect" in res.output
    assert [c["method"] for c in capture_api["calls"]] == ["GET"]


def test_uninstall_installed_skill(capture_api):
    capture_api["routes"][("GET", COMMUNITY)] = (
        200,
        _payload(skill_overrides={"installed": True}),
    )
    res = runner.invoke(app, ["marketplace", "uninstall", "three-point-check", "--yes"])
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "DELETE" and call["path"] == "/api/skills/three-point-check"


def test_uninstall_nothing_installed_is_a_clean_error(capture_api):
    capture_api["routes"][("GET", COMMUNITY)] = (200, _payload())
    res = runner.invoke(app, ["marketplace", "uninstall", "todo-fox", "--yes"])
    assert res.exit_code == 1
    assert [c["method"] for c in capture_api["calls"]] == ["GET", "GET"]


def test_install_without_yes_is_refused(capture_api):
    # The confirm gate itself: without --yes nothing may be sent. Guards the
    # `dangerous=True` on the invoke.run call.
    capture_api["routes"][("GET", COMMUNITY)] = (200, _payload())
    res = runner.invoke(app, ["marketplace", "install", "todo-fox"])
    assert res.exit_code == 1
    assert [c["method"] for c in capture_api["calls"]] == ["GET"]


def test_uninstall_without_yes_is_refused(capture_api):
    capture_api["routes"][("GET", LISTING)] = (
        200,
        {"plugins": [{"id": "todo-fox", "source": "community", "status": "not_connected"}]},
    )
    res = runner.invoke(app, ["marketplace", "uninstall", "todo-fox"])
    assert res.exit_code == 1
    assert all(c["method"] == "GET" for c in capture_api["calls"])

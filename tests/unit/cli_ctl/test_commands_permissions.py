from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app
from jarvis.platform.permissions import ACCEPTED_BUNDLE_IDS

runner = CliRunner()


def test_status_reads_fresh_snapshot(capture_api):
    result = runner.invoke(app, ["permissions", "status"])
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/permissions/status"


def test_request_requires_yes(capture_api):
    result = runner.invoke(app, ["permissions", "request", "microphone"])
    assert result.exit_code == 1
    assert capture_api["calls"] == []


def test_request_with_yes_uses_permission_path(monkeypatch, capture_api):
    # Stub the macOS app activation: on a real Mac (incl. the CI runner) the
    # unstubbed helper honestly aborts when no installed app bundle exists,
    # which is not what THIS test is about (it pins the API path).
    monkeypatch.setattr(
        "jarvis.cli_ctl.commands.permissions._activate_macos_app_for_tcc",
        lambda: None,
    )
    result = runner.invoke(
        app, ["permissions", "request", "screen_recording", "--yes"]
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/permissions/screen_recording/request"


def test_request_activates_app_after_confirmation(monkeypatch, capture_api):
    calls: list[str] = []
    monkeypatch.setattr(
        "jarvis.cli_ctl.commands.permissions._activate_macos_app_for_tcc",
        lambda: calls.append("activate"),
    )

    result = runner.invoke(
        app, ["permissions", "request", "screen_recording", "--yes"]
    )

    assert result.exit_code == 0
    assert calls == ["activate"]


def test_open_settings_with_yes_uses_permission_path(monkeypatch, capture_api):
    # Same macOS-proofing as test_request_with_yes_uses_permission_path.
    monkeypatch.setattr(
        "jarvis.cli_ctl.commands.permissions._activate_macos_app_for_tcc",
        lambda: None,
    )
    result = runner.invoke(
        app, ["permissions", "open-settings", "accessibility", "--yes"]
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/permissions/accessibility/open-settings"


def test_request_dry_run_sends_nothing(capture_api):
    result = runner.invoke(
        app, ["--json", "permissions", "request", "microphone", "--dry-run"]
    )
    assert result.exit_code == 0
    assert capture_api["calls"] == []
    assert "dry_run" in result.stdout


def test_request_dry_run_does_not_activate_app(monkeypatch, capture_api):
    calls: list[str] = []
    monkeypatch.setattr(
        "jarvis.cli_ctl.commands.permissions._activate_macos_app_for_tcc",
        lambda: calls.append("activate"),
    )

    result = runner.invoke(
        app, ["permissions", "request", "microphone", "--dry-run"]
    )

    assert result.exit_code == 0
    assert calls == []
    assert capture_api["calls"] == []


def _activate_with_frontmost(monkeypatch, tmp_path, frontmost_id: str):
    """Run the macOS activation helper against a fake frontmost app."""
    from jarvis.cli_ctl.commands import permissions as module

    bundle = tmp_path / "Personal Jarvis.app"
    bundle.mkdir()
    commands: list[list[str]] = []
    frontmost = SimpleNamespace(bundleIdentifier=lambda: frontmost_id)
    workspace = SimpleNamespace(frontmostApplication=lambda: frontmost)
    appkit = SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)
    )
    monkeypatch.setattr(module, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(module, "_installed_macos_app", lambda: bundle)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command) or SimpleNamespace(returncode=0)
        ),
    )
    monkeypatch.setattr(module.importlib, "import_module", lambda _name: appkit)
    # A fake clock keeps the "never became the foreground app" case instant.
    ticks = iter(range(1000))
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: float(next(ticks)), sleep=lambda _s: None),
    )
    return module, bundle, commands


@pytest.mark.parametrize("bundle_id", ACCEPTED_BUNDLE_IDS)
def test_macos_activation_targets_canonical_bundle_and_waits_for_identity(
    monkeypatch, tmp_path, bundle_id
):
    # Both the managed bundle and the downloaded .dmg app are the installed app;
    # the .dmg one used to be refused here, so `jarvis permissions request`
    # failed on a Mac that only has the downloaded app.
    module, bundle, commands = _activate_with_frontmost(monkeypatch, tmp_path, bundle_id)

    module._activate_macos_app_for_tcc()

    assert commands == [["open", str(bundle)]]


def test_macos_activation_refuses_when_another_app_stays_in_front(monkeypatch, tmp_path):
    module, _bundle, _commands = _activate_with_frontmost(
        monkeypatch, tmp_path, "com.apple.Terminal"
    )

    with pytest.raises(typer.Exit) as excinfo:
        module._activate_macos_app_for_tcc()

    assert excinfo.value.exit_code == 1

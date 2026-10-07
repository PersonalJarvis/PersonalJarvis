from __future__ import annotations

import json
import plistlib
import subprocess
import sys
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


def test_status_reads_the_automation_row_only_on_request(capture_api):
    plain = runner.invoke(app, ["permissions", "status"])
    with_automation = runner.invoke(app, ["permissions", "status", "--include-automation"])

    assert plain.exit_code == 0 and with_automation.exit_code == 0
    first, second = capture_api["calls"][-2:]
    assert first["path"] == second["path"] == "/api/permissions/status"
    assert first["query"] == {}
    assert second["query"] == {"include": "automation"}


def test_request_sends_no_body_because_the_installed_app_is_the_only_grantee(
    monkeypatch, capture_api
):
    monkeypatch.setattr(
        "jarvis.cli_ctl.commands.permissions._activate_macos_app_for_tcc", lambda: None
    )

    result = runner.invoke(app, ["permissions", "request", "microphone", "--yes"])

    assert result.exit_code == 0
    assert capture_api["calls"][-1]["body"] is None


def test_request_has_no_outside_app_option(capture_api):
    # The route answers 403 confirmation_requires_ui to a Bearer-only caller, so the
    # flag could only fail and would invite an agent to try: it does not exist.
    result = runner.invoke(
        app, ["permissions", "request", "microphone", "--allow-outside-app", "--yes"]
    )

    assert result.exit_code == 2
    assert capture_api["calls"] == []
    help_text = runner.invoke(app, ["permissions", "request", "--help"]).output
    assert "allow-outside-app" not in help_text


def test_request_refuses_clearly_without_the_installed_app_and_sends_nothing(
    monkeypatch, tmp_path, capture_api
):
    from jarvis.cli_ctl.commands import permissions as module

    monkeypatch.setattr(module, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(module, "_installed_macos_app", lambda: tmp_path / "Missing.app")

    result = runner.invoke(app, ["permissions", "request", "microphone", "--yes"])

    assert result.exit_code == 1
    assert capture_api["calls"] == []
    message = " ".join(result.output.split())
    assert "installed Personal Jarvis app was not found" in message
    assert "nothing was requested" in message


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


# --- permissions reset: the local tccutil command ------------------------------

_RESET_SERVICES = {
    "microphone": "Microphone",
    "screen_recording": "ScreenCapture",
    "accessibility": "Accessibility",
    "input_monitoring": "ListenEvent",
    "event_posting": "PostEvent",
    "automation": "AppleEvents",
}


class _TccutilRecorder:
    """A hand-written stand-in for ``/usr/bin/tccutil`` that records every argv."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv: list[str]) -> SimpleNamespace:
        self.calls.append(list(argv))
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr=self.stderr)


def _macos_with_app(monkeypatch, tmp_path, bundle_id: str | None):
    """Pretend to be macOS with an installed app carrying ``bundle_id`` (None: no app)."""
    from jarvis.cli_ctl.commands import permissions as module

    app_dir = tmp_path / "Personal Jarvis.app"
    if bundle_id is not None:
        (app_dir / "Contents").mkdir(parents=True)
        (app_dir / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": bundle_id})
        )
    recorder = _TccutilRecorder()
    monkeypatch.setattr(module, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(module, "_installed_macos_app", lambda: app_dir)
    monkeypatch.setattr(module, "_run_tccutil", recorder)
    return module, recorder


def test_reset_is_registered_in_the_command_index() -> None:
    from jarvis.cli_ctl.command_index import COMMAND_INDEX

    assert COMMAND_INDEX["permissions"] == ("status", "request", "open-settings", "reset")


def test_reset_off_macos_says_so_in_one_line_and_exits_non_zero(monkeypatch, capture_api):
    from jarvis.cli_ctl.commands import permissions as module

    recorder = _TccutilRecorder()
    monkeypatch.setattr(module, "detect_platform", lambda: "linux")
    monkeypatch.setattr(module, "_run_tccutil", recorder)

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 1
    message = " ".join(result.output.split())
    assert "only works on macOS" in message
    assert len(result.output.strip().splitlines()) <= 3  # the one sentence (it may wrap)
    assert recorder.calls == []
    assert capture_api["calls"] == []


def test_reset_off_macos_refuses_even_a_dry_run(monkeypatch, capture_api):
    from jarvis.cli_ctl.commands import permissions as module

    monkeypatch.setattr(module, "detect_platform", lambda: "win32")

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--dry-run"])

    assert result.exit_code == 1
    assert "only works on macOS" in " ".join(result.output.split())


@pytest.mark.parametrize("bundle_id", ACCEPTED_BUNDLE_IDS)
@pytest.mark.parametrize(("permission", "service"), sorted(_RESET_SERVICES.items()))
def test_reset_builds_exactly_the_tccutil_command_for_each_permission_and_bundle(
    monkeypatch, tmp_path, capture_api, permission, service, bundle_id
):
    module, recorder = _macos_with_app(monkeypatch, tmp_path, bundle_id)

    result = runner.invoke(app, ["--json", "permissions", "reset", permission, "--yes"])

    assert result.exit_code == 0, result.output
    assert recorder.calls == [["/usr/bin/tccutil", "reset", service, bundle_id]]
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["performed"] is True
    assert payload["bundle_id"] == bundle_id and payload["service"] == service
    assert "Quit and reopen" in payload["message"]
    # It is a local command: nothing goes to the app's REST API (its /reset route
    # refuses scripts on purpose).
    assert capture_api["calls"] == []


@pytest.mark.parametrize("bundle_id", ACCEPTED_BUNDLE_IDS)
def test_reset_bundle_id_option_wins_over_the_installed_app(monkeypatch, tmp_path, bundle_id):
    other = next(candidate for candidate in ACCEPTED_BUNDLE_IDS if candidate != bundle_id)
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, other)

    result = runner.invoke(
        app, ["permissions", "reset", "microphone", "--bundle-id", bundle_id, "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert recorder.calls == [["/usr/bin/tccutil", "reset", "Microphone", bundle_id]]


def test_reset_refuses_a_bundle_id_that_is_not_ours(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])

    result = runner.invoke(
        app,
        ["permissions", "reset", "microphone", "--bundle-id", "com.apple.Safari", "--yes"],
    )

    assert result.exit_code == 2
    assert "--bundle-id must be one of" in " ".join(result.output.split())
    assert recorder.calls == []


def test_reset_needs_yes_and_runs_nothing_without_it(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])
    monkeypatch.delenv("JARVIS_CLI_ASSUME_YES", raising=False)

    result = runner.invoke(app, ["permissions", "reset", "screen_recording"])

    assert result.exit_code == 1
    assert "--yes" in result.output
    assert recorder.calls == []


def test_reset_accepts_the_assume_yes_environment(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])
    monkeypatch.setenv("JARVIS_CLI_ASSUME_YES", "1")

    result = runner.invoke(app, ["permissions", "reset", "screen_recording"])

    assert result.exit_code == 0, result.output
    assert len(recorder.calls) == 1


def test_reset_dry_run_prints_the_command_and_runs_nothing(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[1])

    result = runner.invoke(app, ["--json", "permissions", "reset", "accessibility", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["argv"] == ["/usr/bin/tccutil", "reset", "Accessibility", ACCEPTED_BUNDLE_IDS[1]]
    assert recorder.calls == []


def test_reset_refuses_a_permission_without_a_privacy_record(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])

    result = runner.invoke(app, ["permissions", "reset", "credential_store", "--yes"])

    assert result.exit_code == 1
    assert "no macOS privacy record" in " ".join(result.output.split())
    assert recorder.calls == []


def test_reset_without_an_installed_app_says_what_to_do(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, None)

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 1
    message = " ".join(result.output.split())
    assert "installed Personal Jarvis app was not found" in message
    assert "--bundle-id" in message and "nothing was reset" in message
    assert recorder.calls == []


def test_reset_refuses_an_installed_app_with_a_foreign_bundle_id(monkeypatch, tmp_path):
    _module, recorder = _macos_with_app(monkeypatch, tmp_path, "com.example.other")

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 1
    assert "not one of Personal Jarvis's" in " ".join(result.output.split())
    assert recorder.calls == []


def test_reset_refuses_an_unreadable_info_plist(monkeypatch, tmp_path):
    module, recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])
    (tmp_path / "Personal Jarvis.app" / "Contents" / "Info.plist").write_bytes(b"not a plist")

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 1
    message = " ".join(result.output.split())
    assert "Info.plist could not be read" in message and "nothing was reset" in message
    assert recorder.calls == []


def test_reset_reports_a_failing_tccutil_with_its_status(monkeypatch, tmp_path):
    module, _recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])
    failing = _TccutilRecorder(returncode=70, stderr="tccutil: Failed to reset Microphone\n")
    monkeypatch.setattr(module, "_run_tccutil", failing)

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 1
    message = " ".join(result.output.split())
    assert "status 70" in message and "Failed to reset Microphone" in message
    assert len(failing.calls) == 1


@pytest.mark.parametrize("failure", [OSError("no such file"), subprocess.TimeoutExpired("x", 30)])
def test_reset_survives_tccutil_that_cannot_run(monkeypatch, tmp_path, failure):
    module, _recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])

    def explode(_argv):
        raise failure

    monkeypatch.setattr(module, "_run_tccutil", explode)

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 1
    assert "tccutil could not run" in " ".join(result.output.split())


def test_run_tccutil_is_utf8_and_windowless(monkeypatch):
    """AP-1: every subprocess passes NO_WINDOW_CREATIONFLAGS and decodes UTF-8."""
    from jarvis.cli_ctl.commands import permissions as module
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._run_tccutil(["/usr/bin/tccutil", "reset", "Microphone", "x"])

    assert seen["creationflags"] == NO_WINDOW_CREATIONFLAGS
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert seen["text"] is True and seen["check"] is False
    assert seen["timeout"] == module._TCCUTIL_TIMEOUT_S
    assert "shell" not in seen


@pytest.mark.skipif(sys.platform == "win32", reason="the fake tccutil is a POSIX shell script")
def test_reset_end_to_end_with_a_fake_tccutil_executable(monkeypatch, tmp_path):
    """The real ``_run_tccutil`` path: a fake executable stands in for /usr/bin/tccutil."""
    from jarvis.cli_ctl.commands import permissions as module

    log = tmp_path / "argv.log"
    fake = tmp_path / "tccutil"
    fake.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{log}"\nexit 0\n', encoding="utf-8")
    fake.chmod(0o755)
    app_dir = tmp_path / "Personal Jarvis.app"
    (app_dir / "Contents").mkdir(parents=True)
    (app_dir / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": ACCEPTED_BUNDLE_IDS[0]})
    )
    monkeypatch.setattr(module, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(module, "_installed_macos_app", lambda: app_dir)
    monkeypatch.setattr(module, "_TCCUTIL", str(fake))

    result = runner.invoke(app, ["--json", "permissions", "reset", "input_monitoring", "--yes"])

    assert result.exit_code == 0, result.output
    assert log.read_text(encoding="utf-8").split() == [
        "reset",
        "ListenEvent",
        ACCEPTED_BUNDLE_IDS[0],
    ]


@pytest.mark.parametrize(
    ("permission", "target"),
    [
        ("microphone", ""),
        ("screen_recording", ""),
        ("input_monitoring", ""),
        ("automation", "com.apple.Music"),
    ],
)
@pytest.mark.parametrize("bundle_id", ACCEPTED_BUNDLE_IDS)
def test_reset_returns_the_apps_own_row_to_not_determined_in_fake_tcc(
    monkeypatch, tmp_path, permission, target, bundle_id
):
    """FakeTCC models ``tccutil reset``: the named bundle's row goes back to "not asked yet"."""
    from tests.fakes.fake_tcc import FakeTCC, TccState

    tcc = FakeTCC(bundle_id=bundle_id, granted=(permission,))
    module, _recorder = _macos_with_app(monkeypatch, tmp_path, bundle_id)
    monkeypatch.setattr(module, "_run_tccutil", tcc.run_tccutil)
    assert tcc.state(permission, target) is TccState.GRANTED

    result = runner.invoke(app, ["permissions", "reset", permission, "--yes"])

    assert result.exit_code == 0, result.output
    assert len(tcc.tccutil_calls) == 1
    assert tcc.state(permission, target) is TccState.NOT_DETERMINED


@pytest.mark.parametrize("permission", ["accessibility", "event_posting"])
def test_reset_of_the_accessibility_family_is_accepted_by_fake_tcc(
    monkeypatch, tmp_path, permission
):
    """Accessibility has no "not determined" (boolean trust): it reads denied afterwards."""
    from tests.fakes.fake_tcc import FakeTCC, TccState

    tcc = FakeTCC(bundle_id=ACCEPTED_BUNDLE_IDS[0], granted=("accessibility",))
    module, _recorder = _macos_with_app(monkeypatch, tmp_path, ACCEPTED_BUNDLE_IDS[0])
    monkeypatch.setattr(module, "_run_tccutil", tcc.run_tccutil)

    result = runner.invoke(app, ["permissions", "reset", permission, "--yes"])

    assert result.exit_code == 0, result.output
    assert tcc.state("accessibility") is TccState.DENIED


def test_reset_leaves_the_other_apps_row_alone_in_fake_tcc(monkeypatch, tmp_path):
    from tests.fakes.fake_tcc import FakeTCC, TccState

    ours, other = ACCEPTED_BUNDLE_IDS
    tcc = FakeTCC(bundle_id=other, granted=("microphone",))
    module, _recorder = _macos_with_app(monkeypatch, tmp_path, ours)
    monkeypatch.setattr(module, "_run_tccutil", tcc.run_tccutil)

    result = runner.invoke(app, ["permissions", "reset", "microphone", "--yes"])

    assert result.exit_code == 0, result.output
    assert tcc.tccutil_calls == [["/usr/bin/tccutil", "reset", "Microphone", ours]]
    assert tcc.state("microphone") is TccState.GRANTED

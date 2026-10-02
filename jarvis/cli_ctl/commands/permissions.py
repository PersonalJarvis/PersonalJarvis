"""permissions: inspect and request desktop privacy access."""

from __future__ import annotations

import importlib
import plistlib
import subprocess
import time
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jarvis.cli_ctl import invoke, options, render, safety
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.permissions import ACCEPTED_BUNDLE_IDS, PermissionId, tcc_reset_service

app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and request macOS privacy permissions.",
)


def _installed_macos_app() -> Path:
    from jarvis.setup.macos_app_bundle import installed_macos_app_bundle_path

    return installed_macos_app_bundle_path()


def _activation_error(message: str) -> NoReturn:
    render.error(message)
    raise typer.Exit(code=1)


def _activate_macos_app_for_tcc() -> None:
    """Foreground the canonical bundle before its server invokes a TCC API."""
    if detect_platform() != "darwin":
        return
    bundle = _installed_macos_app()
    if not bundle.is_dir():
        _activation_error(
            "The installed Personal Jarvis app was not found, so nothing was "
            "requested: macOS permissions are only requested for the installed app, "
            "never for the terminal that runs this command. Run the standard "
            "installer, or allow the permission in System Settings yourself."
        )
    try:
        completed = subprocess.run(
            ["open", str(bundle)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError):
        _activation_error("Personal Jarvis could not be activated through LaunchServices.")
    if completed.returncode != 0:
        _activation_error("Personal Jarvis could not be activated through LaunchServices.")

    try:
        appkit = importlib.import_module("AppKit")
        workspace = appkit.NSWorkspace.sharedWorkspace()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            frontmost = workspace.frontmostApplication()
            raw_id = frontmost.bundleIdentifier() if frontmost is not None else None
            if raw_id and str(raw_id) in ACCEPTED_BUNDLE_IDS:
                return
            time.sleep(0.05)
    except Exception as exc:  # noqa: BLE001 - native verification fails closed
        _activation_error(
            "The macOS foreground app could not be verified "
            f"({type(exc).__name__})."
        )
    _activation_error(
        "Personal Jarvis did not become the foreground app. Activate its window "
        "and retry the permission command."
    )


@app.command()
def status(
    include_automation: Annotated[
        bool,
        typer.Option(
            "--include-automation",
            help=(
                "Also read the Automation row (checks a running Music or Spotify, "
                "without asking)."
            ),
        ),
    ] = False,
) -> None:
    """Show each macOS privacy permission and what is waiting on one. Never prompts."""
    invoke.run(
        "GET",
        "/api/permissions/status",
        params={"include": "automation"} if include_automation else None,
    )


@app.command()
def request(
    permission_id: Annotated[
        PermissionId,
        typer.Argument(help="Permission to request from macOS."),
    ],
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Ask macOS for one permission through the same service the app uses.

    Only the installed app is asked for: confirming a grant for the app that
    started Jarvis (a terminal, an IDE) needs the Jarvis window, so there is no
    flag for it here and nothing is requested without the installed bundle.
    """
    invoke.run(
        "POST",
        f"/api/permissions/{permission_id.value}/request",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        # The installed app is the only grantee this command asks for.
        before_request=_activate_macos_app_for_tcc,
    )


@app.command("open-settings")
def open_settings(
    permission_id: Annotated[
        PermissionId,
        typer.Argument(help="Permission pane to open in macOS System Settings."),
    ],
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Open the matching macOS privacy pane through LaunchServices."""
    invoke.run(
        "POST",
        f"/api/permissions/{permission_id.value}/open-settings",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        before_request=_activate_macos_app_for_tcc,
    )


# The system tool that forgets one app's answer for one privacy service. A fixed
# absolute path: nothing from PATH may stand in for it.
_TCCUTIL = "/usr/bin/tccutil"
_TCCUTIL_TIMEOUT_S = 30


def _installed_bundle_id() -> str:
    """The bundle id of the installed app (managed or downloaded), or exit with a reason.

    Read from the app's own ``Info.plist`` (never from the process that runs this
    command, which is a terminal): the managed bundle and the downloaded .dmg app
    carry different ids and therefore keep different privacy records.
    """
    bundle = _installed_macos_app()
    info_path = bundle / "Contents" / "Info.plist"
    if not bundle.is_dir() or not info_path.is_file():
        _activation_error(
            "The installed Personal Jarvis app was not found, so nothing was reset. "
            "Pass --bundle-id to name the app, or run `tccutil reset <service> <bundle id>` "
            "yourself (see docs/product/privacy-safety-and-support/troubleshooting.md)."
        )
    try:
        with info_path.open("rb") as stream:
            raw_id = plistlib.load(stream).get("CFBundleIdentifier")
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        _activation_error(
            f"The installed app's Info.plist could not be read ({type(exc).__name__}), "
            "so nothing was reset. Pass --bundle-id."
        )
    if not isinstance(raw_id, str) or raw_id not in ACCEPTED_BUNDLE_IDS:
        _activation_error(
            "The installed app's bundle id is not one of Personal Jarvis's "
            f"({', '.join(ACCEPTED_BUNDLE_IDS)}), so nothing was reset. Pass --bundle-id."
        )
    return raw_id


def _run_tccutil(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``tccutil`` (the one seam tests replace); UTF-8, no console window (AP-1)."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TCCUTIL_TIMEOUT_S,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


@app.command()
def reset(
    permission_id: Annotated[
        PermissionId,
        typer.Argument(help="Permission whose macOS answer should be forgotten."),
    ],
    bundle_id: Annotated[
        str | None,
        typer.Option(
            "--bundle-id",
            help=(
                "App to reset: ai.personaljarvis.desktop (the downloaded app) or "
                "com.personal-jarvis.desktop (the managed app). Default: the installed app; "
                "name it when both are installed."
            ),
        ),
    ] = None,
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Forget what macOS recorded for Personal Jarvis, so it asks again on next use (macOS only).

    Runs `/usr/bin/tccutil reset <service> <bundle id>` on this Mac. This is the
    way out of a stuck "denied" answer, since macOS never asks twice by itself
    and Personal Jarvis has no Privacy page. It only touches this app's own
    record for one service and never another app's. It does not use the running
    app (the app's own reset route refuses scripts on purpose), so it works with
    Jarvis closed; quit and reopen the app afterwards, then use the feature.
    """
    from jarvis.cli_ctl.__main__ import as_json

    if detect_platform() != "darwin":
        render.error(
            "permissions reset only works on macOS: other systems have no per-app "
            "privacy record to reset."
        )
        raise typer.Exit(code=1)
    service = tcc_reset_service(permission_id)
    if service is None:
        render.error(f"{permission_id.value} has no macOS privacy record to reset.")
        raise typer.Exit(code=1)
    if bundle_id is not None and bundle_id not in ACCEPTED_BUNDLE_IDS:
        render.error(
            f"--bundle-id must be one of: {', '.join(ACCEPTED_BUNDLE_IDS)} "
            "(this command only resets Personal Jarvis's own record)."
        )
        raise typer.Exit(code=2)
    target = bundle_id if bundle_id is not None else _installed_bundle_id()
    argv = [_TCCUTIL, "reset", service, target]
    plan = {
        "permission_id": permission_id.value,
        "service": service,
        "bundle_id": target,
        "argv": argv,
    }
    if dry_run:
        render.emit({"dry_run": True, **plan}, as_json=as_json())
        return
    safety.require_yes_for_local_action(
        f"Resetting the {permission_id.value} permission ({' '.join(argv)})", assume_yes=yes
    )
    try:
        completed = _run_tccutil(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        render.error(f"tccutil could not run ({type(exc).__name__}).")
        raise typer.Exit(code=1) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-200:]
        render.error(
            f"tccutil exited with status {completed.returncode}"
            + (f": {detail}" if detail else ".")
        )
        raise typer.Exit(code=1)
    render.emit(
        {
            "ok": True,
            "performed": True,
            **plan,
            "message": (
                f"The {permission_id.value} record of {target} was reset. Quit and reopen "
                "Personal Jarvis, then use the feature: macOS asks again."
            ),
        },
        as_json=as_json(),
    )


__all__ = ["app"]

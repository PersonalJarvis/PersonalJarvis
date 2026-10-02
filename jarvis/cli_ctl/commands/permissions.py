"""permissions: inspect and request desktop privacy access."""

from __future__ import annotations

import importlib
import subprocess
import time
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jarvis.cli_ctl import invoke, options, render
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.permissions import ACCEPTED_BUNDLE_IDS, PermissionId

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


__all__ = ["app"]

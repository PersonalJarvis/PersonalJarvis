"""system: lifecycle control of the running app (restart, status)."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(no_args_is_help=True, help="App lifecycle control.")


@app.command()
def restart(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Compatibility flag; CLI/API restart requests remain blocked.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Compatibility flag; it is not proof of a user's presence.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the request and exit without restarting."
    ),
) -> None:
    """Refuse a CLI restart; use the desktop UI's explicit Restart action."""
    from jarvis.cli_ctl import safety
    from jarvis.cli_ctl.__main__ import as_json

    path = (
        "/api/settings/restart-app?force=true"
        if force
        else "/api/settings/restart-app"
    )
    if dry_run:
        safety.gate_request(
            "POST",
            path,
            assume_yes=yes,
            dry_run=True,
            as_json=as_json(),
        )
        return  # dry run: preview already printed, nothing sent

    render.error(
        "Desktop restarts require an explicit click in the desktop UI. "
        "Control API and coding-agent clients are not allowed to restart the app; "
        "--yes and --force do not override this boundary."
    )
    raise typer.Exit(code=1)


@app.command("audio-devices")
def audio_devices(
    output: str = typer.Option(
        None,
        "--output",
        help=(
            "Pick the voice OUTPUT device by display name "
            "('auto-headset' restores automatic selection)."
        ),
    ),
    input_: str = typer.Option(
        None,
        "--input",
        help=(
            "Pick the MICROPHONE by display name "
            "('auto-headset' restores automatic selection)."
        ),
    ),
) -> None:
    """List audio devices, or pick where the voice plays / which mic listens.

    Without options: GET /api/settings/audio-devices — one entry per physical
    device plus the current [audio] selection. With --output/--input: PUT the
    pick; it persists to jarvis.toml and applies live to the running voice
    pipeline (reversible, so no --yes gate).
    """
    from jarvis.cli_ctl.__main__ import as_json, make_client

    try:
        with make_client() as client:
            if output is None and input_ is None:
                out = client.request("GET", "/api/settings/audio-devices")
            else:
                body: dict[str, object] = {"persist": True}
                if output is not None:
                    body["output_device"] = output
                if input_ is not None:
                    body["input_device"] = input_
                out = client.request(
                    "PUT", "/api/settings/audio-devices", json=body
                )
    except ApiError as exc:
        render.error(exc.message)
        raise typer.Exit(code=1) from exc
    render.emit(out, as_json=as_json())


@app.command()
def status() -> None:
    """Report server reachability + version (GET /api/control/auth/probe)."""
    from jarvis.cli_ctl.__main__ import as_json, make_client

    try:
        with make_client() as client:
            client.request("GET", "/api/control/auth/probe")
        reachable = True
    except ApiError:
        reachable = False
    render.emit({"reachable": reachable}, as_json=as_json())
    if not reachable:
        raise typer.Exit(code=1)

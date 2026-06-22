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
        help="Restart even while missions are running (this kills them).",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Authorize the restart without a prompt."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the request and exit without restarting."
    ),
) -> None:
    """Cleanly restart the desktop app (POST /api/settings/restart-app).

    This is the deterministic restart path — use it instead of asking the
    voice/CU layer to 'restart yourself' (which mis-routes to the GUI loop).

    A restart kills every in-flight mission, so the server refuses (HTTP 409)
    while missions run and lists them; pass ``--force`` to restart anyway.
    """
    from jarvis.cli_ctl import safety
    from jarvis.cli_ctl.__main__ import as_json, make_client

    if not safety.gate_request(
        "POST", "/api/settings/restart-app",
        assume_yes=yes, dry_run=dry_run, as_json=as_json(),
    ):
        return  # dry run: preview already printed, nothing sent

    try:
        with make_client() as client:
            out = client.request(
                "POST",
                "/api/settings/restart-app",
                params={"force": "true"} if force else None,
            )
    except ApiError as exc:
        if exc.status_code == 409 and isinstance(exc.payload, dict):
            missions = exc.payload.get("missions", []) or []
            lines = [
                f"  - {m.get('id')}  {(m.get('title') or '').strip() or '(no title)'}"
                for m in missions
            ]
            render.error(
                f"{len(missions)} mission(s) still running — restart refused.\n"
                + "\n".join(lines)
                + "\nRe-run with --force to restart anyway (this kills them)."
            )
            raise typer.Exit(code=1) from exc
        render.error(exc.message)
        raise typer.Exit(code=1) from exc
    render.emit(out or {"restarting": True}, as_json=as_json())


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

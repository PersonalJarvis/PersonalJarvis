"""marketplace: connect/disconnect marketplace plugins (/api/marketplace)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Marketplace plugins: list, connect, disconnect.")


@app.command("list")
def list_plugins() -> None:
    """List marketplace plugins + their connection status."""
    invoke.run("GET", "/api/marketplace/plugins")


@app.command("connect-pat")
def connect_pat(
    plugin_id: str = typer.Argument(...),
    token: str = typer.Option(
        ...,
        "--token",
        prompt="Personal access token",
        hide_input=True,
        help="PAT (read from a hidden prompt unless passed; avoid inline).",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Connect a plugin with a personal access token."""
    invoke.run(
        "POST",
        f"/api/marketplace/plugins/{plugin_id}/connect/pat",
        body={"token": token},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command("connect-start")
def connect_start(
    plugin_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Begin an OAuth connect flow (prints the redirect URI + flow id)."""
    invoke.run(
        "POST",
        f"/api/marketplace/plugins/{plugin_id}/connect/start",
        body={},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command("connect-poll")
def connect_poll(plugin_id: str = typer.Argument(...), flow_id: str = typer.Argument(...)) -> None:
    """Poll an in-progress OAuth connect flow."""
    invoke.run("GET", f"/api/marketplace/plugins/{plugin_id}/connect/poll/{flow_id}")


@app.command()
def disconnect(
    plugin_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Disconnect a plugin."""
    invoke.run("DELETE", f"/api/marketplace/plugins/{plugin_id}", assume_yes=yes, dry_run=dry_run)

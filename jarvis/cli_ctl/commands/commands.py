"""commands: browse the Command Registry (the app's curated command catalog).

The registry (``GET /api/commands``) is the one machine-readable catalog of
user-facing app commands — the same catalog the brain's ``app-command`` tool
and the desktop UI consume. Read-only here: executing a command goes through
its own endpoint (see each entry's method+path, or the equivalent curated
CLI command).
"""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke

app = typer.Typer(
    no_args_is_help=True,
    help="Command Registry: list app commands, show one command's schema.",
)


@app.command("list")
def list_commands() -> None:
    """List every registry command (id, endpoint, params, danger, UI section)."""
    invoke.run("GET", "/api/commands")


@app.command()
def show(
    command_id: str = typer.Argument(
        ..., help="Registry command id, e.g. brain-switch."
    ),
) -> None:
    """Show one command's full definition (params schema, voice aliases)."""
    invoke.run("GET", f"/api/commands/{command_id}")

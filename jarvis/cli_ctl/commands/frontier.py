"""frontier: review + acknowledge proposed model upgrades (/api/frontier)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Frontier model auto-switch: pending, ack.")


@app.command()
def pending() -> None:
    """List proposed model upgrades awaiting acknowledgement."""
    invoke.run("GET", "/api/frontier/pending")


@app.command()
def ack(yes: bool = options.yes_opt(), dry_run: bool = options.dry_opt()) -> None:
    """Acknowledge (dismiss) the pending frontier proposals."""
    invoke.run("POST", "/api/frontier/ack", assume_yes=yes, dry_run=dry_run)

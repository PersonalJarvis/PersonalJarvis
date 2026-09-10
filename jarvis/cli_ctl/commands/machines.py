"""machines: paired computers, persistent jobs and agent placement."""

from urllib.parse import quote

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Connected computers and remote agent hosting.")


@app.command("list")
def list_machines() -> None:
    """List computers and their available capabilities."""
    invoke.run("GET", "/api/machines")


@app.command()
def hosts() -> None:
    """Show each agent's execution host and transfer history."""
    invoke.run("GET", "/api/machines/placements")


@app.command()
def move(
    agent_id: str,
    host: str = typer.Option(..., "--host"),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Queue a verified workspace handoff to a host (or 'local')."""
    invoke.run(
        "POST",
        f"/api/machines/agents/{quote(agent_id, safe='')}/move",
        body={"host_id": host},
        dangerous=True,
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def transfer(transfer_id: str) -> None:
    """Read a queued transfer's durable result."""
    invoke.run("GET", f"/api/machines/transfers/{quote(transfer_id, safe='')}")

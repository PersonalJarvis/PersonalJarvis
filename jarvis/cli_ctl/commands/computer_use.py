"""computer-use: drive desktop-automation goals (/api/computer-use/goals)."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(
    no_args_is_help=True,
    help="Computer Use: start, watch, and cancel desktop-automation goals.",
)


@app.command()
def start(
    goal: str = typer.Argument(..., help="What Jarvis should do on the desktop."),
    timeout_s: float = typer.Option(
        120.0, "--timeout-s", help="Mission deadline in seconds (10-3600).",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Start a desktop goal in the background; prints the mission id."""
    invoke.run(
        "POST",
        "/api/computer-use/goals",
        body={"goal": goal, "timeout_s": timeout_s},
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )


@app.command("list")
def list_runs(
    limit: int = typer.Option(20, "--limit", help="Newest-first run count."),
) -> None:
    """List active and recent Computer-Use runs."""
    invoke.run("GET", "/api/computer-use/goals", params={"limit": limit})


@app.command()
def show(mission_id: str = typer.Argument(..., help="Mission id.")) -> None:
    """Show one run: status, goal, exit code, final output."""
    invoke.run("GET", f"/api/computer-use/goals/{mission_id}")


@app.command()
def cancel(
    mission_id: str = typer.Argument(..., help="Mission id."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Cancel one active run."""
    invoke.run(
        "POST",
        f"/api/computer-use/goals/{mission_id}/cancel",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )


@app.command("cancel-all")
def cancel_all(
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Cancel every active run (queued and running)."""
    invoke.run(
        "POST",
        "/api/computer-use/goals/cancel-all",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )

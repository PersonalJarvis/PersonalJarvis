"""board: the "knows-you" dashboard + profile (/api/board, /api/profile)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Personal board: stats, records, achievements, bio.")


@app.command()
def summary(window_days: int = typer.Option(30, "--window-days", min=1, max=365)) -> None:
    """Show personal totals + streaks over a window."""
    invoke.run("GET", "/api/board/personal/summary", params={"window_days": window_days})


@app.command()
def heatmap(days: int = typer.Option(365, "--days", min=7, max=730)) -> None:
    """Show the activity heatmap cells."""
    invoke.run("GET", "/api/board/personal/heatmap", params={"days": days})


@app.command()
def records() -> None:
    """Show personal records."""
    invoke.run("GET", "/api/board/personal/records")


@app.command()
def achievements() -> None:
    """Show unlocked + locked achievements."""
    invoke.run("GET", "/api/board/achievements")


@app.command()
def bio() -> None:
    """Show the AI-generated bio."""
    invoke.run("GET", "/api/board/bio")


@app.command("bio-regenerate")
def bio_regenerate(yes: bool = options.yes_opt(), dry_run: bool = options.dry_opt()) -> None:
    """Regenerate the AI bio."""
    invoke.run("POST", "/api/board/bio/regenerate", assume_yes=yes, dry_run=dry_run)


@app.command()
def profile() -> None:
    """Show the profile (meta + people + review count)."""
    invoke.run("GET", "/api/profile")

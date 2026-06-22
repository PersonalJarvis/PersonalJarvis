"""outputs: browse mission deliverables / artifacts (/api/outputs)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Mission deliverables: list, files, open.")


@app.command("list")
def list_outputs() -> None:
    """List output sessions (a mission's deliverable folders)."""
    invoke.run("GET", "/api/outputs")


@app.command()
def plan(slug: str = typer.Argument(...)) -> None:
    """Show a session's plan + steps."""
    invoke.run("GET", f"/api/outputs/{slug}/plan")


@app.command()
def files(slug: str = typer.Argument(...)) -> None:
    """List the artifacts a mission produced."""
    invoke.run("GET", f"/api/outputs/{slug}/artifacts")


@app.command()
def openers() -> None:
    """List installed editors/apps that can open an artifact."""
    invoke.run("GET", "/api/outputs/openers")


@app.command("open-with")
def open_with(
    slug: str = typer.Argument(...),
    path: str = typer.Argument(..., help="Artifact path within the session."),
    opener: str = typer.Option(..., "--opener", help="Opener id from `outputs openers`."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Open an artifact with a chosen editor (desktop only)."""
    invoke.run(
        "POST",
        f"/api/outputs/{slug}/files/{path}/open-with",
        body={"opener": opener},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command("preferred-opener")
def preferred_opener(
    opener: str = typer.Argument(None, help="Set the default opener; omit to read it."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Get or set the default artifact opener."""
    if opener is None:
        invoke.run("GET", "/api/outputs/preferred-opener")
    else:
        invoke.run(
            "PUT",
            "/api/outputs/preferred-opener",
            body={"opener": opener},
            assume_yes=yes,
            dry_run=dry_run,
        )

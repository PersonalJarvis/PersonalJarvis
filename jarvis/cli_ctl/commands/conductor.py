"""conductor: YAML-first agentic-workflow jobs (/api/conductor)."""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(
    no_args_is_help=True, help="Conductor jobs: list, show, add, run, toggle, delete."
)


@app.command("list")
def list_jobs() -> None:
    """List Conductor jobs + a run summary."""
    invoke.run("GET", "/api/conductor/jobs")


@app.command()
def show(job_id: str = typer.Argument(...)) -> None:
    """Show one job + recent runs."""
    invoke.run("GET", f"/api/conductor/jobs/{job_id}")


@app.command()
def add(
    definition: str = typer.Option(..., "--def", help="Job JSON ('-' reads stdin)."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Add a job from a Job JSON document."""
    raw = sys.stdin.read() if definition == "-" else definition
    try:
        body = json.loads(raw)
    except ValueError as exc:
        render.error(f"--def is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    invoke.run("POST", "/api/conductor/jobs", body=body, assume_yes=yes, dry_run=dry_run)


@app.command()
def run(
    job_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Manually trigger a job run."""
    invoke.run(
        "POST", f"/api/conductor/jobs/{job_id}/run", body={}, assume_yes=yes, dry_run=dry_run
    )


@app.command()
def toggle(
    job_id: str = typer.Argument(...),
    enabled: bool = typer.Option(..., "--enabled/--disabled"),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Enable or disable a job."""
    invoke.run(
        "PATCH",
        f"/api/conductor/jobs/{job_id}",
        body={"enabled": enabled},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def delete(
    job_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete a job."""
    invoke.run("DELETE", f"/api/conductor/jobs/{job_id}", assume_yes=yes, dry_run=dry_run)

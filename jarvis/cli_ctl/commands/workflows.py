"""workflows: imperative multi-step pipelines (/api/workflows)."""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(no_args_is_help=True, help="Workflows: list, show, create, run, delete.")


@app.command("list")
def list_workflows() -> None:
    """List workflows + a run summary."""
    invoke.run("GET", "/api/workflows")


@app.command()
def show(workflow_id: str = typer.Argument(...)) -> None:
    """Show one workflow + recent runs."""
    invoke.run("GET", f"/api/workflows/{workflow_id}")


@app.command()
def create(
    definition: str = typer.Option(..., "--def", help="WorkflowDef JSON ('-' reads stdin)."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Create a workflow from a WorkflowDef JSON document."""
    raw = sys.stdin.read() if definition == "-" else definition
    try:
        body = json.loads(raw)
    except ValueError as exc:
        render.error(f"--def is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    invoke.run("POST", "/api/workflows", body=body, assume_yes=yes, dry_run=dry_run)


@app.command()
def run(
    workflow_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Trigger a workflow run."""
    invoke.run(
        "POST", f"/api/workflows/{workflow_id}/run", body={}, assume_yes=yes, dry_run=dry_run
    )


@app.command()
def delete(
    workflow_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete a workflow."""
    invoke.run("DELETE", f"/api/workflows/{workflow_id}", assume_yes=yes, dry_run=dry_run)


@app.command("run-history")
def run_history(workflow_id: str = typer.Option(None, "--workflow-id")) -> None:
    """List workflow runs (optionally for one workflow)."""
    params = {"workflow_id": workflow_id} if workflow_id else None
    invoke.run("GET", "/api/workflows/runs/", params=params)

"""tasks: drive the persistent task queue (/api/tasks)."""
from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, render

app = typer.Typer(no_args_is_help=True, help="Inspect and manage scheduled tasks.")

_YES = typer.Option(False, "--yes", "-y", help="Authorize the mutation without a prompt.")
_DRY = typer.Option(False, "--dry-run", help="Print the request and exit without sending.")


@app.command("list")
def list_tasks(
    state: str = typer.Option(None, "--state", help="Filter by task state."),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List tasks (optionally filtered by state)."""
    params: dict[str, object] = {"limit": limit}
    if state:
        params["state"] = state
    invoke.run("GET", "/api/tasks", params=params)


@app.command()
def get(task_id: str = typer.Argument(..., help="Task id.")) -> None:
    """Show one task with its step timeline."""
    invoke.run("GET", f"/api/tasks/{task_id}")


@app.command()
def create(
    json_body: str = typer.Option(
        ..., "--json-body",
        help="TaskSpec as JSON (use '-' to read from stdin).",
    ),
    yes: bool = _YES,
    dry_run: bool = _DRY,
) -> None:
    """Create + schedule a task from a TaskSpec JSON document.

    A TaskSpec needs at least: title, trigger {type: after_delay|at_time|
    on_event|every, ...}, action {kind: harness_dispatch|speak|tool_call|
    agent, ...}. Example:

      {"title":"remind","trigger":{"type":"after_delay","delay_seconds":60},
       "action":{"kind":"speak","text":"stand up"}}
    """
    raw = sys.stdin.read() if json_body == "-" else json_body
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        render.error(f"--json-body is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    invoke.run("POST", "/api/tasks", body=spec, assume_yes=yes, dry_run=dry_run)


@app.command()
def cancel(
    task_id: str = typer.Argument(...),
    yes: bool = _YES,
    dry_run: bool = _DRY,
) -> None:
    """Soft-cancel a scheduled/running task."""
    invoke.run("POST", f"/api/tasks/{task_id}/cancel", assume_yes=yes, dry_run=dry_run)


@app.command()
def delete(
    task_id: str = typer.Argument(...),
    yes: bool = _YES,
    dry_run: bool = _DRY,
) -> None:
    """Hard-delete a task (terminal states only, server-enforced)."""
    invoke.run("DELETE", f"/api/tasks/{task_id}", assume_yes=yes, dry_run=dry_run)

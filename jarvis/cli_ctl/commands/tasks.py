"""tasks: drive the persistent task queue (/api/tasks)."""
from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(no_args_is_help=True, help="Inspect and manage scheduled tasks.")


def _run(method: str, path: str, *, params=None, body=None):
    from jarvis.cli_ctl.__main__ import as_json, make_client

    try:
        with make_client() as client:
            out = client.request(method, path, params=params, json=body)
    except ApiError as exc:
        render.error(exc.message)
        raise typer.Exit(code=1) from exc
    render.emit(out, as_json=as_json())


@app.command("list")
def list_tasks(
    state: str = typer.Option(None, "--state", help="Filter by task state."),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List tasks (optionally filtered by state)."""
    params = {"limit": limit}
    if state:
        params["state"] = state
    _run("GET", "/api/tasks", params=params)


@app.command()
def get(task_id: str = typer.Argument(..., help="Task id.")) -> None:
    """Show one task with its step timeline."""
    _run("GET", f"/api/tasks/{task_id}")


@app.command()
def create(
    json_body: str = typer.Option(
        ..., "--json-body",
        help="TaskSpec as JSON (use '-' to read from stdin).",
    ),
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
    _run("POST", "/api/tasks", body=spec)


@app.command()
def cancel(task_id: str = typer.Argument(...)) -> None:
    """Soft-cancel a scheduled/running task."""
    _run("POST", f"/api/tasks/{task_id}/cancel")


@app.command()
def delete(task_id: str = typer.Argument(...)) -> None:
    """Hard-delete a task (terminal states only, server-enforced)."""
    _run("DELETE", f"/api/tasks/{task_id}")

"""missions: drive Phase-6 self-healing missions (/api/missions)."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Self-healing missions: list, dispatch, control.")


@app.command("list")
def list_missions(
    state: str = typer.Option(
        None, "--state",
        help="Comma-separated states: PENDING,RUNNING,APPROVED,FAILED,CANCELLED,TIMED_OUT.",
    ),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List missions (optionally filtered by state)."""
    params: dict[str, object] = {"limit": limit}
    if state:
        params["state"] = state
    invoke.run("GET", "/api/missions", params=params)


@app.command()
def show(mission_id: str = typer.Argument(..., help="Mission id.")) -> None:
    """Show one mission with its events + verdicts."""
    invoke.run("GET", f"/api/missions/{mission_id}")


@app.command()
def result(mission_id: str = typer.Argument(..., help="Mission id.")) -> None:
    """Read a mission's signed outcome and actual deliverable contents."""
    invoke.run("GET", f"/api/missions/{mission_id}/result")


@app.command("tool-approvals")
def tool_approvals(mission_id: str = typer.Argument(..., help="Mission id.")) -> None:
    """List supervisor tool calls waiting for approval in a mission."""
    invoke.run("GET", f"/api/missions/{mission_id}/tool-approvals")


@app.command("approve-tool")
def approve_tool(
    mission_id: str = typer.Argument(..., help="Mission id."),
    trace_id: str = typer.Argument(..., help="Approval trace id."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Approve one paused mission tool call and resume it."""
    invoke.run(
        "POST",
        f"/api/missions/{mission_id}/tool-approvals/{trace_id}/approve",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )


@app.command("deny-tool")
def deny_tool(
    mission_id: str = typer.Argument(..., help="Mission id."),
    trace_id: str = typer.Argument(..., help="Approval trace id."),
    reason: str = typer.Option("user_denied", "--reason", help="Audit reason."),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Deny one paused mission tool call without executing it."""
    invoke.run(
        "POST",
        f"/api/missions/{mission_id}/tool-approvals/{trace_id}/deny",
        body={"reason": reason},
        dry_run=dry_run,
    )


@app.command()
def dispatch(
    prompt: str = typer.Argument(..., help="The task for the worker."),
    language: str = typer.Option("en", "--language", help="de | en (voice readback language)."),
    confirmed: bool = typer.Option(
        False, "--confirmed",
        help="Pre-confirm a task the server flags destructive (skip the 409 gate).",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Dispatch a new self-healing mission — spawns a worker (destructive: --yes)."""
    invoke.run(
        "POST", "/api/missions/dispatch",
        body={"prompt": prompt, "language": language, "confirmed": confirmed},
        assume_yes=yes, dry_run=dry_run, dangerous=True,
    )


@app.command()
def cancel(
    mission_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Cancel a running mission (kills its worker)."""
    invoke.run(
        "POST", f"/api/missions/{mission_id}/cancel",
        assume_yes=yes, dry_run=dry_run, dangerous=True,
    )


@app.command()
def rerun(
    mission_id: str = typer.Argument(...),
    confirmed: bool = typer.Option(False, "--confirmed", help="Confirm a destructive re-run."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Re-dispatch a terminal mission's prompt as a new linked mission."""
    invoke.run(
        "POST", f"/api/missions/{mission_id}/rerun",
        body={"confirmed": confirmed},
        assume_yes=yes, dry_run=dry_run, dangerous=True,
    )


@app.command()
def kill(
    worker_id: str = typer.Argument(..., help="Worker id (from the mission events)."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Hard-kill a worker process by id."""
    invoke.run(
        "POST", f"/api/missions/kill/{worker_id}",
        assume_yes=yes, dry_run=dry_run, dangerous=True,
    )

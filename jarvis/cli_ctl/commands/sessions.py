"""sessions: browse + manage conversation history (/api/chats).

The unified history merges text threads and voice sessions; most commands take a
`kind` (text | voice) plus the id, mirroring /api/chats/{kind}/{id}.
"""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Conversation history: list, show, manage.")


@app.command("list")
def list_sessions(
    days: int = typer.Option(0, "--days", help="Only sessions from the last N days (0 = all)."),
    limit: int = typer.Option(200, "--limit"),
) -> None:
    """List text + voice sessions, newest first."""
    invoke.run("GET", "/api/chats", params={"days": days, "limit": limit})


@app.command("latest-turn")
def latest_turn(
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Restrict the lookup to one voice session.",
    ),
) -> None:
    """Show the latest persisted user transcript and its complete turn."""
    params = {"session_id": session_id} if session_id else None
    invoke.run("GET", "/api/sessions/latest-turn", params=params)


@app.command()
def show(
    kind: str = typer.Argument(..., help="text | voice."),
    session_id: str = typer.Argument(...),
) -> None:
    """Show one conversation with its messages."""
    invoke.run("GET", f"/api/chats/{kind}/{session_id}")


@app.command()
def delete(
    session_id: str = typer.Argument(..., help="Text-thread id (voice is retention-managed)."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete a text conversation thread."""
    invoke.run(
        "DELETE", f"/api/chats/text/{session_id}",
        assume_yes=yes, dry_run=dry_run, dangerous=True,
    )


@app.command()
def resume(
    kind: str = typer.Argument(..., help="text | voice."),
    session_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Seed the brain from a past conversation to continue it in text."""
    invoke.run(
        "POST", f"/api/chats/{kind}/{session_id}/resume",
        assume_yes=yes, dry_run=dry_run, dangerous=False,
    )


@app.command()
def speak(
    kind: str = typer.Argument(..., help="text | voice."),
    session_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Start a voice session seeded from a past conversation (503 on headless)."""
    invoke.run(
        "POST", f"/api/chats/{kind}/{session_id}/speak",
        assume_yes=yes, dry_run=dry_run, dangerous=False,
    )

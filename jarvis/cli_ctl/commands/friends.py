"""friends: friend-registry CRUD + outbound DM (/api/friends).

Channel-link and permission-profile endpoints stay reachable via
`jarvis api friends ...`; this curated group covers the everyday surface
(list/show/add/edit/delete, read the thread, send a message).
"""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(
    no_args_is_help=True,
    help="Friends: list, show, add, edit, delete, message.",
)


def _body_from(json_body: str) -> dict:
    raw = sys.stdin.read() if json_body == "-" else json_body
    try:
        return json.loads(raw)
    except ValueError as exc:
        render.error(f"--json-body is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc


@app.command("list")
def list_friends() -> None:
    """List friends with their channels."""
    invoke.run("GET", "/api/friends")


@app.command()
def show(friend_id: str = typer.Argument(...)) -> None:
    """Show one friend (detail + channels + permission profile)."""
    invoke.run("GET", f"/api/friends/{friend_id}")


@app.command()
def add(
    json_body: str = typer.Option(
        ..., "--json-body",
        help="Friend JSON ('-' reads stdin): {display_name, avatar_url?, note?}.",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Add a friend."""
    invoke.run("POST", "/api/friends", body=_body_from(json_body), assume_yes=yes, dry_run=dry_run)


@app.command()
def edit(
    friend_id: str = typer.Argument(...),
    json_body: str = typer.Option(
        ..., "--json-body", help="Partial friend JSON ('-' reads stdin)."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Edit a friend (partial)."""
    invoke.run(
        "PATCH",
        f"/api/friends/{friend_id}",
        body=_body_from(json_body),
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def delete(
    friend_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete a friend and their channels."""
    invoke.run("DELETE", f"/api/friends/{friend_id}", assume_yes=yes, dry_run=dry_run)


@app.command()
def messages(friend_id: str = typer.Argument(...)) -> None:
    """Show the message thread with a friend."""
    invoke.run("GET", f"/api/friends/{friend_id}/messages")


@app.command()
def message(
    friend_id: str = typer.Argument(...),
    text: str = typer.Option(
        ..., "--text", help="Message body to send via the friend's primary channel."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Send an outbound message to a friend (consequential — needs --yes)."""
    invoke.run(
        "POST",
        f"/api/friends/{friend_id}/messages",
        body={"text": text},
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )

"""contacts: address-book CRUD (/api/contacts).

Placing a call is not a single REST endpoint (it is a brain tool that composes a
contact's number with telephony); use `jarvis telephony outbound` with a number,
or the running assistant, to place a call.
"""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(no_args_is_help=True, help="Contacts: list, show, add, edit, delete.")


@app.command("list")
def list_contacts() -> None:
    """List contacts."""
    invoke.run("GET", "/api/contacts")


@app.command()
def show(slug: str = typer.Argument(...)) -> None:
    """Show one contact."""
    invoke.run("GET", f"/api/contacts/{slug}")


def _body_from(json_body: str) -> dict:
    raw = sys.stdin.read() if json_body == "-" else json_body
    try:
        return json.loads(raw)
    except ValueError as exc:
        render.error(f"--json-body is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def add(
    json_body: str = typer.Option(
        ..., "--json-body", help="Contact JSON ('-' reads stdin): {name, emails?, phones?, ...}."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Add a contact."""
    invoke.run("POST", "/api/contacts", body=_body_from(json_body), assume_yes=yes, dry_run=dry_run)


@app.command()
def edit(
    slug: str = typer.Argument(...),
    json_body: str = typer.Option(
        ..., "--json-body", help="Partial contact JSON ('-' reads stdin)."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Edit a contact (partial)."""
    invoke.run(
        "PATCH",
        f"/api/contacts/{slug}",
        body=_body_from(json_body),
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def delete(
    slug: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete a contact."""
    invoke.run("DELETE", f"/api/contacts/{slug}", assume_yes=yes, dry_run=dry_run)

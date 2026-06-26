"""socials: project social-media links CRUD (/api/socials).

A pure file store (no Brain dependency) — the links surfaced in the desktop
"Socials" section.
"""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(no_args_is_help=True, help="Socials: list, add, edit, delete.")


def _body_from(json_body: str) -> dict:
    raw = sys.stdin.read() if json_body == "-" else json_body
    try:
        return json.loads(raw)
    except ValueError as exc:
        render.error(f"--json-body is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc


@app.command("list")
def list_socials() -> None:
    """List social links."""
    invoke.run("GET", "/api/socials")


@app.command()
def add(
    json_body: str = typer.Option(
        ..., "--json-body",
        help="Social-link JSON ('-' reads stdin): {platform, label, url, enabled?, order?}.",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Add a social link."""
    invoke.run("POST", "/api/socials", body=_body_from(json_body), assume_yes=yes, dry_run=dry_run)


@app.command()
def edit(
    social_id: str = typer.Argument(..., metavar="ID"),
    json_body: str = typer.Option(
        ..., "--json-body", help="Partial social-link JSON ('-' reads stdin)."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Edit a social link (partial)."""
    invoke.run(
        "PATCH",
        f"/api/socials/{social_id}",
        body=_body_from(json_body),
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def delete(
    social_id: str = typer.Argument(..., metavar="ID"),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete a social link."""
    invoke.run("DELETE", f"/api/socials/{social_id}", assume_yes=yes, dry_run=dry_run)

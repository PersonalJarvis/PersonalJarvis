"""contacts: address-book CRUD + vCard import/export (/api/contacts).

``add``/``edit`` take friendly flags (--name/--email/--phone/…) for humans and
scripts alike; ``--json-body`` remains for full-record round-trips. Placing a
call is not a single REST endpoint (it is a brain tool that composes a
contact's number with telephony); use `jarvis telephony outbound` with a
number, or the running assistant, to place a call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from jarvis.cli_ctl import invoke, options, render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(
    no_args_is_help=True,
    help="Contacts: list, show, add, edit, delete, import/export vCard.",
)


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


def _body_from_flags(
    *,
    name: str | None,
    alias: list[str] | None,
    relationship: str | None,
    email: list[str] | None,
    phone: list[str] | None,
    url: list[str] | None,
    tag: list[str] | None,
    organization: str | None,
    role: str | None,
    birthday: str | None,
    note: str | None,
    favorite: bool | None,
) -> dict:
    """Collect only the flags that were actually given (PATCH-friendly)."""
    body: dict = {}
    if name is not None:
        body["name"] = name
    if alias:
        body["aliases"] = list(alias)
    if relationship is not None:
        body["relationship"] = relationship
    if email:
        body["emails"] = list(email)
    if phone:
        body["phones"] = list(phone)
    if url:
        body["urls"] = list(url)
    if tag:
        body["tags"] = list(tag)
    if organization is not None:
        body["organization"] = organization
    if role is not None:
        body["role"] = role
    if birthday is not None:
        body["birthday"] = birthday
    if note is not None:
        body["note"] = note
    if favorite is not None:
        body["favorite"] = favorite
    return body


@app.command()
def add(
    name: str | None = typer.Option(None, "--name", help="Contact name."),
    alias: Annotated[
        list[str] | None, typer.Option("--alias", help="Alias (repeatable).")
    ] = None,
    relationship: str | None = typer.Option(
        None,
        "--relationship",
        help="family|friend|colleague|partner|acquaintance|other",
    ),
    email: Annotated[
        list[str] | None, typer.Option("--email", help="E-mail (repeatable).")
    ] = None,
    phone: Annotated[
        list[str] | None, typer.Option("--phone", help="Phone (repeatable).")
    ] = None,
    url: Annotated[
        list[str] | None, typer.Option("--url", help="Web link (repeatable).")
    ] = None,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", help="Free-form tag (repeatable).")
    ] = None,
    organization: str | None = typer.Option(None, "--organization", help="Company."),
    role: str | None = typer.Option(None, "--role", help="Job title / role."),
    birthday: str | None = typer.Option(
        None, "--birthday", help="ISO date, e.g. 1990-04-12."
    ),
    note: str | None = typer.Option(None, "--note", help="Markdown README."),
    favorite: bool = typer.Option(False, "--favorite", help="Pin as favorite."),
    json_body: str | None = typer.Option(
        None,
        "--json-body",
        help="Raw contact JSON ('-' reads stdin); overrides all field flags.",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Add a contact (field flags, or --json-body for the full record)."""
    if json_body is not None:
        body = _body_from(json_body)
    else:
        body = _body_from_flags(
            name=name,
            alias=alias,
            relationship=relationship,
            email=email,
            phone=phone,
            url=url,
            tag=tag,
            organization=organization,
            role=role,
            birthday=birthday,
            note=note,
            favorite=favorite or None,
        )
        if "name" not in body:
            render.error("--name is required (or pass --json-body).")
            raise typer.Exit(code=2)
    invoke.run("POST", "/api/contacts", body=body, assume_yes=yes, dry_run=dry_run)


@app.command()
def edit(
    slug: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name"),
    alias: Annotated[
        list[str] | None,
        typer.Option("--alias", help="Replaces the alias list (repeatable)."),
    ] = None,
    relationship: str | None = typer.Option(None, "--relationship"),
    email: Annotated[
        list[str] | None,
        typer.Option("--email", help="Replaces the e-mail list (repeatable)."),
    ] = None,
    phone: Annotated[
        list[str] | None,
        typer.Option("--phone", help="Replaces the phone list (repeatable)."),
    ] = None,
    url: Annotated[
        list[str] | None, typer.Option("--url", help="Replaces the URL list.")
    ] = None,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", help="Replaces the tag list.")
    ] = None,
    organization: str | None = typer.Option(None, "--organization"),
    role: str | None = typer.Option(None, "--role"),
    birthday: str | None = typer.Option(None, "--birthday", help="ISO date."),
    note: str | None = typer.Option(None, "--note"),
    favorite: bool | None = typer.Option(
        None, "--favorite/--no-favorite", help="Pin/unpin as favorite."
    ),
    json_body: str | None = typer.Option(
        None, "--json-body", help="Partial contact JSON ('-' reads stdin)."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Edit a contact — only the given flags change (partial PATCH)."""
    if json_body is not None:
        body = _body_from(json_body)
    else:
        body = _body_from_flags(
            name=name,
            alias=alias,
            relationship=relationship,
            email=email,
            phone=phone,
            url=url,
            tag=tag,
            organization=organization,
            role=role,
            birthday=birthday,
            note=note,
            favorite=favorite,
        )
        if not body:
            render.error("Nothing to change — pass at least one field flag.")
            raise typer.Exit(code=2)
    invoke.run(
        "PATCH",
        f"/api/contacts/{slug}",
        body=body,
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


@app.command("import")
def import_vcf(
    file: str = typer.Argument(..., help="Path to a .vcf file."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Import contacts from a vCard file (merges, never clobbers)."""
    path = Path(file)
    if not path.is_file():
        render.error(f"Not a readable file: {file}")
        raise typer.Exit(code=2)
    text = path.read_text(encoding="utf-8", errors="replace")
    invoke.run(
        "POST",
        "/api/contacts/import",
        body={"vcf": text},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def export(
    out: str | None = typer.Option(
        None, "--out", help="Write the .vcf to this file instead of stdout."
    ),
) -> None:
    """Export all contacts as vCard 3.0 (.vcf)."""
    if out is None:
        invoke.run("GET", "/api/contacts/export")
        return
    # --out: fetch without invoke.run so the whole book is not ALSO printed.
    from jarvis.cli_ctl.__main__ import as_json, make_client

    client = make_client()
    try:
        try:
            text = client.request("GET", "/api/contacts/export")
        except ApiError as exc:
            render.error(exc.message)
            raise typer.Exit(code=1) from exc
        Path(out).write_text(str(text), encoding="utf-8")
        render.emit({"ok": True, "wrote": out}, as_json=as_json())
    finally:
        client.close()

"""marketplace: browse, install, connect, disconnect marketplace entries.

``install`` is the command a downloader copies straight off a marketplace page,
so it is also the one place where "did anything actually happen?" must never be
a guess. It therefore narrates in plain words for a person at a terminal — what
the entry is, where it landed, whether it can be used right now, and what is
still missing — while any pipe, script, or agent (``--json`` or a non-TTY
stdout) keeps getting the untouched API payload.
"""

from __future__ import annotations

from typing import Any

import typer

from jarvis.cli_ctl import invoke, options, render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(
    no_args_is_help=True,
    help="Marketplace: browse, install, connect, disconnect.",
)


# ----------------------------------------------------------------------
# Shared plumbing
# ----------------------------------------------------------------------


def _fail(exc: ApiError) -> typer.Exit:
    """Turn an API failure into one plain sentence + a non-zero exit."""
    if exc.status_code is None:
        # Transport failure: the cause-specific diagnosis beats "connection
        # refused" for someone who just wants to know why nothing happened.
        from jarvis.cli_ctl import doctor

        render.error(doctor.unreachable_message(exc.base_url))
    else:
        detail = exc.payload if isinstance(exc.payload, str) else exc.message
        render.error(detail)
    return typer.Exit(code=1)


def _find_entry(index: dict[str, Any], item_id: str) -> tuple[str, dict[str, Any]] | None:
    """Locate ``item_id`` in a community index payload as ("skill"|"plugin", entry)."""
    for skill in index.get("skills") or []:
        if skill.get("name") == item_id:
            return "skill", skill
    for plugin in index.get("plugins") or []:
        if plugin.get("name") == item_id:
            return "plugin", plugin
    return None


def _suggest(index: dict[str, Any], item_id: str) -> str:
    """Closest existing name, phrased as a hint — or an empty string."""
    import difflib

    names = [s.get("name", "") for s in index.get("skills") or []]
    names += [p.get("name", "") for p in index.get("plugins") or []]
    close = difflib.get_close_matches(item_id, [n for n in names if n], n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


_field = render.field


# ----------------------------------------------------------------------
# install
# ----------------------------------------------------------------------


_SKILL_BLURB = (
    "A written instruction sheet for the assistant. It adds no server and no "
    "login of its own."
)
_PLUGIN_BLURB = (
    "A connector to an outside service. Installing it only puts it on your "
    "list — it stays powerless until you connect your account."
)


def _preview(kind: str, entry: dict[str, Any], item_id: str) -> None:
    """Show WHAT is about to be installed, before a single byte is fetched."""
    title = entry.get("title") or entry.get("display_name") or item_id
    parts = [kind]
    if entry.get("version"):
        parts.append(f"v{entry['version']}")
    if entry.get("publisher"):
        parts.append(f"by {entry['publisher']}")
    render.line()
    render.line(f"[bold]{title}[/bold]  ·  {'  ·  '.join(parts)}")
    render.line()
    _field("What", _SKILL_BLURB if kind == "skill" else _PLUGIN_BLURB)
    if entry.get("description"):
        _field("Does", str(entry["description"]))
    if entry.get("source_url"):
        _field("From", str(entry["source_url"]))
    _field("Goes to", "your skills" if kind == "skill" else "your plugin list")
    render.line()


def _report_installed(payload: dict[str, Any]) -> int:
    """Print the after-the-fact status. Returns the process exit code."""
    kind = str(payload.get("kind") or "")
    item_id = str(payload.get("id") or "")
    title = str(payload.get("title") or item_id)
    render.line()
    if payload.get("ready"):
        render.line(f"[green]Installed:[/green] [bold]{title}[/bold]")
    elif payload.get("next_action") == "connect":
        render.line(f"[green]Installed:[/green] [bold]{title}[/bold]")
    else:
        render.line(f"[yellow]Installed, but not usable:[/yellow] [bold]{title}[/bold]")
    render.line()
    _field("Kind", kind or "unknown")
    if payload.get("location"):
        _field("File", str(payload["location"]))

    if payload.get("ready"):
        _field(
            "Status",
            "ready to use — Jarvis picked it up already, no restart needed",
        )
        _field("Check", f"jarvis skills show {item_id}")
        _field("Turn off", f"jarvis skills disable {item_id}")
        render.line()
        return 0

    if payload.get("next_action") == "connect":
        _field(
            "Status",
            "on your list, but NOT connected — it cannot do anything yet",
        )
        _field("Next", f"jarvis marketplace connect-start {item_id}")
        _field("Or", "open the app → Plugins → Community and press Connect")
        render.line()
        return 0

    _field("Status", f"{payload.get('state') or 'unknown'} — Jarvis will not use it")
    if payload.get("problem"):
        _field("Problem", str(payload["problem"]))
    _field("Look", f"jarvis skills show {item_id}")
    render.line()
    return 1


def _report_already_there(kind: str, item_id: str, client: Any) -> int:
    """Nothing to install — say what is already there and how it stands."""
    render.line()
    render.line(f"[green]Already installed:[/green] [bold]{item_id}[/bold]")
    render.line()
    _field("Kind", kind)
    if kind == "skill":
        try:
            detail = client.request("GET", f"/api/skills/{item_id}")
        except ApiError:
            detail = {}
        state = str(detail.get("state") or "unknown")
        ready = state in ("active", "validated")
        _field(
            "Status",
            "ready to use" if ready else f"{state} — Jarvis will not use it",
        )
        if detail.get("error"):
            _field("Problem", str(detail["error"]))
        _field("Look", f"jarvis skills show {item_id}")
        render.line()
        return 0 if ready else 1
    status = "unknown"
    try:
        listing = client.request("GET", "/api/marketplace/plugins")
        for plugin in listing.get("plugins", []) if isinstance(listing, dict) else []:
            if plugin.get("id") == item_id:
                status = str(plugin.get("status") or "unknown")
                break
    except ApiError:
        pass
    if status == "connected":
        _field("Status", "connected and usable")
    else:
        _field("Status", f"{status} — connect it before it can do anything")
        _field("Next", f"jarvis marketplace connect-start {item_id}")
    render.line()
    return 0


@app.command()
def install(
    item_id: str = typer.Argument(..., help="Marketplace name, e.g. three-bullet-brief."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Install a marketplace entry by name — skill or plugin — and report the result.

    At a terminal this shows what the entry is and asks once before installing,
    then states plainly whether the thing is usable now or still needs a step.
    Piped or with ``--json`` it sends the install straight through and prints
    the API payload. Exit code 1 means "not usable": a skill that failed
    validation, an unknown name, or a refused install.
    """
    from jarvis.cli_ctl import safety
    from jarvis.cli_ctl.__main__ import as_json, make_client

    json_out = as_json()
    human = render.is_human() and not json_out
    path = f"/api/marketplace/community/install/{item_id}"

    if dry_run:
        safety.gate_request("POST", path, assume_yes=yes, dry_run=True, as_json=json_out)
        return  # dry run: preview already printed, nothing sent

    client = make_client()
    try:
        if human:
            # Resolve the name FIRST so an unknown entry (or one that is
            # already there) is answered without touching the install path.
            try:
                index = client.request("GET", "/api/marketplace/community")
            except ApiError as exc:
                raise _fail(exc) from exc
            found = _find_entry(index if isinstance(index, dict) else {}, item_id)
            if found is None:
                render.error(
                    f"{item_id!r} is not in the marketplace."
                    f"{_suggest(index if isinstance(index, dict) else {}, item_id)} "
                    "Run `jarvis marketplace browse` to see everything on offer."
                )
                raise typer.Exit(code=1)
            kind, entry = found
            if entry.get("installed"):
                raise typer.Exit(code=_report_already_there(kind, item_id, client))
            _preview(kind, entry, item_id)
            if not yes and not typer.confirm("Install it?", default=False):
                render.line("Cancelled — nothing was installed.")
                raise typer.Exit(code=1)
            render.line("Installing …")

        # Non-destructive today, so the gate waves it through — but routing it
        # through the same gate every other mutation uses means a later risk
        # reclassification of this path applies here without a code change.
        if not safety.gate_request(
            "POST", path, assume_yes=yes or human, as_json=json_out
        ):
            return
        try:
            result = client.request("POST", path)
        except ApiError as exc:
            raise _fail(exc) from exc

        if not human:
            render.emit(result, as_json=json_out)
            return
        raise typer.Exit(code=_report_installed(result if isinstance(result, dict) else {}))
    finally:
        client.close()


@app.command()
def browse() -> None:
    """Show everything the community marketplace offers, and what you already have."""
    from jarvis.cli_ctl.__main__ import as_json, make_client

    json_out = as_json()
    human = render.is_human() and not json_out
    client = make_client()
    try:
        try:
            index = client.request("GET", "/api/marketplace/community")
        except ApiError as exc:
            raise _fail(exc) from exc
        if not human:
            render.emit(index, as_json=json_out)
            return
        payload = index if isinstance(index, dict) else {}
        for kind, key in (("Skills", "skills"), ("Plugins", "plugins")):
            entries = payload.get(key) or []
            render.line()
            render.line(f"[bold]{kind}[/bold] ({len(entries)})")
            if not entries:
                render.line("  nothing published yet")
                continue
            for entry in entries:
                name = entry.get("name", "?")
                mark = "[green]installed[/green]" if entry.get("installed") else "available"
                render.line(f"  {name:<28} {mark}")
        render.line()
        render.line("Install one with: jarvis marketplace install <name>")
        render.line()
    finally:
        client.close()


# ----------------------------------------------------------------------
# Connect / disconnect
# ----------------------------------------------------------------------


@app.command("list")
def list_plugins() -> None:
    """List marketplace plugins + their connection status."""
    invoke.run("GET", "/api/marketplace/plugins")


@app.command("connect-pat")
def connect_pat(
    plugin_id: str = typer.Argument(...),
    token: str = typer.Option(
        ...,
        "--token",
        prompt="Personal access token",
        hide_input=True,
        help="PAT (read from a hidden prompt unless passed; avoid inline).",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Connect a plugin with a personal access token."""
    invoke.run(
        "POST",
        f"/api/marketplace/plugins/{plugin_id}/connect/pat",
        body={"token": token},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command("connect-start")
def connect_start(
    plugin_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Begin an OAuth connect flow (prints the redirect URI + flow id)."""
    invoke.run(
        "POST",
        f"/api/marketplace/plugins/{plugin_id}/connect/start",
        body={},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command("connect-poll")
def connect_poll(plugin_id: str = typer.Argument(...), flow_id: str = typer.Argument(...)) -> None:
    """Poll an in-progress OAuth connect flow."""
    invoke.run("GET", f"/api/marketplace/plugins/{plugin_id}/connect/poll/{flow_id}")


@app.command()
def disconnect(
    plugin_id: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Disconnect a plugin."""
    invoke.run("DELETE", f"/api/marketplace/plugins/{plugin_id}", assume_yes=yes, dry_run=dry_run)

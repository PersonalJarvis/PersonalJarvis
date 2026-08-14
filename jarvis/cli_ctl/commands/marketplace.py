"""marketplace: install, connect and disconnect marketplace plugins/skills."""

from __future__ import annotations

from typing import Any

import typer

from jarvis.cli_ctl import invoke, options, render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(
    no_args_is_help=True,
    help="Marketplace: install community plugins/skills, connect, disconnect.",
)


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


# --- The install standard ---------------------------------------------------
# `jarvis marketplace install <name>` is the CLI surface of the store's
# three-way install standard (CLI / uvx runner / assistant prompt). The
# strings shown in the store come from jarvis/marketplace/install_standard.py
# — a command added or renamed here must be reflected THERE, or the store
# starts advertising a command that does not exist.


def _fetch_community(refresh: bool) -> dict[str, Any]:
    """The community payload, fetched quietly — browsing is not the output."""
    from jarvis.cli_ctl.__main__ import make_client

    try:
        with make_client() as client:
            if refresh:
                return dict(client.request("POST", "/api/marketplace/community/refresh"))
            return dict(client.request("GET", "/api/marketplace/community"))
    except ApiError as exc:
        if exc.status_code is None:
            from jarvis.cli_ctl import doctor

            render.error(doctor.unreachable_message(exc.base_url))
        else:
            render.error(exc.message)
        raise typer.Exit(code=1) from exc


def _entry_named(payload: dict[str, Any], section: str, name: str) -> dict[str, Any] | None:
    return next(
        (e for e in payload.get(section) or [] if isinstance(e, dict) and e.get("name") == name),
        None,
    )


def _echo_trust_summary(lines: list[str]) -> None:
    """The same facts the store's consent dialog shows, BEFORE the confirm
    gate — publisher and where data would flow / what would run. Suppressed in
    --json mode, where output must stay machine-readable."""
    from jarvis.cli_ctl.__main__ import as_json

    if as_json():
        return
    for line in lines:
        typer.echo(line)


@app.command()
def install(
    name: str = typer.Argument(
        ..., help="Package name exactly as listed in the community marketplace."
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-fetch the community index before resolving."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Install a community plugin or skill by name.

    Resolves the name against the community index (plugin first, then skill)
    and runs the matching install route. Community content is UNREVIEWED, so
    without --yes this prints what the entry would be allowed to do and asks.
    """
    payload = _fetch_community(refresh)
    plugin = _entry_named(payload, "plugins", name)
    skill = _entry_named(payload, "skills", name)

    if plugin is not None:
        if not plugin.get("valid"):
            render.error(f"{name!r} is listed but not installable: {plugin.get('error')}")
            raise typer.Exit(code=1)
        if plugin.get("seed_conflict"):
            render.error(f"{name!r} collides with a built-in plugin id and cannot be installed.")
            raise typer.Exit(code=1)
        lines = [
            f"Community plugin {name!r} by {plugin.get('publisher') or 'an unknown publisher'}"
            f"{' · v' + str(plugin['version']) if plugin.get('version') else ''} — not reviewed."
        ]
        mcp = plugin.get("mcp_server") or {}
        if mcp.get("transport") == "http" and mcp.get("url"):
            lines.append(f"Once connected, requests and your token go to: {mcp['url']}")
        elif mcp.get("transport") == "stdio":
            lines.append(
                "Once connected, this command runs locally: " + " ".join(mcp.get("install") or [])
            )
        if skill is not None:
            lines.append(f"(A community skill is also named {name!r}; the plugin wins.)")
        _echo_trust_summary(lines)
        invoke.run(
            "POST",
            f"/api/marketplace/community/plugins/{name}/install",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return

    if skill is not None:
        raw_url = skill.get("raw_url")
        if not raw_url:
            render.error(
                f"skill {name!r} has no direct download — open its source and "
                f"follow its steps: {skill.get('source_url') or 'no source URL published'}"
            )
            raise typer.Exit(code=1)
        _echo_trust_summary(
            [
                f"Community skill {name!r} by {skill.get('publisher') or 'an unknown publisher'}"
                f"{' · v' + str(skill['version']) if skill.get('version') else ''} — not reviewed.",
                f"The instructions are downloaded from: {raw_url}",
            ]
        )
        invoke.run(
            "POST",
            "/api/skills/catalog/install",
            body={
                "name": name,
                "title": skill.get("title") or name,
                "raw_url": raw_url,
                "source_url": skill.get("source_url") or "",
            },
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return

    render.error(
        f"{name!r} is not in the community index. Try --refresh; built-in "
        "connectors are listed by `jarvis marketplace list` and are connected, "
        "not installed."
    )
    raise typer.Exit(code=1)


@app.command()
def uninstall(
    name: str = typer.Argument(..., help="Name of the installed community plugin or skill."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Remove an installed community plugin or skill by name.

    Mirrors the store's Remove buttons: a plugin uninstall also drops its
    stored tokens, usage card and bundled skills; a skill uninstall deletes
    the skill folder.
    """
    payload = _fetch_community(False)
    plugin = _entry_named(payload, "plugins", name)
    skill = _entry_named(payload, "skills", name)

    if plugin is not None and plugin.get("installed"):
        invoke.run(
            "DELETE",
            f"/api/marketplace/community/plugins/{name}",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return
    if skill is not None and skill.get("installed"):
        invoke.run(
            "DELETE",
            f"/api/skills/{name}",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return
    render.error(
        f"{name!r} is not an installed community plugin or skill. Installed "
        "items are marked in `jarvis api marketplace community-browse` or the "
        "Plugins view; built-in connectors are disconnected, not uninstalled."
    )
    raise typer.Exit(code=1)

"""marketplace: install, connect and disconnect marketplace plugins/skills."""

from __future__ import annotations

from typing import Any, NoReturn

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


def _exit_on_api_error(exc: ApiError) -> NoReturn:
    if exc.status_code is None:
        from jarvis.cli_ctl import doctor

        render.error(doctor.unreachable_message(exc.base_url))
    else:
        render.error(exc.message)
    raise typer.Exit(code=1) from exc


def _quiet_get(path: str) -> dict[str, Any]:
    """Fetch a payload without rendering it — resolution, not output."""
    from jarvis.cli_ctl.__main__ import make_client

    try:
        with make_client() as client:
            return dict(client.request("GET", path))
    except ApiError as exc:
        _exit_on_api_error(exc)


def _fetch_community(refresh: bool, *, required: bool = True) -> dict[str, Any] | None:
    """The community payload, fetched quietly — browsing is not the output.

    ``required=False`` returns ``None`` on failure instead of exiting, for
    callers that only decorate their output with index knowledge.
    """
    from jarvis.cli_ctl.__main__ import make_client

    try:
        with make_client() as client:
            if refresh:
                return dict(client.request("POST", "/api/marketplace/community/refresh"))
            return dict(client.request("GET", "/api/marketplace/community"))
    except ApiError as exc:
        if not required:
            return None
        _exit_on_api_error(exc)


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
    """Install a community plugin, skill or wallpaper by name.

    Resolves the name against the community index (plugin first, then skill,
    then wallpaper) and runs the matching install route. Everything here is
    UNREVIEWED, so for plugins and skills without --yes this prints what the
    entry would be allowed to do and asks. Wallpapers skip that prompt for a
    different reason than trust: an import is an image re-encoded by the app —
    no code, no credentials, nothing that can act.
    """
    payload = _fetch_community(refresh) or {}
    plugin = _entry_named(payload, "plugins", name)
    skill = _entry_named(payload, "skills", name)
    wallpaper = _entry_named(payload, "wallpapers", name)

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
        # The SAME two-bullet disclosure the store's consent dialog shows —
        # one surface must not read softer than the other.
        mcp = plugin.get("mcp_server") or {}
        if mcp.get("transport") == "http" and mcp.get("url"):
            lines.append(f"Once connected, requests and your token go to: {mcp['url']}")
            lines.append("Runs no code on your computer; the tools live on that server.")
        elif mcp.get("transport") == "stdio":
            lines.append(
                "Once connected, this command runs locally: " + " ".join(mcp.get("install") or [])
            )
            lines.append(
                "It can read and change files on your computer and connect to the internet."
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
        if not skill.get("installable"):
            render.error(
                f"skill {name!r} is listed but carries no content — the registry "
                "published an incomplete entry. Open its source and follow its "
                f"steps: {skill.get('source_url') or 'no source URL published'}"
            )
            raise typer.Exit(code=1)
        # The name is the ONLY input; the route reads the index server-side.
        # `/api/skills/catalog/install` takes the download URL from the caller,
        # which would make "install this skill" mean "write whatever this URL
        # serves" — see community_skill_install for why that route is not the
        # marketplace's. It also skips the store's install counter.
        origin = (
            "The instructions come from the registry index itself — nothing is downloaded."
            if skill.get("embedded")
            else f"The instructions are downloaded from: {skill.get('raw_url')}"
        )
        _echo_trust_summary(
            [
                f"Community skill {name!r} by {skill.get('publisher') or 'an unknown publisher'}"
                f"{' · v' + str(skill['version']) if skill.get('version') else ''} — not reviewed.",
                origin,
            ]
        )
        invoke.run(
            "POST",
            f"/api/marketplace/community/skills/{name}/install",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return

    if wallpaper is not None:
        if not wallpaper.get("installable"):
            render.error(
                f"wallpaper {name!r} is listed but carries no downloadable "
                "image — the registry published an incomplete entry."
            )
            raise typer.Exit(code=1)
        _echo_trust_summary(
            [
                f"Community wallpaper {name!r} by "
                f"{wallpaper.get('publisher') or 'an unknown publisher'} — "
                "reviewed before publication.",
                f"The image is downloaded from: {wallpaper.get('image_url')}",
                "It is re-encoded on import and lands in the wallpaper picker "
                "under “Yours” — no code runs, no credentials are involved.",
            ]
        )
        invoke.run(
            "POST",
            f"/api/marketplace/community/wallpapers/{name}/install",
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
    # Plugins resolve against the LOCAL catalog, not the community index — a
    # delisted entry or an offline index must never make an installed plugin
    # un-uninstallable (the DELETE route itself only reads local state).
    listed = _quiet_get("/api/marketplace/plugins")
    plugin = next(
        (p for p in listed.get("plugins") or [] if isinstance(p, dict) and p.get("id") == name),
        None,
    )
    if plugin is not None:
        if plugin.get("source") != "community":
            render.error(
                f"{name!r} is a built-in connector — disconnect it instead: "
                f"`jarvis marketplace disconnect {name}`."
            )
            raise typer.Exit(code=1)
        payload = _fetch_community(False, required=False) or {}
        skill = _entry_named(payload, "skills", name)
        if skill is not None and skill.get("installed"):
            _echo_trust_summary(
                [
                    f"(An installed community skill is also named {name!r} — "
                    "run this command again after the plugin is removed.)"
                ]
            )
        invoke.run(
            "DELETE",
            f"/api/marketplace/community/plugins/{name}",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return

    # Skills carry no local provenance marker, so the community index decides
    # whether this name is a marketplace skill — the store's Remove button
    # applies the same rule.
    payload = _fetch_community(False) or {}
    skill = _entry_named(payload, "skills", name)
    if skill is not None and skill.get("installed"):
        invoke.run(
            "DELETE",
            f"/api/skills/{name}",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return

    # Wallpapers DO carry a provenance marker: the import stamped the local
    # copy with its community name, and the payload surfaced that copy's id.
    wallpaper = _entry_named(payload, "wallpapers", name)
    if wallpaper is not None and wallpaper.get("installed") and wallpaper.get("installed_id"):
        invoke.run(
            "DELETE",
            f"/api/wallpapers/uploads/{wallpaper['installed_id']}",
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )
        return

    render.error(
        f"{name!r} is not an installed community plugin, skill or wallpaper. "
        "Installed items are marked in the Plugins view; built-in connectors "
        "are disconnected, not uninstalled."
    )
    raise typer.Exit(code=1)

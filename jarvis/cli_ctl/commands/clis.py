"""clis: external-CLI capability management (/api/clis).

The CLIs Jarvis can drive (gcloud, gh, ...): list/show their status, probe a
binary, install, connect/disconnect auth, and read usage. Spawn-external,
custom-CLI registration, and the natural-language test hub stay reachable via
`jarvis api clis ...`.
"""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(
    no_args_is_help=True,
    help="CLIs: list, show, check, install, connect, disconnect, usage.",
)


def _body_from(json_body: str) -> dict:
    raw = sys.stdin.read() if json_body == "-" else json_body
    try:
        return json.loads(raw)
    except ValueError as exc:
        render.error(f"--json-body is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc


@app.command("list")
def list_clis() -> None:
    """List all CLIs with status (connected, installed, version, 7-day usage)."""
    invoke.run("GET", "/api/clis")


@app.command()
def show(name: str = typer.Argument(...)) -> None:
    """Show one CLI (homepage, install methods, auth mode, secrets set)."""
    invoke.run("GET", f"/api/clis/{name}")


@app.command()
def check(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Probe a CLI's binary + auth (refreshes its status)."""
    invoke.run("POST", f"/api/clis/{name}/check", assume_yes=yes, dry_run=dry_run)


@app.command()
def install(
    name: str = typer.Argument(...),
    method: str = typer.Option(
        ..., "--method", help="Install-method id (see `clis show <name>`)."
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Start an install job for a CLI (output streams in the desktop view)."""
    invoke.run(
        "POST",
        f"/api/clis/{name}/install",
        body={"method": method},
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def connect(
    name: str = typer.Argument(...),
    json_body: str = typer.Option(
        ..., "--json-body",
        help="Auth payload JSON ('-' reads stdin; use stdin for api_key secrets).",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Connect a CLI's auth (oauth_cli flow or api_key)."""
    invoke.run(
        "POST",
        f"/api/clis/{name}/connect",
        body=_body_from(json_body),
        assume_yes=yes,
        dry_run=dry_run,
    )


@app.command()
def disconnect(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Remove a CLI's stored auth credentials."""
    invoke.run("POST", f"/api/clis/{name}/disconnect", assume_yes=yes, dry_run=dry_run)


@app.command()
def usage(name: str = typer.Argument(...)) -> None:
    """Show a CLI's recent usage history."""
    invoke.run("GET", f"/api/clis/{name}/usage")


@app.command("usage-stats")
def usage_stats(name: str = typer.Argument(...)) -> None:
    """Show a CLI's aggregated usage stats (success rate, avg duration, top commands)."""
    invoke.run("GET", f"/api/clis/{name}/usage/stats")

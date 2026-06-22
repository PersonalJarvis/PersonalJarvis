"""mcps: manage MCP servers (/api/mcps)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="MCP servers: list, enable, disable, check, delete.")


@app.command("list")
def list_servers() -> None:
    """List MCP servers + a summary."""
    invoke.run("GET", "/api/mcps")


@app.command()
def enable(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Enable an MCP server."""
    invoke.run("POST", f"/api/mcps/{name}/enable", assume_yes=yes, dry_run=dry_run)


@app.command()
def disable(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Disable an MCP server."""
    invoke.run("POST", f"/api/mcps/{name}/disable", assume_yes=yes, dry_run=dry_run)


@app.command()
def check(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Health-check an MCP server (lists its tools)."""
    invoke.run("POST", f"/api/mcps/{name}/check", assume_yes=yes, dry_run=dry_run)


@app.command("import-claude-desktop")
def import_claude_desktop(yes: bool = options.yes_opt(), dry_run: bool = options.dry_opt()) -> None:
    """Import MCP servers from the Claude Desktop config."""
    invoke.run("POST", "/api/mcps/import-claude-desktop", assume_yes=yes, dry_run=dry_run)


@app.command()
def delete(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Remove an MCP server."""
    invoke.run("DELETE", f"/api/mcps/{name}", assume_yes=yes, dry_run=dry_run)

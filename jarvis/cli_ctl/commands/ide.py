"""ide: control Agentic IDE workspaces and terminal panes."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(no_args_is_help=True, help="Control Agentic IDE terminal panes.")


@app.command("rename-terminal")
def rename_terminal(
    name: Annotated[str, typer.Argument(help="Current terminal call-sign.")],
    new_name: Annotated[str, typer.Argument(help="New terminal call-sign.")],
    dry_run: bool = options.dry_opt(),
) -> None:
    """Rename a running terminal pane without restarting its agent."""
    invoke.run(
        "PATCH",
        f"/api/agentic-ide/terminals/{quote(name, safe='')}",
        body={"name": new_name},
        dry_run=dry_run,
        dangerous=False,
    )


@app.command("archive-terminal")
def archive_terminal(
    name: Annotated[str, typer.Argument(help="Terminal call-sign.")],
    restore: bool = typer.Option(
        False, "--restore", help="Show the chat in the session list again."
    ),
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", help="Workspace id when several tabs are open."),
    ] = None,
    dry_run: bool = options.dry_opt(),
) -> None:
    """Hide a coding session from the chat list, or restore it.

    The terminal keeps running. Closing it is `ide close-terminals`.
    """
    body: dict[str, object] = {"archived": not restore}
    if workspace:
        body["workspace_id"] = workspace
    invoke.run(
        "POST",
        f"/api/agentic-ide/terminals/{quote(name, safe='')}/archive",
        body=body,
        dry_run=dry_run,
        dangerous=False,
    )


@app.command("move-terminal")
def move_terminal(
    name: Annotated[str, typer.Argument(help="Terminal call-sign.")],
    target_workspace: Annotated[
        str, typer.Argument(help="Id of the open workspace to move the terminal into.")
    ],
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", help="Workspace id the terminal is in now."),
    ] = None,
    dry_run: bool = options.dry_opt(),
) -> None:
    """Move a terminal pane into another open workspace.

    Its agent keeps running. The pane may get another call-sign when the
    target workspace already has one by that name; the answer says which.
    """
    body: dict[str, object] = {"target_workspace_id": target_workspace}
    if workspace:
        body["workspace_id"] = workspace
    invoke.run(
        "POST",
        f"/api/agentic-ide/terminals/{quote(name, safe='')}/transfer",
        body=body,
        dry_run=dry_run,
        dangerous=False,
    )


@app.command("close-terminals")
def close_terminals(
    names: Annotated[list[str], typer.Argument(help="Terminal call-signs to close.")],
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Stop several coding agents and close their terminal panes."""
    result = invoke.run(
        "POST",
        "/api/agentic-ide/terminals/close-batch",
        body={"names": names},
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )
    if isinstance(result, dict) and result.get("ok") is False:
        raise typer.Exit(code=1)

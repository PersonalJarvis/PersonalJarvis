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

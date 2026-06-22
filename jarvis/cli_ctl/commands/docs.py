"""docs: browse the in-app documentation (/api/docs)."""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke

app = typer.Typer(no_args_is_help=True, help="Documentation: list, tree, search, show.")


@app.command("list")
def list_docs() -> None:
    """List documentation pages."""
    invoke.run("GET", "/api/docs")


@app.command()
def tree() -> None:
    """Show the Diataxis-grouped doc tree."""
    invoke.run("GET", "/api/docs/grouped")


@app.command()
def search(query: str = typer.Argument(...)) -> None:
    """Search the docs."""
    invoke.run("GET", "/api/docs/search", params={"q": query})


@app.command()
def show(slug: str = typer.Argument(...)) -> None:
    """Show one doc page's body."""
    invoke.run("GET", f"/api/docs/{slug}")

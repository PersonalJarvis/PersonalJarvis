"""wiki: search and read the knowledge wiki (/api/wiki).

Note: deterministic save-fact (wiki-ingest) is a brain-tool, not a REST
endpoint, so it has no curated command here — use the running assistant for
ingestion. Search and read are exposed.
"""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke

app = typer.Typer(no_args_is_help=True, help="Knowledge wiki: recall, page, tree.")


@app.command()
def recall(query: str = typer.Argument(..., help="Search query.")) -> None:
    """Full-text search the wiki."""
    invoke.run("GET", "/api/wiki/search", params={"q": query})


@app.command()
def page(slug: str = typer.Argument(..., help="Vault path / slug, e.g. people/jane.")) -> None:
    """Read a wiki page by vault path / slug."""
    invoke.run("GET", f"/api/wiki/page/{slug}")


@app.command()
def tree() -> None:
    """Show the vault folder tree + stats."""
    invoke.run("GET", "/api/wiki/tree")


@app.command()
def vaults() -> None:
    """List the user's registered Obsidian vaults (connect picker, spec A6)."""
    invoke.run("GET", "/api/setup/obsidian/vaults")

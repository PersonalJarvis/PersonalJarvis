"""wiki: search, read, and explicitly update the knowledge wiki (/api/wiki)."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(
    no_args_is_help=True,
    help="Knowledge wiki: recall, pages, health, and search-index repair.",
)


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


@app.command()
def health() -> None:
    """Show wiki subsystem health: bootstrap, last write, chain failures, backlog (spec A5)."""
    invoke.run("GET", "/api/wiki/health")


@app.command()
def ingest(
    text: str = typer.Argument(..., help="Self-contained fact or summary to store."),
    source: str = typer.Option(
        "cli:wiki-ingest",
        "--source",
        help="Short audit label for the content source.",
    ),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Store a fact through the guarded Wiki curator."""
    invoke.run(
        "POST",
        "/api/wiki/ingest",
        body={"text": text, "source": source},
        dry_run=dry_run,
        dangerous=False,
    )


@app.command()
def reindex(
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Inspect current and expected counts without rebuilding the index.",
    ),
) -> None:
    """Rebuild the wiki search index from the active vault."""
    invoke.run(
        "POST",
        "/api/wiki/reindex",
        params={"dry_run": str(preview).lower()},
    )

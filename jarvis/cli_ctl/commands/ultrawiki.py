"""ultrawiki: browse the readable knowledge base and write the Obsidian vault.

The Explore view answers "what does it actually know about my life?" — this is
the same thing without a browser, which is what an agent, a script, or a
headless server needs. `topics` and `moments` are the two readable layers of
the projection; `export` writes them to disk as Markdown.
"""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(
    no_args_is_help=True,
    help="Readable knowledge base: topics, moments, the entity graph, and the Obsidian vault.",
)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to answer from the knowledge base."),
    k: int = typer.Option(10, "--evidence", "-k", min=1, max=20),
    area: str = typer.Option("", "--area", help="Optional UltraWiki area id."),
) -> None:
    """Answer from retrieved evidence and include source citations."""
    body: dict[str, object] = {"question": question, "k": k}
    if area:
        body["area"] = area
    invoke.run(
        "POST",
        "/api/ultrawiki/ask",
        body=body,
        dangerous=False,
        request_timeout_s=90.0,
    )


@app.command()
def topics(
    search: str = typer.Option("", "--search", "-s", help="Filter topic labels."),
    limit: int = typer.Option(50, "--limit", "-n", help="How many to return."),
) -> None:
    """List what the knowledge base has a name for, most mentioned first."""
    params: dict[str, object] = {"limit": limit}
    if search:
        params["q"] = search
    invoke.run("GET", "/api/ultrawiki/explore/entities", params=params)


@app.command()
def topic(
    key: str = typer.Argument(..., help="Topic key, e.g. 'bora bora' (case-insensitive)."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many moments to include."),
) -> None:
    """Everything known about one topic, with the moments it appears in."""
    invoke.run(
        "GET", f"/api/ultrawiki/explore/entities/{key}", params={"limit": limit}
    )


@app.command()
def moments(
    topic: str = typer.Option("", "--topic", "-t", help="Restrict to one topic key."),
    month: str = typer.Option("", "--month", "-m", help="Restrict to a YYYY-MM bucket."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many to return."),
) -> None:
    """Browse the distilled moments, newest first."""
    params: dict[str, object] = {"limit": limit}
    if topic:
        params["entity"] = topic
    if month:
        params["month"] = month
    invoke.run("GET", "/api/ultrawiki/explore/moments", params=params)


@app.command()
def graph(
    min_mentions: int = typer.Option(
        2, "--min-mentions", help="Hide topics mentioned fewer times than this."
    ),
) -> None:
    """The entity graph: which topics come up together, and how often."""
    invoke.run(
        "GET", "/api/ultrawiki/explore/graph", params={"min_mentions": min_mentions}
    )


@app.command()
def vault() -> None:
    """Where the Obsidian vault is, what is in it, and whether Obsidian knows it."""
    invoke.run("GET", "/api/ultrawiki/vault/status")


@app.command()
def export(
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Write the knowledge base to the Obsidian vault as Markdown.

    Rewrites the generated folders and removes its own stale notes, so it is
    gated like any other destructive command. Notes under "My notes/" are
    never touched.
    """
    invoke.run(
        "POST",
        "/api/ultrawiki/vault/export",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )


@app.command()
def register(
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Add the vault to the Obsidian app's own index.

    Edits a file owned by another program, so it asks first. Requires the
    vault to exist — run ``export`` before this.
    """
    invoke.run(
        "POST",
        "/api/ultrawiki/vault/register",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )

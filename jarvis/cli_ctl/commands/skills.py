"""skills: inspect, author, and manage skills (/api/skills)."""

from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import invoke, options, render

app = typer.Typer(no_args_is_help=True, help="Skills: list, author, enable/disable, catalog.")


@app.command("list")
def list_skills() -> None:
    """List all discovered skills."""
    invoke.run("GET", "/api/skills")


@app.command()
def show(name: str = typer.Argument(...)) -> None:
    """Show one skill's detail."""
    invoke.run("GET", f"/api/skills/{name}")


@app.command()
def draft(
    intent: str = typer.Argument(..., help="What the skill should do."),
    name_hint: str = typer.Option(None, "--name-hint"),
    category: str = typer.Option(None, "--category"),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Generate a skill draft from an intent (AI author; lands as state=draft)."""
    body: dict[str, object] = {"intent": intent}
    if name_hint:
        body["name_hint"] = name_hint
    if category:
        body["category"] = category
    invoke.run("POST", "/api/skills/creator/draft", body=body, assume_yes=yes, dry_run=dry_run)


@app.command()
def commit(
    draft_json: str = typer.Option(..., "--draft", help="Draft JSON ('-' reads stdin)."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Commit a generated draft to disk (still state=draft until enabled)."""
    raw = sys.stdin.read() if draft_json == "-" else draft_json
    try:
        draft = json.loads(raw)
    except ValueError as exc:
        render.error(f"--draft is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    invoke.run(
        "POST", "/api/skills/creator/commit", body={"draft": draft}, assume_yes=yes, dry_run=dry_run
    )


@app.command()
def enable(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Activate a skill."""
    invoke.run("POST", f"/api/skills/{name}/enable", assume_yes=yes, dry_run=dry_run)


@app.command()
def disable(
    name: str = typer.Argument(...),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Deactivate a skill."""
    invoke.run("POST", f"/api/skills/{name}/disable", assume_yes=yes, dry_run=dry_run)


@app.command()
def reload(yes: bool = options.yes_opt(), dry_run: bool = options.dry_opt()) -> None:
    """Re-scan the skills directory."""
    invoke.run("POST", "/api/skills/reload", assume_yes=yes, dry_run=dry_run)


@app.command("catalog-search")
def catalog_search(query: str = typer.Argument(...)) -> None:
    """Search the installable skill catalog."""
    invoke.run("POST", "/api/skills/catalog/search", body={"query": query})


@app.command("catalog-install")
def catalog_install(
    name: str = typer.Argument(...),
    source_url: str = typer.Option(..., "--source-url"),
    title: str = typer.Option(..., "--title"),
    raw_url: str = typer.Option(None, "--raw-url"),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Install a skill from the catalog (lands as a draft)."""
    body: dict[str, object] = {"name": name, "source_url": source_url, "title": title}
    if raw_url:
        body["raw_url"] = raw_url
    invoke.run("POST", "/api/skills/catalog/install", body=body, assume_yes=yes, dry_run=dry_run)

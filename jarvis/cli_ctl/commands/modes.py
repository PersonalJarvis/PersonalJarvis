"""modes: switch how the assistant behaves (/api/modes).

A mode is the assistant's character — Assistant, Friend, Coach, Focus, Coding,
or one you wrote yourself. Switching applies on the next turn, in voice and in
chat, with no restart.
"""

from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke

app = typer.Typer(
    no_args_is_help=True,
    help="Assistant modes: list, show, use, create, delete, restore.",
)


@app.command("list")
def list_modes() -> None:
    """List every mode and show which one is active."""
    invoke.run("GET", "/api/modes")


@app.command()
def show(slug: str = typer.Argument(..., help="Mode id, e.g. friend")) -> None:
    """Show one mode in full, including its character text."""
    invoke.run("GET", f"/api/modes/{slug}")


@app.command()
def use(slug: str = typer.Argument(..., help="Mode id to switch to")) -> None:
    """Switch the active mode. Applies on the next turn — no restart."""
    invoke.run("PUT", "/api/modes/active", body={"slug": slug})


@app.command()
def create(
    name: str = typer.Argument(..., help="Display name, e.g. 'Night Owl'"),
    character: str = typer.Option(..., "--character", "-c", help="How it should behave"),
    slug: str = typer.Option("", "--slug", help="Mode id (derived from the name if omitted)"),
    emoji: str = typer.Option("", "--emoji", help="Shown on the mode card"),
    description: str = typer.Option("", "--description", "-d", help="One line for the card"),
    voice: str = typer.Option("", "--voice", help="TTS voice id this mode speaks in"),
    verbosity: str = typer.Option("normal", "--verbosity", help="brief | normal | rich"),
    proactivity: str = typer.Option("normal", "--proactivity", help="reactive | normal | forward"),
) -> None:
    """Create or replace a mode. Does NOT switch to it — use `modes use` for that."""
    invoke.run(
        "POST",
        "/api/modes",
        body={
            "slug": slug,
            "name": name,
            "character": character,
            "emoji": emoji,
            "description": description,
            "voice": voice,
            "verbosity": verbosity,
            "proactivity": proactivity,
        },
    )


@app.command()
def delete(slug: str = typer.Argument(..., help="Mode id to delete")) -> None:
    """Delete a mode you created. Built-ins are refused; a copy of one is not.

    Destructive and not undoable — the mode's character text is gone with it.
    Deleting the active mode falls back to the default rather than leaving the
    assistant pointing at something that is not there.
    """
    invoke.run("DELETE", f"/api/modes/{slug}", dangerous=True)


@app.command()
def restore(slug: str = typer.Argument(..., help="Built-in mode id to restore")) -> None:
    """Throw away your edits to a built-in mode and bring the shipped one back.

    Destructive in one direction only: your edited copy is deleted, and the
    packaged mode is always recoverable because it was never touched.
    """
    invoke.run("POST", f"/api/modes/{slug}/restore", dangerous=True)

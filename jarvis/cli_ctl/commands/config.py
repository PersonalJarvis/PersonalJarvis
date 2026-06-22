"""config: read/write mutable settings (self-mod) + reply language.

`config set` goes through the server's atomic config-write pipeline (allowlist ->
pre-validate -> backup -> tempfile+replace -> reload-test -> rollback -> audit),
so the CLI never edits jarvis.toml directly. It is destructive (arbitrary config
change) and therefore requires --yes.
"""
from __future__ import annotations

import json

import typer

from jarvis.cli_ctl import invoke, options

app = typer.Typer(
    no_args_is_help=True,
    help="Configuration: get/set mutable settings, list the allowlist, language.",
)
language_app = typer.Typer(no_args_is_help=True, help="Reply-language control.")
app.add_typer(language_app, name="language")


def _coerce(value: str) -> object:
    """Coerce a CLI string to its JSON-native type (true/false/numbers/null),
    falling back to the raw string. So `config set x.flag true` writes a bool,
    `config set x.n 5` writes an int, and `config set brain.primary openai`
    stays a string."""
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


@app.command()
def get(path: str = typer.Argument(..., help="Dotted config path, e.g. brain.primary.")) -> None:
    """Get a config value by dotted path (control-key gated)."""
    invoke.run("GET", "/api/control/config", params={"path": path})


@app.command("list")
def list_mutable() -> None:
    """List the mutable-settings allowlist (path, risk tier, restart needed)."""
    invoke.run("GET", "/api/control/allowlist")


@app.command("set")
def set_value(
    path: str = typer.Argument(..., help="Dotted config path, e.g. brain.primary."),
    value: str = typer.Argument(..., help="New value (JSON-coerced: true/5/\"text\")."),
    reason: str = typer.Option(None, "--reason", help="Audit note for the change."),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Set a mutable config value via the atomic write pipeline (destructive: --yes)."""
    body: dict[str, object] = {"path": path, "value": _coerce(value)}
    if reason:
        body["reason"] = reason
    invoke.run(
        "PUT", "/api/control/config", body=body,
        assume_yes=yes, dry_run=dry_run, dangerous=True,
    )


@language_app.command("get")
def language_get() -> None:
    """Show the current reply language + the available options."""
    invoke.run("GET", "/api/settings/reply-language")


@language_app.command("set")
def language_set(
    lang: str = typer.Argument(..., help="auto | de | en | es."),
    persist: bool = options.persist_opt(),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Set the reply language (hot-reloads, no restart)."""
    invoke.run(
        "PUT", "/api/settings/reply-language",
        body={"language": lang, "persist": persist},
        assume_yes=yes, dry_run=dry_run, dangerous=False,
    )

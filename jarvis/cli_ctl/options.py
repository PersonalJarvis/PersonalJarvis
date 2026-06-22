"""Reusable Typer options for curated commands.

Each helper returns a *fresh* ``typer.Option`` so every command gets its own
instance (the call runs once, at command-definition time). Keeps the safety and
persistence flags identical and self-documenting across every domain.
"""
from __future__ import annotations

import typer


def yes_opt() -> bool:
    return typer.Option(
        False, "--yes", "-y",
        help="Authorize a destructive request without a prompt.",
    )


def dry_opt() -> bool:
    return typer.Option(
        False, "--dry-run",
        help="Print the request that would be sent and exit without sending.",
    )


def persist_opt() -> bool:
    return typer.Option(
        True, "--persist/--no-persist",
        help="Persist the change to config (default) or apply live-only.",
    )

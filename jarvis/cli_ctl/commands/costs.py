"""costs: what the app spent, per provider, model and role (/api/costs).

The same read model the Spend & Tokens section renders. Handy from a terminal
or a coding agent: ``jarvis --json costs summary --days 7`` answers "what did
this week cost, and where did it go" without opening the app.

Filters take comma-separated values (``--provider grok,gemini-live``); the
route accepts that spelling as well as repeated query parameters.
"""
from __future__ import annotations

import typer

from jarvis.cli_ctl import invoke

app = typer.Typer(
    no_args_is_help=True,
    help="Spend and token accounting: totals, line items, rates.",
)

_DAYS = typer.Option(30, "--days", help="Rolling window in days; 0 = everything recorded.")
_PROVIDER = typer.Option("", "--provider", help="Comma-separated provider ids.")
_MODEL = typer.Option("", "--model", help="Comma-separated model ids.")
_ROLE = typer.Option("", "--role", help="realtime | tool | pipeline | agent | worker.")
_SURFACE = typer.Option("", "--surface", help="voice | agent-chat | mission.")
_SEARCH = typer.Option("", "--search", help="Match model, provider or label.")


def _filters(
    days: int, provider: str, model: str, role: str, surface: str, search: str
) -> dict[str, object]:
    params: dict[str, object] = {"days": days}
    for key, value in (
        ("provider", provider),
        ("model", model),
        ("role", role),
        ("surface", surface),
        ("search", search),
    ):
        if value.strip():
            params[key] = value.strip()
    return params


@app.command()
def summary(
    days: int = _DAYS,
    provider: str = _PROVIDER,
    model: str = _MODEL,
    role: str = _ROLE,
    surface: str = _SURFACE,
    search: str = _SEARCH,
) -> None:
    """Totals plus the breakdown by provider, model, role, surface and day."""
    invoke.run(
        "GET",
        "/api/costs/summary",
        params=_filters(days, provider, model, role, surface, search),
    )


@app.command()
def entries(
    days: int = _DAYS,
    provider: str = _PROVIDER,
    model: str = _MODEL,
    role: str = _ROLE,
    surface: str = _SURFACE,
    search: str = _SEARCH,
    sort: str = typer.Option("recent", "--sort", help="recent | cost | tokens."),
    limit: int = typer.Option(50, "--limit"),
    offset: int = typer.Option(0, "--offset"),
) -> None:
    """The individual model calls behind the totals."""
    params = _filters(days, provider, model, role, surface, search)
    params.update({"sort": sort, "limit": limit, "offset": offset})
    invoke.run("GET", "/api/costs/entries", params=params)


@app.command()
def rates(days: int = _DAYS) -> None:
    """The rate card per model — and which models have no published rate."""
    invoke.run("GET", "/api/costs/pricing", params={"days": days})

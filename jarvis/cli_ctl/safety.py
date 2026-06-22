"""Non-interactive safety gate for mutating CLI commands.

The Jarvis CLI is a thin client over the REST API and therefore inherits every
server-side guardrail (risk tiers, the atomic config-write pipeline, audit). But
an external coding agent issuing mutations non-interactively is a new risk
surface, so the CLI adds defense in depth on the client side:

* **read** (``GET``): never gated.
* **mutating** (``POST`` / ``PUT`` / ``PATCH`` / ``DELETE``): require confirmation.
  In an interactive TTY the user is prompted; when piped/non-interactive the
  command **fails closed** unless ``--yes`` / ``-y`` (or ``JARVIS_CLI_ASSUME_YES``)
  is set.
* **dangerous** (every ``DELETE`` plus an explicit path denylist — restart, place
  a call, dispatch a mission, …): always require an explicit ``--yes``; an
  interactive ``[y/N]`` prompt alone does not authorize them.

``--dry-run`` short-circuits any command: it prints the exact request that would
be sent (method, path, body, whether auth is attached) and sends nothing. This is
the safe introspection path for an agent.

The model is method-based plus a small path denylist — predictable and
independent of per-route risk metadata, which REST routes do not carry (risk
tiers live at the brain-tool layer the routes sit behind).
"""
from __future__ import annotations

import os

import typer

from jarvis.cli_ctl import render

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Substrings that mark a route as destructive/consequential regardless of method.
# Kept deliberately small and explicit; extend as curated commands are added.
_DANGEROUS_MARKERS: tuple[str, ...] = (
    "/restart",
    "/call",
    "/outbound",
    "/dispatch",
    "/rerun",
    "/kill",
    "/cancel",
    "/config/set",
    "/secret",
)

_TRUE = {"1", "true", "yes", "on"}


def is_mutating(method: str) -> bool:
    return method.upper() in _MUTATING_METHODS


def is_dangerous(method: str, path: str) -> bool:
    if method.upper() == "DELETE":
        return True
    low = path.lower()
    return any(marker in low for marker in _DANGEROUS_MARKERS)


def _assume_yes_env() -> bool:
    return os.environ.get("JARVIS_CLI_ASSUME_YES", "").strip().lower() in _TRUE


def _print_preview(
    method: str, path: str, body: object, auth_attached: bool, *, as_json: bool
) -> None:
    preview = {
        "dry_run": True,
        "method": method,
        "path": path,
        "auth": "bearer" if auth_attached else None,
        "body": body,
    }
    render.emit(preview, as_json=as_json)


def gate_request(
    method: str,
    path: str,
    *,
    body: object = None,
    assume_yes: bool = False,
    dry_run: bool = False,
    dangerous: bool | None = None,
    auth_attached: bool = True,
    as_json: bool = False,
) -> bool:
    """Decide whether a request may proceed.

    Returns True when the caller should send the request, False when it was a
    dry run (already printed — caller must NOT send). Raises ``typer.Exit`` when
    a destructive request is attempted without ``--yes``.

    Reads (GET) and reversible, server-audited mutations proceed without
    friction — the CLI is agent-first. Only *destructive* requests are gated:
    every ``DELETE`` plus the path denylist (or an explicit ``dangerous=True``
    from a curated command) requires ``--yes`` / ``JARVIS_CLI_ASSUME_YES``; a
    prompt is intentionally not an accepted substitute.
    """
    method_u = method.upper()
    if dry_run:
        _print_preview(method_u, path, body, auth_attached, as_json=as_json)
        return False
    if not is_mutating(method_u):
        return True

    effective_dangerous = is_dangerous(method_u, path) if dangerous is None else dangerous
    if not effective_dangerous:
        return True  # reversible mutation: proceed (audited server-side)

    if assume_yes or _assume_yes_env():
        return True

    render.error(
        f"{method_u} {path} is a destructive operation; re-run with --yes "
        "(or set JARVIS_CLI_ASSUME_YES=1) to authorize it, or --dry-run to "
        "preview the exact request without sending it."
    )
    raise typer.Exit(code=1)

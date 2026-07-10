"""Shared request path for curated commands.

Centralizes the four things every curated command must do identically: resolve
the client from the active profile, run the safety gate (confirm / --yes /
--dry-run) for mutations, map an ``ApiError`` to a clean non-zero exit, and emit
the result honoring the global ``--json`` flag. Curated command modules call
``invoke.run(...)`` instead of re-implementing this.
"""
from __future__ import annotations

from typing import Any

import typer

from jarvis.cli_ctl import render, safety
from jarvis.cli_ctl.client import ApiError


def run(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
    assume_yes: bool = False,
    dry_run: bool = False,
    dangerous: bool | None = None,
) -> None:
    """Resolve client, gate mutations, send the request, render the result.

    ``dangerous`` lets a curated command state its risk explicitly (e.g. a
    provider switch is reversible → False; ``config set`` / a phone call →
    True). When None, the method+path heuristic in ``safety.is_dangerous``
    decides — which is what the generic dynamic ``api`` layer relies on.
    """
    # Local import avoids a load-time cycle with __main__ (which imports the
    # command modules that import this helper).
    from jarvis.cli_ctl.__main__ import as_json, make_client

    json_out = as_json()
    client = make_client()
    try:
        proceed = safety.gate_request(
            method,
            path,
            body=body,
            assume_yes=assume_yes,
            dry_run=dry_run,
            dangerous=dangerous,
            auth_attached=client.has_auth,
            as_json=json_out,
        )
        if not proceed:
            return  # dry run: preview already printed, nothing sent
        try:
            out = client.request(method, path, params=params, json=body)
        except ApiError as exc:
            if exc.status_code is None:
                # Transport failure: replace the terse core message with the
                # cause-specific diagnosis (booting / crashed / not started /
                # remote target), phrased with a little variety.
                from jarvis.cli_ctl import doctor

                render.error(doctor.unreachable_message(exc.base_url))
            else:
                render.error(exc.message)
            raise typer.Exit(code=1) from exc
        render.emit(out, as_json=json_out)
    finally:
        client.close()

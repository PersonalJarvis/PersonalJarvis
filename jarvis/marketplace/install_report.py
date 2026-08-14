"""Tell the public registry that a community listing was installed.

The marketplace storefront shows an install count next to each listing, and a
count has to come from somewhere. The honest options were:

* count deliveries of a file, the way npm and PyPI do — except the registry
  index carries a skill's whole body inline, so installing one involves no
  download at all and there is nothing to count;
* have the app say so.

This is the second one. It is a weaker guarantee, and the storefront presents
the number as an approximation rather than a fact.

What leaves this machine: one POST containing the listing's name, e.g.
``{"entry": "skill:three-bullet-brief"}``. No account, no machine id, no
version, no telemetry of any kind — the receiving end stores a single integer
per listing and does not log the request. Set ``marketplace.report_installs``
to ``false`` in ``jarvis.toml`` and nothing is sent.

The call is fire-and-forget by construction: it runs after the install has
already succeeded, it cannot fail the install, and it never delays a reply.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Mirrors the key the registry accepts. Enforced here too so a malformed name
# never leaves the machine.
_ENTRY = re.compile(r"^(skill|plugin):[a-z0-9][a-z0-9_-]{0,63}$")

_TIMEOUT_SECONDS = 4.0


async def report_install(kind: str, name: str) -> None:
    """Report one install. Never raises, never blocks the caller's result."""
    entry = f"{kind}:{name}".lower()
    if not _ENTRY.match(entry):
        log.debug("install report skipped: %r is not a listing key", entry)
        return

    try:
        from jarvis.core.config import load_config

        marketplace = load_config().marketplace
    except Exception as exc:  # noqa: BLE001 - config problems must not surface here
        log.debug("install report skipped: config unavailable (%s)", exc)
        return

    if not getattr(marketplace, "report_installs", False):
        return
    url = str(getattr(marketplace, "install_report_url", "") or "").strip()
    if not url.startswith("https://"):
        log.debug("install report skipped: no https report URL configured")
        return

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json={"entry": entry})
        # 204 is the documented answer; anything else is worth one debug line
        # and nothing more — a store's counter is not worth a user-facing
        # error on a successful install.
        if response.status_code != 204:
            log.debug("install report for %s answered %s", entry, response.status_code)
    except Exception as exc:  # noqa: BLE001 - offline, DNS, TLS, timeout: all fine
        log.debug("install report for %s did not reach the registry: %s", entry, exc)

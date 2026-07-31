"""Plugin-bridge connector: pull from every connected Jarvis integration.

This is the generic gateway of design doc 02's "Jarvis plugin/CLI bridge"
family: ONE connector that adapts any Jarvis-side integration into the
``RawItem`` stream, instead of a bespoke connector per tool.

Candidate enumeration (module-level helper, NOT part of the connector
protocol): :func:`list_candidates` lists the CONNECTED integrations a user
could turn into UltraWiki sources —

- marketplace plugin connections ("connected" = a token is stored and not
  flagged ``needs_reauth``, the same rule the marketplace UI applies), and
- MCP servers configured (and enabled) in the user's ``mcp.json``.

Discovery finds what is INSTALLED; :mod:`jarvis.ultrawiki.connector_catalog`
decides what is OFFERED. Every candidate is matched against that curated
roster: an integration the roster does not name is dropped (noted once at
debug), and one it does name is enriched with the catalog's real product
name, brand key and status — so no raw id or drifting registry string can
reach a card. :func:`list_offered_integrations` adds the roster entries the
user has NOT connected yet, flagged as such, so the picker can show what is
possible without letting anyone add a dead source.

Every probe is wrapped so a half-configured install (no keyring service,
corrupt catalog, missing mcp.json) shortens the list instead of crashing
it. All imports of the marketplace/MCP machinery are lazy (AP-26).

Prototype honesty: the connector instance takes
``ctx.config['integration_id']`` (a candidate ``id`` such as
``"plugin:github"`` or ``"mcp:filesystem"``) and yields items ONLY when a
pull adapter for that integration is registered. Where no pull adapter
exists yet, ``backfill`` logs one honest structured line and yields
NOTHING — the source still registers, consent still gates it
(``uw_sources.consent``), and the UI can show "pull adapter pending" via
:func:`has_pull_adapter`. No data is ever faked.

Extension contract — how a real per-integration pull adapter plugs in
later:

1. An adapter is a callable ``(ctx, checkpoint) -> AsyncIterator[RawItem]``
   (an async generator function is the natural shape). It owns the
   integration-specific "list items since X" logic — driving the plugin's
   MCP tools, REST client, or CLI — and maps each record to a ``RawItem``
   with a stable ``external_id``, a real ``permalink`` (the platform's
   deep link), and an ISO-8601 UTC ``timestamp_utc``.
2. It registers itself under the candidate id via
   :func:`register_pull_adapter` (idempotent overwrite), typically from
   the runtime's bootstrap — never at import time of this module.
3. The bridge stays a pure pass-through: consent, scheduling, batching,
   and checkpoint persistence remain the runtime's job; adapters must not
   touch the store, embed, or call an LLM (design doc 02, hard rule 1).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from jarvis.ultrawiki import connector_catalog
from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    ConnectorContext,
    IncrementalMode,
    RawItem,
)

log = logging.getLogger(__name__)

#: A pull adapter: ``(ctx, checkpoint) -> AsyncIterator[RawItem]``.
PullAdapter = Callable[[ConnectorContext, "str | None"], AsyncIterator[RawItem]]

_PULL_ADAPTERS: dict[str, PullAdapter] = {}


def register_pull_adapter(integration_id: str, adapter: PullAdapter) -> None:
    """Register (or replace) the pull adapter for one integration id."""
    _PULL_ADAPTERS[integration_id] = adapter


def unregister_pull_adapter(integration_id: str) -> None:
    """Remove a registered pull adapter; missing ids are a no-op."""
    _PULL_ADAPTERS.pop(integration_id, None)


def has_pull_adapter(integration_id: str) -> bool:
    """True when a pull adapter is registered for the integration id."""
    return integration_id in _PULL_ADAPTERS


def _adapter_note(integration_id: str) -> str:
    return "pull adapter available" if has_pull_adapter(integration_id) else "pull adapter pending"


def _marketplace_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    try:
        from jarvis.marketplace.catalog_data import load_catalog
        from jarvis.marketplace.token_store import TokenStore

        catalog = load_catalog()
        store = TokenStore()
    except Exception as exc:
        log.debug("plugin-bridge: marketplace catalog unavailable: %s", exc)
        return candidates
    for spec in catalog.plugins:
        try:
            tokens = store.load(spec.id)
        except Exception as exc:
            log.debug("plugin-bridge: token probe failed for plugin %s: %s", spec.id, exc)
            continue
        if tokens is None or tokens.needs_reauth:
            continue
        integration_id = f"plugin:{spec.id}"
        candidates.append(
            {
                "id": integration_id,
                "kind": "plugin",
                "label": spec.display_name,
                "detail": (
                    f"connected marketplace plugin ({spec.category}); "
                    f"{_adapter_note(integration_id)}"
                ),
                # The machine-readable twin of the note above. Callers used to
                # have to string-match "pull adapter pending" to decide whether
                # this integration can contribute anything — a flag they can
                # branch on beats parsing an English sentence.
                "has_pull_adapter": has_pull_adapter(integration_id),
            }
        )
    return candidates


def _mcp_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    try:
        from jarvis.mcp.state import load_config

        config = load_config()
        servers: Any = config.get("mcpServers") or {}
    except Exception as exc:
        log.debug("plugin-bridge: mcp.json unavailable: %s", exc)
        return candidates
    if not isinstance(servers, dict):
        return candidates
    for name, entry in servers.items():
        try:
            if not isinstance(entry, dict):
                continue
            if not entry.get("enabled", True):
                continue
            integration_id = f"mcp:{name}"
            label = str(entry.get("display") or str(name).replace("-", " ").title())
            description = str(entry.get("description") or "").strip()
            detail = "configured MCP server"
            if description:
                detail += f" ({description})"
            detail += f"; {_adapter_note(integration_id)}"
            candidates.append(
                {
                    "id": integration_id,
                    "kind": "mcp",
                    "label": label,
                    "detail": detail,
                    "has_pull_adapter": has_pull_adapter(integration_id),
                }
            )
        except Exception as exc:
            log.debug("plugin-bridge: skipping mcp.json entry %r: %s", name, exc)
            continue
    return candidates


def _enriched(
    candidate: dict[str, Any], spec: connector_catalog.UltraWikiConnectorSpec
) -> dict[str, Any]:
    """One discovered candidate, dressed in its catalog identity.

    The catalog label OVERWRITES whatever the registry carried: registry
    display strings are user- and vendor-authored and drift into unreadable
    shapes, while the roster holds the product's real name. ``status`` is the
    one field the runtime knows better than the catalog — a registered pull
    adapter means the integration really can be read today.
    """
    integration_id = str(candidate.get("id") or "")
    adapter_ready = has_pull_adapter(integration_id)
    return {
        **candidate,
        "label": spec.label,
        "catalog_id": spec.id,
        "brand": spec.brand,
        "connector_kind": spec.kind,
        "description_key": spec.description_key,
        "status": "available" if adapter_ready else spec.status,
        "connected": True,
        "has_pull_adapter": adapter_ready,
    }


def list_candidates() -> list[dict[str, Any]]:
    """CONNECTED integrations the roster offers as UltraWiki sources.

    Each entry: ``{"id", "kind", "label", "detail", "has_pull_adapter",
    "catalog_id", "brand", "connector_kind", "description_key", "status",
    "connected"}`` where ``kind`` is ``"plugin"`` (marketplace) or ``"mcp"``
    (mcp.json server) — WHERE it was discovered — and ``connector_kind`` is
    always ``"bridge"``.

    Connected-only, so the count keeps meaning "apps this install can read
    from" for the health surface. Anything connected but off the roster is
    dropped. Never raises — a broken registry merely shortens the list.
    """
    # Roster order, not discovery order: the picker must not reshuffle itself
    # because a token expired or an mcp.json entry moved.
    rank = {spec.id: i for i, spec in enumerate(connector_catalog.BRIDGE_CONNECTORS)}
    offered: list[tuple[int, dict[str, Any]]] = []
    claimed: set[str] = set()
    for candidate in _marketplace_candidates() + _mcp_candidates():
        integration_id = str(candidate.get("id") or "")
        spec = connector_catalog.bridge_entry_for(integration_id)
        if spec is None:
            connector_catalog.note_excluded_candidate(
                integration_id, "no curated UltraWiki entry"
            )
            continue
        # One product, one card: a user running their own MCP server for a
        # product they also connected as a plugin gets ONE tile, and the
        # marketplace connection wins because it is the supported path.
        if spec.id in claimed:
            connector_catalog.note_excluded_candidate(
                integration_id, f"{spec.label} is already offered"
            )
            continue
        claimed.add(spec.id)
        offered.append((rank[spec.id], _enriched(candidate, spec)))
    offered.sort(key=lambda pair: pair[0])
    return [row for _, row in offered]


def list_offered_integrations() -> list[dict[str, Any]]:
    """The full picker roster: connected entries first, then the rest.

    A curated integration the user has not connected still belongs in the
    picker — that is the difference between a catalog and a list of what
    happens to be plugged in. It carries ``connected: false`` so the UI sends
    the user to the Plugins store instead of registering a source that could
    never read anything.
    """
    connected = list_candidates()
    seen = {str(row.get("catalog_id") or "") for row in connected}
    missing = [
        {
            "id": f"plugin:{spec.id}",
            "kind": "plugin",
            "label": spec.label,
            "detail": (
                "not connected yet — connect it under Plugins, then add it here"
            ),
            "has_pull_adapter": False,
            "catalog_id": spec.id,
            "brand": spec.brand,
            "connector_kind": spec.kind,
            "description_key": spec.description_key,
            "status": spec.status,
            "connected": False,
        }
        for spec in connector_catalog.BRIDGE_CONNECTORS
        if spec.id not in seen
    ]
    return connected + missing


class PluginBridgeConnector:
    """Adapt one connected Jarvis integration into the ``RawItem`` stream."""

    id = "plugin-bridge"
    label = "Connected Integrations"
    # Auth is owned by the underlying integration (marketplace token / MCP
    # server config); the bridge itself needs no credential of its own.
    auth = AuthKind.NONE
    capabilities = ConnectorCapabilities(
        backfill=True,
        incremental=IncrementalMode.NONE,  # prototype: freshness = re-run backfill
        deletes=False,
        refresh_interval_s=3_600.0,
        reconcile_interval_s=86_400.0,
    )

    async def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]:
        integration_id = str(ctx.config.get("integration_id") or "")
        if not integration_id:
            log.warning(
                "plugin-bridge: source %s has no 'integration_id' configured; yielding nothing",
                ctx.source_id,
            )
            return
        adapter = _PULL_ADAPTERS.get(integration_id)
        if adapter is None:
            log.info(
                "plugin-bridge: pull adapter pending integration_id=%s source_id=%s yielded=0",
                integration_id,
                ctx.source_id,
            )
            return
        async for item in adapter(ctx, checkpoint):
            yield item

    async def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]:
        # capabilities.incremental is NONE: the scheduler re-runs backfill
        # instead. Kept as an empty async generator for protocol parity.
        return
        yield  # pragma: no cover - unreachable; marks this as an async generator


__all__ = [
    "PluginBridgeConnector",
    "PullAdapter",
    "has_pull_adapter",
    "list_candidates",
    "list_offered_integrations",
    "register_pull_adapter",
    "unregister_pull_adapter",
]

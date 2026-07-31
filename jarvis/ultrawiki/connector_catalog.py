"""Declarative catalog of every connector UltraWiki OFFERS as a source.

The sibling of :mod:`jarvis.ultrawiki.provider_catalog`: same doctrine, one
place answering "what can a user add here, what is it called, and what does it
look like". Where the provider catalog covers the four capability slots
(storage, embedding, distill, rerank), this one covers the *sources* — the
built-in connectors plus the CURATED set of connected integrations the plugin
bridge may adapt.

Why it had to exist (2026-07-25 gap): the add-source picker rendered whatever
:func:`jarvis.ultrawiki.connectors.plugin_bridge.list_candidates` happened to
find. That is a raw dump of the user's registries — every marketplace plugin
and every server name in their ``mcp.json`` — so the list showed opaque ids and
whatever string the registry carried, offered tools UltraWiki has no business
reading (a deployment platform, a payment processor), and gave no visual
identity at all. A knowledge base is a product surface, not a registry viewer.

Three rules make this a CATALOG rather than a filter list:

* **It is the allow-list.** An integration the roster does not name is excluded
  from the API answer entirely. Discovery still runs — the roster decides what
  is *offered*, never what is *installed*.
* **The label here WINS.** Registry display strings drift (an MCP server called
  ``grace-plug-in-mcp``); the roster carries the real product name, so no raw
  id or garbled registry string can reach a card.
* **A curated entry appears even when it is not connected yet.** Seeing what is
  possible is the point of a catalog; the entry is then flagged so the user is
  sent to the Plugins store instead of being allowed to add a dead source.

Deliberate non-goals:

* **No behavior gates on an entry id (AP-21).** Everything here is presentation
  plus routing metadata. Whether an integration can actually yield items is a
  runtime fact (``plugin_bridge.has_pull_adapter``), not a catalog claim.
* **No invented services.** Every bridge entry names a plugin the marketplace
  catalog ships AND a brand mark bundled in
  ``jarvis/ui/web/frontend/src/assets/brands/``. Adding one without both is how
  a picker starts advertising things the app cannot connect.
* **No logo files live here.** ``brand`` is only the asset key; the frontend
  resolves it exactly as the Plugins store does, monogram fallback included.
  Trademark review of a bundled mark stays a human checkpoint (repo charter
  §2) — see that folder's ``LOGOS.md`` ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

log = logging.getLogger(__name__)

__all__ = [
    "ConnectorKind",
    "ConnectorStatus",
    "UltraWikiConnectorSpec",
    "CONNECTOR_KINDS",
    "CONNECTOR_STATUSES",
    "NEUTRAL_BRANDS",
    "BUILTIN_CONNECTORS",
    "BRIDGE_CONNECTORS",
    "ALL_CONNECTORS",
    "as_dict",
    "brand_asset_keys",
    "bridge_entry_for",
    "get_connector",
    "is_offered",
    "list_connectors",
    "note_excluded_candidate",
]

#: ``builtin`` — a connector shipped in this package, added directly.
#: ``bridge``  — a connected integration adapted through ``plugin-bridge``.
ConnectorKind = Literal["builtin", "bridge"]

#: What the entry can do TODAY, independent of whether it is connected.
#:
#: ``available``       — a working reader exists; approving it imports data.
#: ``adapter_pending`` — the entry can be registered and consent granted, but
#:                       no pull adapter is built yet, so a sync finds nothing.
#:                       Stated up front rather than discovered after an empty
#:                       import.
ConnectorStatus = Literal["available", "adapter_pending"]

CONNECTOR_KINDS: tuple[ConnectorKind, ...] = ("builtin", "bridge")
CONNECTOR_STATUSES: tuple[ConnectorStatus, ...] = ("available", "adapter_pending")

#: Brand tokens that name a neutral glyph instead of a vendor mark. A built-in
#: connector has no vendor and must never borrow one; the frontend maps these
#: to its own icons. Kept as a closed set so a typo cannot silently become a
#: missing logo.
NEUTRAL_BRANDS: tuple[str, ...] = (
    "vault", "folder", "chat", "wiki", "archive", "link"
)


@dataclass(frozen=True, slots=True)
class UltraWikiConnectorSpec:
    """One offer in the add-source picker."""

    #: Built-in: the connector id (``jarvis.ultrawiki.connectors``). Bridge: the
    #: marketplace plugin id, which is ALSO the brand asset filename.
    id: str
    kind: ConnectorKind
    #: The real product name, in English. For a vendor this is the vendor's own
    #: spelling and is never translated; for a built-in it is the fallback for
    #: non-UI consumers (CLI, API) while the UI renders ``label_key``.
    label: str
    #: Asset key under ``src/assets/brands`` (``<brand>.svg``), or one of
    #: :data:`NEUTRAL_BRANDS`.
    brand: str
    status: ConnectorStatus
    #: i18n key of the one-line description shown under the name.
    description_key: str
    #: Built-ins only: i18n key of the display name (it is our copy, so it
    #: translates — and the conversations entry carries the ``{name}`` token so
    #: the assistant's configured name propagates, charter §4).
    label_key: str = ""
    #: Bridge only: candidate ids this entry claims, beyond ``<id>`` itself.
    #: A candidate arrives as ``plugin:<plugin id>`` or ``mcp:<server name>``;
    #: the prefix is stripped and the remainder matched against these, so a
    #: user's own MCP server for the same product lands on the same card.
    aliases: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Built-in connectors — the local sources, always offered
# ---------------------------------------------------------------------------
#
# ``plugin-bridge`` is deliberately ABSENT: it is plumbing, not a product. Its
# card would read "Connected integration (plugins / MCP)", which is the generic
# label this catalog exists to abolish. The bridge is reached by picking one of
# the bridge entries below, which carry the integration id for it.

BUILTIN_CONNECTORS: tuple[UltraWikiConnectorSpec, ...] = (
    UltraWikiConnectorSpec(
        id="obsidian-vault",
        kind="builtin",
        label="Obsidian vault",
        brand="vault",
        status="available",
        label_key="ultrawiki.sources.connector_obsidian",
        description_key="ultrawiki.connectors.obsidian_vault",
    ),
    UltraWikiConnectorSpec(
        id="local-folder",
        kind="builtin",
        label="Local folder",
        brand="folder",
        status="available",
        label_key="ultrawiki.sources.connector_local_folder",
        description_key="ultrawiki.connectors.local_folder",
    ),
    UltraWikiConnectorSpec(
        id="jarvis-conversations",
        kind="builtin",
        # No fixed assistant name in a user-visible string (charter §4): the UI
        # renders the ``{name}`` token from ``label_key``, and this fallback
        # stays neutral for the CLI and API.
        label="Conversations with your assistant",
        brand="chat",
        status="available",
        label_key="ultrawiki.sources.connector_conversations",
        description_key="ultrawiki.connectors.jarvis_conversations",
    ),
    UltraWikiConnectorSpec(
        id="normal-wiki",
        kind="builtin",
        label="Built-in wiki",
        brand="wiki",
        status="available",
        label_key="ultrawiki.sources.connector_normal_wiki",
        description_key="ultrawiki.connectors.normal_wiki",
    ),
    # The universal on-ramp: whatever export the user already HAS. It is a
    # built-in rather than a bridge entry because it belongs to no vendor —
    # the formats are open standards (mbox, iCalendar, vCard, CSV, PDF), and
    # every service that matters can produce at least one of them.
    UltraWikiConnectorSpec(
        id="export-import",
        kind="builtin",
        label="Export file import",
        brand="archive",
        status="available",
        label_key="ultrawiki.sources.connector_export_import",
        description_key="ultrawiki.connectors.export_import",
    ),
    # The escape hatch: everything the roster will never name. A service with
    # no adapter and no export button had no route at all, and there will
    # always be one — a niche app, a regional network, a company's own tool.
    UltraWikiConnectorSpec(
        id="custom-source",
        kind="builtin",
        label="Custom source",
        brand="link",
        status="available",
        label_key="ultrawiki.sources.connector_custom_source",
        description_key="ultrawiki.connectors.custom_source",
    ),
)


# ---------------------------------------------------------------------------
# Bridge integrations — the CURATED roster
# ---------------------------------------------------------------------------
#
# Every entry satisfies three conditions, and an entry that stops satisfying
# them is removed rather than left to rot:
#
#   1. the marketplace ships the plugin (``jarvis/marketplace/seed_catalog.json``),
#      so it can actually be connected in-app;
#   2. a brand mark for that id is bundled in ``src/assets/brands``, so the tile
#      shows the real logo instead of a monogram;
#   3. the product holds material a personal knowledge base plausibly reads —
#      documents, messages, issues, notes, records.
#
# Condition 3 is why the roster is SHORTER than the marketplace: a deployment
# platform, a payment processor, a design tool or a home-automation hub are
# perfectly good plugins and terrible knowledge sources. Offering them would be
# a promise UltraWiki has no intention of keeping.
#
# Every entry is ``adapter_pending`` today: the bridge has no pull adapter for
# any integration yet (see plugin_bridge's extension contract). Registering an
# adapter flips the runtime status to ``available`` without touching this file.

BRIDGE_CONNECTORS: tuple[UltraWikiConnectorSpec, ...] = (
    UltraWikiConnectorSpec(
        id="github",
        kind="bridge",
        label="GitHub",
        brand="github",
        status="adapter_pending",
        description_key="ultrawiki.connectors.github",
    ),
    UltraWikiConnectorSpec(
        id="notion",
        kind="bridge",
        label="Notion",
        brand="notion",
        status="adapter_pending",
        description_key="ultrawiki.connectors.notion",
    ),
    UltraWikiConnectorSpec(
        id="slack",
        kind="bridge",
        label="Slack",
        brand="slack",
        status="adapter_pending",
        description_key="ultrawiki.connectors.slack",
    ),
    # Google ships as three separate marketplace plugins with three separate
    # marks, and this catalog mirrors that naming rather than inventing one
    # "Google" umbrella: a user connects Drive without connecting Gmail, and a
    # merged card could not represent that honestly.
    UltraWikiConnectorSpec(
        id="google_drive",
        kind="bridge",
        label="Google Drive",
        brand="google_drive",
        status="adapter_pending",
        description_key="ultrawiki.connectors.google_drive",
        aliases=("googledrive", "gdrive"),
    ),
    UltraWikiConnectorSpec(
        id="gmail",
        kind="bridge",
        label="Gmail",
        brand="gmail",
        status="adapter_pending",
        description_key="ultrawiki.connectors.gmail",
        aliases=("google_mail",),
    ),
    UltraWikiConnectorSpec(
        id="google_calendar",
        kind="bridge",
        label="Google Calendar",
        brand="google_calendar",
        status="adapter_pending",
        description_key="ultrawiki.connectors.google_calendar",
        aliases=("googlecalendar", "gcal"),
    ),
    UltraWikiConnectorSpec(
        id="asana",
        kind="bridge",
        label="Asana",
        brand="asana",
        status="adapter_pending",
        description_key="ultrawiki.connectors.asana",
    ),
    UltraWikiConnectorSpec(
        id="linear",
        kind="bridge",
        label="Linear",
        brand="linear",
        status="adapter_pending",
        description_key="ultrawiki.connectors.linear",
    ),
    UltraWikiConnectorSpec(
        id="todoist",
        kind="bridge",
        label="Todoist",
        brand="todoist",
        status="adapter_pending",
        description_key="ultrawiki.connectors.todoist",
    ),
    UltraWikiConnectorSpec(
        id="clickup",
        kind="bridge",
        label="ClickUp",
        brand="clickup",
        status="adapter_pending",
        description_key="ultrawiki.connectors.clickup",
    ),
    UltraWikiConnectorSpec(
        id="dropbox",
        kind="bridge",
        label="Dropbox",
        brand="dropbox",
        status="adapter_pending",
        description_key="ultrawiki.connectors.dropbox",
    ),
    UltraWikiConnectorSpec(
        id="airtable",
        kind="bridge",
        label="Airtable",
        brand="airtable",
        status="adapter_pending",
        description_key="ultrawiki.connectors.airtable",
    ),
    UltraWikiConnectorSpec(
        id="discord",
        kind="bridge",
        label="Discord",
        brand="discord",
        status="adapter_pending",
        description_key="ultrawiki.connectors.discord",
    ),
    UltraWikiConnectorSpec(
        id="telegram",
        kind="bridge",
        label="Telegram",
        brand="telegram",
        status="adapter_pending",
        description_key="ultrawiki.connectors.telegram",
    ),
)


ALL_CONNECTORS: tuple[UltraWikiConnectorSpec, ...] = (
    BUILTIN_CONNECTORS + BRIDGE_CONNECTORS
)

_BY_ID: dict[str, UltraWikiConnectorSpec] = {
    spec.id: spec for spec in ALL_CONNECTORS
}


def _normalize(token: str) -> str:
    """Fold one integration token to its comparison form.

    Registry naming is inconsistent by nature — the marketplace uses
    ``google_drive``, a hand-written ``mcp.json`` entry might say
    ``Google-Drive``. Case, spaces, dots and dashes carry no meaning here, so
    they are folded away before matching. Anything left that does not match the
    roster stays excluded: this normalises SPELLING, it never widens the
    allow-list to a different product.
    """
    folded = token.strip().lower()
    for char in (" ", "-", "."):
        folded = folded.replace(char, "_")
    while "__" in folded:
        folded = folded.replace("__", "_")
    return folded.strip("_")


def _bridge_index() -> dict[str, UltraWikiConnectorSpec]:
    index: dict[str, UltraWikiConnectorSpec] = {}
    for spec in BRIDGE_CONNECTORS:
        for token in (spec.id, *spec.aliases):
            index[_normalize(token)] = spec
    return index


_BRIDGE_INDEX: dict[str, UltraWikiConnectorSpec] = _bridge_index()

#: Candidate ids already reported as excluded, so the debug line is written
#: once per process instead of on every picker poll.
_EXCLUSION_LOGGED: set[str] = set()


def list_connectors() -> tuple[UltraWikiConnectorSpec, ...]:
    """Every offered connector — built-ins first, then the bridge roster."""
    return ALL_CONNECTORS


def get_connector(entry_id: str) -> UltraWikiConnectorSpec | None:
    """One entry by its catalog id. ``None`` when the roster does not name it."""
    return _BY_ID.get(str(entry_id or "").strip())


def bridge_entry_for(integration_id: str) -> UltraWikiConnectorSpec | None:
    """The roster entry a bridge candidate id belongs to, if any.

    ``integration_id`` is the bridge's own form — ``plugin:<id>`` or
    ``mcp:<name>``. The prefix says where the integration was DISCOVERED, which
    is irrelevant to which product it is, so both forms resolve to the same
    entry: a user who wired their own MCP server for Notion gets the Notion
    card, not a second nameless row.
    """
    raw = str(integration_id or "").strip()
    if not raw:
        return None
    _, _, tail = raw.partition(":")
    return _BRIDGE_INDEX.get(_normalize(tail or raw))


def is_offered(integration_id: str) -> bool:
    """True when the roster offers this bridge candidate id."""
    return bridge_entry_for(integration_id) is not None


def note_excluded_candidate(integration_id: str, reason: str = "") -> None:
    """Record — once per id, at debug — that a candidate was left out.

    Exclusion is the designed behaviour, not an error, so it must not produce
    log noise on a picker that polls. It is still worth stating once: "my
    plugin does not show up" is otherwise unanswerable.
    """
    key = str(integration_id or "")
    if key in _EXCLUSION_LOGGED:
        return
    _EXCLUSION_LOGGED.add(key)
    log.debug(
        "connector catalog: %s is connected but not on the UltraWiki roster%s",
        key or "<unnamed integration>",
        f" ({reason})" if reason else "",
    )


def as_dict(spec: UltraWikiConnectorSpec) -> dict[str, Any]:
    """The API payload of one entry (the shape the picker renders)."""
    return {
        "id": spec.id,
        "kind": spec.kind,
        "label": spec.label,
        "label_key": spec.label_key,
        "brand": spec.brand,
        "status": spec.status,
        "description_key": spec.description_key,
        # The connector a source is registered under: a built-in is its own
        # connector, every bridge entry goes through the one bridge.
        "connector": spec.id if spec.kind == "builtin" else "plugin-bridge",
        # Bridge only: the candidate id the source's config carries.
        "integration_id": "" if spec.kind == "builtin" else f"plugin:{spec.id}",
    }


def brand_asset_keys() -> set[str]:
    """Brand keys that must exist as ``<key>.svg`` in the frontend assets.

    Neutral built-in tokens are excluded — they are icons, not marks. Used by
    the test that pins the roster against the bundled files: an entry whose
    logo is missing falls back to a monogram, which is a visible product
    regression rather than a crash, so it needs a test to stay visible.
    """
    return {
        spec.brand
        for spec in ALL_CONNECTORS
        if spec.brand not in NEUTRAL_BRANDS
    }

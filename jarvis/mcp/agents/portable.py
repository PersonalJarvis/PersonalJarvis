"""Carry an agent ecosystem to another machine.

The team a person builds — who is on it, what each one is for, what it may
touch, what it may spend — is the part of Jarvis that took real thought and
that a reinstall or a second computer otherwise throws away. This module makes
that portable: one JSON bundle out, the same bundle in somewhere else.

**What travels is the design, never the operation.** A bundle carries roster
rows and their settings. It does NOT carry:

* secrets of any kind — API keys, tokens, the control key, browser logins;
* the board, chat transcripts or sessions, which are this machine's history and
  frequently private;
* workspace paths and account ids, which name directories and subscription
  seats that do not exist on the other machine.

That exclusion is enforced here rather than trusted to the caller, and pinned by
a test: an export that could leak a credential is a feature nobody can safely
use, so the safe subset is an allowlist — a field is carried because it was
named, never because it happened to exist.

Importing is **adopt-before-mint**, matching the roster's own rule: an agent
whose name is already on the target is updated, not duplicated. Running it twice
therefore changes nothing the second time, which is what makes a bundle safe to
re-apply after a partial failure.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

log = logging.getLogger(__name__)

#: Bundle format. Bumped when a field's meaning changes, so an old bundle is
#: refused with a sentence instead of silently half-applied.
BUNDLE_VERSION: Final[int] = 1

#: The agent fields a bundle carries. An allowlist, deliberately: a denylist
#: leaks every field somebody adds later without thinking about portability.
PORTABLE_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "title",
    "description",
    "tier",
    "provider",
    "model",
    "effort",
    "grant_mode",
    "grants",
    "focus",
    "denies",
    "skills",
    "knowledge_scope",
    "permission_ceiling",
    "approval_rules",
    "daily_budget_usd",
    "max_concurrent_runs",
    "browser_mode",
    "browser_allowed_domains",
    "avatar",
)

#: Fields that must NEVER appear in a bundle, each for a stated reason. Checked
#: by a test against what export actually produces.
NEVER_EXPORTED: Final[dict[str, str]] = {
    "account_id": "names a subscription seat that does not exist on the other machine",
    "workspace_dir": "an absolute path on THIS machine",
    "wiki_namespace": "derived from the agent id on the target",
    "agent_id": "minted by the target roster; matching is by name",
    "parent_agent_id": "an id from this machine's roster",
    "session_id": "this machine's chat session",
    "stats": "run history and spend, which belong to the machine that did the work",
    "created_ms": "the target records its own timestamps",
    "updated_ms": "same",
    "state": "paused/archived is an operational state, not part of the design",
    "checkpoint": "where the figure stands right now on this island",
}

#: The lead seat is Jarvis' own and exists on every install already. Carrying it
#: would overwrite the target's lead with this machine's model choice.
_SKIP_NAMES: Final[frozenset[str]] = frozenset({"jarvis"})


class BundleError(ValueError):
    """A bundle could not be read or applied. The message is user-facing."""


def _agent_payload(row: dict[str, Any]) -> dict[str, Any]:
    """One roster row reduced to what is portable."""
    out: dict[str, Any] = {}
    for key in PORTABLE_FIELDS:
        if key not in row:
            continue
        value = row[key]
        if value is None:
            continue
        out[key] = value
    return out


async def export_bundle(runtime: Any, *, include_archived: bool = False) -> dict[str, Any]:
    """Everything portable about this ecosystem, as a plain dict."""
    agents = await runtime.roster.list(include_archived=include_archived)
    rows = []
    for agent in agents:
        if agent.name.strip().lower() in _SKIP_NAMES:
            continue
        rows.append(_agent_payload(agent.to_dict()))
    return {
        "kind": "jarvis.agent-ecosystem",
        "version": BUNDLE_VERSION,
        "exported_ms": int(time.time() * 1000),
        "agents": rows,
        "agent_count": len(rows),
        "note": (
            "Design only: no secrets, no chat history, no board. Import with "
            "ecosystem_import on the target machine."
        ),
    }


def _validate(bundle: Any) -> list[dict[str, Any]]:
    """The agent rows a bundle carries, or a refusal that says what is wrong."""
    if not isinstance(bundle, dict):
        raise BundleError("a bundle is a JSON object with 'kind', 'version' and 'agents'")
    if bundle.get("kind") != "jarvis.agent-ecosystem":
        raise BundleError(f"this is not a Jarvis ecosystem bundle (kind={bundle.get('kind')!r})")
    version = bundle.get("version")
    if version != BUNDLE_VERSION:
        raise BundleError(
            f"bundle version {version} cannot be read by this Jarvis "
            f"(it understands version {BUNDLE_VERSION})"
        )
    agents = bundle.get("agents")
    if not isinstance(agents, list) or not agents:
        raise BundleError("the bundle carries no agents")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(agents):
        if not isinstance(row, dict):
            raise BundleError(f"agent #{index + 1} is not an object")
        if not str(row.get("name") or "").strip():
            raise BundleError(f"agent #{index + 1} has no name — names are how import matches")
        rows.append(row)
    return rows


async def import_bundle(
    runtime: Any, bundle: Any, *, overwrite: bool = True, dry_run: bool = False
) -> dict[str, Any]:
    """Apply a bundle to this ecosystem: adopt by name, mint what is missing.

    ``overwrite`` decides what happens to an agent that already exists here:
    True updates its settings from the bundle, False leaves it exactly as it is
    and reports it as skipped. ``dry_run`` reports the same plan without
    touching anything, which is how a person sees what an import would do
    before it does it.
    """
    from jarvis.society.roster import RosterError

    rows = _validate(bundle)
    existing = {a.name.strip().lower(): a for a in await runtime.roster.list(include_archived=True)}

    created: list[str] = []
    updated: list[str] = []
    skipped: list[dict[str, str]] = []

    for row in rows:
        name = str(row["name"]).strip()
        key = name.lower()
        payload = _agent_payload(row)
        if key in _SKIP_NAMES:
            skipped.append({"name": name, "why": "the lead seat belongs to this install"})
            continue
        known = existing.get(key)
        if known is not None and not overwrite:
            skipped.append({"name": name, "why": "already here, and overwrite is off"})
            continue
        if dry_run:
            (updated if known is not None else created).append(name)
            continue
        try:
            if known is not None:
                fields = {k: v for k, v in payload.items() if k != "name"}
                await runtime.roster.update(known.agent_id, fields)
                updated.append(name)
            else:
                fields = {
                    k: v for k, v in payload.items() if k not in ("name", "title", "description")
                }
                agent, _ = await runtime.roster.create(
                    name=name,
                    title=str(payload.get("title") or name),
                    description=str(payload.get("description") or ""),
                    **fields,
                )
                created.append(agent.name)
        except RosterError as exc:
            # One bad row must not abort the rest: a half-imported team the
            # person can finish by hand beats an all-or-nothing refusal.
            skipped.append({"name": name, "why": f"refused by the roster: {exc}"})
        except (TypeError, ValueError) as exc:
            skipped.append({"name": name, "why": f"unusable fields: {exc}"})

    return {
        "dry_run": dry_run,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_in_bundle": len(rows),
    }


__all__ = [
    "BUNDLE_VERSION",
    "NEVER_EXPORTED",
    "PORTABLE_FIELDS",
    "BundleError",
    "export_bundle",
    "import_bundle",
]

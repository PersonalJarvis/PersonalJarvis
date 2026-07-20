"""One-shot re-curation of the user profile page (spec 2026-07-20 §3).

Vaults populated before the evidence-tiered redesign (ADR-0029) concentrate
their accumulated slop on the user profile: world-knowledge trivia bullets,
topic detail that belongs on linked topic pages, and unsupported one-off
claims. This module re-judges exactly that ONE page with the asymmetric
curation bar and proposes the cleaned state:

* keep every supported personal fact (identity, people, possessions,
  health, habits, projects) — cleanup must never cost real memory;
* move topic detail onto the topic's own page (creating it when missing,
  cross-linked both ways);
* drop bullets that are world-knowledge trivia or unsupported one-off
  mentions.

Safety posture: **dry-run by default** (proposals are printed, nothing is
written), a full tar.gz vault snapshot before any apply, and every write
goes through ``WikiCurator.apply_external_updates`` with
``all_or_nothing=True`` (backup → secret guard → validate → rollback →
FTS). Manual invocation only (``jarvis.memory.wiki.cli recurate-profile``)
— never scheduled, never on the voice path (AP-9).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.brain.streaming import aggregate, is_length_truncated
from jarvis.core.protocols import BrainMessage, BrainRequest

from .cleanup import _full_snapshot
from .constants import INFERRED_MARKER
from .curator_llm import _extract_json_array
from .journal import normalise_subjects
from .prompt import resolve_user_entity_slug
from .protocols import PageUpdate

log = logging.getLogger(__name__)

_TARGET_DIRS = ("entities", "concepts", "projects")

_RECURATE_SYSTEM = """\
You re-curate the ONE user profile page of a personal knowledge wiki (an
Obsidian vault). You receive the current profile body and the list of pages
that already exist. Propose the cleaned state of the vault as JSON updates.

Curation bar (binding, asymmetric):
- KEEP every supported personal fact: identity, close people, possessions,
  health, habits and recurring activities, values, active projects,
  decisions. Losing a real personal fact is the worst possible outcome of
  this cleanup — when unsure whether a bullet is real memory, keep it.
- MOVE topic detail to the topic's own page: when a profile bullet carries
  detail about a named durable topic (a vehicle, a sport, a person, a tool),
  keep at most a one-line [[wikilink]] bullet on the profile and put the
  detail on the topic page ("update" it, or "create" it when missing,
  cross-linked with the profile in both directions).
- DROP bullets that are world-knowledge trivia with no personal anchor,
  duplicated lines, template scaffolding, or transient status chatter.
  Bullets ending with the marker *(inferred)* may be kept as-is; never
  invent new *(inferred)* lines.

Output contract:
Return ONLY a JSON array of update objects, no prose, no code fences:
    [{"target": "<dir>/<slug>.md", "operation": "update" | "create",
      "new_body": "<full page markdown with frontmatter>",
      "reason": "<short why>"}]
- Include the profile page itself as one "update" (its cleaned full body,
  section structure intact). If the profile needs no change, return [].
- "create" only for pages that do not exist yet; "update" only for pages
  that do. Directories allowed: entities/, concepts/, projects/.
- Preserve the ## Sources section content of every page you touch.
- Never write credentials or secrets. Never touch sessions/ or _archive/.
"""


# Drift guard: the recurate prompt must reference the same literal marker
# the preservation guard exempts (constants.INFERRED_MARKER).
assert INFERRED_MARKER in _RECURATE_SYSTEM


@dataclass(slots=True)
class RecurateReport:
    """Outcome of one re-curation run (dry or applied)."""

    profile_rel: str = ""
    proposals: list[PageUpdate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    applied: bool = False
    backup_path: Path | None = None
    error: str = ""


def _existing_pages(vault_root: Path) -> list[str]:
    pages: list[str] = []
    for directory in _TARGET_DIRS:
        base = vault_root / directory
        if not base.is_dir():
            continue
        pages.extend(
            f"{directory}/{page.name}" for page in sorted(base.glob("*.md"))
        )
    return pages


def _validate_items(
    items: list[Any], *, profile_rel: str, vault_root: Path,
) -> str | None:
    """Deterministic shape check before anything reaches the write pipeline."""
    if not items:
        return None
    for item in items:
        if not isinstance(item, dict):
            return "response contains a non-object item"
        target = str(item.get("target") or "").strip().replace("\\", "/")
        operation = str(item.get("operation") or "").strip()
        body = item.get("new_body")
        parts = target.split("/")
        if (
            len(parts) != 2
            or parts[0] not in _TARGET_DIRS
            or not parts[1].endswith(".md")
            or normalise_subjects((parts[1][:-3],)) != (parts[1][:-3],)
        ):
            return f"unsafe target: {target!r}"
        if operation not in ("update", "create"):
            return f"unsupported operation: {operation!r}"
        if not isinstance(body, str) or not body.strip():
            return f"missing new_body for {target}"
        exists = (vault_root / target).is_file()
        if operation == "create" and exists:
            return f"create targets an existing page: {target}"
        if operation == "update" and not exists:
            return f"update targets a missing page: {target}"
    return None


async def recurate_profile(
    *,
    vault_root: Path,
    config: Any,
    curator: Any,
    registry: Any | None = None,
    apply: bool = False,
    backup_dir: Path | None = None,
) -> RecurateReport:
    """Re-judge the user profile page; dry-run unless ``apply`` is True."""
    report = RecurateReport()
    wiki_cfg = config.memory.wiki
    user_slug = resolve_user_entity_slug(
        getattr(wiki_cfg.session_rollup, "user_entity_slug", "")
    )
    profile_rel = f"entities/{user_slug}.md"
    report.profile_rel = profile_rel
    profile_path = vault_root / profile_rel
    if not profile_path.is_file():
        report.error = f"profile page not found: {profile_rel}"
        return report
    profile_body = profile_path.read_text(encoding="utf-8")

    pages = "\n".join(f"- {page}" for page in _existing_pages(vault_root))
    user_prompt = (
        f"User profile page (binding target): {profile_rel}\n"
        "----- CURRENT PROFILE BODY -----\n"
        f"{profile_body.rstrip()}\n"
        "----- END PROFILE BODY -----\n\n"
        "Existing pages:\n"
        f"{pages or '- (none)'}\n\n"
        "Return the JSON array now."
    )
    request = BrainRequest(
        messages=(BrainMessage(role="user", content=user_prompt),),
        system=_RECURATE_SYSTEM,
        max_tokens=int(wiki_cfg.curator.max_output_tokens),
        temperature=0.2,
        stream=True,
    )

    from jarvis.memory.wiki.provider_chain import (
        build_wiki_provider_chain,
        complete_with_fallback,
        credential_ready_wiki_providers,
    )

    # An injected registry (tests) skips the credential filter — the same
    # convention as WikiCuratorLLM/Consolidator: production uses a fresh
    # registry and only keeps key-ready provider families in the chain.
    credential_filter = registry is None
    if registry is None:
        from jarvis.brain.provider_registry import BrainProviderRegistry

        registry = BrainProviderRegistry()
    available = set(registry.available())
    chain = build_wiki_provider_chain(
        primary=(wiki_cfg.curator.provider.strip() or config.brain.primary),
        model_override=wiki_cfg.curator.model,
        available=available,
        credential_ready=(
            credential_ready_wiki_providers(available=available, config=config)
            if credential_filter
            else available
        ),
    )

    def _validate_response(agg: Any) -> str | None:
        if is_length_truncated(agg.finish_reason, agg.text):
            return "truncated structured output"
        try:
            parsed = _extract_json_array(agg.text)
        except ValueError as exc:
            return f"malformed JSON array: {exc}"
        return _validate_items(
            parsed, profile_rel=profile_rel, vault_root=vault_root,
        )

    result = await complete_with_fallback(
        registry=registry,
        chain=chain,
        request=request,
        timeout_s=float(wiki_cfg.curator.timeout_s),
        label="RecurateProfile",
        aggregate=aggregate,
        validate=_validate_response,
    )
    if result is None:
        report.error = "no provider produced a valid re-curation proposal"
        return report
    agg_response, _provider = result
    items = _extract_json_array(agg_response.text)

    for item in items:
        report.proposals.append(
            PageUpdate(
                target_path=Path(str(item["target"]).strip().replace("\\", "/")),
                operation=str(item["operation"]).strip(),
                new_body=str(item["new_body"]),
                reason=str(item.get("reason") or ""),
            )
        )
        report.reasons.append(str(item.get("reason") or ""))

    if not apply or not report.proposals:
        return report

    snapshot_dir = backup_dir or (vault_root.parent / "wiki-backups")
    report.backup_path = _full_snapshot(vault_root, snapshot_dir)
    write_result = await curator.apply_external_updates(
        report.proposals,
        source_label=f"recurate-profile:{time.strftime('%Y-%m-%d')}",
        verb="merge",
        all_or_nothing=True,
    )
    rejected = [
        *getattr(write_result, "failed_validation", ()),
        *getattr(write_result, "blocked_pii", ()),
        *getattr(write_result, "skipped_due_to_recent_edit", ()),
    ]
    if rejected or not getattr(write_result, "applied", ()):
        report.error = (
            f"{len(rejected)} write(s) rejected; vault left unchanged "
            "(all-or-nothing)"
        )
        return report
    report.applied = True
    return report


__all__ = ["RecurateReport", "recurate_profile"]

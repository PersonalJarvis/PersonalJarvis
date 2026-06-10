"""Living user profile (Wave-2 B6, maintainer decision D4).

The user's entity page (``entities/<slug>.md``) is the structured living
profile the Stage-2 consolidator maintains autonomously: identity,
preferences, work style, values, relationships, active projects and
decisions — the capability the legacy ``data/workspace`` curator had, now
INSIDE the one Obsidian vault (no second notebook).

This module only guarantees the SKELETON: :func:`ensure_profile_skeleton`
appends any missing ``## <section>`` headings (existing content is
byte-preserved; the call is idempotent) or creates a fresh page when none
exists. Filling the sections is the consolidator's job — its prompt names
the profile page as the preferred UPDATE target for identity/preference
facts about the user.

Writes go through ``WikiCurator.apply_external_updates`` (AP-3: link
demotion + AtomicWriter backup/secret-guard/validate/rollback/FTS).
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.memory.wiki.protocols import PageUpdate

if TYPE_CHECKING:
    from jarvis.memory.wiki.curator import WikiCurator

log = logging.getLogger(__name__)

# The structured sections of the living profile, in canonical order.
# The consolidator prompt and the D4 spec both reference this list.
PROFILE_SECTIONS: tuple[str, ...] = (
    "Summary",
    "Identity",
    "Preferences",
    "Work style",
    "Values",
    "Relationships",
    "Active projects",
    "Decisions",
    "Sources",
)


def _fresh_profile_body(slug: str, today: str) -> str:
    name = slug.replace("-", " ").title()
    sections = "\n".join(f"## {s}\n" for s in PROFILE_SECTIONS)
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        f"aliases: [{name}, the user]\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"{sections}"
    )


async def ensure_profile_skeleton(
    *,
    vault_root: Path,
    slug: str,
    curator: "WikiCurator",
) -> bool:
    """Make sure the profile page exists and carries every section.

    Returns ``True`` when a write was applied, ``False`` when the page was
    already complete (idempotent re-run) or the write could not land (the
    failure is logged; boot must never break on profile maintenance).
    """
    slug = (slug or "").strip().lower()
    if not slug:
        return False

    rel = f"entities/{slug}.md"
    abs_path = Path(vault_root) / rel
    today = _dt.date.today().isoformat()

    if not abs_path.is_file():
        body = _fresh_profile_body(slug, today)
        operation = "create"
    else:
        try:
            raw = abs_path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("profile: cannot read %s: %s", rel, exc)
            return False
        missing = [s for s in PROFILE_SECTIONS if f"## {s}" not in raw]
        if not missing:
            return False
        appendix = "\n".join(f"## {s}\n" for s in missing)
        body = raw.rstrip() + "\n\n" + appendix
        operation = "update"

    update = PageUpdate(
        target_path=Path(rel),
        operation=operation,
        new_body=body,
        reason="living-profile skeleton (D4)",
    )
    result = await curator.apply_external_updates(
        [update], source_label=f"profile-skeleton:{slug}", verb="update",
    )
    applied = bool(result.applied)
    if not applied:
        log.warning(
            "profile: skeleton write for %s did not land "
            "(skipped=%d, failed=%d, blocked=%d)",
            rel,
            len(result.skipped_due_to_recent_edit),
            len(result.failed_validation),
            len(result.blocked_pii),
        )
    return applied


__all__ = ["PROFILE_SECTIONS", "ensure_profile_skeleton"]

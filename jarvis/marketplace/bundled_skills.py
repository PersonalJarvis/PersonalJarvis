"""Write and remove the skills an Agent Plugins package brings with it.

A marketplace plugin may bundle `skills/<name>/SKILL.md` alongside its MCP
server — instructions for the model next to the tools they drive. Installing
the plugin materialises those skills in the user's skills directory, where
the ordinary skill machinery (registry, finder, Draft lifecycle) picks them
up with no special case: a bundled skill is a normal skill that happened to
arrive with a plugin.

Ownership is explicit. Each directory this module writes carries a marker
file naming the plugin that owns it, and nothing without a matching marker
is ever overwritten or deleted. Without that, two plausible accidents
follow: installing a plugin would silently replace a same-named skill the
user wrote themselves, and uninstalling it would carry that skill away.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.paths import user_skills_dir
from jarvis.marketplace.agent_plugins_loader import BundledSkill

logger = logging.getLogger(__name__)

# Names the directory as plugin-owned. Dot-prefixed so the skill loader,
# which looks for SKILL.md, ignores it.
MARKER_NAME = ".jarvis-plugin.json"


class SkillOwnershipError(RuntimeError):
    """A skill directory exists and belongs to someone else."""


@dataclass(frozen=True, slots=True)
class SkillWriteResult:
    written: list[str]
    # Skills skipped because a directory of that name is not ours to touch.
    conflicts: list[str]


def _skill_dir(name: str) -> Path:
    """The target directory for ``name``, guarded against escaping the root.

    ``pathlib``'s ``/`` DISCARDS the left side when the right is absolute, so
    an unchecked name could write anywhere the process can. The name has
    already passed the spec rules in `agent_plugins_loader`; this is the
    second, local check that keeps the guarantee next to the write.
    """
    base = user_skills_dir().resolve()
    target = (base / name).resolve()
    if target.parent != base:
        raise SkillOwnershipError(f"skill name {name!r} resolves outside the skills directory")
    return target


def owner_of(directory: Path) -> str | None:
    """The plugin id that owns ``directory``, or None when unowned."""
    marker = directory / MARKER_NAME
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # An unreadable marker means we cannot prove ownership. Treat the
        # directory as somebody else's — refusing to touch it is the safe
        # direction; the alternative deletes data on a corrupt byte.
        logger.warning("unreadable plugin marker in %s — treating as unowned", directory)
        return None
    owner = data.get("plugin")
    return str(owner) if owner else None


def write_bundled_skills(
    plugin_id: str, skills: list[BundledSkill], *, version: str | None = None
) -> SkillWriteResult:
    """Materialise ``skills`` under the user's skills directory.

    Re-installing or updating the same plugin overwrites its own skills.
    A directory owned by anything else is left untouched and reported.
    """
    written: list[str] = []
    conflicts: list[str] = []
    for skill in skills:
        target = _skill_dir(skill.name)
        if target.exists() and owner_of(target) != plugin_id:
            conflicts.append(skill.name)
            continue
        target.mkdir(parents=True, exist_ok=True)

        # Marker first: a crash between the two writes must not leave a skill
        # nobody claims. An orphaned marker on an empty directory is inert.
        marker = target / MARKER_NAME
        marker_tmp = marker.with_suffix(".json.tmp")
        marker_tmp.write_text(
            json.dumps({"plugin": plugin_id, "version": version}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        marker_tmp.replace(marker)

        skill_md = target / "SKILL.md"
        tmp = skill_md.with_suffix(".md.tmp")
        tmp.write_text(skill.skill_md, encoding="utf-8")
        tmp.replace(skill_md)
        written.append(skill.name)

    return SkillWriteResult(written=written, conflicts=conflicts)


def remove_bundled_skills(plugin_id: str, names: list[str]) -> list[str]:
    """Delete the skill directories ``plugin_id`` owns. Returns what went."""
    removed: list[str] = []
    for name in names:
        try:
            target = _skill_dir(name)
        except SkillOwnershipError as exc:
            logger.warning("skipping bundled skill removal: %s", exc)
            continue
        if not target.is_dir() or owner_of(target) != plugin_id:
            continue
        try:
            shutil.rmtree(target)
        except OSError as exc:
            # A locked file costs one leftover directory, never the uninstall.
            logger.warning("could not remove bundled skill %s: %s", name, exc)
            continue
        removed.append(name)
    return removed


__all__ = [
    "MARKER_NAME",
    "SkillOwnershipError",
    "SkillWriteResult",
    "owner_of",
    "remove_bundled_skills",
    "write_bundled_skills",
]

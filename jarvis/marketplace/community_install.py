"""Persist community plugin installs into the user's ``data/`` catalog.

"Install" was always designed to be a file write (public-marketplace-analysis
§1): the catalog loader keeps override entries the seed does not know, so a
community plugin becomes a first-class store card by appending its converted
`PluginSpec` to ``data/plugin_catalog.json``. Uninstall removes exactly that
entry — seed plugins are structurally out of reach here because the loader
re-adds them from the package on every merge.

Writes are atomic (tmp + replace) and go through one place so the
read-modify-write cycle stays on the event loop thread without interleaved
awaits — the same discipline `config_writer` applies to jarvis.toml.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from jarvis.marketplace import catalog_data
from jarvis.marketplace.catalog import PluginSpec

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def seed_plugin_ids() -> frozenset[str]:
    """Ids shipped in the package seed — reserved against community installs."""
    try:
        raw = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8-sig"))
        return frozenset(
            str(plugin["id"])
            for plugin in raw.get("plugins", [])
            if isinstance(plugin, dict) and plugin.get("id")
        )
    except (OSError, ValueError):
        # An unreadable seed already degrades catalog loading; the guard then
        # fails open and the loader's own validation still applies.
        logger.warning("seed catalog unreadable — reserved-id guard is empty")
        return frozenset()


def _read_override() -> dict[str, Any]:
    """The user's override document, or a fresh skeleton matching the seed."""
    try:
        raw = json.loads(catalog_data._DEFAULT_CATALOG_PATH.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict) and isinstance(raw.get("plugins"), list):
            return raw
        logger.warning("plugin catalog override has no plugins list — rebuilding")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        # Never silently discard a user's catalog: an unreadable override is
        # surfaced to the caller instead of being overwritten with a skeleton.
        raise RuntimeError(
            f"data/plugin_catalog.json exists but cannot be parsed ({exc}); "
            "fix or remove it before installing community plugins"
        ) from exc
    version, schema_version = 1, "2026-05-09"
    try:
        seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8-sig"))
        version = int(seed.get("version", version))
        schema_version = str(seed.get("schema_version", schema_version))
    except (OSError, ValueError):
        logger.warning("seed catalog unreadable — using skeleton catalog header")
    return {"version": version, "schema_version": schema_version, "plugins": []}


def _write_override(document: dict[str, Any]) -> None:
    catalog_data._DEFAULT_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = catalog_data._DEFAULT_CATALOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(catalog_data._DEFAULT_CATALOG_PATH)
    catalog_data.clear_cache()


def install_plugin_spec(spec: PluginSpec) -> None:
    """Add (or update) a community entry in the override catalog."""
    document = _read_override()
    document["plugins"] = [
        plugin
        for plugin in document["plugins"]
        if not (isinstance(plugin, dict) and plugin.get("id") == spec.id)
    ]
    document["plugins"].append(spec.model_dump(mode="json"))
    _write_override(document)


def remove_community_plugin(plugin_id: str) -> bool:
    """Drop a community entry from the override. Returns True when removed."""
    document = _read_override()
    before = len(document["plugins"])
    document["plugins"] = [
        plugin
        for plugin in document["plugins"]
        if not (isinstance(plugin, dict) and plugin.get("id") == plugin_id)
    ]
    if len(document["plugins"]) == before:
        return False
    _write_override(document)
    return True


def install_community_skill(name: str, skill_md: str) -> Path:
    """Write a STANDALONE community skill from the index, and return its path.

    Standalone skills carry no ownership marker: the user owns them outright
    and removes them from the Skills view like any other. What they do share
    with a plugin's bundled skills is the rule set — the same
    `validate_bundled_skills` call — because a rule that only applies to one
    of the two publishing routes is a rule an author simply routes around.

    Refuses to overwrite a directory a PLUGIN owns: a standalone submission
    must not be able to take over a skill that arrives with, and disappears
    with, an installed plugin.
    """
    from jarvis.core.paths import user_skills_dir
    from jarvis.marketplace.agent_plugins_loader import validate_bundled_skills
    from jarvis.marketplace.bundled_skills import owner_of

    # Raises AgentPluginError on a bad name, missing frontmatter, an
    # oversized file, or a self-declared risk_policy.
    skills = validate_bundled_skills(
        [{"name": name, "skill_md": skill_md}], plugin_name=name
    )
    skill = skills[0]

    base = user_skills_dir().resolve()
    target = (base / skill.name).resolve()
    if target.parent != base:
        raise ValueError(f"skill name {skill.name!r} resolves outside the skills directory")

    owner = owner_of(target) if target.exists() else None
    if owner is not None:
        raise ValueError(
            f"a skill named {skill.name!r} was installed by the {owner!r} plugin — "
            "remove that plugin instead of overwriting its skill"
        )

    target.mkdir(parents=True, exist_ok=True)
    skill_file = target / "SKILL.md"
    tmp = skill_file.with_suffix(".md.tmp")
    tmp.write_text(skill.skill_md, encoding="utf-8")
    tmp.replace(skill_file)
    return skill_file


__all__ = [
    "install_community_skill",
    "install_plugin_spec",
    "remove_community_plugin",
    "seed_plugin_ids",
]

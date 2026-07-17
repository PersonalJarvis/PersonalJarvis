"""Every SkillRegistry (re)load mirrors the paired-skill capability surface.

Forensic origin (voice session 2026-07-17 10:32): since the serve-first fast
boot the registry's disk scan is deferred, so the one-shot paired-capability
registration at ``set_skill_context`` time ran against an EMPTY skill list
("registered 0 paired capabilities" on every boot) and nothing ever repaired
the capability surface after the scan landed. The evidence gate then found no
capability for the email/calendar domains and spoke its deterministic
"no access" refusal although the plugins were connected and healthy.

Contract under test:
  1. ``reload_sync()`` registers the paired capability of every live paired
     skill it discovered — the deferred boot scan repairs the surface.
  2. A later reload WITHDRAWS orphans (skill removed since the last load),
     so hot reload converges instead of only ever growing the set.
  3. Non-paired skills never contribute a capability.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from jarvis.core.capabilities import _reset_registry_for_tests, get_registry
from jarvis.skills.plugin_coupling import PAIRED_CAP_PREFIX
from jarvis.skills.registry import SkillRegistry


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "user_skills"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _isolated_capability_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _write_paired_skill(root: Path, name: str, plugin_id: str) -> None:
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: test paired skill\n"
        f"plugin_id: {plugin_id}\n"
        "intent_verbs: [lies, check]\n"  # i18n-allow: intent vocabulary under test
        "intent_objects: [postfach, inbox]\n"  # i18n-allow: intent vocabulary under test
        "risk_policy:\n"
        "  default_tier: ask\n"
        "---\n\n## Body\n",
        encoding="utf-8",
    )


def _write_plain_skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: plain skill without pairing\n"
        "---\n\n## Body\n",
        encoding="utf-8",
    )


def _paired_cap_ids() -> set[str]:
    return {
        cap.id
        for cap in get_registry().all()
        if cap.id.startswith(PAIRED_CAP_PREFIX)
    }


def test_reload_sync_registers_paired_caps_after_deferred_scan(
    skills_root: Path,
) -> None:
    _write_paired_skill(skills_root, "plugin-gmail", "gmail")
    _write_plain_skill(skills_root, "morning-routine")
    reg = SkillRegistry(skills_root, bus=None)

    # Boot shape: the capability surface is still empty before the deferred scan…
    assert _paired_cap_ids() == set()

    reg.reload_sync()

    # …and the scan itself repairs it — no second registration pass needed.
    assert _paired_cap_ids() == {f"{PAIRED_CAP_PREFIX}gmail"}


def test_reload_withdraws_orphaned_paired_caps(skills_root: Path) -> None:
    _write_paired_skill(skills_root, "plugin-gmail", "gmail")
    _write_paired_skill(skills_root, "plugin-github", "github")
    reg = SkillRegistry(skills_root, bus=None)
    reg.reload_sync()
    assert _paired_cap_ids() == {
        f"{PAIRED_CAP_PREFIX}gmail",
        f"{PAIRED_CAP_PREFIX}github",
    }

    shutil.rmtree(skills_root / "plugin-github")
    reg.reload_sync()

    assert _paired_cap_ids() == {f"{PAIRED_CAP_PREFIX}gmail"}


def test_resolve_intent_reaches_the_paired_plugin_after_scan(
    skills_root: Path,
) -> None:
    _write_paired_skill(skills_root, "plugin-gmail", "gmail")
    reg = SkillRegistry(skills_root, bus=None)
    reg.reload_sync()

    # i18n-allow: German utterance under test
    cap = get_registry().resolve_intent("check mein Postfach")
    assert cap is not None and cap.id == f"{PAIRED_CAP_PREFIX}gmail"

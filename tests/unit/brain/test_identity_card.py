"""Guards for the ambient identity card (jarvis/brain/identity_card.py).

What the card promises: a deterministic, model-free, hard-capped distillation
of who the user is, rebuilt only when its sources change, absent and silent
when no profile exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain import identity_card as ic

PROFILE = """---
type: entity
entity_kind: person
slug: nova-user
aliases: [Nova User, the user]
---

# Nova User

## Summary

Builds a voice assistant on her own; ships in the evenings.

## Identity

- Lives in **Lisbon**, originally from [[Places/Porto|Porto]]
- Speaks Portuguese and English

## Preferences

- Short answers, no filler
- Metric units

## Work style

- Deep work before noon

## Values

- Owns her own data

## Decisions

- 2026-01-04: chose SQLite over Postgres

## Sources

- session-2026-01-04
"""


CORE_MEMORY = {
    "persona": {"name": "Assistant", "role": "orchestrator"},
    "user_facts": {"general": ["Allergic to peanuts"], "work": ["Runs a solo studio"]},
    "preferences": {"language_default": "en"},
    "current_projects": {"Ambient": "personal knowledge in the prompt"},
}


class _FakeConfig:
    """Minimal duck-typed config (fakes, not mocks)."""

    class _WikiContext:
        def __init__(self, core_memory_path: Path | None) -> None:
            self.core_memory_path = str(core_memory_path) if core_memory_path else None
            self.identity_card = True

    class _WikiIntegration:
        def __init__(self, vault_root: Path) -> None:
            self.vault_root = vault_root

    def __init__(self, *, vault_root: Path, core_memory_path: Path | None = None) -> None:
        self.wiki_integration = self._WikiIntegration(vault_root)
        self.wiki_context = self._WikiContext(core_memory_path)


def _write_vault_profile(root: Path, slug: str, body: str) -> Path:
    page = root / "entities" / f"{slug}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(body, encoding="utf-8")
    return page


@pytest.fixture(autouse=True)
def _isolated_process_cache() -> Any:
    ic.reset_identity_card_cache()
    yield
    ic.reset_identity_card_cache()


# ---------------------------------------------------------------------------
# Distillation — deterministic, capped, flattened
# ---------------------------------------------------------------------------


def test_profile_sections_become_a_card() -> None:
    card = ic.distill_identity_card(profile_markdown=PROFILE)

    assert "- Name: Nova User" in card
    assert "Summary:" in card
    assert "Lisbon" in card
    assert "Short answers" in card
    # Episodic bookkeeping is not identity.
    assert "SQLite" not in card
    assert "session-2026-01-04" not in card


def test_markdown_and_wikilinks_are_flattened() -> None:
    card = ic.distill_identity_card(profile_markdown=PROFILE)

    assert "[[" not in card and "]]" not in card
    assert "**" not in card
    assert "Porto" in card, "a wikilink must keep its display text"


def test_card_never_exceeds_the_cap() -> None:
    fat = "# Someone\n\n## Summary\n\n" + "\n".join(
        f"- fact number {n} that runs on and on and on" for n in range(200)
    )
    card = ic.distill_identity_card(profile_markdown=fat)

    assert 0 < len(card) <= ic.MAX_IDENTITY_CARD_CHARS
    assert not card.endswith(";"), "a truncated group must not end mid-list"


def test_a_smaller_explicit_budget_is_honoured() -> None:
    card = ic.distill_identity_card(profile_markdown=PROFILE, max_chars=80)
    assert len(card) <= 80


def test_distillation_is_deterministic() -> None:
    first = ic.distill_identity_card(profile_markdown=PROFILE, core_memory=CORE_MEMORY)
    second = ic.distill_identity_card(profile_markdown=PROFILE, core_memory=CORE_MEMORY)
    assert first == second


def test_no_sources_yield_no_card_and_no_block() -> None:
    assert ic.distill_identity_card() == ""
    assert ic.distill_identity_card(profile_markdown="   \n\n") == ""
    assert ic.render_identity_block("") == ""


def test_core_memory_alone_fills_the_card() -> None:
    """The fresh-install path: no wiki, still an honest card."""
    card = ic.distill_identity_card(core_memory=CORE_MEMORY)

    assert "Allergic to peanuts" in card
    assert "Runs a solo studio" in card
    assert "Ambient" in card
    # The persona describes the ASSISTANT, never the user.
    assert "orchestrator" not in card


def test_profile_spends_the_budget_before_core_memory() -> None:
    card = ic.distill_identity_card(
        profile_markdown=PROFILE, core_memory=CORE_MEMORY, max_chars=120
    )
    assert "Nova User" in card
    assert "Allergic to peanuts" not in card


def test_restated_facts_are_not_repeated() -> None:
    profile = "# X\n\n## Identity\n\n- Runs a solo studio in Lisbon\n"
    card = ic.distill_identity_card(profile_markdown=profile, core_memory=CORE_MEMORY)
    assert card.count("Runs a solo studio") == 1


def test_section_priority_is_a_subset_of_the_wiki_profile_schema() -> None:
    """Anti-drift: a renamed profile section must fail here, not silently
    disappear from the card."""
    from jarvis.memory.wiki.profile import PROFILE_SECTIONS

    assert set(ic.PROFILE_SECTION_PRIORITY) <= set(PROFILE_SECTIONS)


def test_malformed_sources_never_raise() -> None:
    for junk in ["---\nunterminated frontmatter", "## \n\n#", "\x00\x01", "#" * 500]:
        assert isinstance(ic.distill_identity_card(profile_markdown=junk), str)
    assert ic.distill_identity_card(core_memory={"user_facts": "not a dict"}) != ""
    assert isinstance(ic.distill_identity_card(core_memory={"preferences": 7}), str)


# ---------------------------------------------------------------------------
# Framing — the standing silence mandate
# ---------------------------------------------------------------------------


def test_block_carries_the_silence_mandate() -> None:
    block = ic.render_identity_block("- Name: Nova User")
    lowered = block.lower()

    assert "- Name: Nova User" in block
    assert "silence beats a personal fact nobody asked for" in lowered
    assert "never volunteer a personal detail the question did not ask for" in lowered
    assert "never read this block out" in lowered


# ---------------------------------------------------------------------------
# Cache — rebuild only on a source change, survive a restart
# ---------------------------------------------------------------------------


def _cache(tmp_path: Path, *, seed: bool = True, **kwargs: Any) -> ic.IdentityCardCache:
    vault = tmp_path / "vault"
    core = tmp_path / "core_memory.json"
    if seed:
        core.write_text(json.dumps(CORE_MEMORY), encoding="utf-8")
        _write_vault_profile(vault, "user", PROFILE)
    kwargs.setdefault("cache_path", tmp_path / "identity_card.json")
    kwargs.setdefault("recheck_interval_s", 0.0)
    return ic.IdentityCardCache(
        config=_FakeConfig(vault_root=vault, core_memory_path=core), **kwargs
    )


def test_cache_reads_the_vault_profile_and_core_memory(tmp_path: Path) -> None:
    card = _cache(tmp_path).card()

    assert "Nova User" in card.text
    assert card.sources == ("vault:entities/user.md", "core_memory")
    assert len(card.text) <= ic.MAX_IDENTITY_CARD_CHARS


def test_cache_rebuilds_only_when_the_source_hash_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds: list[int] = []
    real = ic.distill_identity_card

    def counting(**kwargs: Any) -> str:
        builds.append(1)
        return real(**kwargs)

    monkeypatch.setattr(ic, "distill_identity_card", counting)

    cache = _cache(tmp_path)
    first = cache.text()
    assert len(builds) == 1

    # Re-checking unchanged sources must not rebuild, and must not move a byte
    # (the cached system-prompt prefix depends on that).
    for _ in range(3):
        assert cache.text() == first
    assert len(builds) == 1

    (tmp_path / "vault" / "entities" / "user.md").write_text(
        PROFILE.replace("Lisbon", "Madrid"), encoding="utf-8"
    )
    assert "Madrid" in cache.text()
    assert len(builds) == 2


def test_a_held_card_is_served_without_touching_the_sources(tmp_path: Path) -> None:
    cache = _cache(tmp_path, recheck_interval_s=3600.0, clock=lambda: 0.0)
    first = cache.text()

    (tmp_path / "vault" / "entities" / "user.md").unlink()
    (tmp_path / "core_memory.json").unlink()

    assert cache.text() == first, "the recheck interval keeps the turn path IO-free"


def test_the_disk_cache_survives_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _cache(tmp_path).text()
    assert first

    def explode(**_: Any) -> str:
        raise AssertionError("a restart must not have to rebuild an unchanged card")

    monkeypatch.setattr(ic, "distill_identity_card", explode)
    assert _cache(tmp_path, seed=False).text() == first


def test_a_changed_source_beats_a_stale_disk_cache(tmp_path: Path) -> None:
    _cache(tmp_path).text()
    (tmp_path / "vault" / "entities" / "user.md").write_text(
        PROFILE.replace("Nova User", "Other Person"), encoding="utf-8"
    )
    assert "Other Person" in _cache(tmp_path, seed=False).text()


def test_a_corrupt_disk_cache_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "identity_card.json").write_text("{not json", encoding="utf-8")
    assert "Nova User" in _cache(tmp_path).text()


def test_no_profile_and_no_core_memory_stay_silent(tmp_path: Path) -> None:
    cache = ic.IdentityCardCache(
        config=_FakeConfig(
            vault_root=tmp_path / "empty-vault",
            core_memory_path=tmp_path / "missing.json",
        ),
        cache_path=tmp_path / "identity_card.json",
        recheck_interval_s=0.0,
    )
    assert cache.text() == ""
    assert cache.block() == ""


def test_an_unwritable_cache_path_degrades_silently(tmp_path: Path) -> None:
    """Read-only install / headless container: memory-only, never a crash."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = _cache(tmp_path, cache_path=blocked / "identity_card.json")
    assert "Nova User" in cache.text()


# ---------------------------------------------------------------------------
# Source resolution — UltraWiki seam is capability-probed, never assumed
# ---------------------------------------------------------------------------


class _FakeUltraService:
    def __init__(self, markdown: str | None) -> None:
        self._markdown = markdown
        self.calls = 0

    def user_profile_markdown(self) -> str:
        self.calls += 1
        if self._markdown is None:
            raise RuntimeError("seam is broken")
        return self._markdown


class _SeamlessUltraService:
    """An UltraWiki service that has no profile seam yet — today's reality."""


def _patch_ultra(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    import jarvis.ultrawiki.service as uw

    monkeypatch.setattr(uw, "active_search_service", lambda: service)


def test_ultrawiki_profile_wins_when_the_seam_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeUltraService("# Ultra Person\n\n## Identity\n\n- From the store\n")
    _patch_ultra(monkeypatch, service)

    card = _cache(tmp_path).card()

    assert "Ultra Person" in card.text
    assert "Nova User" not in card.text
    assert "ultrawiki:profile" in card.sources
    assert service.calls == 1


@pytest.mark.parametrize(
    "service",
    [None, _SeamlessUltraService(), _FakeUltraService(None), _FakeUltraService("")],
)
def test_absent_or_broken_ultrawiki_seam_falls_back_to_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service: Any
) -> None:
    _patch_ultra(monkeypatch, service)
    assert "Nova User" in _cache(tmp_path).text()


def test_an_async_seam_is_refused_rather_than_awaited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _AsyncSeam:
        async def user_profile_markdown(self) -> str:  # pragma: no cover - never awaited
            return "# Nope"

    _patch_ultra(monkeypatch, _AsyncSeam())
    assert "Nova User" in _cache(tmp_path).text()


def test_an_unimportable_ultrawiki_is_simply_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("jarvis.ultrawiki"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert "Nova User" in _cache(tmp_path).text()


# ---------------------------------------------------------------------------
# Process-wide accessor + the config off switch
# ---------------------------------------------------------------------------


def test_block_accessor_is_off_when_configured_off(tmp_path: Path) -> None:
    config = _FakeConfig(vault_root=tmp_path / "vault")
    config.wiki_context.identity_card = False
    assert ic.identity_card_block(config) == ""


def test_accessors_never_raise_on_a_hostile_config() -> None:
    class Hostile:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(name)

    assert ic.identity_card_block(Hostile()) == ""
    ic.reset_identity_card_cache()
    assert isinstance(ic.identity_card_text(Hostile()), str)

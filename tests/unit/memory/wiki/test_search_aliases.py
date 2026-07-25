"""Guards for the search-alias language bridge.

Fully offline: no provider, no credential, no network. The model call is
replaced by a fake so the parsing, merging, degradation and backfill logic is
what gets tested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.memory.frontmatter import parse_frontmatter
from jarvis.memory.wiki import search_aliases as sa

PAGE = """---
type: entity
slug: nvidia-geforce-rtx-5070-ti
---

# NVIDIA GeForce RTX 5070 Ti

## Summary
The user's GPU, used for local inference workloads.
"""

PAGE_WITH_ALIASES = """---
type: entity
slug: ruben
search_aliases: [Handgepflegt]
---

# Ruben

## Facts
- Lives in San Francisco.
"""


class FakeCfg:
    class brain:  # noqa: N801 — mirrors the config attribute path
        primary = "fake"
        reply_language = "de"


class FakeRegistry:
    def available(self) -> list[str]:
        return ["fake"]


# ---------------------------------------------------------------------------
# Language targeting
# ---------------------------------------------------------------------------


def test_pinned_reply_language_drives_the_bridge() -> None:
    assert sa.target_languages(FakeCfg()) == ("de", "en")


def test_english_user_gets_no_redundant_second_language() -> None:
    class EnglishCfg:
        class brain:  # noqa: N801
            primary = "fake"
            reply_language = "en"

    assert sa.target_languages(EnglishCfg()) == ("en",)


def test_auto_falls_back_to_the_default_locale() -> None:
    class AutoCfg:
        class brain:  # noqa: N801
            primary = "fake"
            reply_language = "auto"

    assert sa.target_languages(AutoCfg()) == ("en",)


# ---------------------------------------------------------------------------
# Response parsing — a model answer is untrusted input
# ---------------------------------------------------------------------------


def test_parses_a_clean_list() -> None:
    assert sa.parse_alias_response("Grafikkarte\nGraka\nGPU") == [
        "Grafikkarte",
        "Graka",
        "GPU",
    ]


def test_tolerates_bullets_numbering_and_quotes() -> None:
    messy = '- Grafikkarte\n2) "Graka"\n* GPU\n'
    assert sa.parse_alias_response(messy) == ["Grafikkarte", "Graka", "GPU"]


def test_rejects_sentences_and_markup() -> None:
    noisy = (
        "Here are the search terms you asked for:\n"
        "Grafikkarte\n"
        "This page is about a graphics card used for local inference work\n"
        "**GPU**\n"
    )
    assert sa.parse_alias_response(noisy) == ["Grafikkarte"]


def test_drops_terms_already_in_the_title() -> None:
    """A word in the title is already indexed at a HIGHER weight."""
    out = sa.parse_alias_response("NVIDIA\nGrafikkarte", title="NVIDIA GeForce RTX")
    assert out == ["Grafikkarte"]


def test_drops_pronouns_that_would_make_a_page_a_magnet() -> None:
    """Observed on the first real run: the user's own entity page was offered
    "ich"/"me"/"myself" — words present in almost every personal question."""
    out = sa.parse_alias_response("ich\nme\nmyself\nBesitzer\nProfil")
    assert out == ["Besitzer", "Profil"]


def test_a_generic_but_identifying_word_survives() -> None:
    """The pronoun filter must not become a general "sounds vague" filter —
    "Auto" genuinely identifies a car page."""
    assert "Auto" in sa.parse_alias_response("Auto\nSportwagen")


def test_caps_the_alias_count() -> None:
    many = "\n".join(f"term{i}" for i in range(50))
    assert len(sa.parse_alias_response(many)) == sa.MAX_ALIASES


def test_empty_and_garbage_responses_are_safe() -> None:
    assert sa.parse_alias_response("") == []
    assert sa.parse_alias_response("\n\n   \n") == []


# ---------------------------------------------------------------------------
# Merging — never destroy what a human curated
# ---------------------------------------------------------------------------


def test_merge_keeps_handwritten_aliases_first() -> None:
    assert sa.merge_aliases(["Handgepflegt"], ["Grafikkarte"]) == [
        "Handgepflegt",
        "Grafikkarte",
    ]


def test_merge_deduplicates_case_insensitively() -> None:
    assert sa.merge_aliases(["GPU"], ["gpu", "Graka"]) == ["GPU", "Graka"]


def test_merge_handles_a_missing_or_odd_existing_field() -> None:
    assert sa.merge_aliases(None, ["a"]) == ["a"]
    assert sa.merge_aliases("not-a-list", ["a"]) == ["a"]


# ---------------------------------------------------------------------------
# Degradation — a keyless install must still write pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_reachable_provider_yields_no_aliases_and_no_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3 universality: without a credential the page is simply as findable
    as it is today — never an exception on the write path."""
    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.credential_ready_wiki_providers",
        lambda **_: set(),
    )
    out = await sa.generate_aliases(
        title="X", body="y", cfg=FakeCfg(), registry=FakeRegistry()
    )
    assert out == []


@pytest.mark.asyncio
async def test_an_exhausted_chain_yields_no_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def dead_chain(**_: Any) -> None:
        return None

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.credential_ready_wiki_providers",
        lambda **_: {"fake"},
    )
    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.complete_with_fallback", dead_chain
    )
    out = await sa.generate_aliases(
        title="X", body="y", cfg=FakeCfg(), registry=FakeRegistry()
    )
    assert out == []


@pytest.mark.asyncio
async def test_a_raising_provider_never_escapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(**_: Any) -> None:
        raise RuntimeError("provider on fire")

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.credential_ready_wiki_providers",
        lambda **_: {"fake"},
    )
    monkeypatch.setattr("jarvis.memory.wiki.provider_chain.complete_with_fallback", boom)
    out = await sa.generate_aliases(
        title="X", body="y", cfg=FakeCfg(), registry=FakeRegistry()
    )
    assert out == []


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch):
    """Replace the whole generator with a deterministic stand-in."""

    async def fake_generate(*, title: str, body: str, **_: Any) -> list[str]:
        return ["Grafikkarte", "Graka"]

    monkeypatch.setattr(sa, "generate_aliases", fake_generate)


@pytest.mark.asyncio
async def test_dry_run_reports_but_writes_nothing(
    tmp_path: Path, fake_model: None
) -> None:
    page = tmp_path / "entities" / "gpu.md"
    page.parent.mkdir(parents=True)
    page.write_text(PAGE, encoding="utf-8")
    before = page.read_text(encoding="utf-8")

    summary = await sa.backfill_vault(
        vault_root=tmp_path, cfg=FakeCfg(), registry=FakeRegistry(), dry_run=True
    )

    assert summary["updated"] == 1
    assert summary["dry_run"] is True
    assert page.read_text(encoding="utf-8") == before, "dry run must not write"


@pytest.mark.asyncio
async def test_apply_writes_the_field_and_keeps_the_body(
    tmp_path: Path, fake_model: None
) -> None:
    page = tmp_path / "entities" / "gpu.md"
    page.parent.mkdir(parents=True)
    page.write_text(PAGE, encoding="utf-8")

    await sa.backfill_vault(
        vault_root=tmp_path, cfg=FakeCfg(), registry=FakeRegistry(), dry_run=False
    )

    meta, body = parse_frontmatter(page.read_text(encoding="utf-8"))
    assert meta[sa.ALIAS_FIELD] == ["Grafikkarte", "Graka"]
    assert meta["slug"] == "nvidia-geforce-rtx-5070-ti", "existing keys survive"
    assert "local inference workloads" in body, "body untouched"


@pytest.mark.asyncio
async def test_pages_that_already_have_aliases_are_left_alone(
    tmp_path: Path, fake_model: None
) -> None:
    page = tmp_path / "entities" / "ruben.md"
    page.parent.mkdir(parents=True)
    page.write_text(PAGE_WITH_ALIASES, encoding="utf-8")

    summary = await sa.backfill_vault(
        vault_root=tmp_path, cfg=FakeCfg(), registry=FakeRegistry(), dry_run=False
    )

    assert summary["skipped_existing"] == 1
    meta, _ = parse_frontmatter(page.read_text(encoding="utf-8"))
    assert meta[sa.ALIAS_FIELD] == ["Handgepflegt"], "curated aliases preserved"


@pytest.mark.asyncio
async def test_overwrite_merges_instead_of_replacing(
    tmp_path: Path, fake_model: None
) -> None:
    page = tmp_path / "entities" / "ruben.md"
    page.parent.mkdir(parents=True)
    page.write_text(PAGE_WITH_ALIASES, encoding="utf-8")

    await sa.backfill_vault(
        vault_root=tmp_path,
        cfg=FakeCfg(),
        registry=FakeRegistry(),
        dry_run=False,
        overwrite=True,
    )

    meta, _ = parse_frontmatter(page.read_text(encoding="utf-8"))
    assert meta[sa.ALIAS_FIELD][0] == "Handgepflegt", "human term still first"
    assert "Grafikkarte" in meta[sa.ALIAS_FIELD]


@pytest.mark.asyncio
async def test_one_unreadable_page_does_not_abort_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_model: None
) -> None:
    (tmp_path / "entities").mkdir(parents=True)
    (tmp_path / "entities" / "good.md").write_text(PAGE, encoding="utf-8")
    (tmp_path / "entities" / "bad.md").write_text(PAGE, encoding="utf-8")

    real_parse = sa.__dict__.get("parse_frontmatter")  # not imported at module level

    def exploding_parse(text: str):
        if "bad-marker" in text:
            raise ValueError("corrupt frontmatter")
        from jarvis.memory.frontmatter import parse_frontmatter as p

        return p(text)

    (tmp_path / "entities" / "bad.md").write_text(
        PAGE.replace("slug:", "slug: bad-marker #"), encoding="utf-8"
    )
    monkeypatch.setattr("jarvis.memory.frontmatter.parse_frontmatter", exploding_parse)

    summary = await sa.backfill_vault(
        vault_root=tmp_path, cfg=FakeCfg(), registry=FakeRegistry(), dry_run=True
    )

    assert summary["scanned"] == 2
    assert summary["failed"] + summary["updated"] == 2
    assert real_parse is None  # sanity: module keeps its import lazy


@pytest.mark.asyncio
async def test_limit_bounds_the_number_of_pages(
    tmp_path: Path, fake_model: None
) -> None:
    (tmp_path / "entities").mkdir(parents=True)
    for i in range(5):
        (tmp_path / "entities" / f"p{i}.md").write_text(PAGE, encoding="utf-8")

    summary = await sa.backfill_vault(
        vault_root=tmp_path,
        cfg=FakeCfg(),
        registry=FakeRegistry(),
        dry_run=True,
        limit=2,
    )
    assert summary["updated"] == 2

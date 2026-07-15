"""Unit tests for DocRegistry."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from jarvis.docs.registry import DocRegistry
from jarvis.docs.schema import DocDiataxis, DocStatus

CONCEPT_MD = """---
title: "Concept: Router"
slug: router-concept
diataxis: explanation
status: active
phase: 5
tags: [brain, routing]
---

# Concept: Router

The main assistant dispatches through a Jarvis-Agent.
"""

HOWTO_MD = """---
title: "How-To: Add a provider"
slug: provider-add
diataxis: howto
status: draft
phase: 4
tags: [brain, plugin]
---

# How-To

Step 1.
"""

LEGACY_MD = """# Phase 1c Test Results

Results.
"""


@pytest.fixture
def doc_root(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "concept-router.md").write_text(CONCEPT_MD, encoding="utf-8")
    (tmp_path / "docs" / "howto-provider.md").write_text(HOWTO_MD, encoding="utf-8")
    (tmp_path / "docs" / "phase1c-test.md").write_text(LEGACY_MD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(doc_root: Path) -> DocRegistry:
    reg = DocRegistry(
        roots=[doc_root / "docs"],
        index_db=doc_root / "index.sqlite",
    )
    reg.reload_sync()
    yield reg
    reg.close()


# ----------------------------------------------------------------------
# Lookup
# ----------------------------------------------------------------------


def test_registry_lists_all_docs(registry: DocRegistry) -> None:
    docs = registry.list()
    assert len(docs) == 3
    slugs = {d.frontmatter.slug for d in docs}
    assert "router-concept" in slugs
    assert "provider-add" in slugs
    # A legacy page receives a synthesized slug.
    assert any("phase1c" in s for s in slugs)


def test_registry_get_by_slug(registry: DocRegistry) -> None:
    doc = registry.get("router-concept")
    assert doc is not None
    assert doc.frontmatter.diataxis == DocDiataxis.EXPLANATION


def test_registry_get_unknown_slug(registry: DocRegistry) -> None:
    assert registry.get("does-not-exist") is None


# ----------------------------------------------------------------------
# Filter
# ----------------------------------------------------------------------


def test_filter_by_diataxis(registry: DocRegistry) -> None:
    howtos = registry.filter(diataxis=DocDiataxis.HOWTO)
    assert len(howtos) == 1
    assert howtos[0].frontmatter.slug == "provider-add"


def test_filter_by_status(registry: DocRegistry) -> None:
    actives = registry.filter(status=DocStatus.ACTIVE)
    # router-concept (active) + Legacy phase1c-test (synth = active)
    slugs = [d.frontmatter.slug for d in actives]
    assert "router-concept" in slugs


def test_filter_by_phase(registry: DocRegistry) -> None:
    phase5 = registry.filter(phase="5")
    assert len(phase5) == 1
    assert phase5[0].frontmatter.slug == "router-concept"


def test_filter_by_tags(registry: DocRegistry) -> None:
    brain = registry.filter(tags=["brain"])
    assert len(brain) == 2
    plugin = registry.filter(tags=["plugin"])
    assert len(plugin) == 1
    assert plugin[0].frontmatter.slug == "provider-add"


def test_filter_combined(registry: DocRegistry) -> None:
    out = registry.filter(diataxis=DocDiataxis.HOWTO, phase="4")
    assert len(out) == 1


# ----------------------------------------------------------------------
# grouped_by_diataxis
# ----------------------------------------------------------------------


def test_grouped_by_diataxis(registry: DocRegistry) -> None:
    groups = registry.grouped_by_diataxis()
    assert DocDiataxis.EXPLANATION in groups
    assert DocDiataxis.HOWTO in groups
    assert DocDiataxis.UNCLASSIFIED in groups
    assert len(groups[DocDiataxis.EXPLANATION]) == 1


# ----------------------------------------------------------------------
# Search-Integration
# ----------------------------------------------------------------------


def test_search_via_registry(registry: DocRegistry) -> None:
    results = registry.search_query("Jarvis-Agent")
    assert len(results) == 1
    assert results[0].slug == "router-concept"


def test_search_with_diataxis_filter(registry: DocRegistry) -> None:
    # Pick a term that appears only in the how-to title.
    results = registry.search_query(
        "provider",
        diataxis=DocDiataxis.HOWTO,
    )
    # The title contains "provider".
    assert any(r.slug == "provider-add" for r in results)


# ----------------------------------------------------------------------
# Reload
# ----------------------------------------------------------------------


def test_reload_picks_up_new_file(registry: DocRegistry, doc_root: Path) -> None:
    new_md = """---
title: "ADR-0099"
slug: adr-0099-test
diataxis: adr
status: active
phase: 6
---

# ADR-0099
"""
    (doc_root / "docs" / "adr-0099.md").write_text(new_md, encoding="utf-8")
    registry.reload_sync()
    docs = registry.list()
    slugs = {d.frontmatter.slug for d in docs}
    assert "adr-0099-test" in slugs


def test_reload_drops_deleted_file(registry: DocRegistry, doc_root: Path) -> None:
    (doc_root / "docs" / "concept-router.md").unlink()
    registry.reload_sync()
    assert registry.get("router-concept") is None


@pytest.mark.asyncio
async def test_async_reload(doc_root: Path) -> None:
    reg = DocRegistry(
        roots=[doc_root / "docs"],
        index_db=doc_root / "index.sqlite",
    )
    await reg.reload()
    assert len(reg.list()) == 3
    reg.close()


@pytest.mark.asyncio
async def test_ensure_loaded_deduplicates_concurrent_first_requests(
    doc_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.docs.registry as registry_module

    calls = 0
    real_discover = registry_module.discover_docs

    def counted_discover(roots: list[Path]):
        nonlocal calls
        calls += 1
        return real_discover(roots)

    monkeypatch.setattr(registry_module, "discover_docs", counted_discover)
    reg = DocRegistry(
        roots=[doc_root / "docs"],
        index_db=doc_root / "index.sqlite",
    )

    assert reg.is_loaded is False
    await asyncio.gather(reg.ensure_loaded(), reg.ensure_loaded())

    assert reg.is_loaded is True
    assert len(reg.list()) == 3
    assert calls == 1
    reg.close()


@pytest.mark.asyncio
async def test_debounce_keeps_newer_reload_deadline(
    doc_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second watcher event must not be cleared by the first timer."""
    reg = DocRegistry(
        roots=[doc_root / "docs"],
        index_db=doc_root / "index.sqlite",
        debounce_ms=20,
    )
    reloads = 0

    async def counted_reload() -> None:
        nonlocal reloads
        reloads += 1

    monkeypatch.setattr(reg, "reload", counted_reload)
    reg._pending_reload = time.monotonic() + 0.020
    first = asyncio.create_task(reg._debounced_reload())
    await asyncio.sleep(0.005)
    reg._pending_reload = time.monotonic() + 0.020
    second = asyncio.create_task(reg._debounced_reload())

    await asyncio.gather(first, second)

    assert reloads == 1
    assert reg._pending_reload is None
    reg.close()


# ----------------------------------------------------------------------
# Bus-Event-Emission
# ----------------------------------------------------------------------


class _StubBus:
    """Minimal bus stub for reload-event tests."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, evt: object) -> None:
        self.events.append(evt)


def test_emit_reloaded_without_loop_does_not_crash(
    doc_root: Path,
) -> None:
    """When no event loop is running, _emit_reloaded should fail silently
    instead of crashing."""
    bus = _StubBus()
    reg = DocRegistry(
        roots=[doc_root / "docs"],
        index_db=doc_root / "index.sqlite",
        bus=bus,
    )
    reg.reload_sync()  # must not crash
    reg.close()


# ----------------------------------------------------------------------
# Multi-Root-Dedup
# ----------------------------------------------------------------------


def test_multi_root_no_double_index(tmp_path: Path) -> None:
    """When two roots overlap, each file appears only once."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "doc.md").write_text(CONCEPT_MD, encoding="utf-8")
    reg = DocRegistry(
        roots=[tmp_path / "a", tmp_path / "a" / "b"],
        index_db=tmp_path / "index.sqlite",
    )
    reg.reload_sync()
    docs = reg.list()
    # Only one doc — no duplicate despite overlapping roots
    assert len(docs) == 1
    reg.close()

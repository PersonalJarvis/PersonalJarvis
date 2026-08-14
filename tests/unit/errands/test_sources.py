"""Pins for the C13 source inventory — the "where could context live?" list.

The inventory is rendered by CODE from what is actually reachable; these pins
hold the degradation promises: a missing tool drops its line, a live errand is
never listed as history, and a machine without apps still yields a usable
block instead of an error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.errands import sources
from jarvis.errands.schema import Errand, ErrandState
from jarvis.errands.sources import render_context_sources
from jarvis.errands.store import ErrandStore


@pytest.fixture
def store(tmp_path: Path) -> ErrandStore:
    return ErrandStore(tmp_path / "errands.db")


@pytest.fixture
def apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "installed_app_names", lambda: ("Spotify", "WhatsApp"))


@pytest.mark.asyncio
async def test_only_wired_tools_are_listed(store: ErrandStore, apps: None) -> None:
    rendered = await render_context_sources(tool_names={"wiki-recall", "browser"}, store=store)
    assert "`wiki-recall`" in rendered
    assert "`browser`" in rendered
    assert "gmail" not in rendered  # not wired -> not promised


@pytest.mark.asyncio
async def test_installed_apps_reach_the_inventory(store: ErrandStore, apps: None) -> None:
    rendered = await render_context_sources(tool_names=set(), store=store)
    assert "WhatsApp" in rendered
    assert "Spotify" in rendered


@pytest.mark.asyncio
async def test_finished_errands_are_history_and_live_ones_are_not(
    store: ErrandStore, apps: None
) -> None:
    await store.save(
        Errand(
            id="done-1",
            goal="book the usual hotel",
            state=ErrandState.COMPLETED,
            outcome="Booked, ref HH-77.",
        )
    )
    await store.save(Errand(id="live-1", goal="find a florist", state=ErrandState.RUNNING))
    rendered = await render_context_sources(tool_names=set(), store=store)
    assert "book the usual hotel" in rendered
    assert "HH-77" in rendered
    assert "find a florist" not in rendered


@pytest.mark.asyncio
async def test_a_bare_machine_yields_an_empty_block_not_an_error(
    store: ErrandStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless server, nothing wired: the gather prompt simply has no SOURCES
    section — the pre-C13 behaviour, never a crash."""
    monkeypatch.setattr(sources, "installed_app_names", lambda: ())
    rendered = await render_context_sources(tool_names=set(), store=store)
    assert rendered == ""


@pytest.mark.asyncio
async def test_a_broken_sweep_never_breaks_the_render(
    store: ErrandStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode() -> tuple[str, ...]:
        raise RuntimeError("registry walk failed")

    monkeypatch.setattr(sources, "installed_app_names", explode)
    rendered = await render_context_sources(tool_names={"wiki-recall"}, store=store)
    assert "`wiki-recall`" in rendered

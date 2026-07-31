"""``wiki-recall`` must answer from whichever memory is actually live.

The reported defect (maintainer, 2026-07-25): UltraWiki mode is on with
thousands of ingested items, and the brain's recall tool still reads the old
vault only — so Jarvis says it knows nothing about material it has indexed.

Pinned here: the mode picks exactly ONE memory (decision D-5), the switch is
invisible to the prompt (same rendered block shape), and no UltraWiki failure
mode may leave the brain with a dead tool.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jarvis.plugins.tool import wiki_recall as mod
from jarvis.plugins.tool.wiki_recall import WikiRecallTool
from jarvis.ultrawiki.service import set_active_service


@dataclass(frozen=True)
class _VaultHit:
    title: str
    snippet: str
    path: Path


class _SpyVaultSearch:
    """Records every vault query so "the vault was not touched" is provable."""

    def __init__(self, root: Path, hits: list[_VaultHit] | None = None) -> None:
        self._root = root
        self._hits = hits or []
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, *, k: int = 5) -> list[_VaultHit]:
        self.queries.append((query, k))
        return list(self._hits[:k])


@dataclass(frozen=True)
class _UltraHit:
    item_id: int = 1
    source_id: str = "obsidian-vault"
    title: str = "Project Phoenix"
    snippet: str = "Phoenix is the ingestion rewrite that started in June."
    permalink: str = "uw://item/1"
    timestamp_utc: str = "2026-07-20T10:00:00Z"
    score: float = 0.031
    matched_by: tuple[str, ...] = field(default_factory=lambda: ("keyword", "vector"))
    context: tuple[str, ...] = ()


class _FakeUltraService:
    def __init__(
        self,
        hits: list[Any] | None = None,
        *,
        error: Exception | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self._hits = hits if hits is not None else [_UltraHit()]
        self._error = error
        self._delay_s = delay_s
        self._store = object()
        self.calls: list[dict[str, Any]] = []

    def _uw_enabled(self) -> bool:
        return True

    async def search(self, **kwargs: Any) -> list[Any]:
        self.calls.append(dict(kwargs))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return list(self._hits)


@pytest.fixture(autouse=True)
def _clean_seam(monkeypatch: pytest.MonkeyPatch):
    from jarvis.core import runtime_refs

    monkeypatch.setattr(runtime_refs, "get_web_app", lambda: None)
    set_active_service(None)
    yield
    set_active_service(None)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    (tmp_path / "10-notes").mkdir()
    (tmp_path / "10-notes" / "phoenix.md").write_text("# Phoenix\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Mode off — byte-identical old behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_off_uses_the_vault_unchanged(vault: Path) -> None:
    search = _SpyVaultSearch(
        vault,
        [_VaultHit("Phoenix", "the ingestion rewrite", vault / "10-notes/phoenix.md")],
    )
    tool = WikiRecallTool(search)

    result = await tool.execute({"query": "phoenix", "k": 3}, ctx=None)

    rel = Path("10-notes/phoenix.md")  # rendered with the host separator
    assert search.queries == [("phoenix", 3)]
    assert result.success is True
    assert result.output == (
        '## Wiki hits for "phoenix"\n'
        f"- **Phoenix** — the ingestion rewrite ({rel})"
    )


@pytest.mark.asyncio
async def test_mode_off_still_reports_a_missing_vault(tmp_path: Path) -> None:
    search = _SpyVaultSearch(tmp_path / "gone")
    tool = WikiRecallTool(search)

    result = await tool.execute({"query": "phoenix"}, ctx=None)

    assert result.success is False
    assert result.error == "vault unavailable"


# ---------------------------------------------------------------------------
# Mode on — UltraWiki owns the answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_on_answers_from_ultrawiki_and_leaves_the_vault_alone(
    vault: Path,
) -> None:
    search = _SpyVaultSearch(vault, [_VaultHit("Stale", "old vault text", vault)])
    ultra = _FakeUltraService()
    set_active_service(ultra)
    tool = WikiRecallTool(search)

    result = await tool.execute({"query": "phoenix", "k": 3}, ctx=None)

    assert search.queries == [], "Ultra mode must not also read the vault (D-5)"
    assert result.success is True
    assert result.output.startswith('## Wiki hits for "phoenix"')
    assert "**Project Phoenix**" in result.output
    assert "ingestion rewrite" in result.output
    assert "obsidian-vault" in result.output  # source label
    assert "2026-07-20T10:00:00Z" in result.output  # timestamp
    assert "uw://item/1" in result.output  # permalink
    assert "old vault text" not in result.output


@pytest.mark.asyncio
async def test_the_explicit_call_passes_no_relevance_floor(vault: Path) -> None:
    """The user asked — Ask-view semantics, gates belong to the injector."""
    ultra = _FakeUltraService()
    set_active_service(ultra)
    tool = WikiRecallTool(_SpyVaultSearch(vault))

    await tool.execute({"query": "phoenix", "k": 4}, ctx=None)

    assert ultra.calls == [
        {
            "query": "phoenix",
            "k": 4,
            "rerank": True,
            "enforce_floor": False,
            "expand_context": True,
        }
    ]


@pytest.mark.asyncio
async def test_empty_ultrawiki_result_is_an_honest_answer_not_a_fallback(
    vault: Path,
) -> None:
    search = _SpyVaultSearch(vault, [_VaultHit("Stale", "old vault text", vault)])
    set_active_service(_FakeUltraService(hits=[]))
    tool = WikiRecallTool(search)

    result = await tool.execute({"query": "phoenix"}, ctx=None)

    assert result.success is True
    assert result.output == 'No wiki matches found for "phoenix".'
    assert search.queries == [], "an empty active memory is an answer, not a failure"


@pytest.mark.asyncio
async def test_a_titleless_item_is_labelled_by_its_source(vault: Path) -> None:
    set_active_service(_FakeUltraService(hits=[_UltraHit(title="")]))
    tool = WikiRecallTool(_SpyVaultSearch(vault))

    result = await tool.execute({"query": "phoenix"}, ctx=None)

    assert "**obsidian-vault**" in result.output


@pytest.mark.asyncio
async def test_a_snippetless_item_falls_back_to_its_expanded_context(
    vault: Path,
) -> None:
    hit = _UltraHit(snippet="", context=("the neighbouring paragraph",))
    set_active_service(_FakeUltraService(hits=[hit]))
    tool = WikiRecallTool(_SpyVaultSearch(vault))

    result = await tool.execute({"query": "phoenix"}, ctx=None)

    assert "the neighbouring paragraph" in result.output


# ---------------------------------------------------------------------------
# Mode on, UltraWiki cannot answer — honest vault fallback, never a dead tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ultrawiki_error_falls_back_to_the_vault(
    vault: Path, caplog: pytest.LogCaptureFixture
) -> None:
    search = _SpyVaultSearch(
        vault, [_VaultHit("Phoenix", "vault text", vault / "10-notes/phoenix.md")]
    )
    set_active_service(_FakeUltraService(error=RuntimeError("store gone")))
    tool = WikiRecallTool(search)

    with caplog.at_level("WARNING", logger=mod.log.name):
        result = await tool.execute({"query": "phoenix"}, ctx=None)

    assert result.success is True
    assert "vault text" in result.output
    assert search.queries == [("phoenix", 5)]
    assert any("store gone" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_a_slow_ultrawiki_falls_back_to_the_vault(
    vault: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(mod, "_ULTRA_TIMEOUT_S", 0.05)
    search = _SpyVaultSearch(
        vault, [_VaultHit("Phoenix", "vault text", vault / "10-notes/phoenix.md")]
    )
    set_active_service(_FakeUltraService(delay_s=1.0))
    tool = WikiRecallTool(search)

    with caplog.at_level("WARNING", logger=mod.log.name):
        result = await tool.execute({"query": "phoenix"}, ctx=None)

    assert result.success is True
    assert "vault text" in result.output
    assert search.queries == [("phoenix", 5)]
    assert any("exceeded" in record.getMessage() for record in caplog.records)

"""Tests for the B5 follow-up wiki tools ``wiki-page-read`` and ``wiki-ingest``.

Hard-negatives:
  1. Both tools MUST be in ROUTER_TOOLS.
  2. ``wiki-page-read`` MUST reject path-traversal attempts ("..", absolute
     paths, anything resolving outside vault_root).
  3. ``wiki-ingest`` MUST surface a clean error when no live curator is
     registered (instead of crashing).
  4. ``wiki-ingest`` MUST NOT claim success when the curator decides the
     content is not salient — the tool must distinguish "no pages touched"
     from "error".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.plugins.tool.wiki_ingest import WikiIngestTool
from jarvis.plugins.tool.wiki_page_read import WikiPageReadTool

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Build a minimal vault with a couple of pages for path-read tests."""
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "sam.md").write_text(
        "# Sam\n\nA person Jarvis knows.\n",
        encoding="utf-8",
    )
    (tmp_path / "people" / "joy.md").write_text(
        "# Joy\n\nAnother person Jarvis knows.\n",
        encoding="utf-8",
    )
    (tmp_path / "schema.md").write_text("# schema\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# wiki-page-read
# ---------------------------------------------------------------------------


def test_wiki_page_read_is_in_router_tools() -> None:
    assert "wiki-page-read" in ROUTER_TOOLS


def test_wiki_page_read_surface(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    assert tool.name == "wiki-page-read"
    assert tool.risk_tier == "safe"
    assert tool.schema["required"] == ["path"]
    assert "path" in tool.schema["properties"]
    assert tool.input_examples, "should advertise at least one example"


@pytest.mark.asyncio
async def test_wiki_page_read_returns_full_content(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    result = await tool.execute({"path": "people/sam.md"}, ctx=None)
    assert result.success is True
    assert "Sam" in result.output
    assert "A person Jarvis knows." in result.output
    # Header prefix tags the source with its vault-relative path.
    assert result.output.startswith("# people/sam.md")


@pytest.mark.asyncio
async def test_wiki_page_read_missing_path_arg(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    result = await tool.execute({}, ctx=None)
    assert result.success is False
    assert "path" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_page_read_missing_file(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    result = await tool.execute({"path": "people/ghost.md"}, ctx=None)
    assert result.success is False
    assert "not found" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_page_read_rejects_traversal(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    result = await tool.execute({"path": "../etc/passwd"}, ctx=None)
    assert result.success is False
    error = (result.error or "").lower()
    assert "vault-relative" in error or "outside" in error


@pytest.mark.asyncio
async def test_wiki_page_read_rejects_absolute_path(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    abs_path = str(vault / "people" / "sam.md")
    result = await tool.execute({"path": abs_path}, ctx=None)
    assert result.success is False


@pytest.mark.asyncio
async def test_wiki_page_read_flags_meta_contract_pages(tmp_path: Path) -> None:
    """A ``type: meta`` page (schema.md) must carry a provenance warning.

    Live incident 2026-07-14: the delegated brain read schema.md (the
    vault's editing contract) and presented its EXAMPLE layout as the
    actual vault contents. The served page must state, deterministically,
    that it is contract — not content.
    """
    tmp_path.joinpath("schema.md").write_text(
        "---\ntype: meta\npurpose: wiki-maintenance-contract\n---\n\n"
        "# Wiki Schema\n\nExample layout: USER.md, people/…\n",
        encoding="utf-8",
    )
    tool = WikiPageReadTool(vault_root=tmp_path)
    result = await tool.execute({"path": "schema.md"}, ctx=None)
    assert result.success is True
    warning = result.output.splitlines()[0].lower()
    assert "system file" in warning or "contract" in warning
    assert "not" in warning  # "... NOT user content" phrasing


@pytest.mark.asyncio
async def test_wiki_page_read_leaves_content_pages_unflagged(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    result = await tool.execute({"path": "people/sam.md"}, ctx=None)
    assert result.success is True
    assert "system file" not in result.output.lower()


@pytest.mark.asyncio
async def test_wiki_page_read_rejects_oversized_file(tmp_path: Path) -> None:
    tmp_path.joinpath("big.md").write_bytes(b"x" * (65 * 1024))
    tool = WikiPageReadTool(vault_root=tmp_path)
    result = await tool.execute({"path": "big.md"}, ctx=None)
    assert result.success is False
    assert "too large" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_page_read_rejects_directory(vault: Path) -> None:
    tool = WikiPageReadTool(vault_root=vault)
    result = await tool.execute({"path": "people"}, ctx=None)
    assert result.success is False


# ---------------------------------------------------------------------------
# wiki-ingest — fake curator double
# ---------------------------------------------------------------------------


@dataclass
class _FakeWriteResult:
    applied: list[Path] = field(default_factory=list)
    skipped_due_to_recent_edit: list[Path] = field(default_factory=list)
    failed_validation: list[Path] = field(default_factory=list)
    blocked_pii: list[Path] = field(default_factory=list)
    backup_path: Path = field(default_factory=Path)


class _FakeCurator:
    """In-memory stand-in for WikiCurator that records ingest calls."""

    def __init__(
        self,
        *,
        result: _FakeWriteResult | None = None,
        raise_on_ingest: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._result = result or _FakeWriteResult()
        self._raise = raise_on_ingest

    async def ingest(self, content: str, source: str) -> _FakeWriteResult:
        self.calls.append((content, source))
        if self._raise is not None:
            raise self._raise
        return self._result


def test_wiki_ingest_is_in_router_tools() -> None:
    assert "wiki-ingest" in ROUTER_TOOLS


def test_wiki_ingest_surface() -> None:
    tool = WikiIngestTool(curator_resolver=lambda: None)
    assert tool.name == "wiki-ingest"
    # H10 (2026-05-17 audit): writes to disk + spawns an LLM curation call,
    # which is the textbook `monitor` tier (executes without prompt but is
    # logged for audit). Was wrongly `safe` until the audit caught it.
    assert tool.risk_tier == "monitor"
    assert tool.schema["required"] == ["text"]
    assert "text" in tool.schema["properties"]
    assert "source" in tool.schema["properties"]


@pytest.mark.asyncio
async def test_wiki_ingest_requires_text() -> None:
    tool = WikiIngestTool(curator_resolver=lambda: _FakeCurator())
    result = await tool.execute({}, ctx=None)
    assert result.success is False
    assert "text" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_rejects_too_short_text() -> None:
    tool = WikiIngestTool(curator_resolver=lambda: _FakeCurator())
    result = await tool.execute({"text": "ja"}, ctx=None)
    assert result.success is False
    assert "too short" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_rejects_too_long_text() -> None:
    tool = WikiIngestTool(curator_resolver=lambda: _FakeCurator())
    huge = "a" * 33_000
    result = await tool.execute({"text": huge}, ctx=None)
    assert result.success is False
    assert "too long" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_no_curator_registered_returns_clean_error() -> None:
    tool = WikiIngestTool(curator_resolver=lambda: None)
    result = await tool.execute(
        {"text": "Joy hat am 14. August Geburtstag."}, ctx=None,
    )
    assert result.success is False
    assert "bootstrap" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_delegates_to_curator_with_default_source() -> None:
    fake = _FakeCurator(
        result=_FakeWriteResult(applied=[Path("people/joy.md")]),
    )
    tool = WikiIngestTool(curator_resolver=lambda: fake)
    result = await tool.execute(
        {"text": "Joy hat am 14. August Geburtstag."}, ctx=None,
    )
    assert result.success is True
    assert len(fake.calls) == 1
    text, source = fake.calls[0]
    assert "Joy" in text
    assert source == "tool:wiki-ingest"
    assert "applied: 1" in result.output
    assert "joy.md" in result.output


@pytest.mark.asyncio
async def test_wiki_ingest_passes_explicit_source() -> None:
    fake = _FakeCurator(result=_FakeWriteResult(applied=[Path("people/joy.md")]))
    tool = WikiIngestTool(curator_resolver=lambda: fake)
    await tool.execute(
        {"text": "Joy hat am 14. August Geburtstag.", "source": "chat:milestone"},
        ctx=None,
    )
    assert fake.calls[0][1] == "chat:milestone"


@pytest.mark.asyncio
async def test_wiki_ingest_reports_not_salient_when_no_updates() -> None:
    """Bug 12/18: a curator no-op is a failure, not a success (fresh-machine
    forensics found the model paraphrasing this as "I stored it" although
    NOTHING was written)."""
    fake = _FakeCurator(result=_FakeWriteResult())   # all three lists empty
    tool = WikiIngestTool(curator_resolver=lambda: fake)
    result = await tool.execute(
        {"text": "Ein belangloser Satz ohne Substanz."}, ctx=None,
    )
    assert result.success is False
    assert "not" in (result.error or "").lower() and "stored" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_does_not_claim_success_for_recent_edit_only() -> None:
    fake = _FakeCurator(
        result=_FakeWriteResult(
            skipped_due_to_recent_edit=[Path("people/joy.md")],
        )
    )
    tool = WikiIngestTool(curator_resolver=lambda: fake)

    result = await tool.execute(
        {"text": "Joy's birthday is August 14th."}, ctx=None,
    )

    assert result.success is False
    assert "recent user edit" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_does_not_claim_success_for_sensitive_content_block() -> None:
    fake = _FakeCurator(
        result=_FakeWriteResult(
            blocked_pii=[Path("people/joy.md")],
        )
    )
    tool = WikiIngestTool(curator_resolver=lambda: fake)

    result = await tool.execute(
        {"text": "A complete statement containing protected data."}, ctx=None,
    )

    assert result.success is False
    assert "sensitive content" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wiki_ingest_surfaces_curator_exception_cleanly() -> None:
    fake = _FakeCurator(raise_on_ingest=RuntimeError("curator boom"))
    tool = WikiIngestTool(curator_resolver=lambda: fake)
    result = await tool.execute(
        {"text": "Joy hat am 14. August Geburtstag."}, ctx=None,
    )
    assert result.success is False
    assert "curator ingest failed" in (result.error or "").lower()
    assert "boom" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Hard-negative: tools must NOT appear in any sub-jarvis tier surface.
# Welle 4 deleted the sub-jarvis tier so the constraint is structural,
# but we re-state it here so a future re-introduction trips this test.
# ---------------------------------------------------------------------------


def test_both_tools_router_tier_only() -> None:
    """Both wiki tools live in ROUTER_TOOLS; sub-jarvis tier was deleted in Welle 4."""
    assert "wiki-page-read" in ROUTER_TOOLS
    assert "wiki-ingest" in ROUTER_TOOLS
    # If a second tier is ever re-introduced, this assertion will not exist
    # but the import-time tier=='router' check in factory.py will fail loudly.

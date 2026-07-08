"""Fresh-machine anchor (spec §7): empty vault, weakest model (i.e. the LLM
never calls a tool -- the deterministic path must not need it), ONE fake
single-family provider -> an explicit wiki command produces a real .md
file, or the pipeline fails honestly (no file, no false "stored").

This is the SAME real stack as ``test_curator_ingest_e2e.py`` /
``test_curator_concurrent_edit.py`` (a real ``WikiCurator`` +
``AtomicWriter`` + ``LogWriter`` + ``VaultIndex`` + ``MarkdownPageRepository``
against a tmp on-disk vault). The two cases differ in HOW the intelligence
layer is faked:

* Success: the curator-LLM is a tiny in-test ``_FakeCuratorLLM`` (house
  rule: fakes, not ``unittest.mock``) that deterministically proposes ONE
  page update -- standing in for the ONE weak/free provider a fresh
  install actually has.
* Failure twin (spec §7 "all providers dead -> honest failure, no file,
  no success phrase"): a REAL ``WikiCuratorLLM`` whose provider fallback
  chain is EXHAUSTED -- every family raises through the real
  ``provider_chain.complete_with_fallback`` code, which returns ``None``,
  so ``propose_updates`` collapses to ``[]``. This drives the genuine
  dead-chain path (a regression in ``provider_chain.py`` would break this
  test), not just the "curator found nothing salient" no-op branch. The
  fake Brain/registry construction is copied from
  ``tests/unit/memory/wiki/test_curator_llm.py``
  (``test_propose_updates_brain_exception_returns_empty``).

Deviation from the task brief's sketch: the brief checks
``vault_root.rglob("*.md")`` truthiness directly. On a REAL vault that is
ambiguous, because ``schema.md`` / ``index.md`` / ``log.md`` are scaffold
files the fixture must create for the real writer / log-writer / vault-index
to function at all -- so "some .md exists" is trivially true even when
nothing was written by the ingest, and "no .md exists" would be trivially
false even when the write correctly did NOT happen (the scaffold is still
there). Both assertions below are rewritten as a before/after delta of the
vault's markdown files, so they actually prove (or disprove) that a page
was added by the ingest call under test.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiCuratorConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.intent import match_wiki_intent
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.vault_index import VaultIndex
from jarvis.plugins.tool.wiki_ingest import WikiIngestTool


class _FakeCuratorLLM:
    """Deterministic proposing-LLM double satisfying the ``CuratorLLM``
    protocol -- stands in for the ONE weak/free provider a fresh install
    actually has. Returns exactly the ``PageUpdate`` list handed to the
    constructor and records every call so the test can assert the write
    path genuinely ran.
    """

    def __init__(self, updates: list[PageUpdate] | None = None) -> None:
        self._updates = list(updates or [])
        self.calls: list[tuple[str, str]] = []

    async def propose_updates(
        self,
        source_content: str,
        source_label: str,
        *,
        repo: Any,
        vault: Any,
    ) -> list[PageUpdate]:
        self.calls.append((source_content, source_label))
        return list(self._updates)


class _DeadBrain:
    """A Brain whose every ``complete`` raises -- stands in for a provider
    that is unreachable (401 / 429 / 403 / network dead). Copied in spirit
    from ``test_curator_llm.py``'s ``FakeBrain(raise_exc=...)``: the async
    generator raises before yielding, so ``streaming.aggregate`` propagates
    the error and ``complete_with_fallback`` crosses to the next family.
    """

    name = "dead-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        raise RuntimeError("provider unreachable (dead-chain fresh-machine)")
        # The unreachable yield makes this an async generator (matches the
        # Brain.complete contract) even though it raises first.
        yield BrainDelta(content="")  # pragma: no cover

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class _DeadRegistry:
    """Stand-in for ``BrainProviderRegistry`` that hands back a ``_DeadBrain``
    for EVERY provider, so no family in the fallback chain can succeed.
    Copied from ``test_curator_llm.py``'s ``FakeRegistry``.
    """

    def __init__(self, brain: Any, *, available: set[str]) -> None:
        self._brain = brain
        self._available = set(available)

    def available(self) -> set[str]:
        return set(self._available)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain


def _entity_body(slug: str, summary_line: str) -> str:
    """Schema-valid entity page body -- copied verbatim from
    ``test_curator_ingest_e2e.py::_entity_body`` (a proven-good fixture)."""
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        "aliases: []\n"
        "created: 2026-05-12\n"
        "updated: 2026-05-12\n"
        "---\n"
        "\n"
        f"# {slug.title()}\n"
        "\n"
        "## Summary\n"
        "\n"
        f"{summary_line}\n"
        "\n"
        "## Facts\n"
        "\n"
        "- TODO\n"
        "\n"
        "## Relationships\n"
        "\n"
        "- TODO\n"
        "\n"
        "## Sources\n"
        "\n"
        "- fresh-machine anchor fixture\n"
    )


def _build_vault(tmp_path: Path) -> Path:
    """The scaffold every real B1 component needs -- copied from
    ``test_curator_ingest_e2e.py::real_stack``: ``schema.md`` for the
    curator-LLM preflight, ``index.md`` / ``log.md`` for
    ``VaultIndex`` / ``LogWriter``. These three files pre-exist any
    ingest call -- they are scaffold, not "content the ingest produced"."""
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text(
        "# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8",
    )
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    return vault_root


async def _build_curator(
    vault_root: Path, tmp_path: Path, llm: Any,
) -> WikiCurator:
    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
    log_writer = LogWriter(log_path=vault_root / "log.md")
    return WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=llm,
        log_writer=log_writer,
        vault_root=vault_root,
    )


@pytest_asyncio.fixture
async def tmp_vault_curator(tmp_path: Path):
    """Empty tmp vault + real ``WikiCurator`` stack + a fake single-provider
    LLM that deterministically proposes ONE page update -- the fresh-machine
    success path. Yields ``(curator, vault_root)``."""
    vault_root = _build_vault(tmp_path)
    update = PageUpdate(
        target_path=vault_root / "entities" / "joy.md",
        operation="create",
        new_body=_entity_body("joy", "Joy's birthday is August 14th."),
        reason="explicit wiki command",
    )
    llm = _FakeCuratorLLM(updates=[update])
    curator = await _build_curator(vault_root, tmp_path, llm)
    return curator, vault_root


@pytest_asyncio.fixture
async def tmp_vault_dead_curator(tmp_path: Path):
    """Same real stack, but with a REAL ``WikiCuratorLLM`` whose provider
    fallback chain is EXHAUSTED: every family in the chain resolves to a
    ``_DeadBrain`` that raises, so the real
    ``provider_chain.complete_with_fallback`` returns ``None`` and
    ``propose_updates`` collapses to ``[]``. This drives the genuine
    dead-chain path, not the salience no-op branch. Yields
    ``(curator, vault_root)``."""
    vault_root = _build_vault(tmp_path)
    cfg = JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3-flash-preview")},
        ),
        memory=MemoryConfig(
            wiki=WikiMemoryConfig(
                curator=WikiCuratorConfig(provider="", model="", timeout_s=5.0),
            ),
        ),
    )
    dead_llm = WikiCuratorLLM(
        config=cfg,
        schema_path=vault_root / "schema.md",
        log_path=vault_root / "log.md",
        registry=_DeadRegistry(
            _DeadBrain(),
            # Every family the wiki chain may cross to is "available" yet dead,
            # so the chain is tried in full and then honestly exhausted.
            available={"gemini", "claude-api", "openrouter", "openai"},
        ),
    )
    curator = await _build_curator(vault_root, tmp_path, dead_llm)
    return curator, vault_root


async def test_explicit_command_produces_a_real_file(tmp_vault_curator):
    """``tmp_vault_curator``: fixture returning (curator, vault_root) with a
    fake proposing LLM that deterministically writes one page."""
    curator, vault_root = tmp_vault_curator
    pages_before = {p.resolve() for p in vault_root.rglob("*.md")}

    utterance = "Schreib ins Wiki, dass Joys Geburtstag am 14. August ist"  # i18n-allow
    m = match_wiki_intent(utterance)
    assert m is not None and m.content is not None

    tool = WikiIngestTool(curator_resolver=lambda: curator)
    result = await tool.execute({"text": m.content, "source": "test"}, ctx=None)

    assert result.success, result.error
    pages_after = {p.resolve() for p in vault_root.rglob("*.md")}
    new_pages = pages_after - pages_before
    assert new_pages, "an explicit wiki command MUST produce a visible page"
    assert any(p.name == "joy.md" for p in new_pages)

    # The write path genuinely ran through the curator LLM exactly once, and
    # the ingested content carried the fact from the utterance.
    assert len(curator._llm.calls) == 1  # noqa: SLF001 -- test double introspection
    ingested_text, _label = curator._llm.calls[0]  # noqa: SLF001
    assert "geburtstag" in ingested_text.lower()  # i18n-allow: fact fragment under test


async def test_dead_provider_chain_fails_honestly(tmp_vault_dead_curator):
    """``tmp_vault_dead_curator``: a REAL curator-LLM whose provider chain is
    exhausted -- every family raises through the real fallback code, so the
    ingest produces no updates and the tool reports honest failure."""
    curator, vault_root = tmp_vault_dead_curator
    pages_before = {p.resolve() for p in vault_root.rglob("*.md")}

    m = match_wiki_intent("write that to the wiki")
    assert m is not None

    tool = WikiIngestTool(curator_resolver=lambda: curator)
    result = await tool.execute(
        {"text": "The deploy key rotated today.", "source": "test"}, ctx=None,
    )

    assert result.success is False           # honest failure, never a lie
    assert result.error                       # with a stated reason
    pages_after = {p.resolve() for p in vault_root.rglob("*.md")}
    assert pages_after == pages_before, "no file on failure"


async def test_curator_none_reports_not_bootstrapped():
    tool = WikiIngestTool(curator_resolver=lambda: None)
    result = await tool.execute(
        {"text": "Something long enough to ingest."}, ctx=None,
    )
    assert result.success is False
    assert "not bootstrapped" in (result.error or "")

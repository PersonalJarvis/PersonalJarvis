"""Tests for the one-shot profile re-curation pass (ADR-0029 cleanup).

A scripted FakeBrain plays the re-curation judge; the curator, writer,
and vault are real-on-tmpfs. Pins: dry-run proposes and writes NOTHING;
apply snapshots the vault first and writes all-or-nothing through
``WikiCurator.apply_external_updates``; unsafe judge output falls to the
next provider family.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    SessionRollupConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.recurate import recurate_profile
from jarvis.memory.wiki.vault_index import VaultIndex

PROFILE_BODY = (
    "---\n"
    "type: entity\n"
    "entity_kind: person\n"
    "slug: ruben\n"
    "aliases: [Ruben]\n"
    "created: 2026-06-01\n"
    "updated: 2026-06-01\n"
    "---\n"
    "\n"
    "# Ruben\n"
    "\n"
    "## Summary\n"
    "\n"
    "The user.\n"
    "\n"
    "## Facts\n"
    "\n"
    "- Enjoys great coffee.\n"
    "- The Eiffel Tower is 330 metres tall.\n"
    "\n"
    "## Relationships\n"
    "\n"
    "## Sources\n"
    "\n"
    "- conversation\n"
)

CLEANED_PROFILE = PROFILE_BODY.replace(
    "- The Eiffel Tower is 330 metres tall.\n", ""
)


class FakeBrain:
    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.received_requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        yield BrainDelta(content=self.response_text)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain

    def available(self) -> set[str]:
        return {"gemini"}


class ScriptedProviderRegistry:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.tried: list[str] = []

    def instantiate(self, name: str, **_kwargs: Any) -> Any:
        self.tried.append(name)
        return FakeBrain(self._responses[name])

    def available(self) -> set[str]:
        return set(self._responses)


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(
            wiki=WikiMemoryConfig(
                session_rollup=SessionRollupConfig(user_entity_slug="ruben"),
            )
        ),
    )


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    profile = vault_root / "entities" / "ruben.md"
    profile.write_text(PROFILE_BODY, encoding="utf-8")
    # Age past the writer's 30s concurrent-edit lock so an immediate apply
    # is not skipped as a recent manual edit.
    aged = time.time() - 120.0
    os.utime(profile, (aged, aged))

    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups"),
        llm=WikiCuratorLLM.__new__(WikiCuratorLLM),
        log_writer=LogWriter(log_path=vault_root / "log.md"),
        vault_root=vault_root,
    )
    return vault_root, curator


def _cleanup_json() -> str:
    return json.dumps(
        [
            {
                "target": "entities/ruben.md",
                "operation": "update",
                "new_body": CLEANED_PROFILE,
                "reason": "drop world-knowledge trivia",
            }
        ]
    )


@pytest.mark.asyncio
async def test_dry_run_proposes_but_writes_nothing(stack) -> None:
    vault_root, curator = stack
    report = await recurate_profile(
        vault_root=vault_root,
        config=_config(),
        curator=curator,
        registry=FakeRegistry(FakeBrain(_cleanup_json())),
        apply=False,
    )

    assert report.error == ""
    assert report.applied is False
    assert report.backup_path is None
    assert [str(u.target_path) for u in report.proposals] == ["entities/ruben.md"]
    # Dry-run must leave the vault byte-identical.
    on_disk = (vault_root / "entities" / "ruben.md").read_text(encoding="utf-8")
    assert on_disk == PROFILE_BODY


@pytest.mark.asyncio
async def test_apply_snapshots_then_writes_cleaned_profile(
    stack, tmp_path: Path,
) -> None:
    vault_root, curator = stack
    report = await recurate_profile(
        vault_root=vault_root,
        config=_config(),
        curator=curator,
        registry=FakeRegistry(FakeBrain(_cleanup_json())),
        apply=True,
        backup_dir=tmp_path / "recurate-backups",
    )

    assert report.error == ""
    assert report.applied is True
    assert report.backup_path is not None and report.backup_path.is_file()
    on_disk = (vault_root / "entities" / "ruben.md").read_text(encoding="utf-8")
    assert "The Eiffel Tower" not in on_disk
    assert "- Enjoys great coffee." in on_disk


@pytest.mark.asyncio
async def test_unsafe_target_falls_through_to_next_provider(stack) -> None:
    vault_root, curator = stack
    registry = ScriptedProviderRegistry(
        {
            "gemini": json.dumps(
                [
                    {
                        "target": "../outside.md",
                        "operation": "update",
                        "new_body": "x",
                    }
                ]
            ),
            "openrouter": _cleanup_json(),
        }
    )
    report = await recurate_profile(
        vault_root=vault_root,
        config=_config(),
        curator=curator,
        registry=registry,
        apply=False,
    )

    assert report.error == ""
    assert registry.tried == ["gemini", "openrouter"]
    assert len(report.proposals) == 1


@pytest.mark.asyncio
async def test_missing_profile_reports_error(stack) -> None:
    vault_root, curator = stack
    (vault_root / "entities" / "ruben.md").unlink()

    report = await recurate_profile(
        vault_root=vault_root,
        config=_config(),
        curator=curator,
        registry=FakeRegistry(FakeBrain("[]")),
        apply=False,
    )

    assert report.error == "profile page not found: entities/ruben.md"
    assert report.proposals == []

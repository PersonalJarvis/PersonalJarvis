"""Background wiki model calls obey the billing rule and a runaway guard.

Live 2026-09-30: ~5 EUR went in three hours while nobody was at the PC — the
wiki kept calling paid keys in the background (~300 calls on keys in 30
days). These tests pin the fix:

* In subscription mode no keyed provider is ever instantiated; a subscription
  that cannot take the work makes it WAIT (candidates stay pending) and the
  next attempt backs off instead of spinning.
* A hard daily cap on the NUMBER of background calls holds even on key-only
  installs, counts every attempt (split-and-retry halves included) and
  survives a restart.
* Drain loops, the age flush and review retries are bounded.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.brain.background_policy import BackgroundDeferred
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    SchedulerConfig,
    WikiIntegrationConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.background_guard import (
    DAILY_CAP_REACHED,
    DEFAULT_DAILY_CALL_CAP,
    WAITING_FOR_SUBSCRIPTION,
    WikiBackgroundGuard,
    daily_cap,
    guard,
)
from jarvis.memory.wiki.consolidator import Consolidator
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.health import health
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal
from jarvis.memory.wiki.lock import VaultLock
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.scheduler import (
    CuratorScheduler,
    TriggerSource,
    fire_journal_trigger,
)
from jarvis.memory.wiki.vault_index import VaultIndex

KEYED = {"gemini", "openrouter", "grok", "openai"}


class _ScriptedBrain:
    name = "scripted-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(self, *, text: str = "[]", finish_reason: str = "stop", fail: bool = False):
        self._text = text
        self._finish_reason = finish_reason
        self._fail = fail
        self.calls = 0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        if self._fail:
            raise RuntimeError("provider down")
        yield BrainDelta(content=self._text)
        yield BrainDelta(finish_reason=self._finish_reason)

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class _RecordingRegistry:
    """Records every ``available()`` probe and every instantiation."""

    def __init__(self, providers: set[str], brain: Any) -> None:
        self._providers = set(providers)
        self._brain = brain
        self.available_calls = 0
        self.instantiated: list[str] = []

    def available(self) -> set[str]:
        self.available_calls += 1
        return set(self._providers)

    def instantiate(self, name: str, **_kwargs: Any) -> Any:
        self.instantiated.append(name)
        return self._brain


def _config(*, cap: int | None = None) -> JarvisConfig:
    integration = (
        WikiIntegrationConfig(max_background_llm_calls_per_day=cap)
        if cap is not None
        else WikiIntegrationConfig()
    )
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
        wiki_integration=integration,
    )


@pytest_asyncio.fixture
async def wiki(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
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
    journal = CandidateJournal(tmp_path / "jarvis.db")
    yield vault_root, curator, journal
    journal.close()


def _consolidator(wiki_tuple, registry: Any, *, config: JarvisConfig) -> Consolidator:
    vault_root, curator, journal = wiki_tuple
    return Consolidator(
        config=config,
        journal=journal,
        curator=curator,
        search=None,
        vault_root=vault_root,
        registry=registry,
    )


def _journal_facts(journal: CandidateJournal, count: int) -> None:
    for index in range(count):
        journal.append(
            [
                CandidateFact(
                    fact=f"Lena fact number {index} about Hamburg.",
                    kind="person",
                    subjects=("lena",),
                )
            ],
            source_label="test",
            turn_hash=f"turn-{index}",
        )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    """Keep write-time alias lookups out of the judge call counts and start
    every test without a chain-failure record from an earlier one."""

    async def _passthrough(page_text: str, **_kwargs: Any) -> str:
        return page_text

    monkeypatch.setattr(
        "jarvis.memory.wiki.consolidator.ensure_page_aliases", _passthrough
    )
    health.record_chain_success()
    yield
    health.record_chain_success()


# --- subscription mode -------------------------------------------------------


async def test_subscription_mode_never_instantiates_a_keyed_provider(
    wiki, background_billing,  # noqa: ANN001
) -> None:
    background_billing.remember_subscription("claude-cli")
    background_billing.sign_out("claude-cli")
    brain = _ScriptedBrain()
    registry = _RecordingRegistry(KEYED | {"claude-cli"}, brain)
    consolidator = _consolidator(wiki, registry, config=_config())
    _journal_facts(wiki[2], 2)

    label = await consolidator.run_once()

    assert label == "judge-deferred"
    assert registry.instantiated == [], "a waiting install must never build a keyed judge"
    assert brain.calls == 0
    assert len(wiki[2].pending()) == 2, "candidates stay pending while waiting"
    snapshot = health.snapshot()
    assert snapshot["background_wait"]["state"] == WAITING_FOR_SUBSCRIPTION
    assert snapshot["last_chain_failure"] is None, "waiting is not a red chain failure"


async def test_deferral_backs_off_instead_of_retrying_every_trigger(
    wiki, background_billing,  # noqa: ANN001
) -> None:
    background_billing.remember_subscription("claude-cli")
    background_billing.sign_out("claude-cli")
    registry = _RecordingRegistry(KEYED | {"claude-cli"}, _ScriptedBrain())
    consolidator = _consolidator(wiki, registry, config=_config())
    _journal_facts(wiki[2], 1)

    assert await consolidator.run_once() == "judge-deferred"
    probes_after_first = registry.available_calls
    for _ in range(5):
        assert await consolidator.run_once() == "judge-deferred"

    assert registry.available_calls == probes_after_first, (
        "inside the backoff window no provider is even looked up"
    )
    assert guard.ready(_config()) is False
    assert len(wiki[2].pending()) == 1


async def test_failing_subscription_waits_instead_of_raising_the_red_banner(
    wiki, background_billing,  # noqa: ANN001
) -> None:
    """Signed in but not answering (rate limited, out of credit): still wait."""
    background_billing.sign_in("claude-cli")
    brain = _ScriptedBrain(fail=True)
    registry = _RecordingRegistry(KEYED | {"claude-cli"}, brain)
    consolidator = _consolidator(wiki, registry, config=_config())
    _journal_facts(wiki[2], 1)

    assert await consolidator.run_once() == "judge-deferred"
    assert registry.instantiated == ["claude-cli"], "only the subscription was tried"
    assert health.snapshot()["last_chain_failure"] is None
    assert health.snapshot()["background_wait"]["state"] == WAITING_FOR_SUBSCRIPTION
    assert len(wiki[2].pending()) == 1


# --- key-only installs -------------------------------------------------------


async def test_key_only_install_keeps_using_its_key(wiki) -> None:  # noqa: ANN001
    brain = _ScriptedBrain(text=json.dumps([]))
    registry = _RecordingRegistry({"gemini"}, brain)
    consolidator = _consolidator(wiki, registry, config=_config())
    _journal_facts(wiki[2], 1)

    await consolidator.run_once()

    assert registry.instantiated == ["gemini"]
    assert guard.calls_today() == 1


async def test_failed_chain_backs_off_on_a_key_only_install(wiki) -> None:  # noqa: ANN001
    registry = _RecordingRegistry({"gemini"}, _ScriptedBrain(fail=True))
    consolidator = _consolidator(wiki, registry, config=_config())
    _journal_facts(wiki[2], 1)

    assert await consolidator.run_once() == "judge-unavailable"
    for _ in range(3):
        assert await consolidator.run_once() == "judge-deferred"

    assert registry.instantiated == ["gemini"], "no spin while the chain is down"
    assert len(wiki[2].pending()) == 1


# --- daily cap ---------------------------------------------------------------


def test_daily_cap_default_is_a_generous_runaway_guard() -> None:
    assert DEFAULT_DAILY_CALL_CAP == 200
    assert daily_cap(JarvisConfig()) == 200
    assert daily_cap(None) == 200
    assert daily_cap(_config(cap=0)) == 1, "a cap below one still allows one call"


def test_daily_cap_stops_calls_and_survives_a_restart() -> None:
    config = _config(cap=2)
    guard.reserve_call(config)
    guard.reserve_call(config)
    with pytest.raises(BackgroundDeferred):
        guard.reserve_call(config)

    state_path = guard._state_path()  # noqa: SLF001 - test seam
    guard.reset_for_tests(state_path=state_path)  # a fresh process
    assert guard.calls_today() == 2
    with pytest.raises(BackgroundDeferred):
        guard.check_ready(config)
    assert health.snapshot()["background_wait"]["state"] == DAILY_CAP_REACHED


def test_daily_cap_resets_on_the_next_day(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(cap=1)
    guard.reserve_call(config)
    assert guard.ready(config) is False

    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(WikiBackgroundGuard, "_today", staticmethod(lambda: tomorrow))
    assert guard.calls_today() == 0
    assert guard.ready(config) is True


async def test_daily_cap_stops_the_judge_and_counts_split_and_retry(wiki) -> None:  # noqa: ANN001
    """The cap holds on key-only installs; each bisected half is a call."""
    brain = _ScriptedBrain(text="[", finish_reason="length")
    registry = _RecordingRegistry({"gemini"}, brain)
    consolidator = _consolidator(wiki, registry, config=_config(cap=2))
    _journal_facts(wiki[2], 4)

    label = await consolidator.run_once()

    # Call 1: the whole batch truncates -> split. Call 2: the left half
    # truncates -> split again. The next half would be call 3: over the cap.
    assert label == "judge-deferred"
    assert brain.calls == 2
    assert guard.calls_today() == 2
    assert len(wiki[2].pending()) == 4, "nothing is lost when the cap stops the work"
    assert await consolidator.run_once() == "judge-deferred"
    assert brain.calls == 2, "no further call until tomorrow"


# --- bounded loops and retries ----------------------------------------------


async def test_drain_stops_on_a_pass_that_made_no_progress(tmp_path: Path) -> None:
    class _AlwaysDirty:
        def __init__(self) -> None:
            self.runs = 0
            self.scheduler: CuratorScheduler | None = None

        async def run_once(self, *, review_keys=None) -> str:  # noqa: ANN001
            self.runs += 1
            assert self.scheduler is not None
            self.scheduler._journal_dirty = True  # noqa: SLF001 - a new append
            return "judge-deferred"

    consolidator = _AlwaysDirty()
    scheduler = CuratorScheduler(
        curator=object(),  # type: ignore[arg-type]
        lock=VaultLock(tmp_path / "curator.lock"),
        config=SchedulerConfig(),
        consolidator=consolidator,
    )
    consolidator.scheduler = scheduler

    await scheduler.trigger(TriggerSource.JOURNAL)
    assert consolidator.runs == 1

    await fire_journal_trigger(scheduler)
    assert consolidator.runs == 2, "a waiting judge is not re-run in a loop"


async def test_age_flush_waits_out_the_backoff() -> None:
    from jarvis.memory.wiki.integration import _journal_age_flush_tick

    class _Journal:
        def oldest_pending_ms(self) -> int:
            return 0  # ancient

    class _Scheduler:
        def __init__(self) -> None:
            self.fired = 0

        def schedule_journal_trigger(self, *, name: str) -> asyncio.Task[Any]:
            self.fired += 1
            return asyncio.create_task(asyncio.sleep(0), name=name)

    scheduler = _Scheduler()
    guard.note_failure("every wiki provider failed")
    assert await _journal_age_flush_tick(_Journal(), scheduler, max_age_min=10) is False
    assert scheduler.fired == 0

    guard.note_progress()
    assert await _journal_age_flush_tick(_Journal(), scheduler, max_age_min=10) is True
    assert scheduler.fired == 1
    await asyncio.sleep(0)


def test_failed_reviews_are_retried_a_bounded_number_of_times(tmp_path: Path) -> None:
    now_s = [1_000.0]
    journal = CandidateJournal(tmp_path / "jarvis.db", clock=lambda: now_s[0])
    key = "live:v2:session:turn-1"
    try:
        claims = 0
        while journal.claim_capture(key, "voice-fact:1", "voice-fact", "a" * 40):
            claims += 1
            assert journal.finish_capture(key, "failed", error_code="provider_timeout")
            assert claims < 50, "retries must be bounded"
        assert claims == 5
    finally:
        journal.close()


# --- search aliases ----------------------------------------------------------


async def test_search_aliases_wait_for_the_subscription(
    monkeypatch: pytest.MonkeyPatch, background_billing,  # noqa: ANN001
) -> None:
    from jarvis.memory.wiki.search_aliases import generate_aliases

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.credential_ready_wiki_providers",
        lambda *, available, config: set(available),
    )
    background_billing.remember_subscription("claude-cli")
    background_billing.sign_out("claude-cli")
    registry = _RecordingRegistry(KEYED | {"claude-cli"}, _ScriptedBrain(text="Hund"))

    aliases = await generate_aliases(
        title="Dog", body="The user's dog.", cfg=_config(), registry=registry,
    )

    assert aliases == []
    assert registry.instantiated == []
    assert health.snapshot()["last_chain_failure"] is None


async def test_search_aliases_respect_the_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.memory.wiki.search_aliases import generate_aliases

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.credential_ready_wiki_providers",
        lambda *, available, config: set(available),
    )
    config = _config(cap=1)
    guard.reserve_call(config)
    registry = _RecordingRegistry({"gemini"}, _ScriptedBrain(text="Hund"))

    aliases = await generate_aliases(
        title="Dog", body="The user's dog.", cfg=config, registry=registry,
    )

    assert aliases == []
    assert registry.instantiated == []

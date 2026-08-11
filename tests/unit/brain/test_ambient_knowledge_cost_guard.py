"""Cost guards for ambient personal knowledge.

Ambient knowledge is only affordable because it is free per turn. Three
properties are load-bearing and each is guarded here:

1. **Zero model calls.** Neither the identity card nor the per-turn wiki
   context may reach a provider — not directly, not through a client library
   (AP-9/AP-11). Guarded twice: a network tripwire around the real code paths,
   and a source scan that keeps a client library out of the modules.
2. **Hard character budgets.** The identity card stays within
   ``MAX_IDENTITY_CARD_CHARS``; the per-turn wiki block stays within the
   injector's ``max_chars``.
3. **The widened gate still costs nothing on smalltalk.** Opening the gate for
   planning turns must not open it for "hello".
"""

from __future__ import annotations

import json
import re
import socket
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.brain import identity_card as ic
from jarvis.brain.wiki_context import WikiContextInjector
from jarvis.brain.wiki_relevance import should_consult_memory

BASE_PROMPT = "You are the assistant."


class ModelCallAttempted(AssertionError):
    """Raised by the tripwire — an ambient path tried to leave the process."""


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly on any outbound call, whatever client library makes it."""

    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise ModelCallAttempted("the ambient path must never call out")

    real_connect = socket.socket.connect

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        # The Windows proactor event loop builds itself on a loopback
        # self-pipe, so loopback stays open; every provider call — including a
        # local endpoint — goes through the HTTP clients tripwired above.
        host = address[0] if isinstance(address, tuple) else address
        if str(host) in {"127.0.0.1", "::1", "localhost"}:
            return real_connect(sock, address)
        raise ModelCallAttempted("the ambient path must never call out")

    monkeypatch.setattr(httpx.Client, "send", tripwire, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "send", tripwire, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", tripwire, raising=False)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect, raising=False)
    monkeypatch.setattr(socket, "create_connection", tripwire, raising=False)


@pytest.fixture(autouse=True)
def _isolated_process_cache() -> Any:
    ic.reset_identity_card_cache()
    yield
    ic.reset_identity_card_cache()


# ---------------------------------------------------------------------------
# Fakes (tests/fakes style — recorded behaviour, not mock scaffolding)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeHit:
    title: str
    snippet: str
    score: float


class SpyVaultSearch:
    def __init__(self, hits: list[FakeHit] | None = None) -> None:
        self._hits = hits or []
        self.queries: list[str] = []

    def search(self, query: str, *, k: int = 5) -> list[FakeHit]:
        self.queries.append(query)
        return list(self._hits[:k])


class FakeConfig:
    class _WikiContext:
        def __init__(self, core_memory_path: Path) -> None:
            self.core_memory_path = str(core_memory_path)
            self.identity_card = True

    class _WikiIntegration:
        def __init__(self, vault_root: Path) -> None:
            self.vault_root = vault_root

    def __init__(self, *, vault_root: Path, core_memory_path: Path) -> None:
        self.wiki_integration = self._WikiIntegration(vault_root)
        self.wiki_context = self._WikiContext(core_memory_path)


def _seed(tmp_path: Path, *, facts: int = 4) -> FakeConfig:
    vault = tmp_path / "vault"
    page = vault / "entities" / "user.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Test Person", "", "## Summary", ""]
    body += [f"- fact {n} about this person and how they like to work" for n in range(facts)]
    body += ["", "## Preferences", "", "- Short answers"]
    page.write_text("\n".join(body), encoding="utf-8")
    core = tmp_path / "core_memory.json"
    core.write_text(
        json.dumps({"user_facts": {"general": [f"core fact {n}" for n in range(facts)]}}),
        encoding="utf-8",
    )
    return FakeConfig(vault_root=vault, core_memory_path=core)


# ---------------------------------------------------------------------------
# 1. Zero model calls
# ---------------------------------------------------------------------------


def test_identity_card_build_makes_no_model_call(tmp_path: Path, no_network: None) -> None:
    cache = ic.IdentityCardCache(
        config=_seed(tmp_path),
        cache_path=tmp_path / "identity_card.json",
        recheck_interval_s=0.0,
    )
    assert "Test Person" in cache.block()
    # A second read must not even reconsider calling out.
    assert cache.block() == cache.block()


@pytest.mark.asyncio
async def test_the_per_turn_wiki_path_makes_no_model_call(no_network: None) -> None:
    search = SpyVaultSearch(
        [FakeHit(title="Weekend", snippet="hiking near the coast", score=0.9)]
    )
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Any ideas for the weekend hiking trip?",
        system_prompt=BASE_PROMPT,
    )

    assert search.queries, "the widened gate must let a planning turn retrieve"
    assert result != BASE_PROMPT


def test_no_client_library_is_reachable_from_the_ambient_modules() -> None:
    """Structural guard: a provider SDK must not even be importable from here.

    Cheaper and more durable than any runtime assertion — it fails at review
    time, the moment someone reaches for a summarizer.
    """
    forbidden = re.compile(
        r"\b(?:anthropic|openai|google\.generativeai|genai|litellm|"
        r"httpx|requests|aiohttp|urllib\.request)\b"
    )
    import jarvis.brain.identity_card
    import jarvis.brain.wiki_relevance
    import jarvis.brain.wiki_relevance_vocab

    for module in (
        jarvis.brain.identity_card,
        jarvis.brain.wiki_relevance,
        jarvis.brain.wiki_relevance_vocab,
    ):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        assert not forbidden.search(source), f"{module.__name__} reaches a client library"


# ---------------------------------------------------------------------------
# 2. Hard character budgets
# ---------------------------------------------------------------------------


def test_identity_card_respects_its_cap_on_a_huge_profile(tmp_path: Path) -> None:
    cache = ic.IdentityCardCache(
        config=_seed(tmp_path, facts=500),
        cache_path=tmp_path / "identity_card.json",
        recheck_interval_s=0.0,
    )
    card = cache.card()
    assert 0 < len(card.text) <= ic.MAX_IDENTITY_CARD_CHARS == 600


def test_the_configured_cap_can_only_lower_the_budget(tmp_path: Path) -> None:
    config = _seed(tmp_path, facts=500)
    config.wiki_context.identity_card_max_chars = 10_000
    ic.reset_identity_card_cache()
    assert len(ic.identity_card_text(config)) <= ic.MAX_IDENTITY_CARD_CHARS


@pytest.mark.asyncio
async def test_the_per_turn_wiki_block_keeps_its_char_cap() -> None:
    """The retrieved half of ambient knowledge stays inside ``max_chars``."""
    from jarvis.brain.wiki_relevance import frame_context_block

    long_snippet = "hiking near the coast " * 200
    search = SpyVaultSearch(
        [
            FakeHit(title="Weekend one", snippet=long_snippet, score=0.9),
            FakeHit(title="Weekend two", snippet=long_snippet, score=0.8),
        ]
    )
    injector = WikiContextInjector(search=search, max_chars=200, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Any ideas for the weekend hiking trip?",
        system_prompt=BASE_PROMPT,
    )

    header = frame_context_block(["x"]).split("\n\nx")[0]
    entries = result.split(header, 1)[1].strip()
    assert entries, "the block must still carry content"
    assert len(entries) <= 200


# ---------------------------------------------------------------------------
# 3. Smalltalk and world knowledge probe strictly and inject nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "Hallo, wie geht es dir?",  # i18n-allow: German smalltalk under test
        "thanks a lot for that",
        "Guten Morgen zusammen",  # i18n-allow: German smalltalk under test
        "How tall is the Eiffel Tower?",
        "What is the fastest animal on earth?",
    ],
)
async def test_smalltalk_and_world_knowledge_never_inject(utterance: str) -> None:
    # Retrieval-first (wiki_relevance module header): these turns now RUN the
    # single-digit-millisecond local probe instead of refusing to look, and the
    # defense against a wrong injection moved to the STRICT coverage bar. The
    # invariant this section pins is unchanged where it matters: an unrelated
    # vault page never reaches the prompt on a smalltalk or world-knowledge
    # turn — the tallest-tower case stays uninjected because nothing retrieved
    # covers the question, not because nobody looked.
    search = SpyVaultSearch([FakeHit(title="Weekend", snippet="hiking", score=0.9)])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(user_text=utterance, system_prompt=BASE_PROMPT)

    verdict = should_consult_memory(utterance)
    assert verdict.consult is True
    assert verdict.strict is True, "smalltalk gets the strict coverage bar"
    assert search.queries, "retrieval-first: the local probe does run"
    assert result == BASE_PROMPT, "an uncovering hit never reaches the prompt"

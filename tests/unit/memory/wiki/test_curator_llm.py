"""Unit tests for ``jarvis.memory.wiki.curator_llm`` (Instance D)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiCuratorConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki import curator_llm as curator_module
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM, _parse_updates
from jarvis.memory.wiki.protocols import PageUpdate


# ---------------------------------------------------------------------
# In-memory fakes — no unittest.mock for collaborators.
# ---------------------------------------------------------------------


@dataclass
class FakeBrainDelta:
    """Mirror of ``BrainDelta`` — typed for clarity in fixtures."""

    content: str | None = None


class FakeBrain:
    """Yields a fixed text response, records the request for assertions."""

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        response_text: str,
        *,
        sleep_s: float = 0.0,
        raise_exc: BaseException | None = None,
        finish_reason: str = "stop",
    ) -> None:
        self.response_text = response_text
        self.sleep_s = sleep_s
        self.raise_exc = raise_exc
        self.finish_reason = finish_reason
        self.received_requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        yield BrainDelta(content=self.response_text)
        yield BrainDelta(
            finish_reason=self.finish_reason,
            usage={"input_tokens": 10, "output_tokens": 20},
        )

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    """Stand-in for ``BrainProviderRegistry`` that yields a pre-built brain."""

    def __init__(
        self,
        brain: Any,
        *,
        fail_on: str | None = None,
        available: set[str] | None = None,
    ) -> None:
        self._brain = brain
        self._fail_on = fail_on
        # Default to the families the wiki fallback chain may cross to, so a
        # configured primary leads and succeeds first. A test pins a narrower
        # set to exercise the all-providers-down (chain-exhausted) path.
        self._available = (
            set(available)
            if available is not None
            else {"gemini", "claude-api", "openrouter", "openai"}
        )
        self.instantiate_calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> set[str]:
        return set(self._available)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiate_calls.append((name, dict(kwargs)))
        if self._fail_on is not None and name == self._fail_on:
            raise KeyError(f"Brain-Provider '{name}' not registered")
        return self._brain


class FakeVault:
    """Minimal ``VaultIndex`` stub with one entry per page type."""

    def __init__(self, slugs_by_type: dict[str, list[str]] | None = None) -> None:
        self._slugs = slugs_by_type or {}

    def pages_by_type(self, page_type: str) -> list[Any]:
        slugs = self._slugs.get(page_type, [])

        class _P:
            def __init__(self, slug: str) -> None:
                self.slug = slug
                self.page_type = page_type
        return [_P(s) for s in slugs]


class FakeRepo:
    """``PageRepository`` is never invoked by Instance D but must satisfy typing."""

    async def load(self, path: Path) -> Any:  # pragma: no cover
        return None

    async def parse(self, raw: str, path: Path) -> Any:  # pragma: no cover
        return None

    def render(self, page: Any) -> str:  # pragma: no cover
        return ""

    def resolve_wikilink(self, link: str, vault_root: Path) -> Path | None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _make_config(
    *,
    primary: str = "gemini",
    curator_provider: str = "",
    curator_model: str = "",
    timeout_s: float = 90.0,
    providers: dict[str, BrainProviderConfig] | None = None,
) -> JarvisConfig:
    """Compose a ``JarvisConfig`` populated only with the fields Instance D reads."""

    brain = BrainConfig(
        primary=primary,
        providers=providers or {
            "gemini": BrainProviderConfig(model="gemini-3-flash-preview"),
            "claude-api": BrainProviderConfig(model="claude-haiku-4-5-20251001"),
        },
    )
    memory = MemoryConfig(
        wiki=WikiMemoryConfig(
            curator=WikiCuratorConfig(
                provider=curator_provider,
                model=curator_model,
                timeout_s=timeout_s,
            ),
        ),
    )
    return JarvisConfig(brain=brain, memory=memory)


def _write_schema(tmp_path: Path, body: str = "type: meta\n# Schema") -> Path:
    """Write a minimal ``schema.md`` and return its path."""
    p = tmp_path / "schema.md"
    p.write_text(body, encoding="utf-8")
    return p


def _ok_response() -> str:
    """A well-formed JSON array the LLM might return."""

    return json.dumps([
        {
            "target": "entities/ruben-luetke.md",
            "operation": "update",
            "new_body": "---\ntype: entity\nslug: ruben-luetke\n---\n\n# Ruben Lütke\n",  # i18n-allow: proper name with umlaut used as wiki-page fixture data
            "rename_from": None,
            "reason": "added phase B1 milestone",
        }
    ])


# ---------------------------------------------------------------------
# _parse_updates — pure-function coverage
# ---------------------------------------------------------------------


def test_parse_updates_happy_path() -> None:
    """A well-formed array produces one ``PageUpdate``."""

    raw = _ok_response()
    updates = _parse_updates(raw)
    assert len(updates) == 1
    assert isinstance(updates[0], PageUpdate)
    assert updates[0].target_path == Path("entities/ruben-luetke.md")
    assert updates[0].operation == "update"
    assert "Ruben" in updates[0].new_body


def test_parse_updates_tolerates_code_fence() -> None:
    """LLMs that wrap JSON in ``` fences still parse."""

    fenced = "```json\n" + _ok_response() + "\n```"
    updates = _parse_updates(fenced)
    assert len(updates) == 1


def test_parse_updates_drops_invalid_operation() -> None:
    """Unknown operation strings are dropped, not raised."""

    raw = json.dumps([
        {"target": "x.md", "operation": "yeet", "new_body": "body"},
        {"target": "y.md", "operation": "update", "new_body": "body"},
    ])
    updates = _parse_updates(raw)
    assert len(updates) == 1
    assert updates[0].operation == "update"


def test_parse_updates_drops_empty_body() -> None:
    """An update with no ``new_body`` is dropped."""

    raw = json.dumps([
        {"target": "x.md", "operation": "create", "new_body": ""},
    ])
    assert _parse_updates(raw) == []


def test_parse_updates_strips_absolute_paths() -> None:
    """Absolute paths can't escape the vault — leading slash is stripped."""

    raw = json.dumps([
        {"target": "/etc/passwd", "operation": "update", "new_body": "x"},
    ])
    updates = _parse_updates(raw)
    assert len(updates) == 1
    assert not updates[0].target_path.is_absolute()


def test_parse_updates_caps_count() -> None:
    """No more than ``_MAX_UPDATES_PER_INGEST`` updates pass through."""

    items = [
        {"target": f"entities/x-{i}.md", "operation": "create", "new_body": f"body {i}"}
        for i in range(60)
    ]
    updates = _parse_updates(json.dumps(items))
    assert len(updates) == 30


def test_parse_updates_rejects_non_array() -> None:
    """A JSON object (not array) is treated as malformed."""

    with pytest.raises(ValueError):
        _parse_updates('{"target": "x"}')


def test_parse_updates_rename_requires_rename_from() -> None:
    """A rename without ``rename_from`` is dropped."""

    raw = json.dumps([
        {"target": "entities/new.md", "operation": "rename", "new_body": "body"},
        {
            "target": "entities/new.md",
            "operation": "rename",
            "rename_from": "entities/old.md",
            "new_body": "body",
        },
    ])
    updates = _parse_updates(raw)
    assert len(updates) == 1
    assert updates[0].rename_from == Path("entities/old.md")


# ---------------------------------------------------------------------
# WikiCuratorLLM end-to-end — patched registry
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_updates_returns_parsed_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: brain responds with a valid array, curator returns it."""

    cfg = _make_config(primary="gemini")
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    updates = await llm.propose_updates(
        "Ruben fixed a bug today.",
        "BrainTurnCompleted",
        repo=FakeRepo(),
        vault=FakeVault(),
    )
    assert len(updates) == 1
    assert updates[0].target_path == Path("entities/ruben-luetke.md")


@pytest.mark.asyncio
async def test_propose_updates_uses_primary_when_provider_empty(tmp_path: Path) -> None:
    """Empty ``provider`` falls back to ``brain.primary``."""

    cfg = _make_config(primary="claude-api", curator_provider="")
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    await llm.propose_updates(
        "Source text", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert registry.instantiate_calls
    name, kwargs = registry.instantiate_calls[0]
    assert name == "claude-api"
    assert kwargs.get("model") == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_propose_updates_uses_explicit_provider_when_configured(tmp_path: Path) -> None:
    """A non-empty ``provider`` overrides ``brain.primary``."""

    cfg = _make_config(primary="gemini", curator_provider="claude-api")
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    await llm.propose_updates(
        "Source", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    name, _ = registry.instantiate_calls[0]
    assert name == "claude-api"


@pytest.mark.asyncio
async def test_propose_updates_uses_explicit_model_when_configured(tmp_path: Path) -> None:
    """An explicit ``model`` field wins over the provider default."""

    cfg = _make_config(
        primary="gemini",
        curator_provider="gemini",
        curator_model="gemini-3.1-pro-preview",
    )
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    await llm.propose_updates("Source", "label", repo=FakeRepo(), vault=FakeVault())
    _, kwargs = registry.instantiate_calls[0]
    assert kwargs.get("model") == "gemini-3.1-pro-preview"


@pytest.mark.asyncio
async def test_propose_updates_empty_source_short_circuits(tmp_path: Path) -> None:
    """Empty / whitespace-only source returns ``[]`` without touching the brain."""

    cfg = _make_config()
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    assert await llm.propose_updates(
        "", "label", repo=FakeRepo(), vault=FakeVault(),
    ) == []
    assert await llm.propose_updates(
        "  \n\n ", "label", repo=FakeRepo(), vault=FakeVault(),
    ) == []
    assert brain.received_requests == []
    assert registry.instantiate_calls == []


@pytest.mark.asyncio
async def test_propose_updates_malformed_json_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Garbled response yields empty list + logged warning, no raise."""

    cfg = _make_config()
    brain = FakeBrain("not json at all <oh no>")
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    caplog.set_level("WARNING")
    result = await llm.propose_updates(
        "real source text", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert result == []
    assert any("malformed" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_propose_updates_partial_json_array_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Truncated JSON inside ``[...]`` is rejected as malformed."""

    cfg = _make_config()
    # Unterminated string inside the array → json.loads raises.
    brain = FakeBrain('[{"target": "x.md", "operation": "update", "new_body": "incomplete')
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    caplog.set_level("WARNING")
    assert await llm.propose_updates(
        "source", "label", repo=FakeRepo(), vault=FakeVault(),
    ) == []


@pytest.mark.asyncio
async def test_propose_updates_timeout_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A brain that sleeps past ``timeout_s`` produces an empty result."""

    cfg = _make_config(timeout_s=0.05)
    brain = FakeBrain(_ok_response(), sleep_s=0.5)
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    caplog.set_level("WARNING")
    result = await llm.propose_updates(
        "source", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert result == []
    # Every provider timed out → the chain is exhausted and gives up honestly.
    assert any("timed out" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_propose_updates_brain_exception_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Any exception raised by the brain becomes a logged warning + empty list."""

    cfg = _make_config()
    brain = FakeBrain("", raise_exc=RuntimeError("provider on fire"))
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    caplog.set_level("WARNING")
    result = await llm.propose_updates(
        "source", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert result == []
    # The chain tries every family, then gives up HONESTLY (not a silent empty).
    assert any(
        "failed" in rec.message.lower() and "provider" in rec.message.lower()
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_propose_updates_missing_schema_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """If ``schema.md`` is unreadable, no LLM call happens."""

    cfg = _make_config()
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=tmp_path / "nonexistent-schema.md",
        registry=registry,
    )
    caplog.set_level("WARNING")
    result = await llm.propose_updates(
        "source", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert result == []
    assert brain.received_requests == []


@pytest.mark.asyncio
async def test_propose_updates_unavailable_provider_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable provider with NOTHING to fall back to yields ``[]``."""

    cfg = _make_config(primary="nonsense-provider")
    brain = FakeBrain(_ok_response())
    # Only the (failing) primary is reachable — the chain has nothing to cross
    # to, so it is exhausted and the curator gives up honestly.
    registry = FakeRegistry(
        brain, fail_on="nonsense-provider", available={"nonsense-provider"},
    )

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    caplog.set_level("WARNING")
    result = await llm.propose_updates(
        "source", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert result == []
    assert any("instantiate" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_propose_updates_reresolves_provider_per_call(tmp_path: Path) -> None:
    """The key-aware fallback chain re-resolves the provider on every call.

    The old single-brain cache (one instantiate per instance) was replaced by a
    per-call chain so a provider switch — or a previously-dead provider that
    recovered — is picked up immediately. A SUCCESSFUL call still stops at the
    first working provider (no needless fallback instantiation), so two
    successful calls instantiate exactly twice — never the whole chain.
    """

    cfg = _make_config()
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    await llm.propose_updates("first source", "l1", repo=FakeRepo(), vault=FakeVault())
    await llm.propose_updates("second source", "l2", repo=FakeRepo(), vault=FakeVault())
    assert len(registry.instantiate_calls) == 2
    assert [name for name, _ in registry.instantiate_calls] == ["gemini", "gemini"]


@pytest.mark.asyncio
async def test_propose_updates_provider_name_property(tmp_path: Path) -> None:
    """``provider_name`` reflects the resolved provider after the first call."""

    cfg = _make_config(primary="gemini")
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    assert llm.provider_name is None
    await llm.propose_updates("source", "label", repo=FakeRepo(), vault=FakeVault())
    assert llm.provider_name == "gemini"


@pytest.mark.asyncio
async def test_propose_updates_passes_request_with_correct_caps(tmp_path: Path) -> None:
    """The request honours ``max_output_tokens`` from the curator config."""

    cfg = _make_config()
    cfg.memory.wiki.curator.max_output_tokens = 1234
    brain = FakeBrain(_ok_response())
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    await llm.propose_updates("source", "label", repo=FakeRepo(), vault=FakeVault())
    assert brain.received_requests
    req = brain.received_requests[0]
    assert req.max_tokens == 1234
    # System prompt carries the verbatim schema + the output contract.
    assert "Output Contract" in (req.system or "")


@pytest.mark.asyncio
async def test_propose_updates_uses_default_registry_if_none_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an injected registry, the curator builds its own ``BrainProviderRegistry``."""

    cfg = _make_config()

    calls: list[tuple[str, dict[str, Any]]] = []
    brain = FakeBrain(_ok_response())

    def _fake_instantiate(self, name: str, **kwargs: Any) -> Any:  # noqa: ARG001
        calls.append((name, dict(kwargs)))
        return brain

    monkeypatch.setattr(
        curator_module.BrainProviderRegistry,
        "instantiate",
        _fake_instantiate,
    )

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
    )
    await llm.propose_updates("source", "label", repo=FakeRepo(), vault=FakeVault())
    assert calls and calls[0][0] == "gemini"


# ---------------------------------------------------------------------
# Module-level configuration-resolution smoke test
# ---------------------------------------------------------------------


def test_resolve_empty_provider_empty_model_uses_cheap_router_model_gemini() -> None:
    """Empty provider + empty model => brain.primary + its cheap router model.

    Even when the provider's brain.providers entry lists a frontier chat
    model, the resolver must NOT use that — it picks the cheap router-tier
    model instead.
    """

    from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

    # brain.providers["gemini"] has the frontier model set, but the
    # resolver should ignore it and return the cheap router-tier model.
    cfg = _make_config(
        primary="gemini",
        providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
    )
    provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
    assert provider == "gemini"
    assert model == "gemini-3-flash-preview"


def test_resolve_claude_api_primary_uses_haiku_not_opus() -> None:
    """claude-api primary + empty model resolves to Haiku, not Opus."""

    from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

    cfg = _make_config(
        primary="claude-api",
        providers={"claude-api": BrainProviderConfig(model="claude-opus-4-8")},
    )
    provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
    assert provider == "claude-api"
    assert model == "claude-haiku-4-5-20251001"


def test_resolve_explicit_curator_model_wins() -> None:
    """An explicit curator model override beats the cheap router-tier model."""

    from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

    cfg = _make_config(
        primary="gemini",
        curator_provider="claude-api",
        curator_model="claude-opus-4-8",
    )
    provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
    assert provider == "claude-api"
    assert model == "claude-opus-4-8"


def test_resolve_explicit_provider_empty_model_uses_cheap_router_model() -> None:
    """Explicit provider + empty model => that provider's cheap router model.

    grok was removed from the brain-provider catalog and from
    TIER_DEFAULTS_BY_PROVIDER["router"], so _cheap_model_for("grok") returns
    None — the resolver propagates that and lets the registry pick its own
    default. This is the correct degradation path for unknown providers.
    """

    from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

    cfg = _make_config(
        primary="gemini",
        curator_provider="grok",
        curator_model="",
        providers={"grok": BrainProviderConfig(model="grok-4-frontier")},
    )
    provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
    assert provider == "grok"
    # grok is no longer in the router-tier defaults or _CHEAP_MODEL_FALLBACK;
    # the resolver returns None so the registry picks its own default.
    assert model is None


def test_resolve_unknown_provider_returns_none_model() -> None:
    """An unknown provider (not in the cheap-model map) resolves model to None."""

    from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

    cfg = JarvisConfig(
        brain=BrainConfig(primary="never-heard-of-it", providers={}),
        memory=MemoryConfig(
            wiki=WikiMemoryConfig(curator=WikiCuratorConfig()),
        ),
    )
    provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
    assert provider == "never-heard-of-it"
    assert model is None


# ---------------------------------------------------------------------
# _cheap_model_for — live-lookup vs. local fallback map
# ---------------------------------------------------------------------


def test_cheap_model_for_uses_local_map_when_brain_manager_unimportable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the lazy ``jarvis.brain.manager`` import fails, the local map wins.

    Forces the ``except`` branch in ``_cheap_model_for`` by replacing the
    module entry in ``sys.modules`` with ``None`` (a sentinel that makes
    ``from jarvis.brain.manager import ...`` raise ``ImportError``). The
    returned value must come from ``_CHEAP_MODEL_FALLBACK``.
    """

    import sys

    from jarvis.memory.wiki.curator_llm import _CHEAP_MODEL_FALLBACK, _cheap_model_for

    # ``None`` in sys.modules makes the import statement raise ImportError.
    # monkeypatch restores the previous state automatically after the test.
    monkeypatch.setitem(sys.modules, "jarvis.brain.manager", None)

    assert _cheap_model_for("gemini") == "gemini-3-flash-preview"
    assert _cheap_model_for("gemini") == _CHEAP_MODEL_FALLBACK["gemini"]
    # Unknown providers still degrade to ``None`` even on the fallback path.
    assert _cheap_model_for("never-heard-of-it") is None


def test_cheap_model_fallback_map_matches_live_router_defaults() -> None:
    """Drift guard: every fallback entry must match the live router default.

    The local ``_CHEAP_MODEL_FALLBACK`` is only a last-resort mirror of
    ``jarvis.brain.manager.get_tier_default_model("router", provider)``. If
    a router default changes upstream, this test fails so the fallback map
    cannot silently drift. Skips when ``jarvis.brain.manager`` is
    unimportable (minimal install).
    """

    from jarvis.memory.wiki.curator_llm import _CHEAP_MODEL_FALLBACK

    try:
        from jarvis.brain.manager import get_tier_default_model
    except Exception:  # noqa: BLE001
        pytest.skip("jarvis.brain.manager unimportable in this environment")

    for provider, fallback_model in _CHEAP_MODEL_FALLBACK.items():
        live = get_tier_default_model("router", provider)
        assert live == fallback_model, (
            f"_CHEAP_MODEL_FALLBACK[{provider!r}]={fallback_model!r} drifted "
            f"from live router default {live!r}"
        )


# ---------------------------------------------------------------------
# Truncation guard — length-capped generations are discarded (Wave-1)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_updates_rejects_length_capped_response(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A response that hit the output-token cap is discarded, not persisted."""

    cfg = _make_config()
    # Well-formed JSON array, but the stream reports it was cut off at the cap.
    brain = FakeBrain(_ok_response(), finish_reason="length")
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    caplog.set_level("WARNING")
    result = await llm.propose_updates(
        "real source text", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert result == []
    assert any("output-token cap" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_propose_updates_accepts_naturally_stopped_response(
    tmp_path: Path,
) -> None:
    """A complete response (finish_reason='stop') still writes normally."""

    cfg = _make_config()
    brain = FakeBrain(_ok_response(), finish_reason="stop")
    registry = FakeRegistry(brain)

    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=_write_schema(tmp_path),
        registry=registry,
    )
    updates = await llm.propose_updates(
        "real source text", "label", repo=FakeRepo(), vault=FakeVault(),
    )
    assert len(updates) == 1

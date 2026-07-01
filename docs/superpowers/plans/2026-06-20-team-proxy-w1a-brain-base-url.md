# Team Proxy — W1a: Brain Provider base_url Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Brain provider actually consume the already-existing-but-unused `BrainProviderConfig.base_url`, via one shared resolver, so a custom endpoint (later: the team proxy) can be configured per provider.

**Architecture:** A single `resolve_provider_endpoint()` helper in `jarvis/core/config.py` returns the effective `(base_url, credential)` for a provider id — the explicit `[brain.providers.<id>].base_url` override if set, else the vendor default; credential stays `get_provider_secret(...)`. Each Brain provider's `_ensure_client()` calls it instead of hardcoding its endpoint. Purely additive: with no override configured, behaviour is byte-for-byte the current behaviour.

**Tech Stack:** Python 3.11, Pydantic v2 config, `openai.AsyncOpenAI`, `anthropic.AsyncAnthropic`, `google-genai` (`genai.Client`), pytest (asyncio_mode=auto), fakes (not mock).

**Scope note:** This is slice W1a of the design at `docs/superpowers/specs/2026-06-20-team-proxy-api-keys-design.md`. STT/TTS endpoint wiring (W1b), team-mode config + UI (W2), and the `keyproxy/` service (W3–W5) are separate plans. W1a ships and is testable on its own.

---

### Task 1: Endpoint resolver in config.py

**Files:**
- Modify: `jarvis/core/config.py` (add after `get_provider_secret`, ~line 2029)
- Test: `tests/unit/core/test_provider_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_provider_endpoint.py
"""W1a: resolve_provider_endpoint — explicit base_url override vs vendor default."""
from __future__ import annotations

import jarvis.core.config as cfg
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    ResolvedEndpoint,
    resolve_provider_endpoint,
)


def _cfg_with(provider_id: str, base_url: str | None) -> JarvisConfig:
    providers = {provider_id: BrainProviderConfig(base_url=base_url)}
    return JarvisConfig(brain=BrainConfig(providers=providers))


def test_returns_vendor_default_when_no_override(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint(
        "grok", vendor_default_base_url="https://api.x.ai/v1", config=JarvisConfig()
    )
    assert isinstance(res, ResolvedEndpoint)
    assert res.base_url == "https://api.x.ai/v1"
    assert res.credential == "sk-real"
    assert res.via_proxy is False


def test_explicit_override_wins(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint(
        "grok",
        vendor_default_base_url="https://api.x.ai/v1",
        config=_cfg_with("grok", "https://proxy.example/p/grok/v1"),
    )
    assert res.base_url == "https://proxy.example/p/grok/v1"
    assert res.credential == "sk-real"


def test_none_default_stays_none(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint("openai", vendor_default_base_url=None, config=JarvisConfig())
    assert res.base_url is None


def test_loads_config_when_not_injected(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    monkeypatch.setattr(cfg, "load_config", lambda: _cfg_with("openai", "https://p/v1"))
    res = resolve_provider_endpoint("openai", vendor_default_base_url=None)
    assert res.base_url == "https://p/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_provider_endpoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'ResolvedEndpoint'`.

- [ ] **Step 3: Write minimal implementation**

Add to `jarvis/core/config.py` immediately after `get_provider_secret` (line ~2029). `dataclass` is already imported in this module; if not, add `from dataclasses import dataclass` at the top with the other stdlib imports.

```python
@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """Effective endpoint + credential for a provider on this turn.

    ``via_proxy`` is always False in W1a; W2 sets it True when the team proxy
    is the resolved target. ``base_url=None`` means "use the SDK's own default".
    """

    base_url: str | None
    credential: str | None
    via_proxy: bool


def resolve_provider_endpoint(
    provider_id: str,
    *,
    vendor_default_base_url: str | None = None,
    config: "JarvisConfig | None" = None,
) -> ResolvedEndpoint:
    """Resolve the endpoint + credential for a provider.

    W1a precedence: explicit ``[brain.providers.<id>].base_url`` override, else
    the vendor default. Credential is the provider's own configured secret. The
    ``config`` arg is for tests; production passes ``None`` → ``load_config()``.
    """
    cfg_obj = config if config is not None else load_config()
    override: str | None = None
    prov = cfg_obj.brain.providers.get(provider_id)
    if prov is not None and prov.base_url:
        override = prov.base_url
    base_url = override or vendor_default_base_url
    credential = get_provider_secret(provider_id)
    return ResolvedEndpoint(base_url=base_url, credential=credential, via_proxy=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_provider_endpoint.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/core/test_provider_endpoint.py
git commit -m "feat(config): resolve_provider_endpoint helper (W1a base_url wiring)"
```

---

### Task 2: Wire OpenRouter

**Files:**
- Modify: `jarvis/plugins/brain/openrouter.py:30-45`
- Test: `tests/unit/plugins/brain/test_provider_base_url.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/plugins/brain/test_provider_base_url.py
"""W1a: each brain provider passes the resolved base_url to its SDK client."""
from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs


def _override(provider_id: str, url: str, monkeypatch) -> None:
    conf = JarvisConfig(brain=BrainConfig(providers={provider_id: BrainProviderConfig(base_url=url)}))
    monkeypatch.setattr(cfg, "load_config", lambda: conf)
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")


def test_openrouter_uses_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("openrouter", "https://proxy/p/openrouter/v1", monkeypatch)
    from jarvis.plugins.brain.openrouter import OpenRouterBrain

    OpenRouterBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://proxy/p/openrouter/v1"
    assert _FakeOpenAI.last_kwargs["api_key"] == "sk-test"


def test_openrouter_default_without_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")
    from jarvis.plugins.brain.openrouter import OpenRouterBrain

    OpenRouterBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://openrouter.ai/api/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py::test_openrouter_uses_override -v`
Expected: FAIL — `base_url` is the hardcoded constant, not the override.

- [ ] **Step 3: Write minimal implementation**

Replace `_ensure_client` in `jarvis/plugins/brain/openrouter.py`:

```python
    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("openrouter", vendor_default_base_url=BASE_URL)
            if not ep.credential:
                raise RuntimeError("No OpenRouter API key found (openrouter_api_key).")
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=ep.credential,
                base_url=ep.base_url,
                timeout=CLIENT_TIMEOUT,
                default_headers={
                    "HTTP-Referer": "https://github.com/PersonalJarvis",
                    "X-Title": "Personal Jarvis",
                },
            )
        return self._client
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k openrouter -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/brain/openrouter.py tests/unit/plugins/brain/test_provider_base_url.py
git commit -m "feat(brain): OpenRouter honours resolved base_url (W1a)"
```

---

### Task 3: Wire Grok

**Files:**
- Modify: `jarvis/plugins/brain/grok.py:30-42`
- Test: `tests/unit/plugins/brain/test_provider_base_url.py` (append)

- [ ] **Step 1: Write the failing test (append to the file from Task 2)**

```python
def test_grok_uses_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("grok", "https://proxy/p/grok/v1", monkeypatch)
    from jarvis.plugins.brain.grok import GrokBrain

    GrokBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://proxy/p/grok/v1"


def test_grok_default_without_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")
    from jarvis.plugins.brain.grok import GrokBrain

    GrokBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://api.x.ai/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py::test_grok_uses_override -v`
Expected: FAIL — hardcoded `BASE_URL`.

- [ ] **Step 3: Write minimal implementation**

Replace `_ensure_client` in `jarvis/plugins/brain/grok.py`:

```python
    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("grok", vendor_default_base_url=BASE_URL)
            if not ep.credential:
                raise RuntimeError(
                    "No Grok API key found "
                    "(grok_api_key / xai_api_key / GROK_API_KEY / XAI_API_KEY)."
                )
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=ep.credential, base_url=ep.base_url, timeout=CLIENT_TIMEOUT
            )
        return self._client
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k grok -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/brain/grok.py tests/unit/plugins/brain/test_provider_base_url.py
git commit -m "feat(brain): Grok honours resolved base_url (W1a)"
```

---

### Task 4: Wire OpenAI

**Files:**
- Modify: `jarvis/plugins/brain/openai.py:25-32`
- Test: `tests/unit/plugins/brain/test_provider_base_url.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_openai_uses_override(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("openai", "https://proxy/p/openai/v1", monkeypatch)
    from jarvis.plugins.brain.openai import OpenAIBrain

    OpenAIBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://proxy/p/openai/v1"


def test_openai_no_override_omits_base_url(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")
    from jarvis.plugins.brain.openai import OpenAIBrain

    OpenAIBrain()._ensure_client()
    # No override + no vendor default → base_url omitted so the SDK uses its own default.
    assert "base_url" not in _FakeOpenAI.last_kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k openai -v`
Expected: FAIL — current code never sets `base_url`, so `test_openai_uses_override` fails.

- [ ] **Step 3: Write minimal implementation**

Replace `_ensure_client` in `jarvis/plugins/brain/openai.py`:

```python
    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("openai")
            if not ep.credential:
                raise RuntimeError("No OpenAI API key found (openai_api_key / OPENAI_API_KEY).")
            from openai import AsyncOpenAI
            kwargs: dict[str, Any] = {"api_key": ep.credential, "timeout": CLIENT_TIMEOUT}
            if ep.base_url:
                kwargs["base_url"] = ep.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k openai -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/brain/openai.py tests/unit/plugins/brain/test_provider_base_url.py
git commit -m "feat(brain): OpenAI honours resolved base_url (W1a)"
```

---

### Task 5: Wire claude-api (Anthropic)

**Files:**
- Modify: `jarvis/plugins/brain/claude_api.py:30-41`
- Test: `tests/unit/plugins/brain/test_provider_base_url.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
class _FakeAnthropic:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeAnthropic.last_kwargs = kwargs


def test_claude_api_uses_override(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    _override("claude-api", "https://proxy/p/claude-api", monkeypatch)
    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

    ClaudeAPIBrain()._ensure_client()
    assert _FakeAnthropic.last_kwargs["base_url"] == "https://proxy/p/claude-api"
    assert _FakeAnthropic.last_kwargs["api_key"] == "sk-test"


def test_claude_api_no_override_omits_base_url(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-test")
    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

    ClaudeAPIBrain()._ensure_client()
    assert "base_url" not in _FakeAnthropic.last_kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k claude_api -v`
Expected: FAIL — `base_url` never set.

- [ ] **Step 3: Write minimal implementation**

Replace `_ensure_client` in `jarvis/plugins/brain/claude_api.py`:

```python
    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("claude-api")
            if not ep.credential:
                raise RuntimeError(
                    "No Anthropic API key found. Please set via Wizard or "
                    "ANTHROPIC_API_KEY in ENV."
                )
            from anthropic import AsyncAnthropic
            # max_retries=0 → BrainManager fallback engages faster on 429
            kwargs: dict[str, Any] = {"api_key": ep.credential, "max_retries": 0, "timeout": 15.0}
            if ep.base_url:
                kwargs["base_url"] = ep.base_url
            self._client = AsyncAnthropic(**kwargs)
        return self._client
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k claude_api -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/brain/claude_api.py tests/unit/plugins/brain/test_provider_base_url.py
git commit -m "feat(brain): claude-api honours resolved base_url (W1a)"
```

---

### Task 6: Wire Gemini

**Files:**
- Modify: `jarvis/plugins/brain/gemini.py` (the `genai.Client(...)` call in `_ensure_client`, ~line 318-328)
- Test: `tests/unit/plugins/brain/test_provider_base_url.py` (append)

- [ ] **Step 1: Read the exact current `_ensure_client` / `genai.Client(...)` call**

Run: `grep -n "genai.Client\|def _ensure_client\|http_options" jarvis/plugins/brain/gemini.py`
Note the exact kwargs already passed to `genai.Client(...)` (api_key, possibly http_options) so the edit preserves them.

- [ ] **Step 2: Write the failing test (append)**

```python
class _FakeGenaiClient:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeGenaiClient.last_kwargs = kwargs


def test_gemini_uses_override(monkeypatch):
    from google import genai

    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)
    _override("gemini", "https://proxy/p/gemini", monkeypatch)
    from jarvis.plugins.brain.gemini import GeminiBrain

    GeminiBrain()._ensure_client()
    http_opts = _FakeGenaiClient.last_kwargs.get("http_options")
    assert http_opts is not None
    # google-genai HttpOptions exposes base_url either as attr or mapping
    base = getattr(http_opts, "base_url", None) or (
        http_opts.get("base_url") if isinstance(http_opts, dict) else None
    )
    assert base == "https://proxy/p/gemini"
```

Adjust the `GeminiBrain` import name / constructor to match the real class if it differs (confirm in Step 1).

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k gemini -v`
Expected: FAIL — no `http_options`/`base_url` passed.

- [ ] **Step 4: Write minimal implementation**

In `gemini.py` `_ensure_client`, resolve and pass `http_options` only when a base_url is set, preserving existing kwargs:

```python
            ep = cfg.resolve_provider_endpoint("gemini")
            # ... existing api_key resolution may be replaced by ep.credential ...
            from google import genai
            from google.genai import types as genai_types
            client_kwargs: dict[str, Any] = {"api_key": ep.credential}
            if ep.base_url:
                client_kwargs["http_options"] = genai_types.HttpOptions(base_url=ep.base_url)
            self._client = genai.Client(**client_kwargs)
```

Preserve any other kwargs the original call passed (e.g. vertex options). If the provider currently reads its key via a different call than `get_provider_secret("gemini")`, keep using `ep.credential` (same source) so behaviour is unchanged when no override is set.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/plugins/brain/test_provider_base_url.py -k gemini -v`
Expected: PASS.

- [ ] **Step 6: Full slice regression + commit**

Run: `pytest tests/unit/core/test_provider_endpoint.py tests/unit/plugins/brain/test_provider_base_url.py -v`
Expected: all PASS.
Run: `ruff check jarvis/core/config.py jarvis/plugins/brain/`
Expected: no new findings on touched lines.

```bash
git add jarvis/plugins/brain/gemini.py tests/unit/plugins/brain/test_provider_base_url.py
git commit -m "feat(brain): Gemini honours resolved base_url via http_options (W1a)"
```

---

## Self-Review

- **Spec coverage:** Covers §4 A1 (resolver) + §4 A3 brain rows (claude-api, openai, openrouter, grok, gemini). STT/TTS rows + Vision = W1b (separate plan). Team-mode flip (`via_proxy`) = W2; the `ResolvedEndpoint.via_proxy` field + `config` injection seam are already in place for it.
- **Placeholder scan:** Task 6 Step 1 is a deliberate "read exact current code" step (Gemini's `_ensure_client` was not fully read at plan time), not a placeholder — the edit shape and test are concrete; only the surrounding kwargs must be preserved verbatim.
- **Type consistency:** `ResolvedEndpoint(base_url, credential, via_proxy)` and `resolve_provider_endpoint(provider_id, *, vendor_default_base_url, config)` are used identically across Tasks 1–6.
- **Safety:** Additive — with no `[brain.providers.<id>].base_url` set, openrouter/grok keep their constants, openai/claude omit `base_url` (SDK default), gemini omits `http_options`. No behaviour change for existing installs.

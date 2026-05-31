"""Unit tests for optimistic/config.py — Sub-Agent 1 (AI Backend).

TDD-first: written before the implementation. All tests are sync because
config loading is pure (no I/O). No pytest-asyncio, no third-party deps
beyond what CONTRACTS.md allows.
"""
from __future__ import annotations

from optimistic.config import LLMSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(env: dict) -> LLMSettings:
    """Import fresh and call load_settings with the given env dict."""
    from optimistic.config import load_settings
    return load_settings(env=env)


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_backend(self):
        """With an empty env dict, backend defaults to 'http'."""
        s = _load({})
        assert s.backend == "http"

    def test_default_base_url(self):
        """Default base_url is the local Ollama endpoint."""
        s = _load({})
        assert s.base_url == "http://localhost:11434/v1"

    def test_default_model(self):
        """Default model is qwen2.5:7b."""
        s = _load({})
        assert s.model == "qwen2.5:7b"

    def test_default_api_key_is_none(self):
        """Default api_key is None (no key required for local Ollama)."""
        s = _load({})
        assert s.api_key is None

    def test_default_timeout(self):
        """Default timeout is 120.0 seconds."""
        s = _load({})
        assert s.timeout == 120.0

    def test_default_system_prompt_is_none(self):
        """Default system_prompt is None."""
        s = _load({})
        assert s.system_prompt is None


# ---------------------------------------------------------------------------
# Environment overrides
# ---------------------------------------------------------------------------

class TestEnvOverrides:
    def test_backend_override(self):
        s = _load({"LLM_BACKEND": "mock"})
        assert s.backend == "mock"

    def test_base_url_override(self):
        s = _load({"LLM_BASE_URL": "https://api.openai.com/v1"})
        assert s.base_url == "https://api.openai.com/v1"

    def test_model_override(self):
        s = _load({"LLM_MODEL": "gpt-4o"})
        assert s.model == "gpt-4o"

    def test_api_key_override(self):
        s = _load({"LLM_API_KEY": "sk-test-123"})
        assert s.api_key == "sk-test-123"

    def test_timeout_override(self):
        s = _load({"LLM_TIMEOUT": "30.5"})
        assert s.timeout == 30.5

    def test_system_prompt_override(self):
        s = _load({"LLM_SYSTEM_PROMPT": "You are a helpful assistant."})
        assert s.system_prompt == "You are a helpful assistant."

    def test_all_overrides_together(self):
        """All keys can be set simultaneously via the env dict."""
        s = _load({
            "LLM_BACKEND": "http",
            "LLM_BASE_URL": "https://custom.api/v1",
            "LLM_MODEL": "mixtral:8x7b",
            "LLM_API_KEY": "sk-abc",
            "LLM_TIMEOUT": "60.0",
            "LLM_SYSTEM_PROMPT": "Be concise.",
        })
        assert s.backend == "http"
        assert s.base_url == "https://custom.api/v1"
        assert s.model == "mixtral:8x7b"
        assert s.api_key == "sk-abc"
        assert s.timeout == 60.0
        assert s.system_prompt == "Be concise."


# ---------------------------------------------------------------------------
# use_mock property
# ---------------------------------------------------------------------------

class TestUseMock:
    def test_use_mock_true_when_backend_is_mock(self):
        """use_mock returns True when backend == 'mock'."""
        s = _load({"LLM_BACKEND": "mock"})
        assert s.use_mock is True

    def test_use_mock_false_when_backend_is_http(self):
        """use_mock returns False when backend == 'http'."""
        s = _load({"LLM_BACKEND": "http"})
        assert s.use_mock is False

    def test_use_mock_false_by_default(self):
        """Default backend is 'http', so use_mock is False."""
        s = _load({})
        assert s.use_mock is False


# ---------------------------------------------------------------------------
# Empty api_key → None
# ---------------------------------------------------------------------------

class TestApiKeyNormalisation:
    def test_empty_string_api_key_becomes_none(self):
        """An empty LLM_API_KEY string must be treated as None."""
        s = _load({"LLM_API_KEY": ""})
        assert s.api_key is None

    def test_whitespace_api_key_also_becomes_none(self):
        """A whitespace-only LLM_API_KEY is also normalised to None."""
        s = _load({"LLM_API_KEY": "   "})
        assert s.api_key is None

    def test_nonempty_api_key_preserved(self):
        """A real api_key value is preserved as-is."""
        s = _load({"LLM_API_KEY": "real-key-xyz"})
        assert s.api_key == "real-key-xyz"


# ---------------------------------------------------------------------------
# Frozen dataclass — no mutation allowed
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_settings_is_frozen(self):
        """LLMSettings is a frozen dataclass — assignment must raise."""
        import pytest
        s = _load({})
        with pytest.raises((AttributeError, TypeError)):
            s.model = "should-fail"  # type: ignore[misc]

"""Unit tests for optimistic/llm.py — Sub-Agent 1 (AI Backend).

TDD-first. Covers:
- mock backend: deterministic, non-empty, instant, no network
- http backend: correct request shape, response parsing, LLMError on failures

Uses httpx.MockTransport so zero real network calls are made.
All tests are sync functions using asyncio.run().
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def _mock_settings(**overrides):
    """Return an LLMSettings with backend='mock' by default."""
    from optimistic.config import load_settings
    env = {
        "LLM_BACKEND": "mock",
        "LLM_MODEL": "test-model",
        "LLM_BASE_URL": "http://localhost:11434/v1",
    }
    env.update(overrides)
    return load_settings(env=env)


def _http_settings(**overrides):
    """Return an LLMSettings with backend='http'."""
    from optimistic.config import load_settings
    env = {
        "LLM_BACKEND": "http",
        "LLM_MODEL": "test-model",
        "LLM_BASE_URL": "http://localhost:11434/v1",
    }
    env.update(overrides)
    return load_settings(env=env)


def _openai_response(content: str) -> httpx.Response:
    """Build a fake OpenAI-compatible /v1/chat/completions 200 response."""
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    return httpx.Response(200, json=body)


def _error_response(status: int) -> httpx.Response:
    """Build an error response with the given HTTP status code."""
    return httpx.Response(status, json={"error": {"message": "error", "type": "server_error"}})


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------

class TestMockBackend:
    def test_mock_returns_non_empty_string(self):
        """Mock backend must return a non-empty string."""
        from optimistic import llm
        settings = _mock_settings()
        result = run(llm.complete("Hello", settings=settings))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mock_is_deterministic(self):
        """Same prompt → same result for mock backend."""
        from optimistic import llm
        settings = _mock_settings()
        r1 = run(llm.complete("same prompt", settings=settings))
        r2 = run(llm.complete("same prompt", settings=settings))
        assert r1 == r2

    def test_mock_echoes_model_name(self):
        """Mock response must contain the model name (per spec: '[mock:{model}] ...')."""
        from optimistic import llm
        settings = _mock_settings(LLM_MODEL="qwen2.5:7b")
        result = run(llm.complete("test", settings=settings))
        assert "qwen2.5:7b" in result

    def test_mock_echoes_prompt_prefix(self):
        """Mock response contains the first part of the prompt."""
        from optimistic import llm
        settings = _mock_settings()
        prompt = "Schreib Max eine Mail"
        result = run(llm.complete(prompt, settings=settings))
        # Per spec: prompt[:120] is echoed
        assert prompt[:20] in result

    def test_mock_does_not_hit_network(self):
        """Mock backend must complete without any network I/O — finishes instantly."""
        import time

        from optimistic import llm
        settings = _mock_settings()
        start = time.monotonic()
        run(llm.complete("ping", settings=settings))
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, f"Mock took {elapsed:.3f}s — should be instant"

    def test_mock_with_system_prompt_still_works(self):
        """Mock backend ignores the system kwarg but must not raise."""
        from optimistic import llm
        settings = _mock_settings()
        result = run(llm.complete("hello", settings=settings, system="Be helpful."))
        assert result


# ---------------------------------------------------------------------------
# HTTP backend — happy path
# ---------------------------------------------------------------------------

class TestHTTPBackendHappyPath:
    def _run_with_transport(self, transport, prompt="hello", system=None, **env_overrides):
        """Run complete() injecting the given httpx transport."""
        from optimistic import llm
        settings = _http_settings(**env_overrides)
        return run(llm.complete(prompt, settings=settings, system=system, _transport=transport))

    def test_http_returns_parsed_content(self):
        """HTTP path extracts choices[0].message.content from the response."""
        expected = "This is the assistant reply."

        def handler(request: httpx.Request) -> httpx.Response:
            return _openai_response(expected)

        transport = httpx.MockTransport(handler)
        result = self._run_with_transport(transport)
        assert result == expected

    def test_http_sends_correct_model(self):
        """Request body must contain the configured model name."""
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        self._run_with_transport(transport, LLM_MODEL="my-custom-model")
        assert captured_body.get("model") == "my-custom-model"

    def test_http_sends_user_message(self):
        """Request body must contain a user message with the prompt."""
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        self._run_with_transport(transport, prompt="say something interesting")
        messages = captured_body.get("messages", [])
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert user_msgs, "No user message in request"
        assert "say something interesting" in user_msgs[-1]["content"]

    def test_http_sends_system_message_when_provided(self):
        """When system is not None, a system message is prepended."""
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        self._run_with_transport(transport, system="You are concise.")
        messages = captured_body.get("messages", [])
        sys_msgs = [m for m in messages if m["role"] == "system"]
        assert sys_msgs, "Expected a system message"
        assert "You are concise." in sys_msgs[0]["content"]

    def test_http_no_system_message_when_none(self):
        """When system is None, no system message is included."""
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        self._run_with_transport(transport, system=None)
        messages = captured_body.get("messages", [])
        sys_msgs = [m for m in messages if m["role"] == "system"]
        assert not sys_msgs, "Expected NO system message when system=None"

    def test_http_sets_stream_false(self):
        """Request body must have stream=False."""
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        self._run_with_transport(transport)
        assert captured_body.get("stream") is False

    def test_http_sends_auth_header_when_api_key_set(self):
        """Authorization: Bearer header must be sent when api_key is set."""
        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        self._run_with_transport(transport, LLM_API_KEY="sk-test-999")
        assert "authorization" in captured_headers
        assert captured_headers["authorization"] == "Bearer sk-test-999"

    def test_http_no_auth_header_when_no_api_key(self):
        """No Authorization header when api_key is None."""
        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return _openai_response("ok")

        transport = httpx.MockTransport(handler)
        # LLM_API_KEY is not set → should be None
        self._run_with_transport(transport)
        assert "authorization" not in captured_headers


# ---------------------------------------------------------------------------
# HTTP backend — error paths → LLMError
# ---------------------------------------------------------------------------

class TestHTTPBackendErrors:
    def _run_with_transport(self, transport, **env_overrides):
        from optimistic import llm
        settings = _http_settings(**env_overrides)
        return run(llm.complete("test", settings=settings, _transport=transport))

    def test_http_500_raises_llm_error(self):
        """A 500 response must raise LLMError."""
        from optimistic.llm import LLMError

        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(500)

        transport = httpx.MockTransport(handler)
        with pytest.raises(LLMError):
            self._run_with_transport(transport)

    def test_http_401_raises_llm_error(self):
        """A 401 Unauthorized response must raise LLMError."""
        from optimistic.llm import LLMError

        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(401)

        transport = httpx.MockTransport(handler)
        with pytest.raises(LLMError):
            self._run_with_transport(transport)

    def test_http_503_raises_llm_error(self):
        """A 503 Service Unavailable response must raise LLMError."""
        from optimistic.llm import LLMError

        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(503)

        transport = httpx.MockTransport(handler)
        with pytest.raises(LLMError):
            self._run_with_transport(transport)

    def test_network_exception_raises_llm_error(self):
        """A network-level exception (e.g. connection refused) must raise LLMError."""
        from optimistic.llm import LLMError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        with pytest.raises(LLMError):
            self._run_with_transport(transport)

    def test_llm_error_is_base_exception(self):
        """LLMError must be a proper Exception subclass."""
        from optimistic.llm import LLMError
        assert issubclass(LLMError, Exception)

    def test_llm_error_message_preserved(self):
        """LLMError should carry a descriptive message."""
        from optimistic.llm import LLMError

        def handler(request: httpx.Request) -> httpx.Response:
            return _error_response(500)

        transport = httpx.MockTransport(handler)
        with pytest.raises(LLMError) as exc_info:
            from optimistic import llm
            from optimistic.config import load_settings
            settings = load_settings(env={"LLM_BACKEND": "http", "LLM_MODEL": "m"})
            run(llm.complete("test", settings=settings, _transport=transport))
        assert str(exc_info.value)  # non-empty message

"""BUG-019 regression reproducer for GeminiBrain stale-cache failure.

Voice-Session 2026-05-11 starting at 17:22 went silent: Jarvis listened,
moved to THINKING, but never spoke. Log evidence:

    Brain gemini(gemini-3-flash-preview) fehlgeschlagen:
    403 Forbidden. {"error": {"code": 403,
        "message": "CachedContent not found (or permission denied)",
        "status": "PERMISSION_DENIED"}}
    Brain-Stream timed out after 40.0s — back to LISTENING

Root cause (annotated in ``jarvis/plugins/brain/gemini.py``):

* ``_ensure_cache`` lazily creates a Gemini context cache with
  ``ttl="3600s"`` and stores its name in ``self._cached_content_name``.
* When Gemini deletes the cache server-side (TTL expiry or eviction),
  the local string stays. The next ``generate_content_stream`` call
  sends the stale id, Gemini answers 403, the exception propagates,
  the BrainManager swaps to the next provider — but no one clears
  ``self._cached_content_name``. Every subsequent voice turn re-hits
  the same dead id and re-fails.

The fix now lives in ``GeminiBrain.complete()``: on a stale-cache 403 it
invalidates the dead cache and retries once inline. These two tests verify
it objectively, with no live API call:

* ``test_stale_cache_403_auto_recovers_without_manual_invalidate``
  drives the fix: a pre-poisoned ``_cached_content_name`` plus a matching
  signature makes the first request carry the dead id; ``complete()`` must
  catch the 403, invalidate, and retry inline so the turn succeeds.
* ``test_invalidate_cache_clears_state_so_next_call_can_succeed``
  proves the manual recovery direction still holds: after
  ``invalidate_cache()`` the provider's local state is clean, and the
  next call no longer sends the stale id.

Both tests use a synthetic ``Fake`` client. They do not require
``google-genai`` to be installed at runtime — the test patches
``_ensure_client`` to return the fake before any real SDK import.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain.gemini import GeminiBrain


_STALE_CACHE_NAME = "cachedContents/dead-id-from-previous-process-or-ttl-expiry"


class _GeminiCachedContentNotFound(Exception):
    """Mirrors the exception shape google-genai raises on a missing cache.

    The real SDK raises ``google.genai.errors.ClientError`` with a JSON
    payload. For the reproducer we only care that:
      (a) it inherits from ``Exception`` (which the BrainManager catches)
      (b) ``str(exc)`` contains the substring ``"CachedContent not found"``
          and ``"403"`` — both of which the log pipeline matches on.
    """

    def __str__(self) -> str:  # noqa: D401
        return (
            "403 Forbidden. {'message': '{\\n  \"error\": {\\n    "
            "\"code\": 403,\\n    \"message\": \"CachedContent not found "
            "(or permission denied)\",\\n    \"status\": "
            "\"PERMISSION_DENIED\"\\n  }\\n}', 'status': 'Forbidden'}"
        )


class _FakeGeminiClient:
    """Minimal stand-in for google-genai's async Client.

    Records the last ``config`` dict passed to
    ``aio.models.generate_content_stream`` so the test can assert on
    whether ``cached_content`` was sent. Raises
    ``_GeminiCachedContentNotFound`` whenever a non-empty
    ``cached_content`` is provided, mirroring the live failure.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        # Self-referential so ``client.aio.models.generate_content_stream``
        # resolves the same way the real SDK does.
        self.aio = SimpleNamespace(models=self)

    async def generate_content_stream(
        self,
        *,
        model: str,
        contents: list[Any],
        config: dict[str, Any],
    ) -> AsyncIterator[Any]:
        self.calls.append(dict(config))
        if config.get("cached_content"):
            raise _GeminiCachedContentNotFound()
        # Healthy path: yield one empty chunk so the stream consumer can
        # finish. The bug we're pinning is about the cache failure, not
        # about token streaming, so an empty-but-valid iterator suffices.
        async def _empty_stream() -> AsyncIterator[Any]:
            for _ in ():
                yield None
        return _empty_stream()


def _provider_with_stale_cache(
    cache_name: str = _STALE_CACHE_NAME,
) -> tuple[GeminiBrain, _FakeGeminiClient]:
    """Build a GeminiBrain whose local state already references a dead cache.

    Mirrors the production state right after a TTL-expired cache: the
    instance has ``_cached_content_name`` set and ``_cache_signature``
    matching whatever the next request will compute, so ``_ensure_cache``
    short-circuits to the existing name instead of creating a new one.
    """
    provider = GeminiBrain(model="gemini-3-flash-preview")
    client = _FakeGeminiClient()
    provider._client = client  # type: ignore[assignment]
    provider._cached_content_name = cache_name
    # Pre-compute the signature ``_ensure_cache`` would build for the
    # request below — that way the cache lookup hits and we exercise the
    # exact production path. ``system_text`` and ``tools_payload`` use
    # the same hashing as the real method.
    import json as _json

    system_text = "X" * (4096 * 4 + 100)  # well above _MIN_CACHE_TOKENS
    tools_payload: list[dict[str, Any]] | None = None
    provider._cache_signature = (
        str(hash(system_text)),
        str(hash(_json.dumps(tools_payload, sort_keys=True, default=str)))
        if tools_payload
        else "",
    )
    return provider, client


def _make_large_system_request() -> BrainRequest:
    """A request whose system prompt is large enough to clear the
    ``_MIN_CACHE_TOKENS = 4096`` floor; otherwise ``_ensure_cache``
    skips the cache path and the bug doesn't surface."""
    return BrainRequest(
        messages=(BrainMessage(role="user", content="ping"),),
        tools=(),
        system="X" * (4096 * 4 + 100),
    )


async def _drain(stream: AsyncIterator[Any]) -> list[Any]:
    """Materialise an async iterator. The reproducer just needs the
    coroutine to be awaited so the exception can fire."""
    out: list[Any] = []
    async for chunk in stream:
        out.append(chunk)
    return out


# ---------------------------------------------------------------------------
# Test 1 — Fix verifier: stale cache 403 ⇒ complete() auto-recovers.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_cache_403_auto_recovers_without_manual_invalidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-019 fix verifier (was the reproducer before the fix landed).

    Setup mimics production state ~1 hour after the cache was created
    (or any time Gemini has evicted it): the local
    ``_cached_content_name`` still points at the now-dead id, and the
    cache signature matches the next request — so ``_ensure_cache``
    returns the stale name instead of creating a new one. The first
    request therefore carries the dead ``cached_content`` and the fake
    Gemini rejects it with the exact ``403 "CachedContent not found"``.

    The fix in ``complete()`` must catch that ONE error, invalidate the
    dead cache, and retry inline (``system_instruction`` + tools, no
    ``cached_content``) so the turn SUCCEEDS — no propagated 403, no
    manual ``invalidate_cache()``, no 40 s timeout into silence.
    """
    monkeypatch.setenv("JARVIS_GEMINI_CONTEXT_CACHE", "1")

    provider, fake_client = _provider_with_stale_cache()

    # Must NOT raise — the fix recovers internally and the stream completes.
    result = await _drain(provider.complete(_make_large_system_request()))
    assert result == []  # empty but successful (recovered) stream

    # Two calls: #1 carried the stale id (rejected), #2 retried inline.
    assert len(fake_client.calls) == 2
    # First call: the smoking gun — stale id sent, system_instruction absent
    # (Gemini rejects cached_content + system_instruction together).
    assert fake_client.calls[0]["cached_content"] == _STALE_CACHE_NAME
    assert "system_instruction" not in fake_client.calls[0]
    # Second call: recovery — no stale id, system prompt back inline.
    assert "cached_content" not in fake_client.calls[1]
    assert "system_instruction" in fake_client.calls[1]

    # The poisoned local state is cleared so later turns start fresh.
    assert provider._cached_content_name is None
    assert provider._cache_signature is None


# ---------------------------------------------------------------------------
# Test 2 — Demonstrator: invalidate_cache() makes the next call clean.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_cache_clears_state_so_next_call_can_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invalidate_cache()`` clears the poisoned local state directly.

    The automatic recovery in ``complete()`` (test 1) calls this on a 403,
    but the method must also stand on its own so the contract is explicit:

    (a) it drops ``_cached_content_name`` and ``_cache_signature``;
    (b) the *next* call no longer sends a ``cached_content`` field built
        from the stale id.

    ``_ensure_cache`` is stubbed to return ``None`` so the next call takes
    the direct (no-cache) path without hitting ``client.aio.caches.create``.
    """
    monkeypatch.setenv("JARVIS_GEMINI_CONTEXT_CACHE", "1")

    provider, fake_client = _provider_with_stale_cache()

    # Poisoned to start (production state ~1h after cache creation).
    assert provider._cached_content_name == _STALE_CACHE_NAME

    # The recovery step:
    provider.invalidate_cache()
    assert provider._cached_content_name is None
    assert provider._cache_signature is None

    # Stub ``_ensure_cache`` so the next call takes the direct path without
    # hitting the real ``client.aio.caches.create`` API. ``None`` forces the
    # inline (system_instruction + tools) path — the same fallback the live
    # ``except`` in ``_ensure_cache`` already takes on its own SDK errors.
    async def _no_cache(*_args: Any, **_kwargs: Any) -> str | None:
        return None

    monkeypatch.setattr(provider, "_ensure_cache", _no_cache)

    # Next call — must succeed without sending cached_content.
    result = await _drain(provider.complete(_make_large_system_request()))
    assert result == []  # empty but successful stream

    assert len(fake_client.calls) == 1
    only_call = fake_client.calls[0]
    assert "cached_content" not in only_call, (
        "After invalidate_cache(), the next call must not carry the "
        "stale cache id."
    )
    assert "system_instruction" in only_call

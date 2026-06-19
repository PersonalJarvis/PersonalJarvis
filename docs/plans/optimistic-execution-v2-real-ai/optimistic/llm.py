"""Hardware-agnostic LLM completion via an OpenAI-compatible HTTP API.

The only external dependency is ``httpx`` (already in the environment).
No litellm, no openai SDK, no GPU/CUDA/OS-specific code.

Two backends:
- ``mock``: returns a deterministic string immediately, no network.
- ``http``: POST to ``{base_url}/chat/completions`` with an OpenAI-shaped body.

Test injection
--------------
Pass ``_transport`` (an ``httpx.AsyncTransport`` or ``httpx.MockTransport``)
to ``complete()`` to bypass real network I/O in tests. When omitted, a real
``httpx.AsyncClient`` is used.
"""
from __future__ import annotations

import httpx

from optimistic.config import LLMSettings


class LLMError(Exception):
    """Raised when an LLM call fails — non-2xx HTTP status or network error.

    Callers (the worker) catch this and retry once before giving up.
    """


async def complete(
    prompt: str,
    *,
    settings: LLMSettings,
    system: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Return the LLM completion for ``prompt``.

    Args:
        prompt:     The user message / task description.
        settings:   Provider configuration (model, URL, key, timeout, …).
        system:     Optional system prompt override. When None, the value from
                    ``settings.system_prompt`` is used if set; pass an empty
                    string to force no system message.
        _transport: Injected httpx transport for unit tests.  When None the
                    default httpx async transport (real network) is used.

    Returns:
        The assistant's reply text (``choices[0].message.content``).

    Raises:
        LLMError: on any network exception or non-2xx HTTP status.
    """
    if settings.use_mock:
        return _mock_complete(prompt, settings=settings)

    return await _http_complete(
        prompt,
        settings=settings,
        system=system,
        _transport=_transport,
    )


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------

def _mock_complete(prompt: str, *, settings: LLMSettings) -> str:
    """Return a deterministic, instant echo — no network, no randomness."""
    # Spec: f"[mock:{settings.model}] {prompt[:120]}"
    return f"[mock:{settings.model}] {prompt[:120]}"


# ---------------------------------------------------------------------------
# HTTP backend
# ---------------------------------------------------------------------------

async def _http_complete(
    prompt: str,
    *,
    settings: LLMSettings,
    system: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """POST to an OpenAI-compatible /v1/chat/completions endpoint."""
    # Build the message list.
    # The system kwarg (explicit call-time override) takes priority over
    # settings.system_prompt; None means "no system message".
    effective_system = system if system is not None else settings.system_prompt
    messages: list[dict] = []
    if effective_system:
        messages.append({"role": "system", "content": effective_system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": settings.model,
        "stream": False,
        "messages": messages,
    }

    headers: dict[str, str] = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    url = f"{settings.base_url.rstrip('/')}/chat/completions"

    try:
        async with httpx.AsyncClient(
            transport=_transport,
            timeout=settings.timeout,
        ) as client:
            response = await client.post(url, json=body, headers=headers)
    except Exception as exc:
        raise LLMError(f"HTTP request failed: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise LLMError(
            f"LLM API returned HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"Unexpected response shape: {exc}") from exc

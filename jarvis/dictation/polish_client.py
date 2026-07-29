"""Provider surface for the dictation polish pass — two adapters, six families.

The one branch in this module
-----------------------------
``PolishFamily.transport`` is either ``"openai_chat"`` or ``"gemini"``. That is
a WIRE FORMAT, not a vendor: five of the six families below speak the
OpenAI-compatible chat schema and one speaks Google's. Nothing anywhere
branches on ``family.id`` — the id exists so a user can pin a preference and so
a log line can name who answered (AP-21: gate on capability, never on a
provider name or model id).

Adding a family is therefore a row in :data:`POLISH_FAMILIES` and nothing else.

Why the chain is credential-derived
-----------------------------------
:func:`resolve_polish_chain` returns the families the user actually holds a key
for, one entry per family, primary first. A depleted or unreachable provider
crosses to a DIFFERENT family; it never falls back to a second model in the same
one, because a rate-limited account is rate-limited for all of its models
(AP-22). When the user holds no key anywhere the chain is EMPTY, the caller
reports ``unavailable`` and delivers the raw transcript — byte-identical to the
behaviour before this feature existed. That empty-chain path is the whole
open-source contract: the maintainer's key must never be the thing that makes
the default safe (AP-23).

Import weight
-------------
``httpx`` and ``google-genai`` are imported inside client construction, which
happens on the first dictation — never at import time, never at boot (AP-26).
``jarvis.core.config`` (Pydantic, heavy) is likewise imported lazily inside the
credential lookup.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

log = logging.getLogger(__name__)

#: The wire formats an adapter exists for. This — not a provider name — is the
#: only thing the code is allowed to branch on.
POLISH_TRANSPORTS: Final[tuple[str, ...]] = ("openai_chat", "gemini")


@dataclass(frozen=True, slots=True)
class PolishFamily:
    """One credential family the polish pass can reach.

    ``secret_candidates`` are keyring slot names in priority order, resolved
    through :func:`jarvis.core.config.get_secret` (keyring -> ENV -> ``.env`` ->
    local file), so a headless VPS with no Secret Service reaches exactly the
    same families as a desktop. An EMPTY tuple marks a keyless family (a local
    engine): it can be pinned, but it is never auto-selected — see
    :func:`resolve_polish_chain`.
    """

    id: str
    label: str
    transport: Literal["openai_chat", "gemini"]
    base_url: str
    secret_candidates: tuple[str, ...]
    default_model: str
    default_timeout_ms: int

    @property
    def needs_key(self) -> bool:
        """Whether this family is reachable only with a credential."""
        return bool(self.secret_candidates)


#: The single source of truth for the polish tier, in auto-selection order.
#:
#: The order is the ranking from the design's provider study: time-to-first-token
#: first, cost second, and — decisively for the first entry — whether the key is
#: likely to be present already. Groq leads because it is the shipped default STT
#: provider, so on most installs the feature turns itself on with no new account;
#: OpenRouter sits late because one OpenRouter key reaches every family, which
#: makes it the best universal floor rather than the best primary.
#:
#: Ollama is last and keyless: it is the offline floor for someone who wants no
#: cloud call at all, but it requires the opt-in local stack, so it is only ever
#: used when explicitly pinned (an auto chain that dialled localhost on every
#: dictation would spend the whole latency budget on a connection refusal).
POLISH_FAMILIES: Final[tuple[PolishFamily, ...]] = (
    PolishFamily(
        id="groq",
        label="Groq",
        transport="openai_chat",
        base_url="https://api.groq.com/openai/v1",
        secret_candidates=("groq_api_key",),
        default_model="llama-3.1-8b-instant",
        default_timeout_ms=1200,
    ),
    PolishFamily(
        id="cerebras",
        label="Cerebras",
        transport="openai_chat",
        base_url="https://api.cerebras.ai/v1",
        secret_candidates=("cerebras_api_key",),
        default_model="llama-3.3-70b",
        default_timeout_ms=1200,
    ),
    PolishFamily(
        id="gemini",
        label="Google Gemini",
        transport="gemini",
        base_url="https://generativelanguage.googleapis.com",
        # The same AI-Studio slots the Gemini brain/TTS/STT already read, so a
        # Gemini-only downloader needs no second credential.
        secret_candidates=(
            "gemini_api_key",
            "google_aistudio_api_key",
            "google_api_key",
        ),
        default_model="gemini-3.1-flash-lite",
        default_timeout_ms=1500,
    ),
    PolishFamily(
        id="openai",
        label="OpenAI",
        transport="openai_chat",
        base_url="https://api.openai.com/v1",
        secret_candidates=("openai_api_key",),
        default_model="gpt-4.1-nano",
        default_timeout_ms=1500,
    ),
    PolishFamily(
        id="openrouter",
        label="OpenRouter",
        transport="openai_chat",
        base_url="https://openrouter.ai/api/v1",
        secret_candidates=("openrouter_api_key",),
        default_model="meta-llama/llama-3.1-8b-instruct",
        default_timeout_ms=1500,
    ),
    PolishFamily(
        id="ollama",
        label="Ollama (local)",
        transport="openai_chat",
        base_url="http://localhost:11434/v1",
        secret_candidates=(),
        default_model="llama3.1:8b",
        default_timeout_ms=3000,
    ),
)

_FAMILY_BY_ID: Final[dict[str, PolishFamily]] = {f.id: f for f in POLISH_FAMILIES}


class PolishProviderError(RuntimeError):
    """A provider call failed in a way the caller may cross a family for.

    Carries the HTTP status when there was one, so the caller can tell a
    depleted account (402/429) from a broken credential (401) in a log line
    without re-parsing the message. The message itself stays English.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class PolishClient(Protocol):
    """What the orchestrator needs from a provider: one bounded completion."""

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        """Return the model's text, or ``None`` when it produced nothing."""
        ...


def family_by_id(family_id: object) -> PolishFamily | None:
    """Look a family up by its id; ``None`` for anything unknown."""
    return _FAMILY_BY_ID.get(str(family_id or "").strip().lower())


def family_has_key(family: PolishFamily) -> bool:
    """Whether this host holds a usable credential for *family*.

    A keyless family answers ``True``: there is nothing to hold. Reachability is
    a separate question and is answered by actually calling it — probing a local
    engine here would put a socket connect on the dictation path.
    """
    if not family.needs_key:
        return True
    return _first_secret(family.secret_candidates) is not None


def _first_secret(candidates: Sequence[str]) -> str | None:
    """First non-empty credential among *candidates*, or ``None``.

    ``jarvis.core.config`` is imported here rather than at module scope because
    it pulls Pydantic and the whole config model; this module must stay free to
    import from anywhere (AP-26).
    """
    from jarvis.core import config as _cfg

    for slot in candidates:
        try:
            value = _cfg.get_secret(slot)
        except Exception as exc:  # noqa: BLE001 — a locked keyring must not break dictation
            log.debug("polish credential lookup failed for %r: %s", slot, exc)
            continue
        if value:
            return value
    return None


def resolve_polish_chain(cfg: Any) -> tuple[PolishFamily, ...]:
    """Key-aware, family-crossing, honest-degrading provider order (AP-22).

    Returns the families the user actually holds a credential for, primary
    first, each from a DIFFERENT family. An empty tuple means no key anywhere —
    the caller then reports ``unavailable`` and delivers the raw text, which is
    exactly today's behaviour and must stay byte-identical (AP-23).

    ``[dictation].polish_provider`` is a user PIN, not a code branch: a
    recognised id is moved to the front and the remaining keyed families follow
    it as cross-family fallbacks, so pinning a preference never costs the user
    their resilience. A keyless local family enters the chain ONLY through such
    a pin. An unrecognised pin is ignored in favour of the auto order rather
    than emptying the chain — a typo in a config file must not silently disable
    a feature the user asked for.
    """
    pin = str(getattr(cfg, "polish_provider", "auto") or "auto").strip().lower()
    pinned = family_by_id(pin) if pin not in ("", "auto") else None
    if pinned is None and pin not in ("", "auto"):
        log.debug(
            "polish provider pin %r is not a known family; using the auto order.", pin
        )

    chain: list[PolishFamily] = []
    if pinned is not None and family_has_key(pinned):
        chain.append(pinned)
    for family in POLISH_FAMILIES:
        if family in chain:
            continue
        # Auto-selection is credential-driven, so a keyless local family is
        # skipped here; it is reachable only as an explicit pin (above).
        if not family.needs_key:
            continue
        if family_has_key(family):
            chain.append(family)
    return tuple(chain)


def resolve_model(family: PolishFamily, cfg: Any, *, primary_id: str) -> str:
    """The model id to use for *family* on this call.

    ``[dictation].polish_model`` applies to the PRIMARY family only. A model id
    is family-specific — ``llama-3.1-8b-instant`` means nothing to Gemini — so
    carrying the user's pinned model across a fallback would turn a recoverable
    outage into a guaranteed 404.
    """
    if family.id == primary_id:
        pinned = str(getattr(cfg, "polish_model", "") or "").strip()
        if pinned:
            return pinned
    return family.default_model


# --------------------------------------------------------------------------- #
# Transport adapters
# --------------------------------------------------------------------------- #


class _SharedHttpClient:
    """One keep-alive ``httpx.AsyncClient`` for every OpenAI-compatible family.

    A fresh client per dictation forces a fresh TCP + TLS handshake, which on a
    1200 ms budget is a meaningful slice of it. The client is rebound whenever
    the running event loop changes so a cached client is never reused across
    loops (each pytest-asyncio test runs in its own loop, and a reused client
    would raise ``RuntimeError: Event loop is closed``).
    """

    __slots__ = ("_client", "_loop")

    def __init__(self) -> None:
        self._client: Any | None = None
        self._loop: Any | None = None

    def get(self) -> Any:
        import asyncio

        import httpx

        loop = asyncio.get_running_loop()
        client = self._client
        if client is None or self._loop is not loop or client.is_closed:
            client = httpx.AsyncClient()
            self._client = client
            self._loop = loop
        return client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception as exc:  # noqa: BLE001 — teardown must never raise
                log.debug("polish HTTP client close failed: %s", exc)


_HTTP = _SharedHttpClient()


async def aclose_shared_client() -> None:
    """Release the pooled HTTP client. Safe to call repeatedly."""
    await _HTTP.aclose()


class OpenAIChatPolishClient:
    """Adapter for every family speaking the OpenAI chat-completions schema."""

    __slots__ = ("_family", "_model", "_api_key")

    def __init__(self, family: PolishFamily, *, model: str, api_key: str | None) -> None:
        self._family = family
        self._model = model
        self._api_key = api_key

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "stream": False,
        }
        data = await self._post(payload, timeout_s=timeout_s)
        choices = data.get("choices") or []
        if not choices:
            return None
        message = (choices[0] or {}).get("message") or {}
        text = str(message.get("content") or "").strip()
        return text or None

    async def _post(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        """POST the payload, retrying ONCE against a schema-shape rejection.

        Newer OpenAI-schema endpoints renamed ``max_tokens`` to
        ``max_completion_tokens`` and pin ``temperature`` to 1 on some models.
        Both show up as a 400 naming the offending field, so the retry is driven
        by what the SERVER said rather than by a model-id allowlist that would
        rot with every release (AP-21).
        """
        import httpx

        url = f"{self._family.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        client = _HTTP.get()

        for attempt in (0, 1):
            try:
                response = await client.post(
                    url, json=payload, headers=headers, timeout=timeout_s
                )
            except httpx.HTTPError as exc:
                raise PolishProviderError(
                    f"{self._family.label} polish request failed: {exc}"
                ) from exc
            if response.status_code < 400:
                return response.json()
            body = _safe_body(response)
            if attempt == 0 and response.status_code == 400:
                adjusted = _relax_payload(payload, body)
                if adjusted is not None:
                    payload = adjusted
                    continue
            raise PolishProviderError(
                f"{self._family.label} polish request returned "
                f"HTTP {response.status_code}: {body[:200]}",
                status=response.status_code,
                retry_after=_retry_after(response),
            )
        # Unreachable: the loop either returns or raises on both attempts.
        raise PolishProviderError(f"{self._family.label} polish request failed")


def _safe_body(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:  # noqa: BLE001 — a body we cannot read is not a new failure
        return ""


def _retry_after(response: Any) -> float | None:
    try:
        value = response.headers.get("retry-after")
    except Exception:  # noqa: BLE001
        return None
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _relax_payload(payload: dict[str, Any], body: str) -> dict[str, Any] | None:
    """Rewrite the one field the server complained about, or ``None``.

    Returns a NEW dict so a retry can never accumulate half-applied edits.
    """
    lowered = body.lower()
    adjusted = dict(payload)
    changed = False
    if "max_completion_tokens" in lowered and "max_tokens" in adjusted:
        adjusted["max_completion_tokens"] = adjusted.pop("max_tokens")
        changed = True
    if "temperature" in lowered and "temperature" in adjusted:
        adjusted.pop("temperature")
        changed = True
    return adjusted if changed else None


class GeminiPolishClient:
    """Adapter for Google's generate-content schema."""

    __slots__ = ("_family", "_model", "_api_key", "_client")

    def __init__(self, family: PolishFamily, *, model: str, api_key: str) -> None:
        self._family = family
        self._model = model
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self, timeout_s: float) -> Any:
        if self._client is None:
            from google import genai

            # google-genai forces ``timeout=None`` on its own httpx client, so
            # an explicit http_options timeout is the ONLY thing below the
            # caller's wait_for that can stop a hung request from holding a
            # connection open after we have already given up on it.
            self._client = genai.Client(
                api_key=self._api_key,
                http_options={"timeout": int(max(timeout_s, 0.1) * 1000)},
            )
        return self._client

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        try:
            client = self._ensure_client(timeout_s)
            from google.genai import types as genai_types

            response = await client.aio.models.generate_content(
                model=self._model,
                contents=[user],
                config=genai_types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — the SDK raises its own hierarchy
            raise PolishProviderError(
                f"{self._family.label} polish request failed: {exc}"
            ) from exc
        text = str(getattr(response, "text", "") or "").strip()
        return text or None


def build_polish_client(family: PolishFamily, *, model: str) -> PolishClient | None:
    """Construct the adapter for *family*, or ``None`` when it is unusable.

    ``None`` (rather than an exception) is the answer for "no credential" and
    for "the SDK this transport needs is not installed", because both are
    ordinary states on some install, and the caller's correct response to both
    is identical: try the next family.
    """
    api_key = _first_secret(family.secret_candidates) if family.needs_key else None
    if family.needs_key and not api_key:
        return None
    try:
        if family.transport == "gemini":
            return GeminiPolishClient(family, model=model, api_key=str(api_key))
        return OpenAIChatPolishClient(family, model=model, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 — an unbuildable family is just the next one
        log.warning(
            "polish client for %r not buildable (%s); trying the next family.",
            family.id,
            exc.__class__.__name__,
        )
        return None


__all__ = [
    "POLISH_FAMILIES",
    "POLISH_TRANSPORTS",
    "GeminiPolishClient",
    "OpenAIChatPolishClient",
    "PolishClient",
    "PolishFamily",
    "PolishProviderError",
    "aclose_shared_client",
    "build_polish_client",
    "family_by_id",
    "family_has_key",
    "resolve_model",
    "resolve_polish_chain",
]

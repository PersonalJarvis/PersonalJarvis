"""Google Gemini Brain with native and portable HTTP transports.

Gemini has its own functionCall format. We normalize to
`BrainDelta.tool_call = {id, name, input}`.

The native ``google-genai`` SDK remains the preferred transport because it
supports Gemini-specific features such as context caching. When that SDK or
one of its native/authentication dependencies cannot import, the provider
uses Google's official OpenAI-compatible HTTPS endpoint. The fallback keeps
core text, vision, streaming, and function-calling available on platforms
where the Google SDK dependency graph has no installable wheel.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash"
OPENAI_COMPAT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_TRANSPORT_NATIVE = "native"
_TRANSPORT_OPENAI_COMPAT = "openai-compatible"

# Gemini 3 requires a thought signature when an application reconstructs an
# assistant function-call step. Jarvis deliberately normalizes provider output
# into BrainDelta/BrainMessage instead of retaining the raw response object, so
# Google's documented validator sentinel is the honest compatibility value.
_TOOL_HISTORY_EXTRA_CONTENT = {
    "google": {"thought_signature": "skip_thought_signature_validator"},
}

# Latency-Sprint-2: ENV switch for the context cache. BrainManager sets this
# when ``[performance].gemini_context_cache = true``. The cache holds the
# system prompt + tools; vision frames stay non-cached (they vary per turn).
_ENV_CONTEXT_CACHE = "JARVIS_GEMINI_CONTEXT_CACHE"
# Minimum cache size: Gemini caches have a token floor (>= ~4096 tokens
# depending on the model). For smaller prefixes the API either rejects it or
# the cache isn't worth it — then we skip it and fall back to the direct path.
_MIN_CACHE_TOKENS = 4096


def _is_stale_context_cache_error(exc: Exception) -> bool:
    """True if *exc* is Gemini's stale-context-cache failure (BUG-019).

    When a server-side cache (``ttl="3600s"``) is evicted before the local
    ``_cached_content_name`` is cleared, the next request carries a dead id
    and Gemini answers ``403 ... "CachedContent not found (or permission
    denied)"``. We match on the message text because the concrete exception
    class differs across ``google-genai`` versions (``ClientError`` /
    ``APIError`` / a wrapped ``Exception``).

    Narrow on purpose: a generic 403 (account/quota) must NOT match, so we
    require a cache marker in the message. The caller additionally gates on
    ``cache_name`` being set, so this only fires when we actually sent one.
    """
    msg = str(exc).lower()
    if "cachedcontent not found" in msg:
        return True
    if "cached_content" in msg and "not found" in msg:
        return True
    if (
        ("403" in msg or "permission_denied" in msg or "permission denied" in msg)
        and "cache" in msg
    ):
        return True
    return False


def _to_gemini_contents(messages: tuple[BrainMessage, ...]) -> list[dict[str, Any]]:
    """BrainMessages → Gemini contents array. Role mapping: assistant→model.

    Multimodal: `BrainMessage.images` are appended as `inline_data` parts
    (`{"inline_data": {"mime_type": ..., "data": ...}}`) — only for user
    messages, since Gemini doesn't accept model-role images as input.
    """
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue  # system goes via system_instruction
        role = "user" if m.role in ("user", "tool") else "model"
        if m.role == "tool":
            # FunctionResponse
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": m.name or "",
                        "response": {"result": str(m.content)},
                    }
                }],
            })
            continue
        text = m.content if isinstance(m.content, str) else json.dumps(m.content, default=str)

        # Multimodal only makes sense on the user role — images are appended to
        # the text part. If text is empty, we leave the text part out.
        # `getattr` for backwards-compat (Protocol pre-Wave-1-B1 had no images).
        images = getattr(m, "images", ()) or ()
        parts: list[dict[str, Any]] = []
        if text:
            parts.append({"text": text})
        if m.role == "user" and images:
            for img in images:
                parts.append({
                    "inline_data": {
                        "mime_type": img.mime,
                        "data": img.data_b64,
                    }
                })
        if not parts:
            # Defensive: a Gemini content must not be empty.
            parts.append({"text": ""})
        contents.append({"role": role, "parts": parts})
    return contents


# OpenAI-specific JSON-schema fields that Gemini's `Tool.functionDeclarations`
# Pydantic validator does NOT accept. Phase 7.3 self-mod tools set
# `strict=True` + `input_examples=[...]` at the schema root. Without this
# cleanup, Gemini rejects the request with GenerateContentConfig validation errors
# (Bug #API-1, 2026-04-29).
_GEMINI_FORBIDDEN_SCHEMA_KEYS: frozenset[str] = frozenset({
    "strict",            # OpenAI strict-mode flag
    "input_examples",    # Phase 7.3 SelfMod input_examples
    "additionalProperties",  # OpenAI 2024-08 strict-mode marker
    "additional_properties",  # google-genai may receive pydantic's snake_case form
    "$schema",           # JSON-Schema meta
    "$id",
    # JSON-schema keywords the google-genai Schema model (extra="forbid")
    # rejects. The Schema model accepts only its documented subset — verified
    # 2026-06-01 against google-genai 1.67 (types.Schema.model_fields). The
    # ``exclusive*`` bounds below are first CONVERTED to ``minimum``/``maximum``
    # in ``_sanitize_for_gemini`` (constraint-preserving) and only then dropped
    # here; the rest are simply not part of Gemini's schema dialect.
    "exclusiveMinimum",  # Pydantic Field(gt=N) — converted to ``minimum``
    "exclusiveMaximum",  # Pydantic Field(lt=N) — converted to ``maximum``
    "exclusive_minimum",  # snake_case variant
    "exclusive_maximum",  # snake_case variant
    "$defs",             # Pydantic emits these for nested models / refs
    "definitions",       # draft-07 definitions block
    "examples",          # plural — Gemini only accepts singular ``example``
    "const",             # not in Gemini's subset
})


def _convert_exclusive_bounds(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON-schema ``exclusive{Minimum,Maximum}`` to ``{minimum,maximum}``.

    Pydantic ``Field(gt=N)`` / ``Field(lt=N)`` emit ``exclusiveMinimum`` /
    ``exclusiveMaximum`` (draft 2020-12 numeric form). Gemini's Schema model
    only knows the inclusive ``minimum`` / ``maximum``, so we down-convert —
    a pragmatic, well-known fix that keeps the bound essentially in place
    (``> 0`` becomes ``>= 0``) rather than silently dropping the constraint.

    Rules:
      * numeric ``exclusiveMinimum`` → ``minimum`` (only if no explicit
        ``minimum`` already present — an inclusive bound must never be
        weakened by the conversion);
      * numeric ``exclusiveMaximum`` → ``maximum`` (same guard);
      * boolean ``exclusive*`` (draft-04 flag form) carries no numeric value,
        so it is left in the dict and dropped later by the forbidden-key pass.

    The originating ``exclusive*`` key itself stays in the returned dict; it is
    removed by ``_sanitize_for_gemini`` (it lives in
    ``_GEMINI_FORBIDDEN_SCHEMA_KEYS``). Mutates a shallow copy, not the input.
    """
    out = dict(schema)
    excl_min = out.get("exclusiveMinimum", out.get("exclusive_minimum"))
    if isinstance(excl_min, (int, float)) and not isinstance(excl_min, bool):
        out.setdefault("minimum", excl_min)
    excl_max = out.get("exclusiveMaximum", out.get("exclusive_maximum"))
    if isinstance(excl_max, (int, float)) and not isinstance(excl_max, bool):
        out.setdefault("maximum", excl_max)
    return out


def _sanitize_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively removes OpenAI-specific fields from a JSON schema.

    Gemini's google-genai SDK validates Tool.functionDeclarations.parameters
    via Pydantic with `extra="forbid"`. Fields like `strict`/`input_examples`
    are OpenAI tool-use conventions and not allowed in Gemini's schema
    subset. Instead of delivering a tool call without tools, we strip those
    fields out — the tools themselves keep working unchanged, only the
    OpenAI-specific hints are gone.

    Exclusive numeric bounds (``exclusiveMinimum``/``exclusiveMaximum`` from
    Pydantic ``Field(gt=...)``/``Field(lt=...)``) are first converted to the
    inclusive ``minimum``/``maximum`` Gemini accepts, then the exclusive key is
    stripped via ``_GEMINI_FORBIDDEN_SCHEMA_KEYS``.
    """
    if not isinstance(schema, dict):
        return schema
    schema = _convert_exclusive_bounds(schema)
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in _GEMINI_FORBIDDEN_SCHEMA_KEYS:
            continue
        if isinstance(v, dict):
            out[k] = _sanitize_for_gemini(v)
        elif isinstance(v, list):
            out[k] = [
                _sanitize_for_gemini(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            out[k] = v
    return out


# Gemini function-name rule (live forensic 2026-06-01, data/jarvis_desktop.log
# 23:34:57): names must match ^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$. Connected
# MCP/marketplace plugin tools (the "plugin-tools" virtual loader) carry names
# that violate this — spaces, slashes, leading digits, over-length. Left raw,
# Gemini rejects the WHOLE request (400 INVALID_ARGUMENT on every offending
# function_declaration[i]); the active provider then fails over into the dead
# fallback chain (claude-api 401 / grok 403) and the chain-error diagnostic is
# spoken aloud. We coerce names to the rule and keep a reverse map so an
# inbound function_call still resolves to the original tool — the executor
# only knows the original name.
_GEMINI_NAME_FORBIDDEN_RE = re.compile(r"[^A-Za-z0-9_.:-]")
# 1 leading char + up to 127 trailing = 128 total (Gemini rule {0,127}).
_GEMINI_NAME_MAXLEN = 128


def _sanitize_gemini_function_name(name: str, taken: set[str]) -> str:
    """Coerce ``name`` to the Gemini function-name rule, unique vs ``taken``.

    Deterministic and identity-preserving for already-valid names (so the
    common case — the router tools — round-trips for free). Collisions get a
    short numeric suffix so the original→sanitized map stays bijective; tool-
    call resolution depends on that round-trip.
    """
    cleaned = _GEMINI_NAME_FORBIDDEN_RE.sub("_", name or "")
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "_" + cleaned
    if len(cleaned) > _GEMINI_NAME_MAXLEN:
        cleaned = cleaned[:_GEMINI_NAME_MAXLEN]
    if cleaned not in taken:
        return cleaned
    base = cleaned
    i = 1
    while cleaned in taken:
        suffix = f"_{i}"
        cleaned = base[: _GEMINI_NAME_MAXLEN - len(suffix)] + suffix
        i += 1
    return cleaned


def _gemini_tool_name_map(tools: tuple[dict[str, Any], ...]) -> dict[str, str]:
    """Deterministic original→Gemini-safe tool-name map.

    Single source of truth for both ``_tools_gemini_format`` (outbound
    declarations) and ``complete()`` (inbound function_call back-translation).
    Idempotent: re-running on the same tuple yields the same mapping, so the
    forward build and the reverse build always agree.
    """
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    for t in tools or ():
        original = t.get("name", "")
        safe = _sanitize_gemini_function_name(original, taken)
        taken.add(safe)
        mapping[original] = safe
    return mapping


def _build_gemini_tool_declarations(
    tools: tuple[dict[str, Any], ...],
) -> tuple[list[dict[str, Any]] | None, dict[str, str]]:
    """Build Gemini ``functionDeclarations`` AND the original→safe name map in
    a single pass.

    Returning both from one call is the single source of truth: the outbound
    declarations (sanitized names) and the inbound function_call back-
    translation in ``complete()`` (reverse of this map) can never disagree, and
    the per-turn tool list is iterated only once.
    """
    if not tools:
        return None, {}
    name_map = _gemini_tool_name_map(tools)
    declarations = []
    for t in tools:
        raw_schema = t.get("input_schema") or t.get("parameters") or t.get("schema") or {}
        schema = _sanitize_for_gemini(raw_schema) if raw_schema else {}
        original = t.get("name", "")
        declarations.append({
            "name": name_map.get(original, original),
            "description": t.get("description", ""),
            "parameters": schema if schema else {"type": "object", "properties": {}},
        })
    return [{"functionDeclarations": declarations}], name_map


def _tools_gemini_format(tools: tuple[dict[str, Any], ...]) -> list[dict[str, Any]] | None:
    """Backward-compatible wrapper — declarations payload only (no name map)."""
    payload, _ = _build_gemini_tool_declarations(tools)
    return payload


def _openai_compat_base_url(resolved_base_url: str | None) -> str:
    """Return the compatibility URL matching a resolved native endpoint.

    ``resolve_provider_endpoint`` returns the native Gemini root. Google's
    compatibility API lives below ``/openai/``; the team proxy mirrors the
    same path shape. An explicitly compatible URL is left unchanged.
    """
    if not resolved_base_url:
        return OPENAI_COMPAT_BASE_URL

    base = resolved_base_url.rstrip("/")
    if base.endswith("/openai"):
        return f"{base}/"
    if base == "https://generativelanguage.googleapis.com":
        return OPENAI_COMPAT_BASE_URL
    return f"{base}/openai/"


def _create_native_client(endpoint: Any) -> Any:
    """Create the preferred google-genai client at the import boundary."""
    from google import genai

    client_kwargs: dict[str, Any] = {"api_key": endpoint.credential}
    if endpoint.base_url:
        from google.genai import types as genai_types

        client_kwargs["http_options"] = genai_types.HttpOptions(
            base_url=endpoint.base_url,
        )
    return genai.Client(**client_kwargs)


def _create_openai_compat_client(endpoint: Any) -> Any:
    """Create the portable client for Gemini's official compatibility API."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=endpoint.credential,
        base_url=_openai_compat_base_url(endpoint.base_url),
        timeout=CLIENT_TIMEOUT,
    )


def _is_native_dependency_import_error(exc: BaseException) -> bool:
    """Whether an exception chain reports an unavailable native SDK module."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ImportError):
            missing = str(getattr(current, "name", "") or "").lower()
            message = str(current).lower()
            if (
                not missing
                or missing == "google"
                or missing.startswith(("google.", "cryptography", "grpc"))
                or any(
                    marker in message
                    for marker in ("google.genai", "google.auth", "cryptography", "grpc")
                )
            ):
                return True
        current = current.__cause__ or current.__context__
    return False


def _openai_compat_request(req: BrainRequest) -> BrainRequest:
    """Copy a request with schemas accepted by Gemini's compatibility API."""
    sanitized_tools: list[dict[str, Any]] = []
    for tool in req.tools:
        copied = dict(tool)
        raw_schema = (
            tool.get("input_schema")
            or tool.get("parameters")
            or tool.get("schema")
            or {}
        )
        copied["input_schema"] = (
            _sanitize_for_gemini(raw_schema)
            if raw_schema
            else {"type": "object", "properties": {}}
        )
        sanitized_tools.append(copied)
    return BrainRequest(
        messages=req.messages,
        tools=tuple(sanitized_tools),
        system=req.system,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=req.stream,
    )


class GeminiBrain:
    name: str = "gemini"
    context_window: int = 1_048_576
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(
        self,
        model: str | None = None,
        *,
        thinking_budget: int | None = None,
    ) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None
        self._transport = _TRANSPORT_NATIVE
        # Latency-Sprint-1: thinking budget controls how much "extended
        # thinking" Gemini 3.x does. ``None`` = SDK default (auto, higher
        # latency). ``0`` = off, ``-1`` = dynamic, ``>0`` = fixed cap.
        self._thinking_budget = thinking_budget
        # Latency-Sprint-2: context-cache name (lazily created on the first
        # call with system+tools). Key: (system_hash, tools_hash) → cache_name.
        # Only one entry per instance, because system+tools are constant for
        # a running voice session. On change (e.g. a tool reload), the cache
        # is discarded via ``invalidate_cache()``.
        self._cached_content_name: str | None = None
        self._cache_signature: tuple[str, str] | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("gemini")
            if not ep.credential:
                raise RuntimeError(
                    "No Gemini API key found. Set GEMINI_API_KEY or "
                    "GOOGLE_AIStudio_API_KEY in .env / Credential Manager."
                )
            try:
                self._client = _create_native_client(ep)
                self._transport = _TRANSPORT_NATIVE
            except (ImportError, AttributeError) as exc:
                # A platform can have the OpenAI SDK and a valid Gemini key
                # while google-genai's auth/native dependency graph is not
                # installable. Keep Gemini itself usable through Google's
                # documented HTTPS compatibility API. Log only the exception
                # class: dependency messages can contain local paths, and the
                # credential must never enter logs.
                log.info(
                    "Gemini native SDK unavailable (%s); using the "
                    "OpenAI-compatible transport",
                    type(exc).__name__,
                )
                self._client = _create_openai_compat_client(ep)
                self._transport = _TRANSPORT_OPENAI_COMPAT
        return self._client

    async def _ensure_cache(
        self, system_text: str, tools_payload: list[dict[str, Any]] | None,
    ) -> str | None:
        """Latency-Sprint-2: lazy init of the context cache.

        Creates a Gemini cache (system+tools) on the first call and returns
        the cache name — subsequent calls can reference it via
        ``cached_content``. On too-small a prefix or an API error, returns
        ``None``, and the caller falls back to the direct path (brain stays
        functional).
        """
        # Cache signature: hash + cache, so a tool reload re-triggers the
        # lazy init. Hash on the serialized content — cheap and sufficient
        # for an identity check.
        sig = (
            str(hash(system_text)),
            str(hash(json.dumps(tools_payload, sort_keys=True, default=str)))
            if tools_payload
            else "",
        )
        if self._cache_signature == sig and self._cached_content_name:
            return self._cached_content_name

        # Estimate the minimum size (heuristic: 1 token ~ 4 chars).
        approx_tokens = (len(system_text) + len(json.dumps(tools_payload or []))) // 4
        if approx_tokens < _MIN_CACHE_TOKENS:
            log.debug(
                "Gemini cache skipped (prefix ~%d tokens < %d minimum size)",
                approx_tokens, _MIN_CACHE_TOKENS,
            )
            return None

        try:
            from google.genai import types as _genai_types
            cache = await self._client.aio.caches.create(
                model=self._model,
                config=_genai_types.CreateCachedContentConfig(
                    system_instruction=system_text or None,
                    tools=tools_payload or None,
                    ttl="3600s",
                ),
            )
            self._cached_content_name = getattr(cache, "name", None)
            self._cache_signature = sig
            log.info("Gemini context cache created: %s (tokens ~%d)",
                     self._cached_content_name, approx_tokens)
            return self._cached_content_name
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini cache create failed, falling back to direct: %s", exc)
            return None

    def invalidate_cache(self) -> None:
        """Forget cache identity, for example after a tool reload.

        ⚠ BUG-019 (2026-05-11) — this method is defined but NEVER called
        automatically. The only production trigger that should invalidate
        the cache is the server-side TTL expiry (CachedContent is created
        with ``ttl="3600s"`` in ``_ensure_cache``), which Gemini signals by
        returning 403 "CachedContent not found (or permission denied)" on
        the next ``generate_content_stream`` call. That 403 is caught by
        ``BrainManager`` at ``manager.py:1440`` as a generic provider
        failure — it does NOT clear the provider's local cache reference,
        so the next voice turn re-uses the dead cache name and re-fails
        with the same 403. See the long comment around the
        ``generate_content_stream`` call below for the full failure
        timeline and the suggested fix path.
        """
        self._cached_content_name = None
        self._cache_signature = None

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        if self._transport == _TRANSPORT_OPENAI_COMPAT:
            async for delta in stream_complete(
                client,
                self._model,
                _openai_compat_request(req),
                supports_vision=self.supports_vision,
                assistant_tool_call_extra_content=_TOOL_HISTORY_EXTRA_CONTENT,
            ):
                yield delta
            return

        contents = _to_gemini_contents(req.messages)
        system_parts: list[str] = [m.content for m in req.messages
                                   if m.role == "system" and isinstance(m.content, str)]
        if req.system:
            system_parts.append(req.system)

        config_dict: dict[str, Any] = {
            "temperature": req.temperature,
            "max_output_tokens": req.max_tokens,
        }
        system_text = "\n\n".join(system_parts) if system_parts else ""
        # One pass builds both the outbound declarations and the name map.
        tools_payload, _tool_name_map = _build_gemini_tool_declarations(req.tools)
        # Inbound resolution: Gemini calls back the SANITIZED name; the tool
        # executor only knows the original. Invert the forward map (empty when
        # there are no tools). See _build_gemini_tool_declarations.
        tool_name_reverse = {safe: original for original, safe in _tool_name_map.items()}

        # Latency-Sprint-2: if caching is enabled, try to put system+tools
        # into a cache entry. On success, set ``cached_content`` instead of
        # ``system_instruction``+``tools`` (Gemini doesn't allow both at
        # once). On skip/fail: direct path.
        cache_name: str | None = None
        if os.environ.get(_ENV_CONTEXT_CACHE) == "1":
            cache_name = await self._ensure_cache(system_text, tools_payload)

        if cache_name:
            config_dict["cached_content"] = cache_name
            # System instruction + tools now live in the cache — do NOT send
            # them again, or Gemini rejects the request.
        else:
            if system_text:
                config_dict["system_instruction"] = system_text
            if tools_payload:
                config_dict["tools"] = tools_payload

        # Jarvis runs its OWN tool-use loop, so Gemini's Automatic Function
        # Calling (AFC) must be OFF. With AFC on (the SDK default) Gemini makes
        # its own tool round-trips and, finding no executable Python callable for
        # a declaration-only tool, leaks the function_call as response TEXT.
        # Forensic 2026-06-27: a voice "switch the worker from antigravity to
        # codex" hit AFC ("AFC is enabled with max remote calls: 10"), the call
        # leaked, the recovery path ran the WRONG tool, and the turn stalled
        # 108 s into the brain-timeout fallback. ``disable=True`` makes Gemini
        # return a clean structured function_call the stream loop consumes — fast
        # and exact. Best-effort: an SDK too old to know the field must not kill
        # the call (it just keeps the slower legacy behavior).
        try:
            from google.genai import types as _genai_types
            config_dict["automatic_function_calling"] = (
                _genai_types.AutomaticFunctionCallingConfig(disable=True)
            )
        except Exception:  # noqa: BLE001 — never break the brain call over an SDK shape change
            log.debug("could not disable Gemini automatic_function_calling", exc_info=True)

        # Latency-Sprint-1: thinking budget. Only when explicitly set —
        # otherwise we leave the choice to the SDK default (previous
        # behavior). ``ThinkingConfig`` is available from google-genai >= 0.7;
        # older SDK versions don't have the field and raise AttributeError. In
        # that case we ignore the value instead of killing the whole brain
        # call (best-effort optimization).
        if self._thinking_budget is not None:
            try:
                from google.genai import types as _genai_types
                config_dict["thinking_config"] = _genai_types.ThinkingConfig(
                    thinking_budget=self._thinking_budget,
                )
            except (ImportError, AttributeError):
                pass

        # ╔══════════════════════════════════════════════════════════════╗
        # ║  BUG-019 ROOT CAUSE — STALE GEMINI CONTEXT-CACHE REFERENCE   ║
        # ╠══════════════════════════════════════════════════════════════╣
        # ║                                                              ║
        # ║ Symptom (Voice-Session 2026-05-11 starting at 17:22):        ║
        # ║   User hears nothing after speaking. Pipeline state goes     ║
        # ║   LISTENING → THINKING → silently back to LISTENING; TTS is  ║
        # ║   never invoked. The orb spins, the user waits, no answer.  ║
        # ║                                                              ║
        # ║ Log trace per failing turn (data/jarvis_desktop.log):        ║
        # ║   T+0.0   → Brain ...                                        ║
        # ║   T+0.7   Brain gemini(gemini-3-flash-preview) failed        ║
        # ║           403 Forbidden. "CachedContent not found            ║
        # ║           (or permission denied)"                            ║
        # ║   T+40.0  Brain-Stream timed out after 40.0s — back to       ║
        # ║           LISTENING                                          ║
        # ║                                                              ║
        # ║ Root cause:                                                  ║
        # ║   * ``_ensure_cache`` creates a server-side Gemini cache     ║
        # ║     with ``ttl="3600s"`` and stores its name in              ║
        # ║     ``self._cached_content_name``.                           ║
        # ║   * After 1 h (or sooner, if Google's infra evicts it for    ║
        # ║     other reasons), Gemini deletes the cache server-side.    ║
        # ║   * Local Python state STILL holds the dead cache name.      ║
        # ║   * The next ``generate_content_stream`` call below sends    ║
        # ║     ``config_dict["cached_content"] = <dead_name>`` and      ║
        # ║     Gemini answers 403.                                      ║
        # ║   * That exception propagates up to ``BrainManager._call_    ║
        # ║     provider_chain`` (manager.py:1440), which logs the       ║
        # ║     provider as failed, appends to ``provider_errors`` and   ║
        # ║     continues to the next provider in the fallback chain —  ║
        # ║     but it does NOT touch the failing provider's state.     ║
        # ║   * Therefore ``self._cached_content_name`` remains the      ║
        # ║     same dead string FOREVER, until Jarvis restarts.         ║
        # ║   * Each subsequent voice turn repeats the same 403; the     ║
        # ║     fallback chain spends ~40 s rotating through Anthropic,  ║
        # ║     OpenRouter, OpenAI, etc., usually never producing text   ║
        # ║     in time. The pipeline's hard cap (``brain_timeout_s =    ║
        # ║     40.0`` in ``speech/pipeline.py``) trips and returns to   ║
        # ║     LISTENING with an empty response — which then hits the   ║
        # ║     "filler/ack response suppressed" branch in               ║
        # ║     ``_handle_utterance`` and silently `return True`s        ║
        # ║     without calling ``_speak``.                              ║
        # ║   * That is the silent THINKING → LISTENING transition the   ║
        # ║     user observes.                                           ║
        # ║                                                              ║
        # ║ Why ``invalidate_cache()`` doesn't help:                     ║
        # ║   The method exists (see above) but has no automatic         ║
        # ║   trigger. It is only called by ``scripts/voice_e2e_probe``  ║
        # ║   and by unit tests. The production code path never reaches  ║
        # ║   it after a 403.                                            ║
        # ║                                                              ║
        # ║ Right fix (for the upcoming PR — do not implement here yet,  ║
        # ║ this commit is annotation-only at the user's request):       ║
        # ║                                                              ║
        # ║   Wrap this ``generate_content_stream`` call in a try/except ║
        # ║   that recognises the cache-not-found error class. When it   ║
        # ║   fires, call ``self.invalidate_cache()`` and retry ONCE     ║
        # ║   without the ``cached_content`` field (re-add ``system_     ║
        # ║   instruction`` and ``tools`` to ``config_dict`` for the     ║
        # ║   retry). Pseudocode:                                        ║
        # ║                                                              ║
        # ║       try:                                                   ║
        # ║           stream = await client.aio.models.generate_         ║
        # ║               content_stream(...)                            ║
        # ║       except <PermissionDenied / 403 with                    ║
        # ║                "CachedContent not found">:                   ║
        # ║           self.invalidate_cache()                            ║
        # ║           config_dict.pop("cached_content", None)            ║
        # ║           if system_text:                                    ║
        # ║               config_dict["system_instruction"] = system_text║
        # ║           if tools_payload:                                  ║
        # ║               config_dict["tools"] = tools_payload           ║
        # ║           stream = await client.aio.models.generate_         ║
        # ║               content_stream(...)  # retry without cache     ║
        # ║                                                              ║
        # ║   This costs one extra round trip in the rare case the      ║
        # ║   cache expired, and the very next turn will lazily re-     ║
        # ║   create a fresh cache via ``_ensure_cache``. The current    ║
        # ║   broken state (stale name forever) is avoided.              ║
        # ║                                                              ║
        # ║ Alternative considered: clear the cache on the BrainManager  ║
        # ║ side by giving the manager a ``provider.invalidate_cache()`` ║
        # ║ hook to call after specific error kinds. Rejected because    ║
        # ║ it leaks Gemini-specific semantics into the cross-provider   ║
        # ║ orchestration layer. The cache lives in this file; the      ║
        # ║ recovery should live here too.                               ║
        # ╚══════════════════════════════════════════════════════════════╝
        # BUG-019 recovery: a server-side context cache can be evicted (TTL
        # expiry / Gemini infra) while our local ``_cached_content_name``
        # still points at it. The next request then carries a dead id and
        # Gemini answers 403 "CachedContent not found". Previously that
        # propagated, the BrainManager swapped providers, and the poisoned
        # cache name survived forever → every later voice turn 40s-timed-out
        # into silence "until restart". We now catch that ONE error, drop the
        # dead cache, and retry once with system_instruction + tools inline.
        # The cache-not-found error fires before any token is generated, so
        # the from-scratch retry yields no duplicate chunks.
        attempt = 0
        yielded_delta = False
        while True:
            attempt += 1
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config_dict,
                )
                async for chunk in stream:
                    # Text
                    text = getattr(chunk, "text", None)
                    if text:
                        yielded_delta = True
                        yield BrainDelta(content=text)

                    # Function-Calls
                    candidates = getattr(chunk, "candidates", None) or []
                    for cand in candidates:
                        content_obj = getattr(cand, "content", None)
                        if content_obj is None:
                            continue
                        parts = getattr(content_obj, "parts", None) or []
                        for part in parts:
                            fc = getattr(part, "function_call", None)
                            if fc is None:
                                continue
                            name = getattr(fc, "name", "")
                            args = getattr(fc, "args", {}) or {}
                            if hasattr(args, "items"):
                                args = dict(args)
                            yielded_delta = True
                            yield BrainDelta(tool_call={
                                "id": f"gemini_{uuid4().hex[:8]}",
                                "name": tool_name_reverse.get(name, name),
                                "input": args,
                            })
                        finish = getattr(cand, "finish_reason", None)
                        if finish:
                            yielded_delta = True
                            yield BrainDelta(finish_reason=str(finish))

                    usage = getattr(chunk, "usage_metadata", None)
                    if usage is not None:
                        yielded_delta = True
                        yield BrainDelta(usage={
                            "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                            "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
                            "cache_read_input_tokens": int(
                                getattr(usage, "cached_content_token_count", 0) or 0
                            ),
                        })
                return
            except Exception as exc:  # noqa: BLE001 — BUG-019 stale-cache recovery
                if (
                    attempt == 1
                    and cache_name
                    and _is_stale_context_cache_error(exc)
                ):
                    log.warning(
                        "Gemini stale context-cache (BUG-019) — invalidating "
                        "and retrying once without cache: %s", exc,
                    )
                    self.invalidate_cache()
                    config_dict.pop("cached_content", None)
                    if system_text:
                        config_dict["system_instruction"] = system_text
                    if tools_payload:
                        config_dict["tools"] = tools_payload
                    continue
                if (
                    not yielded_delta
                    and self._transport == _TRANSPORT_NATIVE
                    and _is_native_dependency_import_error(exc)
                ):
                    # Some google-genai releases import auth/native modules
                    # only when the first stream starts. Recover only before
                    # any delta was emitted, so a retry can never duplicate
                    # text or execute a tool twice.
                    ep = cfg.resolve_provider_endpoint("gemini")
                    if not ep.credential:
                        raise
                    log.info(
                        "Gemini native stream dependency unavailable (%s); "
                        "using the OpenAI-compatible transport",
                        type(exc).__name__,
                    )
                    self._client = _create_openai_compat_client(ep)
                    self._transport = _TRANSPORT_OPENAI_COMPAT
                    async for delta in stream_complete(
                        self._client,
                        self._model,
                        _openai_compat_request(req),
                        supports_vision=self.supports_vision,
                        assistant_tool_call_extra_content=_TOOL_HISTORY_EXTRA_CONTENT,
                    ):
                        yield delta
                    return
                raise

    def estimate_cost(self, req: BrainRequest) -> float:
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 1.25 + req.max_tokens * 5) / 1_000_000

"""The ONLY per-vendor knowledge in the proxy.

Three concerns live here and nowhere else:

1. ``PROVIDER_VENDORS`` — the ``provider_id -> (vendor, default_base_url)``
   map. This is the wire contract shared with the client; it gets the
   anti-drift parity treatment (see ``tests/test_parity.py``).
2. Credential rules per *vendor* — how to read the inbound per-user token from
   the request, and how to place the real vendor key on the outbound request.
3. Usage parsing per *vendor* — best-effort token-count extraction from a
   buffered response body (JSON or SSE). A parse miss returns ``None`` and
   never raises.

Adding a provider that uses an existing vendor is a one-line edit to
``PROVIDER_VENDORS``. Adding a genuinely new vendor wire shape touches the three
dispatch tables below.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# --------------------------------------------------------------------------
# provider_id -> (vendor, default real base url)  — THE WIRE CONTRACT
# --------------------------------------------------------------------------

PROVIDER_VENDORS: dict[str, tuple[str, str]] = {
    "claude-api": ("anthropic", "https://api.anthropic.com"),
    "openai": ("openai_compatible", "https://api.openai.com/v1"),
    "openrouter": ("openai_compatible", "https://openrouter.ai/api/v1"),
    "grok": ("openai_compatible", "https://api.x.ai/v1"),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com"),
    "groq-api": ("openai_compatible", "https://api.groq.com/openai/v1"),
}

# The set of vendors the credential / usage dispatch tables understand.
KNOWN_VENDORS: frozenset[str] = frozenset(
    {"openai_compatible", "anthropic", "gemini"}
)


def resolve_provider(provider_id: str) -> tuple[str, str] | None:
    """``provider_id -> (vendor, default_base_url)`` or ``None`` if unknown."""
    return PROVIDER_VENDORS.get(provider_id)


# --------------------------------------------------------------------------
# inbound credential extraction (read the per-user token off the request)
# --------------------------------------------------------------------------


def _bearer(value: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    The ``Bearer`` scheme is REQUIRED: a header without it returns ``None`` so
    the caller fails closed (401). A bare header value is never treated as a
    token.
    """
    if not value:
        return None
    prefix = "bearer "
    if value.lower().startswith(prefix):
        return value[len(prefix):].strip() or None
    return None


def extract_inbound_token(
    vendor: str,
    headers: dict[str, str],
    *,
    query: dict[str, str],
) -> str | None:
    """Read the inbound per-user token per the vendor's credential rule.

    ``headers`` keys are treated case-insensitively by the caller (it lowercases
    them); this function assumes lowercase keys.
    """
    if vendor == "openai_compatible":
        return _bearer(headers.get("authorization"))
    if vendor == "anthropic":
        return (headers.get("x-api-key") or "").strip() or None
    if vendor == "gemini":
        header = (headers.get("x-goog-api-key") or "").strip()
        if header:
            return header
        return (query.get("key") or "").strip() or None
    return None


# --------------------------------------------------------------------------
# outbound credential placement (set the real vendor key, drop the inbound one)
# --------------------------------------------------------------------------


def place_outbound_credential(
    vendor: str,
    *,
    headers: dict[str, str],
    query: dict[str, str],
    real_key: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (headers, query) with the real vendor credential set in place.

    The caller has already stripped all inbound auth headers (header hygiene);
    this only *sets* the real credential. ``headers`` / ``query`` are mutated
    and also returned for convenience.
    """
    if vendor == "openai_compatible":
        headers["authorization"] = f"Bearer {real_key}"
    elif vendor == "anthropic":
        headers["x-api-key"] = real_key
    elif vendor == "gemini":
        headers["x-goog-api-key"] = real_key
        # Never forward an inbound ?key= (the proxy token) to the vendor.
        query.pop("key", None)
    return headers, query


# --------------------------------------------------------------------------
# usage parsing (best-effort)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedUsage:
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def _iter_sse_data_objects(body: bytes) -> list[dict]:
    """Yield JSON objects from SSE ``data:`` lines; ignore non-JSON lines."""
    objects: list[dict] = []
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return objects
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def _try_whole_json(body: bytes) -> dict | None:
    try:
        obj = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _parse_openai_usage(body: bytes) -> ParsedUsage | None:
    candidates: list[dict] = []
    whole = _try_whole_json(body)
    if whole is not None:
        candidates.append(whole)
    candidates.extend(_iter_sse_data_objects(body))

    model: str | None = None
    usage: dict | None = None
    for obj in candidates:
        if isinstance(obj.get("model"), str):
            model = obj["model"]
        if isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
    if usage is None:
        return None
    return ParsedUsage(
        model=model,
        prompt_tokens=_as_int(usage.get("prompt_tokens")),
        completion_tokens=_as_int(usage.get("completion_tokens")),
        total_tokens=_as_int(usage.get("total_tokens")),
    )


def _parse_anthropic_usage(body: bytes) -> ParsedUsage | None:
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def absorb(obj: dict) -> None:
        nonlocal model, input_tokens, output_tokens
        msg = obj.get("message")
        if isinstance(msg, dict):
            if isinstance(msg.get("model"), str):
                model = msg["model"]
            mu = msg.get("usage")
            if isinstance(mu, dict):
                if (v := _as_int(mu.get("input_tokens"))) is not None:
                    input_tokens = v
                if (v := _as_int(mu.get("output_tokens"))) is not None:
                    output_tokens = v
        if isinstance(obj.get("model"), str):
            model = obj["model"]
        u = obj.get("usage")
        if isinstance(u, dict):
            if (v := _as_int(u.get("input_tokens"))) is not None:
                input_tokens = v
            # message_delta carries the final cumulative output count.
            if (v := _as_int(u.get("output_tokens"))) is not None:
                output_tokens = v

    whole = _try_whole_json(body)
    if whole is not None:
        absorb(whole)
    for obj in _iter_sse_data_objects(body):
        absorb(obj)

    if input_tokens is None and output_tokens is None:
        return None
    total = None
    if input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return ParsedUsage(
        model=model,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total,
    )


def _parse_gemini_usage(body: bytes) -> ParsedUsage | None:
    candidates: list[dict] = []
    whole = _try_whole_json(body)
    if whole is not None:
        candidates.append(whole)
    candidates.extend(_iter_sse_data_objects(body))

    model: str | None = None
    meta: dict | None = None
    for obj in candidates:
        if isinstance(obj.get("modelVersion"), str):
            model = obj["modelVersion"]
        if isinstance(obj.get("usageMetadata"), dict):
            meta = obj["usageMetadata"]
    if meta is None:
        return None
    return ParsedUsage(
        model=model,
        prompt_tokens=_as_int(meta.get("promptTokenCount")),
        completion_tokens=_as_int(meta.get("candidatesTokenCount")),
        total_tokens=_as_int(meta.get("totalTokenCount")),
    )


def parse_usage(vendor: str, body: bytes) -> ParsedUsage | None:
    """Best-effort usage parse for a buffered body; never raises."""
    try:
        if vendor == "openai_compatible":
            return _parse_openai_usage(body)
        if vendor == "anthropic":
            return _parse_anthropic_usage(body)
        if vendor == "gemini":
            return _parse_gemini_usage(body)
    except Exception:  # noqa: BLE001 — metering must never fail the request
        return None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None

"""Live provider connectivity test — the honest "does this provider actually
answer?" check behind the API-Keys "Test" button.

The desktop API-Keys view shows a green "configured" badge whenever a key STRING
is present in the credential store. That badge says nothing about whether the
provider answers: a stored-but-invalid key, an out-of-credits account, or a
missing model all look identical to "configured". This module makes a REAL,
minimal call through the exact plugin the app uses and classifies the outcome
into an honest status that separates an *integration* problem (broken code /
unreachable) from an *account* state (invalid key / no credits / not set).

``classify_provider_error`` is the pure, message-string core (kept regex-only,
no SDK imports, so it works uniformly across the Anthropic/OpenAI-compatible/
Gemini/httpx error shapes). ``PROVIDER_TEST_STATUSES`` is the single source of
truth for the status vocabulary; the Pydantic ``Literal`` and the TS union
mirror it (anti-drift, BUG-008 class).
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

# ── Status vocabulary — SINGLE SOURCE OF TRUTH ────────────────────────────────
# Mirrored by the Pydantic Literal (provider_routes) and the TS union
# (useProviders.ts). A parity test asserts they stay in lock-step.
OK = "ok"                                # real call succeeded end-to-end
NOT_CONFIGURED = "not_configured"        # no credential stored — nothing to test
BAD_KEY = "bad_key"                      # provider rejected the credential (401)
NO_CREDITS = "no_credits"                # reached + auth ok, but out of credits / quota
RATE_LIMITED = "rate_limited"            # 429 transient throttle
MODEL_UNAVAILABLE = "model_unavailable"  # auth ok, configured model 404 / no access
UNREACHABLE = "unreachable"              # network / DNS / timeout / provider 5xx
ERROR = "error"                          # anything else — a possible integration bug

PROVIDER_TEST_STATUSES: tuple[str, ...] = (
    OK,
    NOT_CONFIGURED,
    BAD_KEY,
    NO_CREDITS,
    RATE_LIMITED,
    MODEL_UNAVAILABLE,
    UNREACHABLE,
    ERROR,
)

# Statuses where the INTEGRATION itself is proven sound — the provider was
# reached and answered at the protocol level; only the credential/account/model
# is the blocker. The UI frames these as "would work with a valid funded key".
INTEGRATION_OK_STATUSES: frozenset[str] = frozenset(
    {OK, NOT_CONFIGURED, BAD_KEY, NO_CREDITS, RATE_LIMITED, MODEL_UNAVAILABLE}
)

_HTTP_CODE_RE = re.compile(r"\b([45]\d\d)\b")

# Money / quota / budget markers — a credential that AUTHENTICATES but is refused
# for a billing/budget/quota reason (an *account* state, not a bad key). This is
# the SINGLE canonical list shared with the runtime dead-list classifier
# (jarvis/brain/manager.py::_is_account_blocked_exc) so the test-badge and the live
# fallback chain can never drift apart on what "out of credits / over budget" means.
# Every entry is a phrase observed live or documented from a real provider 4xx body.
#
# HARD INVARIANT: none of these may be a substring of a transient
# "rate limit exceeded" message (that must stay rate_limited / take the cooldown,
# never dead-list) — so there is deliberately NO bare "limit exceeded" / "limit"
# here, only qualified forms ("key limit", "spend limit", "total limit", …).
BILLING_LIMIT_MARKERS = (
    "credit",            # "credit balance too low", "used all available credits"
    "spending limit",
    "spend limit",
    "key limit",         # OpenRouter per-key cap: "Key limit exceeded (total limit)"
    "total limit",       # OpenRouter per-key cap (the parenthetical)
    "credit limit",
    "usage limit",
    "monthly limit",
    "budget",            # "monthly budget exceeded for this key"
    "billing",           # OpenAI "check your plan and billing details"
    "insufficient_quota",
    "insufficient quota",
    "out of funds",
    "out of credits",
    "no credits",
    "payment",           # HTTP 402 Payment Required bodies
    "quota exceeded",
    "exceeded your quota",
    "exceeded your current quota",
    "prepayment",        # Gemini "prepayment credits are depleted"
    "depleted",
    "plan and billing",
    "plans & billing",
)

# Back-compat alias (anything importing the old private name keeps working).
_BILLING_MARKERS = BILLING_LIMIT_MARKERS

# "No key stored" shapes from the plugins' own _ensure_client guards.
_MISSING_KEY_MARKERS = (
    ("kein", "gefunden"),          # "Kein OpenAI-API-Key gefunden ..."
    ("no api key",),               # "No API key found ..."
    ("no key found",),
    ("not configured",),
    ("missing api key",),
)

# Reachability failures (no HTTP status code present).
_UNREACHABLE_MARKERS = (
    "connection error",
    "connection",
    "timed out",
    "timeout",
    "could not resolve",
    "getaddrinfo",
    "network",
    "econnrefused",
    "name or service not known",
)


def _has_billing(msg: str) -> bool:
    return any(m in msg for m in _BILLING_MARKERS)


def classify_provider_error(message: str | None) -> str:
    """Map a raw provider error *message* to a :data:`PROVIDER_TEST_STATUSES` value.

    Pure + regex-only on purpose: it must work identically whether the string
    came from an Anthropic ``AuthenticationError``, an OpenAI-compatible
    ``RateLimitError``, a Gemini ``ClientError`` or a bare ``httpx`` failure —
    without importing (or depending on) any provider SDK.
    """
    if not message:
        return ERROR
    msg = message.lower()

    # "No credential stored" beats everything — there is nothing to authenticate.
    for markers in _MISSING_KEY_MARKERS:
        if all(m in msg for m in markers):
            return NOT_CONFIGURED

    code_match = _HTTP_CODE_RE.search(msg)
    code = int(code_match.group(1)) if code_match else None

    if code == 400:
        # A 400 means the provider PARSED our request and rejected its CONTENT
        # (an unknown model id, a foreign voice, an unsupported response_format /
        # parameter). The credential still AUTHENTICATED (else 401) and cleared
        # billing (else 402), so the integration itself is sound — this must NOT
        # show as a red "bad key". The live trigger: OpenRouter TTS answered
        # HTTP 400 "Mistral TTS only supports response_format=\"mp3\". Got
        # \"pcm\"." for a perfectly valid, funded key. Frame it as
        # "would work with a valid model/voice" (integration_ok), never red.
        if _has_billing(msg):
            return NO_CREDITS
        if any(
            k in msg
            for k in (
                "model",
                "voice",
                "response_format",
                "format",
                "parameter",
                "unsupported",
                "not supported",
                "does not exist",
            )
        ):
            return MODEL_UNAVAILABLE
        return ERROR
    if code == 401:
        return BAD_KEY
    if code == 402:
        return NO_CREDITS
    if code == 403:
        return NO_CREDITS if _has_billing(msg) else BAD_KEY
    if code == 429:
        return NO_CREDITS if _has_billing(msg) else RATE_LIMITED
    if code == 404:
        return MODEL_UNAVAILABLE if "model" in msg else ERROR
    if code is not None and 500 <= code <= 599:
        # Provider-side outage — not the user's key.
        return UNREACHABLE

    # No HTTP status code in the message.
    if any(m in msg for m in _UNREACHABLE_MARKERS):
        return UNREACHABLE
    if _has_billing(msg):
        return NO_CREDITS
    return ERROR


# ── The live per-tier connectivity test ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    """Outcome of one live provider test.

    ``status`` is one of :data:`PROVIDER_TEST_STATUSES`; ``detail`` is a short,
    human-readable note (never a secret); ``latency_ms`` is the round-trip time
    of the real call when one was made.
    """

    provider: str
    status: str
    detail: str = ""
    latency_ms: float = 0.0


def _is_credential_present(spec: Any) -> bool:
    from jarvis.brain.app_control import is_credential_present

    return is_credential_present(spec)


def _resolve_brain_model(cfg: Any, provider: str) -> str:
    """Best-effort lookup of the configured model for a brain provider."""
    try:
        providers = getattr(getattr(cfg, "brain", None), "providers", None)
        if isinstance(providers, dict):
            pc = providers.get(provider)
        else:
            pc = getattr(providers, provider, None)
        return getattr(pc, "model", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _default_make_tts(cfg: Any, provider: str) -> Any:
    from jarvis.plugins.tts import _build_provider

    return _build_provider(cfg.tts, provider)


def _default_make_stt(cfg: Any, provider: str) -> Any:
    import copy as _copy

    from jarvis.plugins.stt import build_stt_from_config

    stt_cfg = _copy.copy(cfg.stt)
    try:
        object.__setattr__(stt_cfg, "provider", provider)
    except Exception:  # noqa: BLE001
        stt_cfg.provider = provider  # type: ignore[attr-defined]
    return build_stt_from_config(stt_cfg)


def _default_codex_status() -> Any:
    from jarvis.codex_auth import CodexAuthService

    return CodexAuthService().status()


def _default_antigravity_status() -> Any:
    from jarvis.google_cli.auth_service import GoogleCliAuthService

    return GoogleCliAuthService().status()


async def _silence_chunks():
    """One ~0.5 s 16 kHz mono PCM16 silence chunk — enough for a cloud STT to
    authenticate and answer (with an empty transcript); never long enough to
    matter for cost. Imported lazily to avoid a hard protocol import at module
    load."""
    from jarvis.core.protocols import AudioChunk

    sr = 16000
    pcm = b"\x00\x00" * int(sr * 0.5)
    yield AudioChunk(pcm=pcm, sample_rate=sr, timestamp_ns=0, channels=1)


async def run_provider_test(
    spec: Any,
    cfg: Any,
    *,
    present: bool | None = None,
    brain_probe: Callable[[str, str], Awaitable[Any]] | None = None,
    make_tts: Callable[[Any, str], Any] | None = None,
    make_stt: Callable[[Any, str], Any] | None = None,
    codex_status: Callable[[], Any] | None = None,
    antigravity_status: Callable[[], Any] | None = None,
    timeout_s: float = 25.0,
) -> ProviderTestResult:
    """Run a REAL minimal call against ``spec``'s provider and classify it.

    The network-touching seams (``brain_probe`` / ``make_tts`` / ``make_stt`` /
    ``codex_status``) default to the production wiring and are injectable so the
    dispatch logic is unit-testable without hitting a live provider.
    """
    provider = spec.id

    # Codex is OAuth-or-key: a connected ChatGPT login IS a working subagent.
    if spec.auth_mode == "codex":
        status_fn = codex_status or _default_codex_status
        try:
            st = status_fn()
        except Exception as exc:  # noqa: BLE001
            return ProviderTestResult(provider, ERROR, f"{type(exc).__name__}: {exc}")
        if getattr(st, "connected", False):
            return ProviderTestResult(provider, OK, getattr(st, "message", "") or "Connected.")
        return ProviderTestResult(
            provider, NOT_CONFIGURED, getattr(st, "message", "") or "Not connected.",
        )

    # Antigravity is OAuth-only (Google subscription): a connected login IS a
    # working brain. NEVER run a real agy/gemini turn here — it is slow (~8s) and
    # bills the subscription; the connected status is the honest, instant signal.
    # (A real turn would also false-OK on AntigravityBrain's empty progress tick.)
    if spec.auth_mode == "antigravity":
        status_fn = antigravity_status or _default_antigravity_status
        try:
            st = status_fn()
        except Exception as exc:  # noqa: BLE001
            return ProviderTestResult(provider, ERROR, f"{type(exc).__name__}: {exc}")
        if getattr(st, "connected", False):
            return ProviderTestResult(provider, OK, getattr(st, "message", "") or "Connected.")
        return ProviderTestResult(
            provider, NOT_CONFIGURED, getattr(st, "message", "") or "Not connected.",
        )

    # API-key providers: a missing credential is "not_configured" — nothing to test.
    if spec.auth_mode == "api_key":
        is_present = present if present is not None else _is_credential_present(spec)
        if not is_present:
            return ProviderTestResult(provider, NOT_CONFIGURED, "No credential stored.")

    if spec.tier == "brain":
        if brain_probe is None:
            async def brain_probe(p: str, m: str) -> Any:  # type: ignore[misc]
                from jarvis.brain.healthcheck import BrainHealthChecker
                from jarvis.brain.provider_registry import BrainProviderRegistry

                checker = BrainHealthChecker(BrainProviderRegistry())
                return await checker.probe(p, m, timeout_s=timeout_s)

        model = _resolve_brain_model(cfg, provider)
        hr = await brain_probe(provider, model)
        if getattr(hr, "ok", False):
            return ProviderTestResult(provider, OK, "", getattr(hr, "duration_ms", 0.0))
        err = getattr(hr, "error", None)
        return ProviderTestResult(
            provider, classify_provider_error(err), err or "", getattr(hr, "duration_ms", 0.0),
        )

    if spec.tier == "tts":
        builder = make_tts or _default_make_tts
        try:
            inst = builder(cfg, provider)
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderTestResult(provider, classify_provider_error(detail), str(exc))
        start = perf_counter()
        try:
            total = 0
            async for chunk in inst.synthesize("Test."):
                total += len(getattr(chunk, "pcm", b"") or b"")
                if total > 0:
                    break
            dur = (perf_counter() - start) * 1000.0
            if total > 0:
                return ProviderTestResult(provider, OK, f"{total} audio bytes", dur)
            return ProviderTestResult(provider, ERROR, "synthesized 0 bytes", dur)
        except Exception as exc:  # noqa: BLE001
            dur = (perf_counter() - start) * 1000.0
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderTestResult(provider, classify_provider_error(detail), str(exc), dur)

    # stt
    builder = make_stt or _default_make_stt
    try:
        inst = builder(cfg, provider)
    except (ImportError, ModuleNotFoundError):
        # M5: the local faster-whisper STT needs the [desktop] extra. On a headless
        # base install the import raises — that is "not installed", an actionable
        # amber chip, NOT a red "integration bug".
        return ProviderTestResult(
            provider,
            NOT_CONFIGURED,
            "Local STT not installed — add the [desktop] extra (pip install -e '.[desktop]').",
        )
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        return ProviderTestResult(provider, classify_provider_error(detail), str(exc))

    if spec.auth_mode == "none":
        # Local provider (faster-whisper): a successful build loads the model;
        # we do not run a network call. Building it IS the test.
        return ProviderTestResult(provider, OK, "Local provider built.")

    start = perf_counter()
    try:
        tr = await asyncio.wait_for(inst.transcribe(_silence_chunks()), timeout=timeout_s)
        dur = (perf_counter() - start) * 1000.0
        text = (getattr(tr, "text", "") or "").strip()
        return ProviderTestResult(provider, OK, f"transcript={text!r}", dur)
    except TimeoutError:
        dur = (perf_counter() - start) * 1000.0
        return ProviderTestResult(provider, UNREACHABLE, f"timeout after {timeout_s:.0f}s", dur)
    except Exception as exc:  # noqa: BLE001
        dur = (perf_counter() - start) * 1000.0
        detail = f"{type(exc).__name__}: {exc}"
        return ProviderTestResult(provider, classify_provider_error(detail), str(exc), dur)

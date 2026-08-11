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
    "spending cap",
    "spend limit",
    "spend cap",
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
    # Machine-readable TOKEN twins of the prose forms above. A transport that
    # redacts a provider body (the Codex app-server keeps only method + code)
    # can still forward a bounded `error.type`, and that token is then the ONLY
    # evidence of the account state — spelled with underscores, so the
    # space-separated entries above never match it. Both are terminal shapes
    # ("reached"/"exceeded" against a quota), never a transient throttle, so
    # they satisfy the hard invariant and are safe for the dead-list path in
    # manager.py::_is_account_blocked_exc, which shares this list.
    "usage_limit_reached",
    "quota_exceeded",
)

# Transient throttling as a machine TOKEN, for the same redacted-transport
# reason — but kept OUT of the billing list on purpose: a rate limit clears by
# itself and must never dead-list a funded account. Checked after the billing
# markers so a message carrying both still reads as the terminal state.
_RATE_LIMIT_TOKEN_MARKERS = (
    "rate_limit_exceeded",
    "rate limit exceeded",
    "too many requests",
)

# Back-compat alias (anything importing the old private name keeps working).
_BILLING_MARKERS = BILLING_LIMIT_MARKERS

# "No key stored" shapes from the plugins' own _ensure_client guards.
_MISSING_KEY_MARKERS = (
    ("kein", "gefunden"),  # i18n-allow: legacy German key-error shape
    ("no api key",),               # "No API key found ..."
    ("no key found",),
    ("not configured",),
    ("missing api key",),
    # Local OpenAI-compatible provider without a configured server URL —
    # "enter the address on the card", the same actionable amber as a
    # missing key (see jarvis/plugins/brain/local_openai.py).
    ("needs a server url",),
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
    # Local providers phrase it honestly ("Ollama not reachable at … — is it
    # running?"); the classifier must land on UNREACHABLE, not a red ERROR.
    "not reachable",
)

# A reachable LOCAL server with nothing to run — the fix is a pull/load, not
# a key or a network check (see the local brain plugins' error texts).
_NO_LOCAL_MODEL_MARKERS = (
    "no models installed",
    "lists no models",
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
    if any(m in msg for m in _NO_LOCAL_MODEL_MARKERS):
        # Reachable local server, nothing loaded — "pull a model", not an
        # integration bug and not a connectivity problem.
        return MODEL_UNAVAILABLE
    if any(m in msg for m in _UNREACHABLE_MARKERS):
        return UNREACHABLE
    if _has_billing(msg):
        return NO_CREDITS
    if any(m in msg for m in _RATE_LIMIT_TOKEN_MARKERS):
        # A throttle whose transport dropped the HTTP status. Without this the
        # only honest read left is "possible integration bug", which sends the
        # user hunting a defect instead of waiting or topping up.
        return RATE_LIMITED
    return ERROR


# ── Turning a provider's own 4xx body into a sentence ────────────────────────
# A provider writes its error body for a log, not for the person holding the
# key. Google answers a spent free-tier DAY with ~500 characters of nested JSON
# whose single actionable fact — *this model, N requests, per day* — sits in the
# middle of it. Printed verbatim on a card the whole thing reads as a broken key
# or a broken integration, and that is exactly what it cost live (2026-08-11):
# the Tool Model tab went red over a daily cap while the SAME key kept serving
# the realtime session from a different quota, so the obvious conclusion drawn
# was "the key is wrong" — and no amount of re-testing could disprove it,
# because every retry reproduced the same wall of JSON.
#
# Pure and regex-only for the same reason as the classifier above: it must read
# a Gemini ``ClientError`` without importing any provider SDK. A shape it does
# not recognise returns "" — the caller then keeps the raw text, so an unknown
# error is never replaced by a confident guess about what it means.

# Google's prose summary line: "limit: 20, model: gemini-3.6-flash".
_GOOGLE_QUOTA_PROSE_RE = re.compile(r"limit:\s*(\d+)\s*,\s*model:\s*([A-Za-z0-9._-]+)")
# The machine twins, for a body whose prose line was truncated in transport.
_GOOGLE_QUOTA_VALUE_RE = re.compile(r'"quotaValue"\s*:\s*"?(\d+)"?')
_GOOGLE_QUOTA_MODEL_RE = re.compile(r'"model"\s*:\s*"([A-Za-z0-9._-]+)"')

# The free-tier DAILY caps — the one quota shape that comes back on a clock
# rather than on a payment, which is why it earns its own wording. Either
# marker alone is conclusive; Google sends both.
_GOOGLE_FREE_TIER_DAY_MARKERS = (
    "generaterequestsperdayperprojectpermodel-freetier",
    "generate_content_free_tier_requests",
)


def explain_provider_error(message: str | None) -> str:
    """A short actionable sentence for *message*, or ``""`` to keep it raw.

    Only recognised shapes are rewritten. The caller is expected to fall back to
    the original text (``explain_provider_error(x) or x``) so that an unfamiliar
    error still reaches the user in full rather than being flattened into a
    friendly sentence that does not fit it.
    """
    if not message:
        return ""
    if not any(m in message.lower() for m in _GOOGLE_FREE_TIER_DAY_MARKERS):
        return ""

    prose = _GOOGLE_QUOTA_PROSE_RE.search(message)
    if prose is not None:
        limit, model = prose.group(1), prose.group(2)
    else:
        limit_match = _GOOGLE_QUOTA_VALUE_RE.search(message)
        model_match = _GOOGLE_QUOTA_MODEL_RE.search(message)
        limit = limit_match.group(1) if limit_match else ""
        model = model_match.group(1) if model_match else ""

    allowance = f"{limit} requests per day" if limit else "a limited number of requests per day"
    subject = model or "this model"
    return (
        f"Free tier day limit reached — Google allows {allowance} for {subject}, "
        "and today's are spent. The key itself is valid: this cap is counted per "
        "model per day, and a realtime session draws on a different quota "
        "entirely. It resets at midnight Pacific time. To lift it now, add "
        "billing to this key's Google project or point this tier at another model."
    )


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
    """The model the runtime would actually run for a brain provider.

    Mirrors the manager's resolution order (explicit config override, then the
    router-tier frontier default). Falling through to "" here would hand the
    probe to the plugin's hardcoded ``DEFAULT_MODEL`` — which can drift from the
    curated tier default and made a fresh install's health check 404 on a model
    id the real dispatch path never uses (AP-23 class).
    """
    model = ""
    try:
        providers = getattr(getattr(cfg, "brain", None), "providers", None)
        if isinstance(providers, dict):
            pc = providers.get(provider)
        else:
            pc = getattr(providers, provider, None)
        model = getattr(pc, "model", "") or ""
    except Exception:  # noqa: BLE001
        model = ""
    if model:
        return model
    try:
        # Lazy: manager imports this module (BILLING_LIMIT_MARKERS), so a
        # module-level import here would be a cycle.
        from jarvis.brain.manager import get_tier_default_model

        return get_tier_default_model("router", provider) or ""
    except Exception:  # noqa: BLE001
        return ""


def _resolve_provider_option(cfg: Any, provider: str, field: str) -> str:
    """Read a per-provider model or voice override without assuming its family."""
    try:
        providers = getattr(getattr(cfg, "brain", None), "providers", None)
        provider_config = providers.get(provider) if isinstance(providers, dict) else None
        return str(getattr(provider_config, field, "") or "")
    except Exception:  # noqa: BLE001
        return ""


async def _default_realtime_probe(spec: Any, cfg: Any, *, timeout_s: float) -> float:
    """Open and close the exact selected realtime provider.

    A sibling text-model call cannot validate the duplex endpoint, model id,
    session schema, audio formats, or SDK event contract. The provider adapter's
    ``open_session`` returns only after its handshake is accepted, so this is a
    small but authoritative connectivity check with no audio sent.
    """
    from jarvis.core.config import get_secret_any
    from jarvis.core.registry import load
    from jarvis.realtime.protocol import RealtimeProvider, RealtimeSessionConfig

    provider_cls = load("jarvis.realtime", spec.id, protocol=RealtimeProvider)
    credentials = tuple(getattr(provider_cls, "credential_candidates", ()) or ())
    api_key = get_secret_any(credentials) if credentials else None
    if credentials and not api_key:
        raise RuntimeError("No API key configured for the realtime provider")
    if api_key:
        provider = provider_cls(api_key=api_key)
    else:
        # A keyless card (a server the user hosts themselves) declares no
        # credential slots at all. Building it the key way would demand a
        # credential it does not have and report a working endpoint as broken —
        # so it is built the same way the session factory builds it, from its
        # own configuration.
        from_runtime_config = getattr(provider_cls, "from_runtime_config", None)
        provider = (
            from_runtime_config(cfg) if callable(from_runtime_config) else provider_cls()
        )
    session_config = RealtimeSessionConfig(
        instructions="Connection validation only. Do not respond until audio arrives.",
        language="en",
        model=_resolve_provider_option(cfg, spec.id, "model"),
        voice=_resolve_provider_option(cfg, spec.id, "voice"),
        input_sample_rate=int(getattr(provider, "input_sample_rate", 16_000)),
        output_sample_rate=int(getattr(provider, "output_sample_rate", 24_000)),
        modalities=("audio",),
    )
    started = perf_counter()
    session = await asyncio.wait_for(
        provider.open_session(session_config), timeout=timeout_s
    )
    try:
        return (perf_counter() - started) * 1000.0
    finally:
        await session.close()


def _default_make_tts(cfg: Any, provider: str) -> Any:
    from jarvis.plugins.tts import _build_provider

    return _build_provider(cfg.tts, provider)


def _local_readiness(provider: str) -> Any:
    """On-disk readiness of an on-device provider; ``None`` for cloud ones.

    Lazily imported and defensively wrapped: a probe failure must not turn the
    provider test into an integration error about the probe itself.
    """
    try:
        from jarvis.speech.local_models import local_status

        return local_status(provider)
    except Exception:  # noqa: BLE001 — an unavailable probe means "not local"
        return None


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
    realtime_probe: Callable[[Any, Any], Awaitable[float]] | None = None,
    codex_status: Callable[[], Any] | None = None,
    antigravity_status: Callable[[], Any] | None = None,
    timeout_s: float = 60.0,
    model: str | None = None,
) -> ProviderTestResult:
    """Run a REAL minimal call against ``spec``'s provider and classify it.

    The network-touching seams (``brain_probe`` / ``make_tts`` / ``make_stt`` /
    ``codex_status``) default to the production wiring and are injectable so the
    dispatch logic is unit-testable without hitting a live provider.

    ``model`` (brain tier only) probes that exact model instead of the
    provider's configured/default one — used by tiers with their own model pin
    (e.g. the Tool Model), so the health verdict matches what that tier runs.

    ``timeout_s`` bounds the wait for the FIRST byte/delta, not a dead endpoint
    (the SDK ``connect`` timeout, ~5s, catches those). It must clear the slowest
    LEGITIMATE provider's time-to-first-byte, else a slow-but-healthy provider is
    mislabelled ``unreachable``/integration-error: NVIDIA NIM's free dev tier was
    measured at 13-30s+ TTFB (queue/cold-start), far above OpenAI/OpenRouter's
    ~2s, so the ceiling is generous rather than voice-path-tight.
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

    # On-device providers: the equivalent question is not "is there a key" but
    # "are the engine and the model actually on this machine". Asked BEFORE
    # anything is built, because building an on-device provider is deliberately
    # cheap — the weights load on first use (AP-26), so a successful
    # construction proves nothing at all about whether it can run.
    local_state = _local_readiness(provider)
    if local_state is not None and not local_state.ready:
        return ProviderTestResult(provider, NOT_CONFIGURED, local_state.detail)

    # Default 1-token probe, shared by the brain tier AND the realtime tier below.
    if brain_probe is None:
        async def brain_probe(p: str, m: str) -> Any:  # type: ignore[misc]
            from jarvis.brain.healthcheck import BrainHealthChecker
            from jarvis.brain.provider_registry import BrainProviderRegistry

            checker = BrainHealthChecker(BrainProviderRegistry())
            return await checker.probe(p, m, timeout_s=timeout_s)

    if spec.tier == "realtime":
        started = perf_counter()
        try:
            if realtime_probe is None:
                latency_ms = await _default_realtime_probe(
                    spec, cfg, timeout_s=min(timeout_s, 20.0)
                )
            else:
                latency_ms = await asyncio.wait_for(
                    realtime_probe(spec, cfg), timeout=min(timeout_s, 20.0)
                )
            return ProviderTestResult(
                provider,
                OK,
                "Realtime session handshake accepted.",
                latency_ms,
            )
        except TimeoutError:
            latency_ms = (perf_counter() - started) * 1000.0
            return ProviderTestResult(
                provider,
                UNREACHABLE,
                "Realtime session handshake timed out.",
                latency_ms,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (perf_counter() - started) * 1000.0
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderTestResult(
                provider,
                classify_provider_error(detail),
                detail,
                latency_ms,
            )

    if spec.tier == "brain":
        probe_model = model or _resolve_brain_model(cfg, provider)
        hr = await brain_probe(provider, probe_model)
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

        async def _first_audio_bytes() -> int:
            total = 0
            async for chunk in inst.synthesize("Test."):
                total += len(getattr(chunk, "pcm", b"") or b"")
                if total > 0:
                    break
            return total

        start = perf_counter()
        try:
            # Bounded like the STT branch below: an unresponsive TTS stream must
            # surface as an honest "unreachable" instead of hanging the request
            # (and the UI's "Testing…" spinner) forever.
            total = await asyncio.wait_for(_first_audio_bytes(), timeout=timeout_s)
            dur = (perf_counter() - start) * 1000.0
            if total > 0:
                return ProviderTestResult(provider, OK, f"{total} audio bytes", dur)
            return ProviderTestResult(provider, ERROR, "synthesized 0 bytes", dur)
        except TimeoutError:
            dur = (perf_counter() - start) * 1000.0
            return ProviderTestResult(provider, UNREACHABLE, f"timeout after {timeout_s:.0f}s", dur)
        except Exception as exc:  # noqa: BLE001
            dur = (perf_counter() - start) * 1000.0
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderTestResult(provider, classify_provider_error(detail), str(exc), dur)

    if spec.tier == "dictation":
        # This tier is probed by the dictation layer, which owns the wording
        # pass and is the only place allowed to call it — a call site under
        # jarvis/brain/ would put a model call one import away from the spoken
        # hot path (AP-11, guarded by test_polish_boot_safety.py).
        #
        # The refusal is explicit rather than a fall-through: without it the
        # card dropped into the STT branch below and reported the user's SPEECH
        # RECOGNIZER under a wording-provider's name — four cards reading red
        # over a broken local model while every one of them worked.
        return ProviderTestResult(
            provider,
            ERROR,
            "This card is tested by the dictation layer, not by the provider test.",
        )

    # stt
    builder = make_stt or _default_make_stt
    try:
        # Built off the event loop: the local faster-whisper build LOADS the model
        # synchronously (seconds), which would otherwise freeze every other request
        # (and the whole API-Keys screen) while a test/section-health check runs.
        inst = await asyncio.to_thread(builder, cfg, provider)
    except (ImportError, ModuleNotFoundError):
        # The on-device engines are optional packages a base install omits. That
        # is "not installed" — an actionable amber chip pointing at the in-app
        # install, NOT a red "integration bug".
        return ProviderTestResult(
            provider,
            NOT_CONFIGURED,
            "The local speech engine is not installed. Install it from this card.",
        )
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        return ProviderTestResult(provider, classify_provider_error(detail), str(exc))

    # An on-device recogniser has to LOAD its model before it can answer, and a
    # multi-gigabyte checkpoint reads from disk for tens of seconds the first
    # time. Held to a network-shaped ceiling it would report "unreachable" while
    # working perfectly, so the local path gets a longer one.
    stt_timeout = max(timeout_s, 180.0) if local_state is not None else timeout_s
    start = perf_counter()
    try:
        tr = await asyncio.wait_for(
            inst.transcribe(_silence_chunks()), timeout=stt_timeout
        )
        dur = (perf_counter() - start) * 1000.0
        text = (getattr(tr, "text", "") or "").strip()
        # Silence in, silence out: an empty transcript is the CORRECT answer
        # here, and the run itself is the proof — the model loaded and decoded.
        detail = (
            f"Ran on this machine — {local_state.model_label} loaded and decoded."
            if local_state is not None
            else f"transcript={text!r}"
        )
        return ProviderTestResult(provider, OK, detail, dur)
    except TimeoutError:
        dur = (perf_counter() - start) * 1000.0
        if local_state is not None:
            return ProviderTestResult(
                provider,
                ERROR,
                f"{local_state.model_label} did not finish loading within "
                f"{stt_timeout:.0f}s on this machine.",
                dur,
            )
        return ProviderTestResult(provider, UNREACHABLE, f"timeout after {timeout_s:.0f}s", dur)
    except Exception as exc:  # noqa: BLE001
        dur = (perf_counter() - start) * 1000.0
        detail = f"{type(exc).__name__}: {exc}"
        return ProviderTestResult(provider, classify_provider_error(detail), str(exc), dur)

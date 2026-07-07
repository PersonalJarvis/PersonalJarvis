"""REST-API für Brain-/TTS-/STT-Provider und ihre Credentials.

Endpoints:
    GET    /api/providers                    → Liste mit configured/active Status
    POST   /api/secrets/{key}                → Secret setzen (Whitelist gegen wizard.SECRETS)
    DELETE /api/secrets/{key}                → Secret löschen
    POST   /api/brain/switch                 → aktiven Brain-Provider wechseln (+ persist)
    POST   /api/tts/switch                   → aktiven TTS-Provider wechseln (persist in jarvis.toml)
    POST   /api/stt/switch                   → aktiven STT-Provider wechseln (persist in jarvis.toml)
    POST   /api/subagent/switch              → aktiven Subagent-Provider wechseln (3-layer persist)

Wird vom WebServer in `_build_app()` eingehängt:
    from .provider_routes import router as provider_router
    app.include_router(provider_router)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from jarvis.brain import provider_test as _provider_test
from jarvis.brain import section_health as _section_health
from jarvis.brain.model_catalog import ModelInfo, catalog_spec, classify_model
from jarvis.codex_auth import CodexAuthService
from jarvis.core import config as cfg_mod
from jarvis.core.events import SecretConfigured
from jarvis.missions.worker_runtime.provider_map import (
    CODEX_SUBAGENT_CANONICAL as _CODEX_SUBAGENT_CANONICAL,
)
from jarvis.missions.worker_runtime.provider_map import (
    CODEX_SUBAGENT_SLUGS as _CODEX_SUBAGENT_SLUGS,
)
from jarvis.setup.wizard import SECRETS as WIZARD_SECRETS

from .provider_spec import PROVIDERS, ProviderSpec, get_spec, provider_billing

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["providers"])


# Whitelist aller Keys die gesetzt/gelöscht werden dürfen — exakt die im
# Setup-Wizard deklarierten Slots.
ALLOWED_SECRET_KEYS: frozenset[str] = frozenset(s.key for s in WIZARD_SECRETS)

# Local providers allowed to stay active in the airgapped privacy profile.
# Empty since v1.0.1: Ollama was removed 2026-04-21, and the local
# "faster-whisper" STT dictation provider was removed from the user-selectable
# catalog (see provider_spec.py). With no local brain/STT provider left, the
# airgapped profile admits no provider switch — an honest state, not a
# regression: airgapped means "local only", and there is currently no local
# provider to switch TO. (Wake still runs its own local Whisper off this list.)
LOCAL_PROVIDERS: frozenset[str] = frozenset()

# Codex subagent slugs (_CODEX_SUBAGENT_SLUGS / _CODEX_SUBAGENT_CANONICAL) are
# imported from jarvis.missions.worker_runtime.provider_map — the single source
# of truth shared with app_control + the worker selector (BUG-008 anti-drift).

# The provider catalog stays here; the credential-presence heuristic + secret-slot
# alias map live in jarvis.brain.app_control (single source of truth). They are
# imported lazily inside _is_credential_present below so the UI route and the
# brain's switch-provider tool never drift (BUG-008 class) without paying a heavy
# module-load import or risking an import cycle.


# ----------------------------------------------------------------------
# Request-Bodies
# ----------------------------------------------------------------------


class SecretBody(BaseModel):
    value: str = Field(..., min_length=1, description="Roher Secret-Wert (API-Key, Token, ...)")


class SwitchBody(BaseModel):
    provider: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="In jarvis.toml schreiben")


class CodexBinaryPathBody(BaseModel):
    binary_path: str = Field(default="", max_length=1024)


# Provider connectivity-test outcome. ``ProviderTestStatusLiteral`` MUST mirror
# the single source of truth in ``jarvis.brain.provider_test`` — the runtime
# assert below is the five-layer anti-drift guard (BUG-008 class), and the TS
# union in ``useProviders.ts`` is the UI mirror.
ProviderTestStatusLiteral = Literal[
    "ok",
    "not_configured",
    "bad_key",
    "no_credits",
    "rate_limited",
    "model_unavailable",
    "unreachable",
    "error",
]
assert set(get_args(ProviderTestStatusLiteral)) == set(
    _provider_test.PROVIDER_TEST_STATUSES
), "provider-test status vocabulary drift (Pydantic Literal vs SSOT)"


class ProviderTestResponse(BaseModel):
    provider: str
    status: ProviderTestStatusLiteral
    detail: str = ""
    latency_ms: float = 0.0
    # True when the provider was reached and answered at the protocol level —
    # i.e. the integration code is sound and only the credential/account/model
    # is the blocker (ok / bad_key / no_credits / rate_limited / model_unavailable
    # / not_configured). False only for ``unreachable`` / ``error``.
    integration_ok: bool = True


# Per-provider model picker. ``CatalogSourceLiteral`` is the honest provenance of
# the model list (live fetch vs. served-from-cache vs. offline static fallback) —
# the UI must never present ``static`` as the live catalog.
CatalogSourceLiteral = Literal["live", "cache", "static", "curated"]


class BrainModelInfo(BaseModel):
    id: str
    label: str
    # Presentation-only classification for the picker's filter chips + star
    # (jarvis.brain.model_catalog.classify_model). Independent booleans — never
    # gate behavior (AP-21). ``free`` = zero-cost (OpenRouter ``:free``);
    # ``frontier`` = flagship band; ``value`` = strong price/performance band;
    # ``starred`` = a maintainer-picked favourite.
    free: bool = False
    frontier: bool = False
    value: bool = False
    starred: bool = False
    # Tri-state vision-input capability from the provider's model metadata
    # (OpenRouter ``architecture.input_modalities``): True = understands
    # images, False = text-only, None = the provider endpoint does not expose
    # modality data (unknown — treated as capable, no regression). The
    # Computer-Use model picker hides ONLY explicit ``False`` entries.
    vision: bool | None = None


class BrainModelsResponse(BaseModel):
    provider: str
    current_model: str
    models: list[BrainModelInfo]
    source: CatalogSourceLiteral
    fetched_at: float = 0.0
    # What the picker writes: "model" (brain/stt/cartesia) or "voice" (most TTS).
    selects: str = "model"


class BrainModelBody(BaseModel):
    # Empty string is meaningful: reset the provider to its frontier default.
    model: str = Field(default="", max_length=200)
    persist: bool = Field(default=True)


class BrainModelProbe(BaseModel):
    """Honest outcome of a real 1-token call against the *selected* model."""

    status: ProviderTestStatusLiteral
    detail: str = ""
    latency_ms: float = 0.0
    integration_ok: bool = True


class BrainModelSaveResponse(BaseModel):
    ok: bool
    provider: str
    model: str
    persisted: bool
    applied_live: bool
    restart_required: bool
    # Only brain providers run a live 1-token probe; TTS/STT save without one.
    probe: BrainModelProbe | None = None


# Phase 3: selectable Computer-Use model per provider. CU runs on the provider's
# main ``model`` by default; a pinned ``cu_model`` lets the user run CU on a
# different (e.g. stronger) model than chat. ``cu_model == ""`` means "use my
# main model". Separate from the model endpoints so those stay untouched.
class CuModelBody(BaseModel):
    cu_model: str = Field(default="", max_length=200)  # "" -> use the main model
    persist: bool = Field(default=True)


class CuModelResponse(BaseModel):
    ok: bool = True
    provider: str
    cu_model: str          # the pinned value ("" = use the main model)
    effective_model: str   # the model Computer-Use would actually run
    uses_main: bool        # True when nothing is pinned (effective == main model)
    persisted: bool = False
    restart_required: bool = False


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------


def _persist_brain_primary_fallback(provider: str) -> bool:
    """Persist ``brain.primary`` directly when the manager's switch signature
    is too old to accept ``persist=`` (TypeError fallback path).

    Returns ``True`` iff the disk write succeeded. This exists so the legacy
    fallback never silently drops persistence: even when ``switch`` cannot
    persist, we still attempt the write here and report the honest outcome.
    """
    try:
        from jarvis.core import config_writer

        config_writer.set_brain_primary(provider)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Fallback persist of brain.primary=%r failed: %s", provider, exc)
        return False


def _is_credential_present(spec: ProviderSpec, binary_path: str | None = None) -> bool:
    """Heuristik je nach auth_mode.

    Delegates to the shared implementation in :mod:`jarvis.brain.app_control`
    (imported lazily) so the UI route and the brain's ``switch-provider`` tool
    use the *same* check — anti-drift, BUG-008 class. Name/signature preserved
    for the rest of this module.
    """
    from jarvis.brain.app_control import is_credential_present

    return is_credential_present(spec, binary_path)


def _cli_installed(spec: ProviderSpec) -> bool | None:
    if spec.id == "codex":
        return CodexAuthService().status().installed
    return None


def _has_openai_brain_credential() -> bool:
    """True iff an OpenAI API key usable by the legacy Codex brain is configured.

    Codex is no longer switchable as the main Brain because Computer Use needs
    screenshot-capable planning. This helper is kept for old payload fields and
    defensive compatibility with the CodexBrain plugin.
    """
    return bool(
        cfg_mod.get_secret("codex_openai_api_key")
        or cfg_mod.get_secret("openai_api_key", "OPENAI_API_KEY")
    )


def _codex_brain_usable() -> bool:
    """Legacy readiness signal for Codex credentials.

    The Brain switch rejects Codex earlier via ``brain_switchable=False``; Codex
    belongs in the Subagent section. The value remains in ``/api/providers`` for
    older UI consumers that still read ``codex_brain_ready``.
    """
    if _has_openai_brain_credential():
        return True
    try:
        return bool(CodexAuthService(_codex_binary_path()).status().connected)
    except Exception:  # noqa: BLE001
        return False


def _spec_to_payload(
    spec: ProviderSpec,
    *,
    active_brain: str | None,
    active_tts: str | None,
    active_stt: str | None,
) -> dict[str, Any]:
    if spec.tier == "brain":
        active = spec.id == active_brain
    elif spec.tier == "tts":
        active = spec.id == active_tts
    else:
        active = spec.id == active_stt

    secrets_set = {k: bool(cfg_mod.get_secret(k)) for k in spec.secret_keys}
    codex_status = None
    if spec.id == "codex":
        codex_status = CodexAuthService(_codex_binary_path()).status().to_dict()
    antigravity_status = None
    if spec.id == "antigravity":
        from jarvis.google_cli.auth_service import GoogleCliAuthService

        antigravity_status = GoogleCliAuthService().status().to_dict()

    payload = {
        "id": spec.id,
        "label": spec.label,
        "tier": spec.tier,
        "auth_mode": spec.auth_mode,
        "secret_keys": list(spec.secret_keys),
        "secrets_set": secrets_set,
        "dashboard_url": spec.dashboard_url,
        "login_cli": list(spec.login_cli) if spec.login_cli else None,
        "install_hint": spec.install_hint,
        "credential_path_hint": spec.credential_path_hint,
        "brain_switchable": spec.brain_switchable,
        # Plain-English help + how it is billed (api / subscription /
        # subscription_or_api / local) so the UI explains "which key or
        # subscription, and what for" without guessing.
        "credential_help": spec.credential_help,
        "signup_url": spec.signup_url,
        "billing": provider_billing(spec),
        # Maintainer-recommended pick for this tier (UI badge) + the model it
        # points at. Presentation only — never gates behavior (AP-21).
        "recommended": spec.recommended,
        "recommended_model": spec.recommended_model,
        # Gemini's AI-Studio-vs-Vertex split; None for single-path providers.
        "alt_credential": (
            {
                "label": spec.alt_credential.label,
                "billing": spec.alt_credential.billing,
                "credential_help": spec.alt_credential.credential_help,
                "dashboard_url": spec.alt_credential.dashboard_url,
                "credential_path_hint": spec.alt_credential.credential_path_hint,
            }
            if spec.alt_credential is not None
            else None
        ),
        "configured": (
            bool(antigravity_status["connected"])
            if antigravity_status is not None
            else _is_credential_present(
                spec,
                _codex_binary_path() if spec.id == "codex" else None,
            )
        ),
        "active": active,
        "cli_installed": _cli_installed(spec),
    }
    if antigravity_status is not None:
        payload["antigravity_status"] = antigravity_status
    if codex_status is not None:
        payload["codex_status"] = codex_status
        # Back-compat only. Codex is not rendered as a switchable Brain anymore;
        # the Subagent section owns its ChatGPT login and activation.
        payload["codex_brain_ready"] = _codex_brain_usable()
    return payload


def _active_brain(request: Request) -> str | None:
    brain = getattr(request.app.state, "brain", None)
    if brain is None:
        return None
    return getattr(brain, "active_provider", None) or getattr(brain, "name", None)


def _active_tts(request: Request) -> str | None:
    """The TTS provider actually powering voice output — the resolved cross-family
    provider, not the raw configured default.

    Mirrors ``_active_brain`` reporting the LIVE provider. Without this, a user
    whose only key is (say) ElevenLabs sees an amber "Gemini Flash TTS: no key set"
    dot even though the runtime crossed to ElevenLabs and voice works — pointing
    them at the wrong fix and masking that the fallback is healthy. Only reports a
    DIFFERENT provider when the runtime genuinely crossed away from the configured
    one; otherwise returns the raw configured value so the health lookup behaves
    exactly as before. Health must never 500, so any resolver error falls back to
    the configured value.
    """
    cfg = _resolve_cfg(request)
    tts_cfg = getattr(cfg, "tts", None) if cfg else None
    if tts_cfg is None:
        return None
    configured = getattr(tts_cfg, "provider", None)
    try:
        from jarvis.plugins.tts import (
            _canonical_tts_name,
            _resolve_keyed_tts_provider,
        )

        resolved, _ = _resolve_keyed_tts_provider((configured or "").lower(), tts_cfg)
        if _canonical_tts_name((configured or "").lower()) != resolved:
            return resolved
    except Exception as exc:  # noqa: BLE001 — the health panel must never 500
        log.debug("resolved-provider health probe failed (%s); using configured.", exc)
    return configured


def _active_stt(request: Request) -> str | None:
    """The STT provider actually powering voice input — the resolved cross-family
    provider, not the raw configured default (which may be a dead, keyless default
    the runtime already crossed away from). See ``_active_tts`` for the rationale.
    """
    cfg = _resolve_cfg(request)
    stt_cfg = getattr(cfg, "stt", None) if cfg else None
    if stt_cfg is None:
        return None
    configured = (getattr(stt_cfg, "provider", None) or "").strip() or None
    if not configured:
        return configured
    try:
        from jarvis.plugins.stt import _resolve_keyed_stt_provider

        resolved = _resolve_keyed_stt_provider(configured)
        if resolved and resolved != configured:
            return resolved
    except Exception as exc:  # noqa: BLE001 — the health panel must never 500
        log.debug("resolved-provider health probe failed (%s); using configured.", exc)
    return configured


def _resolve_cfg(request: Request):
    """Liefert die JarvisConfig.

    Server haengt sie als ``app.state.config`` (nicht ``cfg``!) — siehe
    ``server.py::_build_app``. Fallback auf ``load_config()`` falls die App
    headless gestartet wurde und kein Bootstrap stattfand.
    """
    cfg_attr = getattr(request.app.state, "config", None) or getattr(
        request.app.state, "cfg", None
    )
    if cfg_attr is not None:
        return cfg_attr
    try:
        return cfg_mod.load_config()
    except Exception:  # noqa: BLE001
        return None


def _codex_binary_path(request: Request | None = None) -> str | None:
    cfg = _resolve_cfg(request) if request is not None else None
    if cfg is None:
        try:
            cfg = cfg_mod.load_config()
        except Exception:  # noqa: BLE001
            cfg = None
    return getattr(getattr(cfg, "codex", None), "binary_path", "") or None


def _apply_worker_in_memory(request: Request, provider: str) -> None:
    """Best-effort in-memory update of ``cfg.brain.worker.provider``.

    So the next ``/jarvis-agent/status`` reflects the choice immediately (the
    worker itself only re-reads at restart). Frozen / detached cfg is not an error.
    """
    cfg = _resolve_cfg(request)
    if cfg is None or getattr(cfg, "brain", None) is None:
        return
    sub = getattr(cfg.brain, "worker", None)
    try:
        if sub is None:
            from jarvis.core.config import BrainTierConfig

            cfg.brain.worker = BrainTierConfig(provider=provider)
        else:
            sub.provider = provider
    except Exception as exc:  # noqa: BLE001 — frozen models / detached cfg are not errors
        log.debug("In-memory worker.provider update skipped: %s", exc)


def _apply_worker_model_in_memory(request: Request, model: str) -> None:
    """Best-effort in-memory update of ``cfg.brain.worker.model``.

    Mirrors :func:`_apply_worker_in_memory`; a missing ``worker``
    block is created with the router primary as provider so the override is
    never silently dropped.
    """
    cfg = _resolve_cfg(request)
    if cfg is None or getattr(cfg, "brain", None) is None:
        return
    sub = getattr(cfg.brain, "worker", None)
    try:
        if sub is None:
            from jarvis.core.config import BrainTierConfig

            cfg.brain.worker = BrainTierConfig(
                provider=getattr(cfg.brain, "primary", "") or "", model=model,
            )
        else:
            sub.model = model
    except Exception as exc:  # noqa: BLE001 — frozen models / detached cfg are not errors
        log.debug("In-memory worker.model update skipped: %s", exc)


async def _emit(request: Request, event: Any) -> None:
    bus = getattr(request.app.state, "bus", None) or _bus_from_brain(request)
    if bus is None:
        return
    try:
        await bus.publish(event)
    except Exception as exc:  # noqa: BLE001
        log.warning("Konnte Event nicht publishen: %s", exc)


def _bus_from_brain(request: Request):
    brain = getattr(request.app.state, "brain", None)
    if brain is None:
        return None
    return getattr(brain, "_bus", None) or getattr(brain, "bus", None)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    """Liefert die komplette Provider-Liste mit aktuellem Status pro Provider."""
    active_brain = _active_brain(request)
    active_tts = _active_tts(request)
    active_stt = _active_stt(request)

    providers = [
        _spec_to_payload(
            spec,
            active_brain=active_brain,
            active_tts=active_tts,
            active_stt=active_stt,
        )
        for spec in PROVIDERS
    ]
    return {"providers": providers}


@router.post("/providers/{provider_id}/test")
async def test_provider_connection(
    provider_id: str, request: Request
) -> ProviderTestResponse:
    """Run a REAL minimal call against ``provider_id`` and report the honest
    outcome.

    Unlike the ``configured`` flag in ``GET /providers`` (a credential-PRESENCE
    check), this actually reaches the provider: it distinguishes a working
    provider (``ok``) from an invalid key (``bad_key``), an out-of-credits
    account (``no_credits``), a missing key (``not_configured``), an
    unreachable endpoint (``unreachable``) or an integration bug (``error``).
    """
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unbekannter Provider: {provider_id}")

    cfg = _resolve_cfg(request)
    if cfg is None:
        raise HTTPException(
            status_code=503, detail="Konfiguration nicht verfügbar (Headless-Mode?)"
        )

    result = await _provider_test.run_provider_test(spec, cfg)
    return ProviderTestResponse(
        provider=result.provider,
        status=result.status,
        detail=result.detail,
        latency_ms=round(result.latency_ms, 1),
        integration_ok=result.status in _provider_test.INTEGRATION_OK_STATUSES,
    )


# ----------------------------------------------------------------------
# Section health — the at-a-glance API-Keys tab indicators
# ----------------------------------------------------------------------

# Section-health status vocabulary. ``SectionHealthStatusLiteral`` MUST mirror the
# SSOT in ``jarvis.brain.section_health`` — the runtime assert is the five-layer
# anti-drift guard (BUG-008 class) and the TS ``SectionHealthStatus`` union in
# ``useProviders.ts`` is the UI mirror.
SectionHealthStatusLiteral = Literal["ok", "needs_setup", "error", "unknown"]
assert set(get_args(SectionHealthStatusLiteral)) == set(
    _section_health.SECTION_HEALTH_STATUSES
), "section-health status vocabulary drift (Pydantic Literal vs SSOT)"

# Cache the rollup briefly so opening the API-Keys page / switching tabs does not
# re-run the REAL connectivity tests on every render. ``?refresh=true`` (used by
# the UI after a key save / provider switch) bypasses it.
_SECTION_HEALTH_TTL_S = 45.0


class SectionHealth(BaseModel):
    """One tab's rolled-up health. Only ``needs_setup`` (amber) and ``error``
    (red) draw a dot in the UI; ``ok`` / ``unknown`` stay silent."""

    status: SectionHealthStatusLiteral = "unknown"
    # Machine-readable cause for the UI tooltip + debugging: the underlying
    # provider-test status ("bad_key"/"no_credits"/…), "not_configured",
    # "no_active", "local", "ok", or "unknown". Not shown verbatim to the user.
    reason: str = "unknown"
    # Plain-English one-liner for the hover tooltip (provider label + detail).
    detail: str = ""


class SectionHealthResponse(BaseModel):
    sections: dict[str, SectionHealth]
    checked_at: float = 0.0
    cached: bool = False


async def _tier_section_health(cfg: Any, spec: ProviderSpec | None) -> SectionHealth:
    """Health of one provider tier, derived from its ACTIVE provider only.

    A tier is only as healthy as the single provider currently powering it —
    deliberately NOT "does any provider here lack a key" (that would paint every
    tab red, since unused providers are normally left empty).
    """
    if spec is None:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="no_active",
            detail="No active provider selected",
        )
    # Local providers (faster-whisper, SAPI) have no key to be invalid; if one is
    # the active provider it is usable. Skip the real test — it could force a heavy
    # model load on page open for no signal we don't already have.
    if getattr(spec, "auth_mode", None) == "none":
        return SectionHealth(
            status=_section_health.OK,
            reason="local",
            detail=f"{spec.label}: local, no key needed",
        )
    try:
        configured = _is_credential_present(
            spec, _codex_binary_path() if spec.id == "codex" else None
        )
    except Exception:  # noqa: BLE001 — a probe failure is "not set up", not a crash
        configured = False
    if not configured:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="not_configured",
            detail=f"{spec.label}: no key set",
        )
    try:
        result = await _provider_test.run_provider_test(spec, cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("section-health test for %s failed: %s", spec.id, exc)
        return SectionHealth(
            status=_section_health.UNKNOWN, reason="error", detail=f"{spec.label}: check failed"
        )
    status = _section_health.section_status_for_test(result.status, configured=True)
    return SectionHealth(
        status=status,
        reason=result.status,
        detail=f"{spec.label}: {result.detail or result.status}",
    )


def _worker_usable(provider: str) -> bool:
    """Best-effort "is the selected heavy-task worker connected/keyed?".

    Provider-agnostic: a CLI login (Codex / Antigravity / Claude) is usable when
    its auth service reports connected; an API-keyed worker reuses the brain
    provider's credential. Any probe failure degrades to "not usable" rather than
    raising (AP-22/23 — never brick on the maintainer's favourite worker).
    """
    p = (provider or "").lower()
    try:
        if p in _CODEX_SUBAGENT_SLUGS or p in {"codex", "openai-codex"}:
            connected = bool(CodexAuthService(_codex_binary_path()).status().connected)
            return connected or _has_openai_brain_credential()
        if p == "antigravity":
            from jarvis.google_cli.auth_service import GoogleCliAuthService

            return bool(GoogleCliAuthService().status().connected)
        if p in {"claude-api", "claude"}:
            from jarvis.claude_auth import ClaudeAuthService

            st = ClaudeAuthService().status()
            return bool(
                getattr(st, "connected", False) or getattr(st, "api_key_present", False)
            )
        spec = get_spec(p)
        return bool(spec is not None and _is_credential_present(spec))
    except Exception:  # noqa: BLE001
        return False


def _worker_flagged_dead(provider: str) -> bool:
    """True when the SELECTED worker provider is proven dead/cooling right
    now — signals the presence-only ``_worker_usable`` cannot see (the
    2026-07-06 gap: an expired-in-place OAuth token, a session-dead flag, a
    quota cooldown). Cheap + offline; any probe failure degrades to False
    (fall back to the presence check, never a false red).
    """
    p = (provider or "").lower()
    try:
        if p in {"claude-api", "claude"}:
            from jarvis.claude_quota_state import claude_in_quota_cooldown
            from jarvis.missions.init import _claude_cli_auth_viable

            return claude_in_quota_cooldown() or not _claude_cli_auth_viable()
        if p in _CODEX_SUBAGENT_SLUGS or p in {"codex", "openai-codex"}:
            from jarvis.codex_auth_state import codex_needs_reauth
            from jarvis.codex_quota_state import codex_in_quota_cooldown

            return codex_needs_reauth() or codex_in_quota_cooldown()
    except Exception:  # noqa: BLE001
        return False
    return False


def _jarvis_agent_section_health(cfg: Any) -> SectionHealth:
    """Subagents tab: reflects whether the SELECTED heavy-task worker is usable.

    A real "does it answer" call for a CLI worker is heavy, so v1 reports the
    connectedness signal — connected/keyed → ok, otherwise needs_setup. Since
    2026-07-07 it distinguishes degraded (fallback carries) from error
    (nothing reachable).
    """
    brain = getattr(cfg, "brain", None) if cfg is not None else None
    if brain is None:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="no_active",
            detail="No subagent worker selected",
        )
    sub = getattr(brain, "worker", None)
    provider = (getattr(sub, "provider", None) if sub else None) or getattr(
        brain, "primary", None
    )
    if not provider:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="no_active",
            detail="No subagent worker selected",
        )
    spec = get_spec(provider)
    label = spec.label if spec is not None else provider
    if _worker_usable(provider) and not _worker_flagged_dead(provider):
        return SectionHealth(
            status=_section_health.OK, reason="ok", detail=f"Subagent worker: {label}"
        )
    # The selected worker cannot run right now. Distinguish "a fallback
    # family carries the missions" (amber) from "nothing is reachable —
    # the next mission WILL fail" (red).
    try:
        from jarvis.missions.init import reachable_worker_families

        families = reachable_worker_families()
    except Exception:  # noqa: BLE001
        families = []
    if families:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="degraded",
            detail=(
                f"Subagent worker '{label}' is unavailable — missions run on "
                f"{families[0]} until it is reconnected"
            ),
        )
    return SectionHealth(
        status=_section_health.ERROR,
        reason="no_provider",
        detail=(
            f"No subagent provider is reachable — missions will fail. "
            f"Reconnect '{label}' or add an API key."
        ),
    )


def _advanced_section_health(request: Request) -> SectionHealth:
    """Advanced tab: every integration here is OPTIONAL, so it never reports
    ``needs_setup`` — only ``error`` when something the user actually configured
    is failing. Today that is telephony's cached reachability check; otherwise the
    tab stays silent (``unknown``)."""
    contributions: list[str] = []
    detail = ""
    reason = "unknown"
    tm = getattr(request.app.state, "telephony_manager", None)
    if tm is not None and getattr(tm, "reachable", None) is False:
        err = getattr(tm, "reachable_error", None)
        if err:
            contributions.append(_section_health.ERROR)
            detail = f"Telephony unreachable: {err}"
            reason = "telephony"
    return SectionHealth(
        status=_section_health.aggregate(contributions), reason=reason, detail=detail
    )


@router.get("/providers/section-health")
async def section_health(request: Request, refresh: bool = False) -> SectionHealthResponse:
    """Per-tab health for the API-Keys segmented tabs ("is this part working?").

    The brain/tts/stt tiers get a REAL connectivity test of their active provider
    (run in parallel), the Subagents tab reflects whether the selected worker is
    connected, and the Advanced tab only flags a configured optional integration
    that is actually failing. The result is cached for a few seconds so opening the
    page / switching tabs does not re-run the real calls each render;
    ``?refresh=true`` forces a fresh check after a key save or provider switch.
    """
    cache = getattr(request.app.state, "_section_health_cache", None)
    now = time.time()
    if (
        not refresh
        and isinstance(cache, dict)
        and now - cache.get("checked_at", 0.0) < _SECTION_HEALTH_TTL_S
    ):
        return SectionHealthResponse(
            sections=cache["payload"], checked_at=cache["checked_at"], cached=True
        )

    cfg = _resolve_cfg(request)
    sections: dict[str, SectionHealth] = {}

    if cfg is None:
        for key in ("brain", "tts", "stt", "subagents", "advanced"):
            sections[key] = SectionHealth(
                status=_section_health.UNKNOWN,
                reason="unavailable",
                detail="Configuration unavailable",
            )
    else:
        brain_spec = get_spec(_active_brain(request) or "")
        tts_spec = get_spec(_active_tts(request) or "")
        stt_spec = get_spec(_active_stt(request) or "")
        sections["brain"], sections["tts"], sections["stt"] = await asyncio.gather(
            _tier_section_health(cfg, brain_spec),
            _tier_section_health(cfg, tts_spec),
            _tier_section_health(cfg, stt_spec),
        )
        try:
            sections["subagents"] = _jarvis_agent_section_health(cfg)
        except Exception as exc:  # noqa: BLE001
            log.warning("section-health subagent check failed: %s", exc)
            sections["subagents"] = SectionHealth()
        try:
            sections["advanced"] = _advanced_section_health(request)
        except Exception as exc:  # noqa: BLE001
            log.warning("section-health advanced check failed: %s", exc)
            sections["advanced"] = SectionHealth()

    request.app.state._section_health_cache = {"checked_at": now, "payload": sections}
    return SectionHealthResponse(sections=sections, checked_at=now, cached=False)


# ----------------------------------------------------------------------
# Per-provider model picker (live catalog + pin + honest probe)
# ----------------------------------------------------------------------


def _get_model_catalog(request: Request):
    """Lazily build + stash a process-wide :class:`ModelCatalog` on app.state.

    A singleton so the 6 h cache is shared across requests (and its asyncio lock
    actually serialises concurrent fetches) instead of re-reading the cache file
    per call.
    """
    cat = getattr(request.app.state, "model_catalog", None)
    if cat is None:
        from jarvis.brain.model_catalog import ModelCatalog

        cat = ModelCatalog()
        try:
            request.app.state.model_catalog = cat
        except Exception as exc:  # noqa: BLE001 — detached app.state is not an error
            log.debug("Could not stash model_catalog on app.state: %s", exc)
    return cat


def _current_brain_model(cfg: Any, provider: str) -> str:
    """The model currently in effect for ``provider`` (override or frontier
    default), so the picker can highlight the active selection."""
    from jarvis.brain.manager import get_tier_default_model

    pc = None
    providers = getattr(getattr(cfg, "brain", None), "providers", None)
    if isinstance(providers, dict):
        pc = providers.get(provider)
    model = getattr(pc, "model", None) if pc is not None else None
    return model or get_tier_default_model("router", provider) or ""


def _provider_cu_model(cfg: Any, provider: str) -> str:
    """The pinned Computer-Use model for ``provider`` ("" when none is set)."""
    providers = getattr(getattr(cfg, "brain", None), "providers", None)
    pc = providers.get(provider) if isinstance(providers, dict) else None
    return (getattr(pc, "cu_model", None) or "") if pc is not None else ""


def _set_cu_model_in_memory(cfg: Any, provider: str, value: str) -> None:
    """Update ``cfg.brain.providers[provider].cu_model`` live so the next CU
    mission uses it without a restart (the loop reads cfg fresh each mission).
    Best-effort: a frozen/detached cfg is not an error."""
    try:
        providers = cfg.brain.providers
        pc = providers.get(provider)
        if pc is None:
            from jarvis.core.config import BrainProviderConfig

            pc = BrainProviderConfig()
            providers[provider] = pc
        pc.cu_model = value
    except Exception as exc:  # noqa: BLE001 — frozen/detached cfg is acceptable
        log.debug("In-memory cu_model update skipped for %s: %s", provider, exc)


async def _probe_brain_model(
    provider: str, model: str, *, timeout_s: float = 20.0
) -> _provider_test.ProviderTestResult:
    """Run a REAL 1-token call against the *specific* ``model`` and classify it.

    Unlike :func:`provider_test.run_provider_test` (which probes the *configured*
    model), this validates the model the user just selected — so a typo or a
    model the key has no access to comes back as ``model_unavailable`` rather
    than silently "saved but broken". Module-level so it is monkeypatchable.
    """
    from jarvis.brain.healthcheck import BrainHealthChecker
    from jarvis.brain.provider_registry import BrainProviderRegistry

    checker = BrainHealthChecker(BrainProviderRegistry())
    hr = await checker.probe(provider, model, timeout_s=timeout_s)
    if getattr(hr, "ok", False):
        return _provider_test.ProviderTestResult(
            provider, _provider_test.OK, "", getattr(hr, "duration_ms", 0.0)
        )
    err = getattr(hr, "error", None)
    return _provider_test.ProviderTestResult(
        provider,
        _provider_test.classify_provider_error(err),
        err or "",
        getattr(hr, "duration_ms", 0.0),
    )


def _require_catalog_provider(provider_id: str):
    """Validate that ``provider_id`` has a model/voice catalog.

    Returns ``(spec, cat)`` (the provider spec + the catalog spec). 404 unknown
    provider; 400 a provider with no catalog (e.g. faster-whisper is fine, but a
    provider absent from PROVIDER_CATALOG is rejected).
    """
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unbekannter Provider: {provider_id}")
    cat = catalog_spec(provider_id)
    if cat is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{provider_id}' bietet keine Modell-/Stimmen-Auswahl.",
        )
    return spec, cat


def _cartesia_model(tts: Any) -> str:
    """Read Cartesia's model from its own sub-table ``[tts.cartesia].model_id``.

    Cartesia does NOT use the global ``[tts] model`` (which holds Gemini's TTS
    model); reading that would show a nonsensical Gemini id on the Cartesia card.
    """
    if tts is None:
        return ""
    sub: Any = None
    extra = getattr(tts, "model_extra", None)
    if isinstance(extra, dict):
        sub = extra.get("cartesia")
    if sub is None:
        sub = getattr(tts, "cartesia", None)
    if isinstance(sub, dict):
        return str(sub.get("model_id") or "")
    return str(getattr(sub, "model_id", "") or "")


def _current_selection(cfg: Any, provider_id: str, cat: Any) -> str:
    """The value currently in effect for ``provider_id``'s picker (tier-aware).

    Brain → the per-provider model; TTS → the global voice (``voice_de``) or model;
    Cartesia → its own ``[tts.cartesia].model_id``; STT → the global ``stt.model``.
    """
    if cat.tier == "brain":
        return _current_brain_model(cfg, provider_id)
    if cat.tier == "tts":
        tts = getattr(cfg, "tts", None)
        if cat.selects == "voice":
            return getattr(tts, "voice_de", "") or ""
        if provider_id == "cartesia":
            return _cartesia_model(tts) or "sonic-3.5"
        return getattr(tts, "model", "") or ""
    if cat.tier == "stt":
        return getattr(getattr(cfg, "stt", None), "model", "") or ""
    return ""


def _brain_model_info(m: ModelInfo) -> BrainModelInfo:
    """Wire a catalog ``ModelInfo`` into the API model, attaching the
    presentation-only filter/star tags from ``classify_model``."""
    tags = classify_model(m.id, m.label)
    return BrainModelInfo(
        id=m.id,
        label=m.label,
        free=tags.free,
        frontier=tags.frontier,
        value=tags.value,
        starred=tags.starred,
        vision=(
            ("image" in m.input_modalities)
            if m.input_modalities is not None
            else None
        ),
    )


@router.get("/providers/{provider_id}/models")
async def list_brain_models(
    provider_id: str, request: Request, refresh: bool = False
) -> BrainModelsResponse:
    """Return the model/voice catalog for ``provider_id`` for the picker dropdown.

    Brain providers fetch their own live ``/v1/models`` (so a freshly released
    model appears with no code change); TTS/STT return a curated voice/model list.
    ``selects`` tells the UI whether it picks a model or a voice. ``source`` is
    honest: ``live`` / ``cache`` / ``static`` / ``curated``.
    """
    _spec, cat = _require_catalog_provider(provider_id)
    catalog = _get_model_catalog(request)
    result = await catalog.list_models(provider_id, force_refresh=refresh)
    cfg = _resolve_cfg(request)
    current = _current_selection(cfg, provider_id, cat)
    # Safety net: for a curated TTS/STT list, never echo a value that isn't in the
    # list (e.g. a stale global value belonging to a different provider) — show the
    # placeholder instead. Brain keeps its value (custom model ids are allowed).
    if cat.tier != "brain" and current and current not in {m.id for m in result.models}:
        current = ""
    return BrainModelsResponse(
        provider=provider_id,
        current_model=current,
        models=[_brain_model_info(m) for m in result.models],
        source=result.source,
        fetched_at=result.fetched_at,
        selects=result.selects,
    )


async def _apply_brain_model(
    provider_id: str, model: str, body: BrainModelBody, request: Request, *, probe: bool
) -> BrainModelSaveResponse:
    """Persist + live-apply a brain provider's model, optionally probing it."""
    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import set_brain_provider_model

            set_brain_provider_model(provider_id, model=model)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    brain = getattr(request.app.state, "brain", None)
    applied_live = False
    if brain is not None and hasattr(brain, "apply_provider_model"):
        try:
            applied_live = bool(brain.apply_provider_model(provider_id, model))
        except Exception as exc:  # noqa: BLE001
            log.warning("Live model apply for %s failed: %s", provider_id, exc)
            applied_live = False
    restart_required = brain is None

    probe_payload: BrainModelProbe | None = None
    if probe:
        cfg = _resolve_cfg(request)
        probe_model = model or _current_brain_model(cfg, provider_id)
        result = await _probe_brain_model(provider_id, probe_model)
        probe_payload = BrainModelProbe(
            status=result.status,
            detail=result.detail,
            latency_ms=round(result.latency_ms, 1),
            integration_ok=result.status in _provider_test.INTEGRATION_OK_STATUSES,
        )

    await _emit(
        request,
        SecretConfigured(key=f"brain.providers.{provider_id}.model", action="set"),
    )
    return BrainModelSaveResponse(
        ok=True, provider=provider_id, model=model, persisted=persisted,
        applied_live=applied_live, restart_required=restart_required, probe=probe_payload,
    )


def _apply_tts_selection(
    provider_id: str, value: str, selects: str, body: BrainModelBody, request: Request
) -> BrainModelSaveResponse:
    """Persist + live-apply a TTS voice/model (global ``[tts]`` block)."""
    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import (
                set_tts_cartesia_model,
                set_tts_model,
                set_tts_voice,
            )

            if selects == "voice":
                set_tts_voice(value)
            elif provider_id == "cartesia":
                # Cartesia's model lives in its own [tts.cartesia] sub-table.
                set_tts_cartesia_model(value)
            else:
                set_tts_model(value)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "tts", None) is not None:
        try:
            if selects == "voice":
                cfg.tts.voice_de = value  # type: ignore[attr-defined]
                cfg.tts.voice_en = value  # type: ignore[attr-defined]
            elif provider_id == "cartesia":
                extra = getattr(cfg.tts, "model_extra", None)
                if isinstance(extra, dict):
                    sub = extra.get("cartesia")
                    if not isinstance(sub, dict):
                        sub = {}
                        extra["cartesia"] = sub
                    sub["model_id"] = value
            else:
                cfg.tts.model = value  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen/detached cfg is not an error
            log.debug("In-memory tts selection update skipped: %s", exc)

    # Live-apply into the running SpeechPipeline (rebuild the TTS instance), so the
    # next ``_speak()`` uses the new voice without a restart.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_tts") and cfg is not None:
        try:
            from jarvis.plugins.tts import build_tts_from_config

            pipeline.set_tts(build_tts_from_config(cfg.tts))
            applied_live = True
        except Exception as exc:  # noqa: BLE001
            log.error("TTS live re-apply for %s failed: %s", provider_id, exc, exc_info=True)

    return BrainModelSaveResponse(
        ok=True, provider=provider_id, model=value, persisted=persisted,
        applied_live=applied_live, restart_required=not applied_live, probe=None,
    )


def _apply_stt_model(
    provider_id: str, value: str, body: BrainModelBody, request: Request
) -> BrainModelSaveResponse:
    """Persist a STT model (global ``[stt] model``). Takes effect on voice restart."""
    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import set_stt_model

            set_stt_model(value)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "stt", None) is not None:
        try:
            cfg.stt.model = value  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("In-memory stt model update skipped: %s", exc)

    return BrainModelSaveResponse(
        ok=True, provider=provider_id, model=value, persisted=persisted,
        applied_live=False, restart_required=True, probe=None,
    )


@router.put("/providers/{provider_id}/model")
async def set_brain_model(
    provider_id: str, body: BrainModelBody, request: Request
) -> BrainModelSaveResponse:
    """Pin a provider's model/voice, persist it, live-apply where possible.

    - **Brain** (incl. Codex): ``[brain.providers.<id>].model`` + live-apply +
      a real 1-token probe (skipped for Codex — the ChatGPT-login CLI path is slow
      and ignores the model id anyway). Empty ``model`` resets to the frontier default.
    - **TTS**: the global ``[tts]`` voice (``voice_de``/``voice_en``) or model
      (Cartesia) + live re-apply into the running SpeechPipeline.
    - **STT**: the global ``[stt] model`` (restart-required — the STT engine is
      built once at pipeline boot).
    """
    spec, cat = _require_catalog_provider(provider_id)
    value = body.model.strip()

    if cat.tier == "brain":
        # Codex / Antigravity probes would drive a slow subscription CLI (and
        # bill a real call); skip the live probe for those OAuth-CLI providers.
        do_probe = getattr(spec, "auth_mode", None) not in ("codex", "antigravity")
        return await _apply_brain_model(provider_id, value, body, request, probe=do_probe)
    if cat.tier == "tts":
        return _apply_tts_selection(provider_id, value, cat.selects, body, request)
    return _apply_stt_model(provider_id, value, body, request)


@router.get("/providers/{provider_id}/cu-model")
async def get_cu_model(provider_id: str, request: Request) -> CuModelResponse:
    """Return the per-provider Computer-Use model selection (Phase 3).

    ``cu_model`` is the pinned value ("" = use the provider's main model);
    ``effective_model`` is what CU would actually run. The dropdown options reuse
    the existing ``GET /providers/{id}/models`` catalog.
    """
    _spec, cat = _require_catalog_provider(provider_id)
    if cat.tier != "brain":
        raise HTTPException(
            status_code=400,
            detail="A Computer-Use model only applies to brain providers.",
        )
    cfg = _resolve_cfg(request)
    pinned = _provider_cu_model(cfg, provider_id)
    effective = pinned or _current_brain_model(cfg, provider_id)
    return CuModelResponse(
        provider=provider_id,
        cu_model=pinned,
        effective_model=effective,
        uses_main=not bool(pinned),
    )


@router.put("/providers/{provider_id}/cu-model")
async def set_cu_model(
    provider_id: str, body: CuModelBody, request: Request
) -> CuModelResponse:
    """Pin (or clear with "") the per-provider Computer-Use model (Phase 3).

    Persists to ``[brain.providers.<id>].cu_model`` (+ drift-soll) and updates the
    in-memory config so the next CU mission uses it with no restart. No live brain
    probe — the model is validated lazily the next time CU dispatches.
    """
    _spec, cat = _require_catalog_provider(provider_id)
    if cat.tier != "brain":
        raise HTTPException(
            status_code=400,
            detail="A Computer-Use model only applies to brain providers.",
        )
    value = body.cu_model.strip()

    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import set_brain_provider_model

            set_brain_provider_model(provider_id, cu_model=value)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    _set_cu_model_in_memory(cfg, provider_id, value)
    await _emit(
        request,
        SecretConfigured(key=f"brain.providers.{provider_id}.cu_model", action="set"),
    )
    effective = value or _current_brain_model(cfg, provider_id)
    return CuModelResponse(
        ok=True,
        provider=provider_id,
        cu_model=value,
        effective_model=effective,
        uses_main=not bool(value),
        persisted=persisted,
        restart_required=False,
    )


@router.get("/codex/status")
async def codex_status(request: Request) -> dict[str, Any]:
    return CodexAuthService(_codex_binary_path(request)).status().to_dict()


@router.post("/codex/binary-path")
async def codex_set_binary_path(body: CodexBinaryPathBody, request: Request) -> dict[str, Any]:
    value = body.binary_path.strip()
    try:
        from jarvis.core.config_writer import set_codex_binary_path

        set_codex_binary_path(value)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}") from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "codex", None) is not None:
        try:
            cfg.codex.binary_path = value  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "binary_path": value}


@router.post("/codex/login")
async def codex_login(request: Request) -> dict[str, Any]:
    service = CodexAuthService(_codex_binary_path(request))
    status = service.status()
    if not status.installed:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Codex CLI ist nicht installiert",
                "install_command": "npm i -g @openai/codex",
            },
        )
    try:
        proc = service.start_login()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"codex login konnte nicht gestartet werden: {type(exc).__name__}: {exc}",
        ) from exc
    return {"ok": True, "pid": proc.pid, "message": "codex login wurde im Terminal gestartet"}


@router.post("/codex/logout")
async def codex_logout(request: Request) -> dict[str, Any]:
    service = CodexAuthService(_codex_binary_path(request))
    status = service.status()
    if not status.installed:
        raise HTTPException(status_code=409, detail="Codex CLI ist nicht installiert")
    ok, error = service.logout_blocking()
    if not ok:
        raise HTTPException(status_code=500, detail=error or "codex logout fehlgeschlagen")
    return {"ok": True, "message": "Codex wurde getrennt"}


# M6: STT/TTS engines build ONCE at voice-pipeline bootstrap, so a key feeding them
# is unused until the next voice start. Surface restart_required so the UI shows the
# "active from next voice start" hint instead of implying the new key is live now.
# (Brain provider keys hot-reload, so they are deliberately NOT listed here.)
_RESTART_REQUIRED_SECRET_KEYS: frozenset[str] = frozenset({
    "groq_api_key", "deepgram_api_key",        # STT
    "cartesia_api_key", "elevenlabs_api_key",  # TTS
})


@router.post("/secrets/{key}")
async def set_secret_value(key: str, body: SecretBody, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unbekannter Secret-Key: {key}")
    if not cfg_mod.set_secret(key, body.value):
        raise HTTPException(status_code=500, detail="Keyring-Write fehlgeschlagen")
    await _emit(request, SecretConfigured(key=key, action="set"))
    return {
        "ok": True,
        "key": key,
        "restart_required": key in _RESTART_REQUIRED_SECRET_KEYS,
    }


@router.delete("/secrets/{key}")
async def delete_secret_value(key: str, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unbekannter Secret-Key: {key}")
    cfg_mod.delete_secret(key)  # idempotent — wirft nicht wenn nicht vorhanden
    await _emit(request, SecretConfigured(key=key, action="delete"))
    return {"ok": True, "key": key}


@router.post("/brain/switch")
async def brain_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    brain = getattr(request.app.state, "brain", None)
    if brain is None or not hasattr(brain, "switch"):
        # The brain is built on a background task after boot, so a very early
        # click can land before it is ready. It can also be genuinely absent on
        # a headless build. Either way "wait and retry" is the honest guidance —
        # the old "headless mode" wording misdiagnosed a fresh-install brain that
        # simply had not finished building yet (see BrainManager.from_tier_config
        # default-router synthesis).
        raise HTTPException(
            status_code=503,
            detail=(
                "Brain is still starting up or unavailable — wait a moment and "
                "try again. If it persists, check the server logs."
            ),
        )

    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unbekannter Provider: {body.provider}")
    if spec.tier == "brain" and not spec.brain_switchable:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{spec.label} is subagent-only in Jarvis. It cannot be used as "
                "the main Brain provider because it cannot see Computer-Use "
                "screenshots. Activate it in the Subagent section instead."
            ),
        )

    cfg = _resolve_cfg(request)
    profile_name = getattr(getattr(cfg, "profile", None), "name", "default")
    if profile_name == "airgapped" and body.provider not in LOCAL_PROVIDERS:
        raise HTTPException(
            status_code=403,
            detail="Privacy-Mode aktiv — nur lokale Provider erlaubt.",
        )

    available = []
    if hasattr(brain, "available_providers"):
        try:
            available = list(brain.available_providers())
        except Exception:  # noqa: BLE001
            available = []
    if available and body.provider not in available:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{body.provider}' ist nicht im Plugin-Registry verfügbar",
        )

    # Akzeptanzkriterium: Provider ohne gespeicherten Key duerfen nicht aktiviert
    # werden. Analog zu tts_switch/stt_switch — der Switch wuerde sonst
    # nominell gelingen, aber der erste Turn faellt mit "missing_key" und der
    # Provider landet in _dead_providers. Sauberer 409 statt stiller Fehler.
    # Reihenfolge: 404 (Provider unbekannt/nicht im Registry) kommt VOR
    # 409 (Provider bekannt, aber Credentials fehlen) — Identifiability vor
    # Konfiguration.
    #
    # Defensive legacy branch: Codex/Antigravity are rejected above as
    # ``brain_switchable=False``. If that guard is ever relaxed, keep credential
    # checks explicit instead of letting a switch succeed and fail on first turn.
    if spec.id == "codex":
        if not _codex_brain_usable():
            raise HTTPException(
                status_code=409,
                detail=(
                    "Codex can't be a brain yet — add an OpenAI API key (fast) or "
                    "run 'codex login' (ChatGPT subscription, slower CLI path)."
                ),
            )
    elif spec.id == "antigravity":
        # OAuth-only: no API key. Gate on the Google CLI login being present,
        # mirroring the codex branch (the CLI bills the Google subscription).
        from jarvis.google_cli.auth_service import GoogleCliAuthService

        if not GoogleCliAuthService().status().connected:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Antigravity isn't connected — sign in with Google (install "
                    "agy or the Gemini CLI and log in), then activate."
                ),
            )
    elif not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{spec.label} hat keinen gespeicherten API-Key. "
                "Erst Key in der Karte speichern, dann aktivieren."
            ),
        )

    # ``persisted`` reflects the ACTUAL disk outcome, not the echoed request
    # flag — a failed write must surface as persisted=false so the UI knows the
    # choice will not survive a restart (anti-silent-drop, AD-OE6). We start
    # pessimistic and only flip to True when the manager confirms the write.
    persisted = False
    try:
        await brain.switch(body.provider, persist=body.persist)
    except TypeError:
        # Genuinely old switch signature without the persist kwarg. We must NOT
        # silently drop persistence: attempt the disk write directly here, and
        # if even that path is unavailable, report persisted=false so the UI is
        # honest about the outcome.
        await brain.switch(body.provider)
        if body.persist:
            persisted = _persist_brain_primary_fallback(body.provider)
    except Exception as exc:  # noqa: BLE001
        log.exception("Brain-Switch zu '%s' fehlgeschlagen", body.provider)
        raise HTTPException(
            status_code=500,
            detail=f"Switch fehlgeschlagen: {type(exc).__name__}: {exc}",
        ) from exc
    else:
        # Normal path: read the real persist outcome the manager recorded.
        if body.persist:
            persisted = bool(getattr(brain, "last_persist_ok", False))

    # Switch-Validierung: BrainManager.switch() returnt silent bei einem
    # KeyError im Plugin-Registry. Wir lesen den Live-State zurueck und
    # propagieren einen Fehler, falls der Wechsel nicht angekommen ist —
    # sonst sieht das Frontend "200 OK" trotz No-Op und der User wundert
    # sich, warum die UI bei alt bleibt.
    actual = getattr(brain, "active_provider", None)
    if actual != body.provider:
        log.warning(
            "Brain-Switch silent failure: requested=%s, actual=%s",
            body.provider, actual,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Switch zu '{body.provider}' nicht angewendet "
                f"(aktuell: {actual!r}). Provider eventuell nicht ladbar."
            ),
        )
    if body.persist and not persisted:
        log.warning(
            "Brain-Switch to '%s' applied live but persistence to disk FAILED — "
            "the choice will not survive a restart.",
            body.provider,
        )
    return {"ok": True, "active": body.provider, "persisted": persisted}


@router.post("/tts/switch")
async def tts_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Wechselt den aktiven TTS-Provider live, ohne Pipeline-Restart.

    Schritte:
      1. TOML-Persist (ueberlebt Restart)
      2. ``cfg.tts.provider`` in-memory updaten
      3. Wenn die SpeechPipeline aktiv ist (``app.state.speech_pipeline``):
         neuen TTS-Provider via ``build_tts_from_config`` bauen und in die
         Pipeline injizieren. Der naechste ``_speak()``-Call nutzt die neue
         Stimme. ``restart_required=false``.
      4. Wenn keine Pipeline laeuft (Headless/Voice abgeschaltet): nur Persist,
         ``restart_required=true`` als ehrliche Info an die UI.

    Frueher (vor 2026-04-25) gab es Schritt 3 nicht — der Switch persistierte
    nur in der TOML, die laufende Pipeline behielt aber ihren alten
    TTS-Provider in ``self._tts``. User klickte "switch", UI sagte OK, aber
    er hoerte trotzdem den alten Provider bis zum App-Neustart.
    """
    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unbekannter Provider: {body.provider}"
        )
    if spec.tier != "tts":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' ist kein TTS-Provider (tier={spec.tier})",
        )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=f"Provider '{body.provider}' hat keine Credentials — erst API-Key setzen.",
        )

    if body.persist:
        try:
            from jarvis.core.config_writer import set_tts_provider

            set_tts_provider(body.provider)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    # Best-effort In-Memory-Update: wenn cfg an app.state haengt und das
    # Pydantic-Model nicht frozen ist, koennen Subscriber den Wert sofort lesen.
    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "tts", None) is not None:
        try:
            cfg.tts.provider = body.provider  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — frozen models sind kein Fehler
            pass

    # Live-Switch in die laufende SpeechPipeline (Hauptzweck dieses Fixes).
    # Wenn die Pipeline nicht laeuft (Headless / Voice abgeschaltet), faellt
    # der Switch auf "restart_required=true" zurueck — ehrliche UI-Info.
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    restart_required = True
    live_switched = False
    if pipeline is not None and hasattr(pipeline, "set_tts") and cfg is not None:
        try:
            from jarvis.plugins.tts import build_tts_from_config

            new_tts = build_tts_from_config(cfg.tts)
            pipeline.set_tts(new_tts)
            restart_required = False
            live_switched = True
            log.info(
                "TTS-Live-Switch in laufende SpeechPipeline: provider=%s",
                body.provider,
            )
        except Exception as exc:  # noqa: BLE001
            # Live-Switch fehlgeschlagen — Persist hat aber geklappt, also
            # waere ein Restart die zweite Option. Wir loggen den Root-Cause
            # mit Stack, damit der User im Log sieht, was schief ging.
            log.error(
                "TTS-Live-Switch fehlgeschlagen — Restart noetig: %s: %s",
                type(exc).__name__, exc, exc_info=True,
            )
            restart_required = True

    await _emit(request, SecretConfigured(key="tts.provider", action="set"))

    return {
        "ok": True,
        "active": body.provider,
        "persisted": body.persist,
        "live_switched": live_switched,
        "restart_required": restart_required,
    }


# ----------------------------------------------------------------------
# Per-model VOICE picker + audio preview (OpenRouter TTS)
# ----------------------------------------------------------------------
#
# A TTS model (Gemini Flash TTS, Kokoro, MAI-Voice, ...) ships its OWN set of
# voices, each speaking a specific language (or multilingual). These two routes
# feed the desktop voice picker: list the chosen model's voices tagged by
# language, and synthesise a short spoken sample so the user can HEAR a voice
# before committing. The provider id is always ``openrouter-tts`` today; the
# routes 400 for any other id rather than guessing.

# The only TTS provider that exposes a per-model voice list + preview so far.
_VOICE_PICKER_PROVIDER = "openrouter-tts"

# The fixed sentence spoken by the preview, per language. Long enough that a
# voice's timbre and character are actually audible (a one-liner made every
# voice sound alike), but still short enough to stay a cheap, quick preview.
# Every supported runtime-output language has an entry (never a de/en-only
# table — AP-21 / runtime-language doctrine).
_TTS_PREVIEW_SAMPLES: dict[str, str] = {
    "de": (
        "Hallo! Ich bin dein persönlicher Assistent. "
        "So klingt meine Stimme, wenn ich für dich spreche und dir zuhöre."
    ),
    "en": (
        "Hi there! I am your personal assistant. "
        "This is how my voice sounds when I speak with you and help you out."
    ),
    "es": (
        "¡Hola! Soy tu asistente personal. "
        "Así suena mi voz cuando hablo contigo y te ayudo con tus tareas."
    ),
}
_TTS_PREVIEW_DEFAULT_LANG = "en"


def _pcm_to_wav(pcm: bytes, *, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap int16 little-endian PCM in a minimal in-memory WAV container.

    Mirrors ``jarvis.plugins.stt.openrouter_stt._wrap_pcm_as_wav`` so an
    ``<audio>`` element can play the OpenRouter TTS stream (raw 24 kHz mono
    s16le PCM) directly without a client-side decoder.
    """
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(max(1, channels))
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


class TtsVoiceEntry(BaseModel):
    id: str
    # ISO-639-1 code ("en"/"de"/"es"/"fr"/…) or "multi" for a multilingual /
    # voice-agnostic model. The UI maps it to a flag chip (unknown → the code).
    language: str


class TtsVoicesResponse(BaseModel):
    provider: str
    model: str
    voices: list[TtsVoiceEntry]
    # The model's own safe default voice (used to pre-select the picker).
    default: str = ""
    # The voice currently persisted in [tts] IF it is valid for this model,
    # else "" (a stale voice from another model shows the placeholder instead).
    current: str = ""


def _tts_voice_entries(provider: str, model: str) -> tuple[list[dict], str, str]:
    """Voice payload for a TTS provider: ``(entries, resolved_model, default)``.

    Cross-provider (design 2026-07-07): OpenRouter exposes per-model voices
    (filtered to the allowlisted models); every other allowed family serves its
    curated voices from ``curated_catalog``. Raises ``HTTPException(400)`` for an
    unknown / unsupported / non-allowlisted provider or model.
    """
    from jarvis.plugins.tts import _canonical_tts_name
    from jarvis.plugins.tts import curated_catalog as cc

    fam = _canonical_tts_name(provider)
    if fam == "openrouter":
        from jarvis.plugins.tts.openrouter_speech_models import (
            MODEL_DEFAULT_VOICE,
            coerce_speech_model,
            voice_entries_for_model,
        )

        resolved = coerce_speech_model(model)
        if not cc.is_allowed("openrouter", resolved):
            raise HTTPException(
                status_code=400,
                detail=f"Model {resolved!r} is not on the TTS allowlist.",
            )
        entries = voice_entries_for_model(resolved)
        default = MODEL_DEFAULT_VOICE.get(resolved, "") or (
            entries[0]["id"] if entries else ""
        )
        return entries, resolved, default

    models = cc.allowed_models(family=fam)
    if not models:
        raise HTTPException(
            status_code=400,
            detail=f"No curated TTS voices for provider {provider!r}.",
        )
    model_id = models[0].model_id
    voices = cc.allowed_voices(fam, model_id)
    if voices:
        entries = [{"id": v.id, "language": v.language} for v in voices]
    else:
        # A model-level provider (e.g. Cartesia): fall back to the static catalog
        # pick list; those ids are language-agnostic voice/model handles.
        from jarvis.brain.model_catalog import TTS_CATALOG

        _sel, ms = TTS_CATALOG.get(fam, ("voice", []))
        entries = [{"id": m.id, "language": cc.MULTILINGUAL} for m in ms]
    default = entries[0]["id"] if entries else ""
    return entries, model_id, default


@router.get("/tts/voices")
async def list_tts_voices(
    request: Request, provider: str = "", model: str = ""
) -> TtsVoicesResponse:
    """Voices for a TTS provider, each tagged with its spoken language.

    Feeds the voice picker. ``language`` is an ISO-639-1 code or ``"multi"``
    (multilingual / voice-agnostic). Serves EVERY allowlisted family (Inworld,
    Gemini, ElevenLabs, Grok, Cartesia, OpenRouter) — not OpenRouter-only. An
    unknown / non-allowlisted provider or model is a clean 400.
    """
    prov = (provider or "").strip() or (_active_tts(request) or _VOICE_PICKER_PROVIDER)
    entries, resolved, default = _tts_voice_entries(prov, model)
    # Reflect the persisted voice only when it belongs to THIS model, so the
    # picker never shows a stale voice from a previously selected model.
    cfg = _resolve_cfg(request)
    persisted = getattr(getattr(cfg, "tts", None), "voice_de", "") or ""
    valid_ids = {e["id"] for e in entries}
    current = persisted if persisted in valid_ids else ""
    return TtsVoicesResponse(
        provider=prov,
        model=resolved,
        voices=[TtsVoiceEntry(**e) for e in entries],
        default=default,
        current=current,
    )


class TtsVoiceBody(BaseModel):
    # Empty is not meaningful here — a voice must be chosen. Bounded like the
    # other selection bodies.
    voice: str = Field(default="", max_length=200)
    persist: bool = Field(default=True)


@router.post("/tts/voice")
async def set_tts_voice_selection(
    body: TtsVoiceBody, request: Request
) -> BrainModelSaveResponse:
    """Persist + live-apply the global TTS voice (``[tts] voice_de``/``voice_en``).

    A TTS model ships several voices; this pins the chosen one. Reuses the shared
    ``_apply_tts_selection`` path (config-soll-synced write + a live rebuild of
    the running SpeechPipeline's TTS) so the next spoken turn uses it without a
    restart when voice is active.
    """
    voice = body.voice.strip()
    if not voice:
        raise HTTPException(status_code=400, detail="A voice id is required.")
    # Persist against the ACTIVE TTS provider, not a hardcoded OpenRouter id, so
    # picking an Inworld/Gemini/ElevenLabs voice writes it for that provider.
    active = _active_tts(request) or _VOICE_PICKER_PROVIDER
    return _apply_tts_selection(
        active,
        voice,
        "voice",
        BrainModelBody(model=voice, persist=body.persist),
        request,
    )


class TtsPreviewBody(BaseModel):
    provider: str = Field(default=_VOICE_PICKER_PROVIDER)
    model: str = Field(default="", max_length=200)
    voice: str = Field(default="", max_length=200)
    # The sample language to speak ("de" | "en" | "es"). Falls back to English.
    language: str = Field(default=_TTS_PREVIEW_DEFAULT_LANG, max_length=16)


@router.post("/tts/preview")
async def tts_preview(body: TtsPreviewBody) -> Response:
    """Synthesise a SHORT spoken sample with the given model + voice.

    Returns ``audio/wav`` bytes (24 kHz mono s16le wrapped in a WAV container) so
    an ``<audio>`` element can play it directly. Kept cheap: one tiny fixed
    sentence. Any failure (no key / 4xx / transport) is a clean 4xx/5xx JSON
    error — never a 500 that breaks the page — so the picker can show a toast.
    """
    lang = (body.language or _TTS_PREVIEW_DEFAULT_LANG).lower().split("-", 1)[0]
    sample = _TTS_PREVIEW_SAMPLES.get(lang, _TTS_PREVIEW_SAMPLES[_TTS_PREVIEW_DEFAULT_LANG])

    from jarvis.core.config import TTSConfig
    from jarvis.plugins.tts import _build_provider, _canonical_tts_name

    fam = _canonical_tts_name(body.provider)
    voice = body.voice.strip() or None
    # Build the EXACT requested family (not the key-aware cross-resolve) so the
    # preview plays what the user picked. A missing key makes synthesize fall
    # back / error → an honest 502 toast, never a broken page.
    tcfg = TTSConfig(provider=fam, model=body.model or None)
    try:
        tts = _build_provider(tcfg, fam)
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        raise HTTPException(
            status_code=400, detail=f"Cannot preview {body.provider!r}: {exc}"
        ) from exc

    pcm = bytearray()
    sample_rate = 24_000
    try:
        async for chunk in tts.synthesize(sample, voice=voice, language_code=lang):
            pcm += bytes(chunk.pcm)
            sample_rate = chunk.sample_rate
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        raise HTTPException(
            status_code=502, detail=f"Voice preview failed: {exc}"
        ) from exc
    finally:
        aclose = getattr(tts, "aclose", None)
        if aclose is not None:
            await aclose()

    if not pcm:
        raise HTTPException(status_code=502, detail="Voice preview produced no audio.")
    wav = _pcm_to_wav(bytes(pcm), sample_rate=sample_rate, channels=1)
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/stt/switch")
async def stt_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Wechselt den aktiven STT-Provider. Persistiert in jarvis.toml (tomlkit).

    Wie bei TTS gibt es keinen Live-Switch — der STT-Provider wird beim
    SpeechPipeline-Bootstrap einmalig instanziiert (Whisper-Model laden ist
    teuer). Der Switch greift daher erst nach dem naechsten Voice-Restart
    bzw. App-Neustart. Response markiert das mit ``restart_required: true``.
    """
    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unbekannter Provider: {body.provider}"
        )
    if spec.tier != "stt":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' ist kein STT-Provider (tier={spec.tier})",
        )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=f"Provider '{body.provider}' hat keine Credentials — erst API-Key setzen.",
        )

    if body.persist:
        try:
            from jarvis.core.config_writer import set_stt_provider

            set_stt_provider(body.provider)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "stt", None) is not None:
        try:
            cfg.stt.provider = body.provider  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — frozen models sind kein Fehler
            pass

    await _emit(request, SecretConfigured(key="stt.provider", action="set"))

    return {
        "ok": True,
        "active": body.provider,
        "persisted": body.persist,
        "restart_required": True,
    }


@router.post("/jarvis-agent/switch")
async def jarvis_agent_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Wechselt den aktiven SUBAGENT-Provider (``[brain.sub_jarvis].provider``).

    Das ist der Heavy-Task-Worker (lies Repo, baue Feature, reproduziere Bug) —
    getrennt vom leichten Router-Brain (``[brain].primary``). Bisher war die
    Subagent-Sektion read-only; dieser Endpoint gibt ihr — analog zu
    ``stt_switch`` — den Schreibpfad fuer den "Als aktiv"-Wechsel.

    Schritte:
      1. Validierung gegen die subagent-faehigen Provider (provider_map MAPPINGS).
      2. 409 wenn kein API-Key / OAuth-Token gespeichert (kein stiller No-Op —
         analog zu brain/tts/stt-switch, sonst faellt der erste Mission-Step
         mit ``missing_key``).
      3. 3-Schichten-Persist via ``config_writer.set_sub_jarvis_provider``
         (TOML + config-soll.json + ENV). Der config-soll-Pin ist
         entscheidend: ohne ihn rollt der Drift-Guard den Switch in 5 Min
         zurueck (gleiche Klasse wie der brain.primary-Persist-Bug).
      4. In-Memory-Update fuer sofortiges UI-Feedback (``/openclaw/status``
         liest ``cfg.brain.sub_jarvis.provider``).

    Der Worker wird beim Mission-Bootstrap einmalig verdrahtet
    (``jarvis.missions.init._worker_factory`` liest ``sub_jarvis_provider`` als
    Closure-Variable), daher greift der Switch fuer laufende Missionen erst
    nach Voice-/App-Restart: ``restart_required: true`` (wie bei STT).
    """
    from jarvis.missions.worker_runtime.provider_map import (
        JARVIS_TO_WORKER_SLUG,
        canonical_worker_provider,
    )

    # Normalize (lower/strip + ``openclaw-claude`` -> ``claude-api``) so the
    # accepted set matches what the UI cards display.
    provider = canonical_worker_provider(body.provider) or ""

    # Codex is a DIRECT worker (CodexDirectWorker) with no worker-harness slug —
    # it is not in JARVIS_TO_WORKER_SLUG. Handle it explicitly: it can be backed by
    # the ChatGPT subscription (OAuth, ``codex login``) OR an OpenAI API key.
    if provider in _CODEX_SUBAGENT_SLUGS:
        codex_connected = CodexAuthService(_codex_binary_path(request)).status().connected
        has_key = bool(
            cfg_mod.get_secret("codex_openai_api_key")
            or cfg_mod.get_provider_secret("codex")
        )
        if not (codex_connected or has_key):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Codex is not connected — run 'codex login' (ChatGPT) or save "
                    "an OpenAI API key first, then activate."
                ),
            )
        persisted = False
        if body.persist:
            try:
                from jarvis.core.config_writer import set_worker_provider

                set_worker_provider(_CODEX_SUBAGENT_CANONICAL)
                persisted = True
            except FileNotFoundError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
                ) from exc
        _apply_worker_in_memory(request, _CODEX_SUBAGENT_CANONICAL)
        await _emit(request, SecretConfigured(key="brain.worker.provider", action="set"))
        return {
            "ok": True,
            "active": _CODEX_SUBAGENT_CANONICAL,
            "persisted": persisted,
            "restart_required": True,
        }

    # Antigravity (Google subscription) is a DIRECT worker over the OAuth Google
    # CLI — no OpenClaw slug, no API key. Mirror of the codex branch: gate on the
    # OAuth login being present, then persist the "antigravity" slug.
    from jarvis.missions.worker_runtime.provider_map import (
        ANTIGRAVITY_SUBAGENT_CANONICAL,
        ANTIGRAVITY_SUBAGENT_SLUGS,
    )

    if provider in ANTIGRAVITY_SUBAGENT_SLUGS:
        from jarvis.google_cli.auth_service import GoogleCliAuthService

        # Dual billing (mirror of codex): the Google subscription OAuth login OR
        # a Gemini API key (per token). Either is enough to run the worker.
        antigravity_connected = GoogleCliAuthService().status().connected
        antigravity_key = bool(
            cfg_mod.get_secret("gemini_api_key", env_fallback="GEMINI_API_KEY")
        )
        if not (antigravity_connected or antigravity_key):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Antigravity is not connected — sign in with Google "
                    "(install agy or the Gemini CLI and log in) or set a Gemini "
                    "API key, then activate."
                ),
            )
        persisted = False
        if body.persist:
            try:
                from jarvis.core.config_writer import set_worker_provider

                set_worker_provider(ANTIGRAVITY_SUBAGENT_CANONICAL)
                persisted = True
            except FileNotFoundError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
                ) from exc
        _apply_worker_in_memory(request, ANTIGRAVITY_SUBAGENT_CANONICAL)
        await _emit(request, SecretConfigured(key="brain.worker.provider", action="set"))
        return {
            "ok": True,
            "active": ANTIGRAVITY_SUBAGENT_CANONICAL,
            "persisted": persisted,
            "restart_required": True,
        }

    if provider not in JARVIS_TO_WORKER_SLUG:
        known = ", ".join(sorted(JARVIS_TO_WORKER_SLUG))
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{body.provider}' ist kein Subagent-faehiger Provider. "
                f"Verfuegbar: {known}."
            ),
        )

    # Key-Check — a provider without a stored credential cannot be activated.
    # ``claude-api`` counts the live Claude Max OAuth login (read by the
    # ClaudeDirectWorker from ~/.claude/.credentials.json) as a credential, so a
    # fresh Claude-Max user who only ran `claude login` (no stored API key) can
    # still select it — mirrors the codex/antigravity OAuth branches above.
    has_credential = bool(cfg_mod.get_provider_secret(provider))
    if not has_credential and provider == "claude-api":
        try:
            from jarvis.missions.isolation.env import read_live_claude_oauth_token

            has_credential = bool(read_live_claude_oauth_token())
        except Exception:  # noqa: BLE001
            has_credential = False
    if not has_credential:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{provider} hat keinen gespeicherten Key. "
                "Erst Key beim Brain-Provider setzen, dann aktivieren."
            ),
        )

    # 3-layer persist. ``persisted`` reflects the ACTUAL disk outcome (AD-OE6).
    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import set_worker_provider

            set_worker_provider(provider)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    # Best-effort in-memory update so the next /jarvis-agent/status reflects the
    # choice immediately (the worker itself only re-reads on restart).
    _apply_worker_in_memory(request, provider)

    await _emit(request, SecretConfigured(key="brain.worker.provider", action="set"))

    return {
        "ok": True,
        "active": provider,
        "persisted": persisted,
        "restart_required": True,
    }


class SubagentModelBody(BaseModel):
    """Body for the subagent model override. Empty string is meaningful:
    it resets to the active subagent provider's deep (frontier) model."""

    model: str = Field(default="", max_length=128)
    persist: bool = Field(default=True)


@router.post("/jarvis-agent/model")
async def jarvis_agent_model(body: SubagentModelBody, request: Request) -> dict[str, Any]:
    """Pin which MODEL the Jarvis-Agent worker runs (``[brain.sub_jarvis].model``).

    The dedicated worker LLM, separate from the router brain: the worker
    chain reads it per spawn (``provider_chain._resolve_provider_chain``) and
    ``/jarvis-agent/status`` displays it as ``sub_model_override`` /
    ``model_resolved``. No allowlist on the model id — providers add models
    faster than we could pin them; a typo simply falls back at the provider
    when rejected. Empty string = the documented sentinel for "provider's
    deep model".

    3-layer persist via ``config_writer.set_sub_jarvis_model`` —
    ``brain.sub_jarvis.model`` is drift-guard pinned, so a TOML-only write
    would be reverted within minutes (BUG-010 class).
    """
    model = body.model.strip()

    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import set_worker_model

            set_worker_model(model)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    # Best-effort in-memory update so the next /jarvis-agent/status reflects the
    # choice immediately (workers resolve their chain per spawn from config).
    _apply_worker_model_in_memory(request, model)

    await _emit(request, SecretConfigured(key="brain.worker.model", action="set"))

    return {
        "ok": True,
        "model": model,
        "persisted": persisted,
        "restart_required": True,
    }


"""REST API for the Brain, TTS, STT, Realtime and Dictation-polish providers.

Endpoints:
    GET    /api/providers                    → list configured and active status
    POST   /api/secrets/{key}                → set an allowlisted wizard secret
    DELETE /api/secrets/{key}                → delete a secret
    POST   /api/brain/switch                 → switch the active Brain provider
    POST   /api/tts/switch                   → switch the active TTS provider
    POST   /api/stt/switch                   → switch the active STT provider
    POST   /api/realtime/switch              → switch the active Realtime provider
    POST   /api/jarvis-agent/switch          → switch the Jarvis-Agent provider
    POST   /api/computer-use/switch          → switch the Computer-Use provider (persist)

Mounted by the WebServer in ``_build_app()``:
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

# The canonical slug is re-exported even though this module no longer uses it
# directly: the anti-drift parity test pins BOTH names to the single source
# (test_codex_jarvis_agent_parity.py, BUG-008 class).
from jarvis.missions.worker_runtime.provider_map import (
    CODEX_SUBAGENT_CANONICAL as _CODEX_SUBAGENT_CANONICAL,  # noqa: F401
)
from jarvis.missions.worker_runtime.provider_map import (
    CODEX_SUBAGENT_SLUGS as _CODEX_SUBAGENT_SLUGS,
)
from jarvis.setup.wizard import SECRETS as WIZARD_SECRETS

from .provider_spec import (
    DICTATION_SPEC_ID_BY_FAMILY,
    PROVIDERS,
    ProviderSpec,
    dictation_family_id,
    get_spec,
    provider_billing,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["providers"])


# Exact allowlist of credential slots declared by the setup wizard.
ALLOWED_SECRET_KEYS: frozenset[str] = frozenset(s.key for s in WIZARD_SECRETS)

# The airgapped local-provider allowlist (LOCAL_PROVIDERS) moved to
# jarvis.brain.app_control: the lock is enforced inside apply_provider_switch
# so voice, REST, CLI, and brain tools all share it.

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
    value: str = Field(..., min_length=1, description="Raw secret value (API key, token, etc.)")


class SwitchBody(BaseModel):
    provider: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Write the selection to jarvis.toml")


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
    # Tri-state tool-calling capability from provider model metadata. ``None``
    # means the catalog does not expose it; runtime activation still performs
    # the authoritative capability probe.
    tools: bool | None = None


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


# Realtime needs BOTH a model AND a voice per provider (unlike every other
# picker, which serves ONE selection) — a small dedicated control + endpoint
# rather than the search-heavy BrainModelSelector. Curated lists only
# (jarvis.brain.model_catalog.REALTIME_MODELS/REALTIME_VOICES), no live fetch.
class RealtimeOptionInfo(BaseModel):
    id: str
    label: str


class RealtimeOptionsResponse(BaseModel):
    provider: str
    models: list[RealtimeOptionInfo]
    voices: list[RealtimeOptionInfo]
    current_model: str
    current_voice: str


class RealtimeOptionsBody(BaseModel):
    # Omitted (None) -> leave unchanged; "" -> explicitly clear (provider
    # default), mirroring CuModelBody's "" contract.
    model: str | None = Field(default=None, max_length=200)
    voice: str | None = Field(default=None, max_length=100)


class RealtimeOptionsSaveResponse(BaseModel):
    ok: bool = True
    provider: str
    model: str
    voice: str
    restart_required: bool = False
    session_restarted: bool = False


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------


def _polish_family(spec: ProviderSpec) -> Any | None:
    """The dictation-polish family behind ``spec``, or ``None`` for any other card.

    The lookup goes through the polish module's own registry so the family's
    credential candidates are read where they are declared, never copied here.
    """
    if getattr(spec, "tier", None) != "dictation":
        return None
    from jarvis.dictation.polish_client import family_by_id

    return family_by_id(dictation_family_id(spec.id))


def _is_credential_present(spec: ProviderSpec, binary_path: str | None = None) -> bool:
    """Apply the credential check for the provider's authentication mode.

    Delegates to the shared implementation in :mod:`jarvis.brain.app_control`
    (imported lazily) so the UI route and the brain's ``switch-provider`` tool
    use the *same* check — anti-drift, BUG-008 class. Name/signature preserved
    for the rest of this module.

    ONE local addition: a dictation-polish card answers through its polish
    FAMILY instead. Such a card renders a single key field, but the pass itself
    accepts any slot in the family's candidate list — a Google user holding only
    ``google_api_key`` has a perfectly working polish pass, and the single-slot
    check would call that card "no key set" and offer a fix for a problem that
    does not exist. The shared implementation is untouched because it never sees
    these specs: ``apply_provider_switch`` rejects a dictation spec on its tier
    long before the credential check.
    """
    family = _polish_family(spec)
    if family is not None:
        from jarvis.dictation.polish_client import family_has_key

        return family_has_key(family)

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


def _stored_base_url(spec: ProviderSpec) -> str | None:
    """The persisted ``[brain.providers.<id>].base_url`` override, or ``None``.

    Only read for cards that expose the URL field; "" (the cleared state the
    writer leaves behind) reports as ``None`` so the UI shows the placeholder.
    """
    if not spec.supports_base_url:
        return None
    try:
        prov = cfg_mod.load_config().brain.providers.get(spec.id)
    except Exception:  # noqa: BLE001 — a card list must never 500 on config trouble
        return None
    value = (getattr(prov, "base_url", None) or "").strip()
    return value or None


def _local_runtime_payload(
    spec: ProviderSpec, *, model_override: str | None = None
) -> dict[str, Any] | None:
    """On-disk truth for a provider that runs locally; ``None`` for cloud cards.

    The card cannot infer readiness from the absence of a key field: a local
    provider has no credential to check, so without this probe every local card
    would render as "ready" the moment it exists — the exact defect that forced
    the local Whisper card off the list in 2026-07-03. Presence of a catalog
    entry is what makes a provider local here; no code branches on its name
    (AP-21).
    """
    try:
        from jarvis.speech.local_models import local_status

        status = local_status(spec.id, model_override=model_override)
    except Exception as exc:  # noqa: BLE001 — the provider list must never 500
        log.debug("Local-runtime probe for %s failed (%s); reporting none.", spec.id, exc)
        return None
    if status is None:
        return None
    return {
        "runtime": status.runtime,
        "engine_installed": status.engine_installed,
        "model_present": status.model_present,
        "model_label": status.model_label,
        "ready": status.ready,
        "detail": status.detail,
    }


def _installed_local_models(provider_id: str, models: list[Any]) -> list[Any]:
    """Drop catalog entries whose files are not on this machine.

    A no-op for cloud providers and for any local entry the model catalog does
    not describe as a downloadable bundle, so it can be applied unconditionally
    (AP-21: locality is a catalog fact, not a provider name).
    """
    try:
        from jarvis.speech.local_models import (
            SHERPA_BUNDLES,
            bundle_present,
            get_local_provider,
        )

        if get_local_provider(provider_id) is None:
            return models
        kept = [
            m
            for m in models
            if getattr(m, "id", "") not in SHERPA_BUNDLES
            or bundle_present(getattr(m, "id", ""))
        ]
    except Exception as exc:  # noqa: BLE001 — the picker must never 500
        log.debug("Local model filter for %s failed (%s); listing all.", provider_id, exc)
        return models
    if len(kept) != len(models):
        log.debug(
            "Local provider %s: %d of %d catalogued models are downloaded.",
            provider_id,
            len(kept),
            len(models),
        )
    return kept


def _spec_to_payload(
    spec: ProviderSpec,
    *,
    active_brain: str | None,
    active_tts: str | None,
    active_stt: str | None,
    active_realtime: str | None = None,
    active_computer_use: str | None = None,
    active_dictation: str | None = None,
    local_model_override: str | None = None,
) -> dict[str, Any]:
    if spec.tier == "brain":
        active = spec.id == active_brain
    elif spec.tier == "tts":
        active = spec.id == active_tts
    elif spec.tier == "realtime":
        active = spec.id == active_realtime
    elif spec.tier == "dictation":
        active = spec.id == active_dictation
    else:
        active = spec.id == active_stt

    secrets_set = {k: bool(cfg_mod.get_secret(k)) for k in spec.secret_keys}
    # The runtime resolves credentials through family fallback chains
    # (config.PROVIDER_SECRET_CANDIDATES); the form state must match, or a
    # single-key install sees a green card WITH an empty "enter a key" box
    # demanding a second key it does not need (Realtime cards, field report
    # 2026-07-21). Per-slot: dedicated value present OR the family chain
    # resolves one. Only single-slot api_key specs get the family OR — a
    # multi-slot spec (e.g. Twilio) has no meaningful per-slot fallback.
    secrets_effective = dict(secrets_set)
    if (
        spec.auth_mode == "api_key"
        and len(spec.secret_keys) == 1
        and not all(secrets_set.values())
    ):
        # A dictation-polish card has its own candidate list (polish_client),
        # which the brain's alias map knows nothing about; ask the family.
        if _polish_family(spec) is not None:
            family_present = _is_credential_present(spec)
        else:
            from jarvis.brain.app_control import AUTH_PROVIDER_ALIASES

            alias = AUTH_PROVIDER_ALIASES.get(spec.id, spec.id)
            try:
                family_present = bool(cfg_mod.get_provider_secret(alias))
            except Exception:  # noqa: BLE001 -- unknown family means no fallback
                family_present = False
        if family_present:
            secrets_effective = dict.fromkeys(spec.secret_keys, True)
    from .provider_spec import secret_slot_consumers

    secret_shared_with = {
        k: [label for label in secret_slot_consumers(k) if label != spec.label]
        for k in spec.secret_keys
    }
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
        # Fallback-aware per-slot state + the other surfaces sharing each
        # slot; the form renders "covered by your shared key" and warns
        # before deleting a key that other tiers still read.
        "secrets_effective": secrets_effective,
        "secret_shared_with": secret_shared_with,
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
        # Local/self-hosted cards: editable server URL
        # (PUT /providers/{id}/base-url); None base_url = vendor default.
        "supports_base_url": spec.supports_base_url,
        "default_base_url": spec.default_base_url,
        "base_url": _stored_base_url(spec),
        # Maintainer-recommended pick for this tier (UI badge) + the model it
        # points at. Presentation only — never gates behavior (AP-21).
        "recommended": spec.recommended,
        "recommended_model": spec.recommended_model,
        # Inverse of recommended: a "Not recommended" caution badge + tooltip
        # (e.g. NVIDIA NIM's slow free tier). Presentation only (AP-21).
        "caution": spec.caution,
        # This card powers a feature nothing else depends on. The UI renders an
        # "Optional" chip, and the health rollup stays silent when it has no key
        # instead of raising a permanent "needs setup" dot. Presentation +
        # nag-suppression only — never a behavior gate (AP-21).
        "optional": spec.optional,
        # Dictation-polish cards only: the value ``[dictation].polish_provider``
        # actually stores. The card id and the polish FAMILY id differ ("groq"
        # is already the brain card), so a client pinning this tier must send
        # THIS, not ``id`` — an unknown family id is ignored by
        # ``resolve_polish_chain`` and the pin would silently do nothing.
        # ``None`` on every other card.
        "polish_family": dictation_family_id(spec.id) or None,
        # On-device cards only: whether the engine and its weights are REALLY
        # here, so the UI can offer the install instead of a false "ready".
        # ``None`` on every cloud card.
        "local_runtime": _local_runtime_payload(
            spec, model_override=local_model_override
        ),
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
        # Overlay, not a new tier (see the CU-own-provider plan): a brain
        # provider can be BOTH the main Brain ("active") AND/OR the dedicated
        # Computer-Use planner ("computer_use_active") — the two selections
        # are independent, so this never touches "active" above.
        "computer_use_active": (
            spec.tier == "brain" and spec.id == active_computer_use
        ),
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


def _active_realtime(request: Request) -> str | None:
    """Return the configured or first credential-ready realtime provider.

    Resolution delegates to the plugin registry when no explicit selection
    exists, so a fresh install with only one arbitrary supported key activates
    that family without a provider-name default (AP-21/AP-22).
    """
    try:
        cfg = _resolve_cfg(request)
        realtime_cfg = getattr(getattr(cfg, "brain", None), "realtime", None)
        provider = (getattr(realtime_cfg, "provider", None) or "").strip()
        if provider:
            return provider
        from jarvis.realtime.factory import realtime_available_provider

        return realtime_available_provider(cfg)
    except Exception as exc:  # noqa: BLE001 -- the health panel must never 500
        log.debug("active-realtime resolution failed (%s); using None.", exc)
        return None


def _polish_enabled(cfg: Any) -> bool:
    """Whether ``[dictation].polish`` is switched on. Never raises."""
    dictation = getattr(cfg, "dictation", None) if cfg is not None else None
    return bool(getattr(dictation, "polish", False))


def _resolve_polish_subject(cfg: Any) -> str | None:
    """Resolve the polish card id from *cfg*. BLOCKING — never call on the loop.

    ``[dictation].polish_provider`` stores a polish FAMILY id ("groq"), while
    every card, health section and subject id in this module is a ProviderSpec
    id ("groq-polish"); the translation happens here so no other layer has to
    know that the two vocabularies differ.

    ``None`` — the pass is off, or the user holds no key in any family — is an
    ORDINARY state, not a fault: the polish pass is optional and its absence
    leaves dictation behaving exactly as it did before. The health rollup
    renders it silently (see :func:`_dictation_section_health`). Any resolver
    error also degrades to ``None``, because the health panel must never 500.
    """
    try:
        if not _polish_enabled(cfg):
            return None
        from jarvis.dictation.polish_client import resolve_polish_chain

        chain = resolve_polish_chain(getattr(cfg, "dictation", None))
        if not chain:
            return None
        return DICTATION_SPEC_ID_BY_FAMILY.get(chain[0].id)
    except Exception as exc:  # noqa: BLE001 — the health panel must never 500
        log.debug("active-polish resolution failed (%s); using None.", exc)
        return None


def _polish_subject_key(cfg: Any) -> tuple[Any, ...] | None:
    """A cheap value that changes exactly when the polish subject could.

    ``None`` means "not answerable right now", and its only effect is that the
    memo below is neither read nor written — one extra resolve, never a stale
    answer.

    The expensive half is delegated to
    :func:`jarvis.dictation.polish_client.polish_chain_fingerprint`, which is
    the polish tier's OWN cache key (the credential revisions plus the identity
    of the config file); reusing it means the health panel cannot disagree with
    the dictation path about when the answer went stale. The ``polish`` switch
    is added on top because this function answers ``None`` when the pass is
    off, which the chain fingerprint does not model.
    """
    try:
        from jarvis.dictation.polish_client import polish_chain_fingerprint

        return (
            _polish_enabled(cfg),
            *polish_chain_fingerprint(getattr(cfg, "dictation", None)),
        )
    except Exception as exc:  # noqa: BLE001 — an unfingerprintable host re-resolves
        log.debug("polish subject key unavailable (%s); resolving afresh.", exc)
        return None


async def _warm_active_polish(request: Request) -> None:
    """Resolve the polish subject in a worker thread, so the read below is free.

    :func:`_active_polish` is called from ``_section_health_subjects``, which is
    synchronous and feeds the rollup's cache fingerprint — and the API-Keys
    screen POLLS that rollup. The resolve behind it walks up to seven
    credential slots (OS keyring, then ENV, then ``.env``) and reads the config
    file; on a Linux desktop with a locked keyring or a slow D-Bus Secret
    Service that blocks for SECONDS. This repo already has that exact bug in
    its register — a ``load_config`` on the event loop stalling everything the
    loop owns, the Jarvis Bar included — so the resolve happens here, off the
    loop, before anything synchronous needs the answer.

    Cheap on the common path: the memo is keyed on
    :func:`_polish_subject_key`, so a poll that changed nothing does not even
    reach the thread. Stored on ``app.state`` rather than in a module global so
    two apps in one process (tests, an embedded second server) cannot answer
    each other's questions.
    """
    cfg = _resolve_cfg(request)
    key = _polish_subject_key(cfg)
    if key is None:
        return
    cached = getattr(request.app.state, "_polish_subject_cache", None)
    if isinstance(cached, tuple) and cached[0] == key:
        return
    request.app.state._polish_subject_cache = (
        key,
        await asyncio.to_thread(_resolve_polish_subject, cfg),
    )


def _active_polish(request: Request) -> str | None:
    """The card currently powering the dictation polish pass, or ``None``.

    A memo read whenever :func:`_warm_active_polish` already ran for the same
    key — which is the case for both routes that ask, so the polling path pays
    a handful of dict lookups and one ``stat``. It still resolves inline when
    nothing warmed it, because an unwarmed caller deserves a correct answer
    more than it deserves a fast one; that path is the one the direct unit
    tests take.
    """
    cfg = _resolve_cfg(request)
    key = _polish_subject_key(cfg)
    cached = getattr(request.app.state, "_polish_subject_cache", None)
    if key is not None and isinstance(cached, tuple) and cached[0] == key:
        return cached[1]
    subject = _resolve_polish_subject(cfg)
    if key is not None:
        request.app.state._polish_subject_cache = (key, subject)
    return subject


def _active_computer_use(request: Request) -> str | None:
    """The active dedicated Computer-Use planner provider.

    Falls back to ``brain.primary`` when no dedicated
    ``[brain.computer_use].provider`` is configured yet — Computer-Use runs
    on the main Brain until the user picks a dedicated CU provider (mirrors
    ``BrainManager._cu_provider``'s empty-string-means-unset default, so the
    "Active" badge and the actual dispatch chain always agree). Never raises:
    any resolver error falls back to ``None``.
    """
    try:
        cfg = _resolve_cfg(request)
        brain_cfg = getattr(cfg, "brain", None)
        tier_cfg = getattr(brain_cfg, "tool_model", None) or getattr(
            brain_cfg, "computer_use", None
        )
        provider = (getattr(tier_cfg, "provider", None) or "").strip()
        if provider == "auto":
            provider = ""
        if provider:
            return provider
        return (getattr(brain_cfg, "primary", None) or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — the health panel must never 500
        log.debug("active-computer-use resolution failed (%s); using None.", exc)
        return None


def _resolve_cfg(request: Request):
    """Return the active Jarvis configuration.

    The server stores it as ``app.state.config`` (not ``cfg``); see
    ``server.py::_build_app``. Fall back to ``load_config()`` when a headless
    app started without the normal bootstrap.
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


def _apply_worker_model_in_memory(request: Request, model: str) -> None:
    """Best-effort in-memory update of ``cfg.brain.worker.model``.

    A missing ``worker`` block is created with the router primary as provider,
    so the override is never silently dropped.
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
        log.warning("Could not publish event: %s", exc)


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
    """Return every provider with its current configured and active status."""
    active_brain = _active_brain(request)
    active_tts = _active_tts(request)
    active_stt = _active_stt(request)
    active_realtime = _active_realtime(request)
    active_computer_use = _active_computer_use(request)
    # Resolved in a worker thread first (keyring + config file); the read that
    # follows is then a memo hit. See ``_warm_active_polish``.
    await _warm_active_polish(request)
    active_dictation = _active_polish(request)
    # When a local STT provider is ALREADY the active one, its card must report
    # on the checkpoint the config names — not the catalog default. Otherwise a
    # user who pinned a different Whisper size in jarvis.toml would read a
    # reassuring "ready" about a model their install never loads.
    local_model_override: str | None = None
    try:
        stt_cfg = getattr(_resolve_cfg(request), "stt", None)
        if (getattr(stt_cfg, "provider", "") or "").strip() == "faster-whisper":
            local_model_override = (getattr(stt_cfg, "model", "") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — the provider list must never 500
        log.debug("Local STT model override lookup failed (%s); using the default.", exc)

    # Off the event loop: building the payload reads every secret slot from the
    # OS keyring and probes the Codex/Google CLI status — all synchronous. On the
    # loop it stalled EVERY concurrent request for seconds each time the UI
    # refetched after a key save / switch / test (the "whole screen feels stuck"
    # complaint); in a worker thread the loop stays responsive.
    def _build() -> list[dict[str, Any]]:
        return [
            _spec_to_payload(
                spec,
                active_brain=active_brain,
                active_tts=active_tts,
                active_stt=active_stt,
                active_realtime=active_realtime,
                active_computer_use=active_computer_use,
                active_dictation=active_dictation,
                local_model_override=local_model_override,
            )
            for spec in PROVIDERS
        ]

    return {"providers": await asyncio.to_thread(_build)}


# Belt-and-suspenders ceiling for the whole /test call. run_provider_test's own
# timeout_s (60 s, generous for NVIDIA NIM's 13-30 s cold-start TTFB) bounds the
# individual probe; this outer bound guarantees the HTTP response itself.
_PROVIDER_TEST_HARD_TIMEOUT_S = 75.0


async def _run_tier_test(
    spec: ProviderSpec, cfg: Any, *, model: str | None = None
) -> Any:
    """Dispatch a card to the probe that can actually judge it.

    Every tier but one is judged by ``run_provider_test``. A dictation card is
    NOT a speech-to-text provider, and it used to fall through to the STT branch
    there — so "Test" on a wording card built the user's RECOGNIZER and reported
    its verdict under the card's name (a broken local model turned four working
    cloud cards red), while the keyless local card answered "ok" in 0.0 ms
    having asked its server nothing at all.

    The wording probe therefore lives in the dictation layer, which is the only
    place allowed to call that pass (AP-11), and this function is the one seam
    that knows which card goes where.
    """
    family = _polish_family(spec)
    if family is not None:
        from jarvis.dictation.polish_probe import probe_polish_family

        return await probe_polish_family(family, cfg, model=model or "")
    return await _provider_test.run_provider_test(spec, cfg, model=model)


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
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")

    cfg = _resolve_cfg(request)
    if cfg is None:
        raise HTTPException(
            status_code=503, detail="Configuration is unavailable (headless mode?)"
        )

    # Hard ceiling ABOVE run_provider_test's own per-call timeout (60 s): the
    # route must always answer, else the UI's "Testing…" spinner never resolves.
    # Any async path that slips past the inner bounds is cut here and reported
    # as an honest "unreachable" instead of a hung HTTP request.
    try:
        result = await asyncio.wait_for(
            _run_tier_test(spec, cfg),
            timeout=_PROVIDER_TEST_HARD_TIMEOUT_S,
        )
    except TimeoutError:
        result = _provider_test.ProviderTestResult(
            provider=spec.id,
            status=_provider_test.UNREACHABLE,
            detail=(
                f"Test timed out after {_PROVIDER_TEST_HARD_TIMEOUT_S:.0f}s — "
                "the provider did not answer."
            ),
            latency_ms=_PROVIDER_TEST_HARD_TIMEOUT_S * 1000.0,
        )
    # This exact result is newer than any overlapping section sweep. Cancel the
    # older snapshot so the UI refresh cannot resurrect a pre-test status.
    _invalidate_section_health_state(request)
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
_SECTION_HEALTH_KEYS = (
    "brain",
    "computer-use",
    "tts",
    "stt",
    "realtime",
    "dictation",
    "subagents",
    "advanced",
)


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
    # Exact provider/integration this result belongs to. The frontend must never
    # attach a result to a different active card, even while a slow probe from the
    # previous selection is still completing.
    subject_id: str | None = None


class SectionHealthResponse(BaseModel):
    sections: dict[str, SectionHealth]
    checked_at: float = 0.0
    cached: bool = False


async def _tier_section_health(
    cfg: Any,
    spec: ProviderSpec | None,
    *,
    model: str | None = None,
    optional: bool = False,
    probe: bool = True,
) -> SectionHealth:
    """Health of one provider tier, derived from its ACTIVE provider only.

    A tier is only as healthy as the single provider currently powering it —
    deliberately NOT "does any provider here lack a key" (that would paint every
    tab red, since unused providers are normally left empty).

    ``model`` probes that exact model for a brain-tier spec — used by sections
    whose tier carries its own model pin (Tool Model), so the dot reflects what
    that tier actually runs, not the general brain model.

    ``optional`` marks a tier the install does not depend on. A missing key then
    reports ``ok``/``not_configured_optional`` instead of amber ``needs_setup``,
    because the feature simply does not run and everything else is unaffected —
    a tab that asks forever to be set up for something nothing is waiting on is
    a defect, not a reminder. The caller passes it because the flag must survive
    ``spec is None`` (no key anywhere = no active provider = exactly the state
    that must stay silent); a spec that carries ``optional`` itself also counts.
    An optional tier still turns RED when a key IS present and failing — the
    rule suppresses nagging, never a real fault.

    ``probe=False`` skips the live provider call and reports on credential
    presence alone. For a tier whose credential is a key another tier already
    owns and tests, a second network round-trip on every page open buys no
    signal it does not already have.
    """
    optional = optional or bool(getattr(spec, "optional", False))
    if spec is None:
        if optional:
            return SectionHealth(
                status=_section_health.OK,
                reason="not_configured_optional",
                detail="Optional: not set up, and nothing depends on it",
                subject_id=None,
            )
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="no_active",
            detail="No active provider selected",
            subject_id=None,
        )
    # Local providers have no key to be invalid — but "no key needed" is NOT the
    # same as "usable", and reading it that way is how an on-device card comes to
    # claim it works on a machine where its engine and weights were never
    # installed. So ask the disk (a cheap file check, no model load) before
    # calling the tier healthy. The real inference test still stays off the
    # page-open path: it would force a multi-gigabyte load for no extra signal.
    if getattr(spec, "auth_mode", None) == "none":
        local_state = _local_runtime_payload(spec)
        if local_state is not None and not local_state["ready"]:
            return SectionHealth(
                status=_section_health.NEEDS_SETUP,
                reason="not_installed",
                detail=f"{spec.label}: {local_state['detail']}",
                subject_id=spec.id,
            )
        return SectionHealth(
            status=_section_health.OK,
            reason="local",
            detail=f"{spec.label}: local, no key needed",
            subject_id=spec.id,
        )
    try:
        configured = _is_credential_present(
            spec, _codex_binary_path() if spec.id == "codex" else None
        )
    except Exception:  # noqa: BLE001 — a probe failure is "not set up", not a crash
        configured = False
    if not configured:
        if optional:
            return SectionHealth(
                status=_section_health.OK,
                reason="not_configured_optional",
                detail=f"{spec.label}: optional, no key set",
                subject_id=spec.id,
            )
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="not_configured",
            detail=f"{spec.label}: no key set",
            subject_id=spec.id,
        )
    if not probe:
        return SectionHealth(
            status=_section_health.OK,
            reason="configured",
            detail=f"{spec.label}: key set",
            subject_id=spec.id,
        )
    try:
        result = await _run_tier_test(spec, cfg, model=model)
    except Exception as exc:  # noqa: BLE001
        log.warning("section-health test for %s failed: %s", spec.id, exc)
        return SectionHealth(
            status=_section_health.UNKNOWN,
            reason="error",
            detail=f"{spec.label}: check failed",
            subject_id=spec.id,
        )
    status = _section_health.section_status_for_test(result.status, configured=True)
    return SectionHealth(
        status=status,
        reason=result.status,
        detail=f"{spec.label}: {result.detail or result.status}",
        subject_id=spec.id,
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
        from jarvis.core.config import get_jarvis_agent_secret

        if p in _CODEX_SUBAGENT_SLUGS or p in {"codex", "openai-codex"}:
            status = CodexAuthService(_codex_binary_path()).status()
            has_key = bool(
                cfg_mod.get_secret(
                    "codex_openai_api_key", env_fallback="CODEX_OPENAI_API_KEY"
                )
            )
            return bool(status.connected or (status.installed and has_key))
        if p == "antigravity":
            from jarvis.google_cli.auth_service import GoogleCliAuthService

            return bool(
                GoogleCliAuthService().status().connected
                or get_jarvis_agent_secret("gemini")
            )
        if p in {"claude-api", "claude"}:
            from jarvis.claude_auth import ClaudeAuthService

            st = ClaudeAuthService().status()
            return bool(
                getattr(st, "connected", False)
                or get_jarvis_agent_secret("claude-api")
            )
        return bool(get_jarvis_agent_secret(p))
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


def _claude_worker_display_label(*, default: str) -> str:
    """Honest display name for the Claude worker slot in health messages.

    The ``claude-api`` spec label says "(API-Key)", but the SAME slot runs on
    the Claude subscription OAuth login whenever one exists — a degraded
    banner blaming the "API-Key" then reads as nonsense to a subscription
    user (2026-07-10 report: "I selected the subscription, the key is
    irrelevant"). The CLI status is authoritative because current macOS
    releases keep the login in Keychain rather than a plaintext bearer file;
    the file probe remains only for an expired legacy login. Any failure keeps
    the spec label rather than breaking the health check.
    """
    try:
        from jarvis.claude_auth import ClaudeAuthService
        from jarvis.claude_credentials import freshest_claude_oauth

        status = ClaudeAuthService().status()
        subscription_connected = status.connected and status.mode == "subscription"
        oauth_status = freshest_claude_oauth().status
    except Exception:  # noqa: BLE001
        subscription_connected = False
        oauth_status = "absent"
    return (
        "Claude (subscription)"
        if subscription_connected or oauth_status != "absent"
        else default
    )


def _selected_jarvis_agent_provider(cfg: Any) -> str | None:
    """Return the exact provider selected for Jarvis-Agent work."""
    brain = getattr(cfg, "brain", None) if cfg is not None else None
    if brain is None:
        return None
    worker = getattr(brain, "worker", None)
    provider = (getattr(worker, "provider", None) if worker else None) or getattr(
        brain, "primary", None
    )
    return (provider or "").strip() or None


def _jarvis_agent_section_health(cfg: Any) -> SectionHealth:
    """Jarvis-Agents tab: report whether the selected worker is usable.

    A real "does it answer" call for a CLI worker is heavy, so v1 reports the
    connectedness signal — connected/keyed → ok, otherwise needs_setup. Since
    2026-07-07 it distinguishes degraded (fallback carries) from error
    (nothing reachable).
    """
    from jarvis.brain.assistant_name import agent_brand

    # Display brand follows the wake-word-derived assistant name (2026-07-17).
    brand = agent_brand(cfg)
    provider = _selected_jarvis_agent_provider(cfg)
    if provider is None:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="no_active",
            detail=f"No {brand} worker selected",
            subject_id=None,
        )
    spec = get_spec(provider)
    label = spec.label if spec is not None else provider
    if (provider or "").lower() in {"claude-api", "claude"}:
        label = _claude_worker_display_label(default=label)
    if _worker_usable(provider) and not _worker_flagged_dead(provider):
        return SectionHealth(
            status=_section_health.OK,
            reason="ok",
            detail=f"{brand} worker: {label}",
            subject_id=provider,
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
                f"{brand} worker '{label}' is unavailable — missions run on "
                f"{families[0]} until it is reconnected"
            ),
            subject_id=provider,
        )
    return SectionHealth(
        status=_section_health.ERROR,
        reason="no_provider",
        detail=(
            f"No {brand} provider is reachable — missions will fail. "
            f"Reconnect '{label}' or add an API key."
        ),
        subject_id=provider,
    )


async def _realtime_section_health(cfg: Any, spec: ProviderSpec | None) -> SectionHealth:
    """Test the active provider's actual duplex handshake.

    Credential presence alone previously painted a depleted or schema-broken
    provider green. Reuse the standard tier health mapping so the Realtime tab
    reports the same honest account/integration states as every other tier.
    """
    return await _tier_section_health(cfg, spec)


async def _dictation_section_health(
    cfg: Any, spec: ProviderSpec | None, *, enabled: bool
) -> SectionHealth:
    """Dictation-polish tab: honest about readiness, never nagging.

    The pass rewrites nothing the user depends on — with no key it reports
    "unavailable" and delivers the raw transcript, which is byte-identical to
    the behaviour before the feature existed. So this section deviates from the
    other tiers twice, both deliberately:

    * ``optional=True`` — no key anywhere stays silent instead of raising an
      amber dot on every install forever. That dot would be the feature's most
      visible effect on the majority of users, which is the opposite of what an
      optional convenience should do.
    * ``probe=False`` — no live call. The credential here is always a key some
      other tier already owns and tests (the Groq speech-to-text key, the
      Gemini/OpenAI/OpenRouter brain key), so probing again on page open would
      duplicate a request without producing a signal we do not already have.

    ``enabled`` is reported separately from "no key", because "you switched it
    off" and "you have no key for it" are different answers to "why is nothing
    happening" — and both must stay dot-free.
    """
    if not enabled:
        return SectionHealth(
            status=_section_health.OK,
            reason="disabled",
            detail="Dictation polish is switched off",
            subject_id=None,
        )
    return await _tier_section_health(cfg, spec, optional=True, probe=False)


def _advanced_section_health(request: Request) -> SectionHealth:
    """Advanced tab: every integration here is OPTIONAL, so it never reports
    ``needs_setup`` — only ``error`` when something the user actually configured
    is failing. Today that is telephony's cached reachability check; otherwise the
    tab stays silent (``unknown``)."""
    contributions: list[str] = []
    detail = ""
    reason = "unknown"
    tm = getattr(request.app.state, "telephony_manager", None)
    subject_id = "telephony" if tm is not None else None
    if tm is not None and getattr(tm, "reachable", None) is False:
        err = getattr(tm, "reachable_error", None)
        if err:
            contributions.append(_section_health.ERROR)
            detail = f"Telephony unreachable: {err}"
            reason = "telephony"
    return SectionHealth(
        status=_section_health.aggregate(contributions),
        reason=reason,
        detail=detail,
        subject_id=subject_id,
    )


def _section_health_subjects(request: Request, cfg: Any) -> dict[str, str | None]:
    """Capture the exact runtime selection behind every health section."""
    telephony = getattr(request.app.state, "telephony_manager", None)
    return {
        "brain": _active_brain(request),
        "computer-use": _active_computer_use(request),
        "tts": _active_tts(request),
        "stt": _active_stt(request),
        "realtime": _active_realtime(request),
        "dictation": _active_polish(request),
        "subagents": _selected_jarvis_agent_provider(cfg),
        "advanced": "telephony" if telephony is not None else None,
    }


def _section_health_fingerprint(
    request: Request,
    cfg: Any,
    subjects: dict[str, str | None],
) -> tuple[tuple[str, str], ...]:
    """Return a secret-free cache key for the full health selection snapshot.

    Provider identity alone is insufficient: changing a Brain, Computer-Use,
    TTS, STT, or Realtime model while its provider stays selected must also
    supersede the old probe. Otherwise an old model timeout can be cached and
    displayed against the newly selected, working model.
    """
    telephony = getattr(request.app.state, "telephony_manager", None)
    reachable = getattr(telephony, "reachable", None) if telephony is not None else None
    brain = getattr(cfg, "brain", None) if cfg is not None else None
    providers = getattr(brain, "providers", None)

    def _provider_value(section: str, field: str) -> str:
        provider_id = subjects.get(section) or ""
        provider_cfg = providers.get(provider_id) if isinstance(providers, dict) else None
        return str(getattr(provider_cfg, field, None) or "")

    tts = getattr(cfg, "tts", None) if cfg is not None else None
    stt = getattr(cfg, "stt", None) if cfg is not None else None
    worker = getattr(brain, "worker", None) if brain is not None else None
    tts_extra = getattr(tts, "model_extra", None)
    cartesia = tts_extra.get("cartesia") if isinstance(tts_extra, dict) else None
    cartesia_model = cartesia.get("model_id") if isinstance(cartesia, dict) else None
    # The dictation section answers from config, not from a live probe, so its
    # two inputs must enter the key themselves — otherwise flipping the polish
    # switch shows the previous verdict for up to _SECTION_HEALTH_TTL_S.
    dictation = getattr(cfg, "dictation", None) if cfg is not None else None
    configuration = (
        ("brain-model", _provider_value("brain", "model")),
        ("computer-use-model", _provider_value("computer-use", "tool_model")),
        ("tts-model", str(getattr(tts, "model", None) or "")),
        ("tts-voice-de", str(getattr(tts, "voice_de", None) or "")),
        ("tts-voice-en", str(getattr(tts, "voice_en", None) or "")),
        ("tts-cartesia-model", str(cartesia_model or "")),
        ("stt-model", str(getattr(stt, "model", None) or "")),
        ("realtime-model", _provider_value("realtime", "model")),
        ("realtime-voice", _provider_value("realtime", "voice")),
        ("jarvis-agent-model", str(getattr(worker, "model", None) or "")),
        ("dictation-polish", "1" if _polish_enabled(cfg) else "0"),
        ("dictation-provider", str(getattr(dictation, "polish_provider", None) or "")),
        ("advanced-reachable", repr(reachable)),
    )
    return (
        tuple((key, subjects.get(key) or "") for key in _SECTION_HEALTH_KEYS)
        + configuration
    )


def _invalidate_section_health_state(request: Request) -> None:
    """Discard cached and in-flight health work after a configuration change."""
    request.app.state._section_health_cache = None
    tasks = getattr(request.app.state, "_section_health_tasks", None)
    if not isinstance(tasks, dict):
        return
    for task in tuple(tasks.values()):
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
    tasks.clear()


@router.get("/providers/section-health")
async def section_health(request: Request, refresh: bool = False) -> SectionHealthResponse:
    """Per-tab health for the API-Keys segmented tabs ("is this part working?").

    Every selectable provider surface is checked against one immutable selection
    snapshot. Cache entries and in-flight tasks carry that snapshot fingerprint,
    so a slow result from a previous provider can never be reused for the current
    one. Checks for a superseded snapshot are cancelled without blocking the new
    provider's check.
    """
    while True:
        cfg = _resolve_cfg(request)
        # The dictation subject is the one subject whose resolution blocks (see
        # ``_warm_active_polish``); every other one is an attribute read. Warm
        # it off the loop BEFORE the snapshot is taken, because the snapshot
        # feeds the cache fingerprint and this route is polled.
        await _warm_active_polish(request)
        subjects = _section_health_subjects(request, cfg)
        fingerprint = _section_health_fingerprint(request, cfg, subjects)
        cache = getattr(request.app.state, "_section_health_cache", None)
        now = time.time()
        if (
            not refresh
            and isinstance(cache, dict)
            and cache.get("fingerprint") == fingerprint
            and now - cache.get("checked_at", 0.0) < _SECTION_HEALTH_TTL_S
        ):
            return SectionHealthResponse(
                sections=cache["payload"],
                checked_at=cache["checked_at"],
                cached=True,
            )

        lock = getattr(request.app.state, "_section_health_task_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            request.app.state._section_health_task_lock = lock

        async with lock:
            tasks = getattr(request.app.state, "_section_health_tasks", None)
            if not isinstance(tasks, dict):
                tasks = {}
                request.app.state._section_health_tasks = tasks

            # A globally selected provider changed. Its previous task is obsolete
            # and must not hold the new provider behind a 60-second timeout.
            for old_fingerprint, old_task in tuple(tasks.items()):
                if old_fingerprint == fingerprint:
                    continue
                if isinstance(old_task, asyncio.Task) and not old_task.done():
                    old_task.cancel()
                tasks.pop(old_fingerprint, None)

            task = tasks.get(fingerprint)
            if not isinstance(task, asyncio.Task) or task.done():
                task = asyncio.create_task(
                    _compute_section_health(request, cfg, subjects),
                    name="section-health-snapshot",
                )
                tasks[fingerprint] = task

        try:
            sections = await asyncio.shield(task)
        except asyncio.CancelledError:
            # ``shield`` distinguishes an obsolete shared task from cancellation
            # of this HTTP request. Retry only the former against the new snapshot.
            if task.cancelled():
                refresh = True
                continue
            raise

        current_cfg = _resolve_cfg(request)
        await _warm_active_polish(request)
        current_subjects = _section_health_subjects(request, current_cfg)
        current_fingerprint = _section_health_fingerprint(
            request, current_cfg, current_subjects
        )
        if current_fingerprint != fingerprint:
            refresh = True
            continue

        checked_at = time.time()
        request.app.state._section_health_cache = {
            "checked_at": checked_at,
            "fingerprint": fingerprint,
            "payload": sections,
        }
        async with lock:
            tasks = getattr(request.app.state, "_section_health_tasks", None)
            if isinstance(tasks, dict) and tasks.get(fingerprint) is task:
                tasks.pop(fingerprint, None)
        return SectionHealthResponse(
            sections=sections,
            checked_at=checked_at,
            cached=False,
        )


async def _compute_section_health(
    request: Request,
    cfg: Any,
    subjects: dict[str, str | None],
) -> dict[str, SectionHealth]:
    """Compute one immutable, provider-bound health snapshot."""
    sections: dict[str, SectionHealth] = {}

    if cfg is None:
        for key in _SECTION_HEALTH_KEYS:
            sections[key] = SectionHealth(
                status=_section_health.UNKNOWN,
                reason="unavailable",
                detail="Configuration unavailable",
                subject_id=subjects.get(key),
            )
        return sections

    async def _safe_check(
        section: str,
        check: Any,
    ) -> SectionHealth:
        try:
            return await check
        except Exception as exc:  # noqa: BLE001
            log.warning("section-health %s check failed: %s", section, exc)
            return SectionHealth(
                status=_section_health.UNKNOWN,
                reason="error",
                detail="Health check failed",
                subject_id=subjects.get(section),
            )

    checks = {
        "brain": _tier_section_health(cfg, get_spec(subjects["brain"] or "")),
        # The Tool Model tier has its own model pin (tool_model → cu_model);
        # probe THAT model, not the general brain model. An unset pin falls
        # through to run_provider_test's own resolution (model → tier default).
        "computer-use": _tier_section_health(
            cfg,
            get_spec(subjects["computer-use"] or ""),
            model=_provider_cu_model(cfg, subjects["computer-use"] or "") or None,
        ),
        "tts": _tier_section_health(cfg, get_spec(subjects["tts"] or "")),
        "stt": _tier_section_health(cfg, get_spec(subjects["stt"] or "")),
        "realtime": _realtime_section_health(
            cfg, get_spec(subjects["realtime"] or "")
        ),
        # Optional tier: silent without a key, never amber (see the function).
        "dictation": _dictation_section_health(
            cfg,
            get_spec(subjects["dictation"] or ""),
            enabled=_polish_enabled(cfg),
        ),
        "subagents": asyncio.to_thread(_jarvis_agent_section_health, cfg),
        "advanced": asyncio.to_thread(_advanced_section_health, request),
    }
    results = await asyncio.gather(
        *(_safe_check(section, check) for section, check in checks.items())
    )
    sections.update(zip(checks, results, strict=True))
    return sections


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
    if pc is None:
        return ""
    return getattr(pc, "tool_model", None) or getattr(pc, "cu_model", None) or ""


def _set_brain_model_in_memory(cfg: Any, provider: str, value: str) -> None:
    """Keep route-level config aligned with the live BrainManager selection.

    The manager owns a separate config object. Without this mirror update,
    section health can keep probing the previous model after the new model has
    already been persisted and applied successfully.
    """
    try:
        providers = cfg.brain.providers
        pc = providers.get(provider)
        if pc is None:
            from jarvis.core.config import BrainProviderConfig

            pc = BrainProviderConfig()
            providers[provider] = pc
        pc.model = value or None
    except Exception as exc:  # noqa: BLE001 -- detached config is best-effort
        log.debug("In-memory brain model update skipped for %s: %s", provider, exc)


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
        pc.tool_model = value
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
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    cat = catalog_spec(provider_id)
    if cat is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{provider_id}' does not expose model or voice selection.",
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
    Cartesia → its own ``[tts.cartesia].model_id``; STT → its per-provider pin in
    ``[stt.models]``, falling back to the global ``[stt] model`` (which is the
    on-device checkpoint name, so it is only ever the right answer there).
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
        stt = getattr(cfg, "stt", None)
        pins = getattr(stt, "models", None) or {}
        pinned = ""
        if hasattr(pins, "get"):
            pinned = str(pins.get(provider_id, "") or "")
        if pinned:
            return pinned
        return getattr(stt, "model", "") or ""
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
        tools=(
            ("tools" in m.supported_parameters)
            if m.supported_parameters is not None
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
    # On-device providers may only offer what is DOWNLOADED. The catalog lists
    # every voice/model the provider can use, but a picker entry whose files are
    # absent is an option that produces silence (TTS) or an error (STT) when
    # chosen — the same "offered but not there" defect as a card claiming ready
    # before it is installed. Filtered here rather than in the catalog so the
    # catalog stays the complete list a download route can work from.
    models = _installed_local_models(provider_id, result.models)
    current = _current_selection(cfg, provider_id, cat)
    # Safety net: for a curated TTS/STT list, never echo a value that isn't in the
    # list (e.g. a stale global value belonging to a different provider) — show the
    # placeholder instead. Brain keeps its value (custom model ids are allowed).
    if cat.tier != "brain" and current and current not in {m.id for m in models}:
        current = ""
    return BrainModelsResponse(
        provider=provider_id,
        current_model=current,
        models=[_brain_model_info(m) for m in models],
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
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    _set_brain_model_in_memory(cfg, provider_id, model)

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
    _invalidate_section_health_state(request)
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
                status_code=500, detail=f"TOML write failed: {exc}"
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

    _invalidate_section_health_state(request)
    return BrainModelSaveResponse(
        ok=True, provider=provider_id, model=value, persisted=persisted,
        applied_live=applied_live, restart_required=not applied_live, probe=None,
    )


def _apply_stt_model(
    provider_id: str, value: str, body: BrainModelBody, request: Request
) -> BrainModelSaveResponse:
    """Persist a STT model for ``provider_id``. Takes effect on voice restart.

    Written to ``[stt.models].<provider id>`` — the slot the STT factory reads
    (``jarvis.plugins.stt.resolve_stt_model``). The global ``[stt] model`` is
    kept in step ONLY for an on-device recognizer, because that key holds a
    faster-whisper checkpoint name and the local engine is its one consumer;
    writing a cloud model id there would leave the local path pointing at a
    checkpoint that does not exist.
    """
    on_device = False
    try:
        from jarvis.plugins.stt import provider_runs_on_device

        on_device = provider_runs_on_device(provider_id)
    except Exception as exc:  # noqa: BLE001 — an unknown provider is treated as cloud
        log.debug("STT on-device probe failed for %s (%s); treating as cloud.",
                  provider_id, exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core.config_writer import set_stt_model, set_stt_provider_model

            set_stt_provider_model(provider_id, value)
            if on_device:
                set_stt_model(value)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "stt", None) is not None:
        try:
            pins = dict(getattr(cfg.stt, "models", None) or {})  # type: ignore[attr-defined]
            if value:
                pins[provider_id] = value
            else:
                pins.pop(provider_id, None)
            cfg.stt.models = pins  # type: ignore[attr-defined]
            if on_device:
                cfg.stt.model = value  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("In-memory stt model update skipped: %s", exc)

    _invalidate_section_health_state(request)
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


class BaseUrlBody(BaseModel):
    # None or "" clears the override → back to the vendor default.
    base_url: str | None = Field(default=None, max_length=500)


class BaseUrlResponse(BaseModel):
    ok: bool = True
    provider: str
    base_url: str | None
    default_base_url: str | None


@router.put("/providers/{provider_id}/base-url")
async def set_provider_base_url(provider_id: str, body: BaseUrlBody) -> BaseUrlResponse:
    """Set (or clear) the server URL of a local/self-hosted provider.

    Only cards with ``supports_base_url`` accept one (400 otherwise). The URL
    is normalized to a bare server root (a pasted ``…/v1`` suffix is stripped,
    ``0.0.0.0`` maps to localhost) and persisted atomically to
    ``[brain.providers.<id>].base_url``; clearing falls back to the vendor
    default (ollama: OLLAMA_HOST → http://localhost:11434).
    """
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    if not spec.supports_base_url:
        raise HTTPException(
            status_code=400,
            detail=f"'{provider_id}' does not accept a server URL.",
        )
    cleaned = (body.base_url or "").strip()
    if cleaned:
        if not cleaned.lower().startswith(("http://", "https://")):
            raise HTTPException(
                status_code=422,
                detail="The server URL must start with http:// or https://.",
            )
        from jarvis.plugins.brain.ollama import normalize_server_root

        cleaned = normalize_server_root(cleaned)
    from jarvis.core import config_writer

    config_writer.set_provider_base_url(provider_id, cleaned or None)
    return BaseUrlResponse(
        provider=provider_id,
        base_url=cleaned or None,
        default_base_url=spec.default_base_url,
    )


@router.post("/providers/{provider_id}/local-install")
async def start_local_install(provider_id: str) -> dict[str, Any]:
    """Install an on-device provider's engine and download its model.

    This is the §3 "recoverable in-app" contract for the local providers: the
    engine is an optional pip package and the weights are a multi-gigabyte
    download, and neither may require a terminal. Returns immediately with a
    ``state`` the UI polls — a synchronous route would hold the request open
    for minutes — and a second call while a run is in flight joins it instead
    of starting a duplicate download.
    """
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    from jarvis.speech.local_install import start_install

    result = await asyncio.to_thread(start_install, provider_id)
    if result.get("state") == "error" and "not a local provider" in result.get(
        "message", ""
    ):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/providers/{provider_id}/local-install/status")
async def get_local_install_status(provider_id: str) -> dict[str, Any]:
    """Report install progress AND the independent on-disk readiness probe.

    The probe is the authority: a provider installed by some other route (a
    previous app run, the ``[full]`` extra, a manual pip) reads as ready even
    though this process never installed anything, and a finished install whose
    model turns out unreadable reads as an error rather than a false success.
    """
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    from jarvis.speech.local_install import install_status

    result = await asyncio.to_thread(install_status, provider_id)
    if result.get("state") == "error" and "not a local provider" in result.get(
        "message", ""
    ):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


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

    Persists the canonical ``tool_model`` and legacy ``cu_model`` keys, then
    updates both live config objects so the next mission uses it without restart.
    No live brain probe: dispatch validates the model lazily.
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

            set_brain_provider_model(
                provider_id, tool_model=value, cu_model=value
            )
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    _set_cu_model_in_memory(cfg, provider_id, value)
    live_brain = getattr(request.app.state, "brain", None)
    manager_cfg = getattr(live_brain, "_config", None)
    if manager_cfg is not None and manager_cfg is not cfg:
        _set_cu_model_in_memory(manager_cfg, provider_id, value)
    await _emit(
        request,
        SecretConfigured(key=f"brain.providers.{provider_id}.cu_model", action="set"),
    )
    effective = value or _current_brain_model(cfg, provider_id)
    _invalidate_section_health_state(request)
    return CuModelResponse(
        ok=True,
        provider=provider_id,
        cu_model=value,
        effective_model=effective,
        uses_main=not bool(value),
        persisted=persisted,
        restart_required=False,
    )


# ── GET/PUT /providers/{id}/realtime-options ───────────────────────────────
# Selectable Realtime model + voice, per realtime provider (openai-realtime /
# gemini-live). Realtime needs BOTH per provider, so this is a small dedicated
# endpoint rather than reusing GET/PUT /providers/{id}/model.


def _require_realtime_provider(provider_id: str) -> ProviderSpec:
    """404 unknown id; 400 a non-realtime-tier id (mirrors /realtime/switch)."""
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    if spec.tier != "realtime":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider_id}' is not a Realtime provider (tier={spec.tier})",
        )
    return spec


def _current_realtime_selection(cfg: Any, provider_id: str) -> tuple[str, str]:
    """The (model, voice) currently pinned for ``provider_id`` in
    ``[brain.providers.<id>]`` ("" / "" when unset)."""
    providers = getattr(getattr(cfg, "brain", None), "providers", None)
    pc = providers.get(provider_id) if isinstance(providers, dict) else None
    model = (getattr(pc, "model", None) or "") if pc is not None else ""
    voice = (getattr(pc, "voice", None) or "") if pc is not None else ""
    return model, voice


def _validate_realtime_option(
    *, provider_id: str, field: str, value: str | None, allowed: set[str]
) -> None:
    """Reject non-empty Realtime selections outside the curated catalog."""
    if value is None or value == "":
        return
    if value in allowed:
        return
    allowed_values = sorted(allowed)
    raise HTTPException(
        status_code=422,
        detail={
            "code": f"unsupported_realtime_{field}",
            "message": (
                f"Unsupported Realtime {field} '{value}' for provider "
                f"'{provider_id}'."
            ),
            "allowed_values": allowed_values,
        },
    )


@router.get("/providers/{provider_id}/realtime-options")
async def get_realtime_options(provider_id: str, request: Request) -> RealtimeOptionsResponse:
    """Return the curated model+voice catalog for a realtime provider, plus
    the currently pinned selection.

    Realtime needs BOTH a model AND a voice per provider (unlike the
    single-selection ``/models`` endpoint), so this reads the two curated
    dicts directly rather than going through ``catalog_spec``.
    """
    _require_realtime_provider(provider_id)
    from jarvis.brain.model_catalog import REALTIME_MODELS, REALTIME_VOICES

    cfg = _resolve_cfg(request)
    current_model, current_voice = _current_realtime_selection(cfg, provider_id)
    return RealtimeOptionsResponse(
        provider=provider_id,
        models=[
            RealtimeOptionInfo(id=m.id, label=m.label)
            for m in REALTIME_MODELS.get(provider_id, [])
        ],
        voices=[
            RealtimeOptionInfo(id=v.id, label=v.label)
            for v in REALTIME_VOICES.get(provider_id, [])
        ],
        current_model=current_model,
        current_voice=current_voice,
    )


@router.put("/providers/{provider_id}/realtime-options")
async def set_realtime_options(
    provider_id: str, body: RealtimeOptionsBody, request: Request
) -> RealtimeOptionsSaveResponse:
    """Pin the model and/or voice for a realtime provider.

    Persists to ``[brain.providers.<id>].model`` / ``.voice`` (+ drift-soll)  # i18n-allow: config-soll filename
    and updates the in-memory config. If this provider owns the active realtime
    call, that call is closed and reopened immediately; otherwise the selection
    applies to the next session. No process restart is required. Only fields
    present in the body are written — an omitted field leaves its current value
    untouched.
    """
    spec = _require_realtime_provider(provider_id)
    model = body.model.strip() if body.model is not None else None
    voice = body.voice.strip() if body.voice is not None else None
    from jarvis.brain.model_catalog import REALTIME_MODELS, REALTIME_VOICES

    _validate_realtime_option(
        provider_id=provider_id,
        field="model",
        value=model,
        allowed={option.id for option in REALTIME_MODELS.get(provider_id, ())},
    )
    _validate_realtime_option(
        provider_id=provider_id,
        field="voice",
        value=voice,
        allowed={option.id for option in REALTIME_VOICES.get(provider_id, ())},
    )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider '{provider_id}' has no configured credentials. "
                "Add its API key first."
            ),
        )

    if model is not None or voice is not None:
        try:
            from jarvis.core.config_writer import set_brain_provider_model

            set_brain_provider_model(provider_id, model=model, voice=voice)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "brain", None) is not None:
        try:
            providers = cfg.brain.providers
            pc = providers.get(provider_id)
            if pc is None:
                from jarvis.core.config import BrainProviderConfig

                pc = BrainProviderConfig()
                providers[provider_id] = pc
            if model is not None:
                pc.model = model
            if voice is not None:
                pc.voice = voice
        except Exception as exc:  # noqa: BLE001 — frozen/detached cfg is not an error
            log.debug(
                "In-memory realtime-options update skipped for %s: %s", provider_id, exc
            )

    await _emit(
        request,
        SecretConfigured(key=f"brain.providers.{provider_id}", action="set"),
    )
    current_model, current_voice = _current_realtime_selection(cfg, provider_id)
    selected_provider = str(
        getattr(getattr(getattr(cfg, "brain", None), "realtime", None), "provider", "")
        or ""
    )
    session_restarted = False
    if selected_provider == provider_id:
        from jarvis.ui.web.voice_runtime import reconnect_realtime

        session_restarted = reconnect_realtime(
            request, reason=f"realtime_options:{provider_id}"
        )
    _invalidate_section_health_state(request)
    return RealtimeOptionsSaveResponse(
        ok=True,
        provider=provider_id,
        model=current_model,
        voice=current_voice,
        restart_required=False,
        session_restarted=session_restarted,
    )


# ── POST /providers/{id}/realtime-voice-preview ────────────────────────────
# Speak a short sample with one of a realtime provider's voices so the user
# can HEAR a voice before pinning it — same product contract as the TTS
# voice picker's POST /tts/preview below, but per realtime provider.

# Ceiling for one preview synthesis. Generous: the OpenAI path opens a real
# realtime session (handshake + generation), Gemini may cross its sibling
# bridge on a 429 — but the route must always answer so the play button's
# spinner resolves.
_REALTIME_PREVIEW_TIMEOUT_S = 30.0

# BCP-47 pronunciation pins for the sample languages. Gemini's prebuilt
# voices are language-agnostic; an unpinned call auto-detects per word and
# can code-switch mid-sentence, so the sample pins the language it speaks.
_REALTIME_PREVIEW_LANG_CODES = {"de": "de-DE", "en": "en-US", "es": "es-ES"}


async def _gemini_live_voice_sample(
    api_key: str, *, model: str, voice: str, text: str, language: str
) -> tuple[bytes, int]:
    """Sample a Gemini Live voice via the Gemini TTS family.

    The Live API serves the same 30 prebuilt voices (Puck, Charon, Kore, …)
    as the Gemini TTS models, so a cheap ``generate_content`` call renders the
    identical voice without opening a duplex Live session. ``model`` (the
    pinned LIVE model) deliberately plays no role here — voices, not models,
    are what the sample demonstrates.
    """
    del model
    from jarvis.plugins.tts.gemini_flash_tts import GEMINI_TTS_SAMPLE_RATE, GeminiFlashTTS

    tts = GeminiFlashTTS(
        api_key=api_key,
        # One generation = one coherent voice take; no OS fallback voice may
        # ever impersonate the sampled voice.
        chunk_by_sentence=False,
        allow_sapi5_fallback=False,
    )
    pcm = bytearray()
    sample_rate = GEMINI_TTS_SAMPLE_RATE
    async for chunk in tts.synthesize(
        text, voice=voice, language_code=_REALTIME_PREVIEW_LANG_CODES.get(language)
    ):
        pcm += bytes(chunk.pcm)
        sample_rate = chunk.sample_rate
    return bytes(pcm), sample_rate


async def _openai_realtime_voice_sample(
    api_key: str, *, model: str, voice: str, text: str, language: str
) -> tuple[bytes, int]:
    """Sample an OpenAI Realtime voice via a one-shot realtime session.

    Marin and Cedar exist ONLY in the Realtime API (no ``/v1/audio/speech``
    counterpart), so the sample opens the same duplex channel a live call
    uses — which also makes it exactly what a call will sound like. No
    microphone audio is sent; the session closes after one spoken response.
    """
    del language  # the sample text itself carries the language
    import base64

    from openai import AsyncOpenAI  # lazy (AP-26)

    output_rate = 24_000
    client = AsyncOpenAI(api_key=api_key)
    pcm = bytearray()
    try:
        async with client.realtime.connect(model=model or "gpt-realtime") as conn:
            # Mirrors the GA session shape of the live adapter
            # (jarvis/plugins/realtime/openai_realtime.py::_session_payload),
            # minus transcription/tools: manual-response mode so the server
            # never speaks on its own.
            await conn.session.update(
                session={
                    "type": "realtime",
                    "instructions": (
                        "You generate voice samples. When asked to speak, say "
                        "the requested text verbatim and nothing else."
                    ),
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": output_rate},
                            "turn_detection": {
                                "type": "server_vad",
                                "create_response": False,
                                "interrupt_response": False,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": output_rate},
                            "voice": voice,
                        },
                    },
                }
            )
            # Request the response only after the server confirmed the session
            # (voice included) — a response generated before the update lands
            # would speak the DEFAULT voice, silently mislabeling the sample.
            response_requested = False
            async for event in conn:
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "session.updated" and not response_requested:
                    response_requested = True
                    await conn.response.create(
                        response={
                            "instructions": (
                                "Say exactly the following, in a natural tone, "
                                f"and nothing else: {text}"
                            )
                        }
                    )
                elif event_type == "response.output_audio.delta":
                    pcm += base64.b64decode(getattr(event, "delta", "") or "")
                elif event_type == "response.done":
                    response = getattr(event, "response", None)
                    status = str(getattr(response, "status", "") or "")
                    if status and status != "completed" and not pcm:
                        details = getattr(response, "status_details", None)
                        raise RuntimeError(
                            f"the provider ended the sample as '{status}'"
                            + (f" ({details})" if details else "")
                        )
                    break
                elif event_type == "error":
                    error = getattr(event, "error", None)
                    message = str(getattr(error, "message", "") or error or "unknown error")
                    raise RuntimeError(message)
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
    return bytes(pcm), output_rate


# Keyed like REALTIME_MODELS/REALTIME_VOICES: adding a realtime provider means
# adding its catalog entries AND its sampler here (the parity test guards it).
_REALTIME_PREVIEW_SAMPLERS: dict[str, Any] = {
    "gemini-live": _gemini_live_voice_sample,
    "openai-realtime": _openai_realtime_voice_sample,
}


class RealtimeVoicePreviewBody(BaseModel):
    voice: str = Field(default="", max_length=200)
    # The sample language to speak ("de" | "en" | "es"). Falls back to English.
    language: str = Field(default="en", max_length=16)
    # The realtime model to sample through where the sampler needs one
    # (openai-realtime); "" = the adapter default. Validated when non-empty.
    model: str = Field(default="", max_length=200)


@router.post("/providers/{provider_id}/realtime-voice-preview")
async def realtime_voice_preview(
    provider_id: str, body: RealtimeVoicePreviewBody
) -> Response:
    """Speak a SHORT sample with one of a realtime provider's voices.

    Returns ``audio/wav`` (24 kHz mono s16le in a WAV container) so the voice
    picker can play it directly — the same response contract as
    ``POST /tts/preview``. The sample is generated with the credential stored
    for THIS provider. Any failure (no key / quota / transport) is a clean
    4xx/5xx JSON error the picker can toast — never a page-breaking 500.
    """
    _require_realtime_provider(provider_id)
    from jarvis.brain.model_catalog import REALTIME_MODELS, REALTIME_VOICES

    voice = body.voice.strip()
    if not voice:
        raise HTTPException(status_code=400, detail="A voice id is required.")
    _validate_realtime_option(
        provider_id=provider_id,
        field="voice",
        value=voice,
        allowed={option.id for option in REALTIME_VOICES.get(provider_id, ())},
    )
    model = body.model.strip()
    _validate_realtime_option(
        provider_id=provider_id,
        field="model",
        value=model,
        allowed={option.id for option in REALTIME_MODELS.get(provider_id, ())},
    )
    sampler = _REALTIME_PREVIEW_SAMPLERS.get(provider_id)
    if sampler is None:
        raise HTTPException(
            status_code=400,
            detail=f"Voice preview is not available for provider '{provider_id}'.",
        )

    api_key = cfg_mod.get_provider_secret(provider_id)
    if not api_key:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider '{provider_id}' has no configured credentials. "
                "Add its API key first."
            ),
        )

    lang = (body.language or _TTS_PREVIEW_DEFAULT_LANG).lower().split("-", 1)[0]
    sample = _TTS_PREVIEW_SAMPLES.get(lang, _TTS_PREVIEW_SAMPLES[_TTS_PREVIEW_DEFAULT_LANG])

    try:
        pcm, sample_rate = await asyncio.wait_for(
            sampler(api_key, model=model, voice=voice, text=sample, language=lang),
            timeout=_REALTIME_PREVIEW_TIMEOUT_S,
        )
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Voice preview timed out after {_REALTIME_PREVIEW_TIMEOUT_S:.0f}s — "
                "the provider did not answer."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        raise HTTPException(
            status_code=502, detail=f"Voice preview failed: {exc}"
        ) from exc

    if not pcm:
        raise HTTPException(
            status_code=502,
            detail=(
                "Voice preview produced no audio — check the provider's API key "
                "and quota."
            ),
        )
    wav = _pcm_to_wav(pcm, sample_rate=sample_rate, channels=1)
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/codex/status")
async def codex_status(request: Request) -> dict[str, Any]:
    """Honest snapshot of the Codex CLI login.

    Off the event loop: the probe SPAWNS the CLI binary, which costs a few
    hundred milliseconds. On the loop that pause froze everything else running
    in this process — including the realtime voice socket, where it surfaced as
    an audible hole mid-sentence (forensic 2026-07-27, see
    ``jarvis.audio.player.DEFAULT_OUTPUT_BUFFER_S``).
    """
    service = CodexAuthService(_codex_binary_path(request))
    status = await asyncio.to_thread(service.status)
    return status.to_dict()


@router.post("/codex/test")
async def codex_test(request: Request) -> dict[str, Any]:
    """Live CLI test: cache-busting binary + version + login probe.

    Re-augments PATH first, so a CLI installed after app start is found without
    a restart. Runs off the event loop — the probe spawns the real binary.
    """
    import asyncio

    from jarvis.agent_cli_probe import test_codex

    binary_path = _codex_binary_path(request)
    return (await asyncio.to_thread(test_codex, binary_path)).to_dict()


@router.post("/codex/binary-path")
async def codex_set_binary_path(body: CodexBinaryPathBody, request: Request) -> dict[str, Any]:
    value = body.binary_path.strip()
    try:
        from jarvis.core.config_writer import set_codex_binary_path

        set_codex_binary_path(value)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"TOML write failed: {exc}") from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "codex", None) is not None:
        try:
            cfg.codex.binary_path = value  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("In-memory Codex path update skipped: %s", exc)
    return {"ok": True, "binary_path": value}


@router.post("/codex/login")
async def codex_login(request: Request) -> dict[str, Any]:
    service = CodexAuthService(_codex_binary_path(request))
    status = await asyncio.to_thread(service.status)
    if not status.installed:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Codex CLI is not installed",
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
            detail=f"codex login could not be started: {type(exc).__name__}: {exc}",
        ) from exc
    return {"ok": True, "pid": proc.pid, "message": "codex login was started in a terminal"}


@router.post("/codex/logout")
async def codex_logout(request: Request) -> dict[str, Any]:
    service = CodexAuthService(_codex_binary_path(request))
    status = await asyncio.to_thread(service.status)
    if not status.installed:
        raise HTTPException(status_code=409, detail="Codex CLI is not installed")
    ok, error = await asyncio.to_thread(service.logout_blocking)
    if not ok:
        raise HTTPException(status_code=500, detail=error or "Codex logout failed")
    return {"ok": True, "message": "Codex was disconnected"}


# M6: STT/TTS engines build ONCE at voice-pipeline bootstrap, so a key feeding them
# is unused until the next voice start. Surface restart_required so the UI shows the
# "active from next voice start" hint instead of implying the new key is live now.
# (Brain provider keys hot-reload, so they are deliberately NOT listed here.)
_RESTART_REQUIRED_SECRET_KEYS: frozenset[str] = frozenset({
    "groq_api_key", "deepgram_api_key",        # STT
    "cartesia_api_key", "elevenlabs_api_key",  # TTS
})


@router.post("/secrets/{key}", openapi_extra={"x-jarvis-dangerous": True})
async def set_secret_value(key: str, body: SecretBody, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown secret key: {key}")
    if not cfg_mod.set_secret(key, body.value):
        raise HTTPException(status_code=500, detail="Keyring write failed")
    await _emit(request, SecretConfigured(key=key, action="set"))
    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "key": key,
        "restart_required": key in _RESTART_REQUIRED_SECRET_KEYS,
    }


@router.delete("/secrets/{key}")
async def delete_secret_value(key: str, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown secret key: {key}")
    if not cfg_mod.delete_secret(key):
        raise HTTPException(
            status_code=500,
            detail=(
                "Credential deletion could not be verified. The platform "
                "credential store may be locked or unavailable."
            ),
        )
    await _emit(request, SecretConfigured(key=key, action="delete"))
    _invalidate_section_health_state(request)
    return {"ok": True, "key": key}


# Maps apply_provider_switch error kinds to HTTP statuses. Route-level concern:
# the shared switch logic in jarvis.brain.app_control stays transport-agnostic.
_SWITCH_ERROR_STATUS: dict[str, int] = {
    "unknown_tier": 400,
    "unknown_provider": 404,
    "wrong_tier": 400,
    "subagent_only": 409,
    "missing_credential": 409,
    # Same shape as a missing credential, for the on-device providers: the
    # engine or its model is not on this machine yet.
    "not_installed": 409,
    "subagent_unavailable": 409,
    "airgapped_locked": 403,
    "persist_failed": 500,
    "switch_failed": 500,
    "switch_not_applied": 500,
}


@router.post("/brain/switch")
async def brain_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Switch the active main-brain provider.

    Validation and the switch itself are delegated to the ONE shared
    implementation (``app_control.apply_provider_switch``) that the voice
    gate and the brain tools also use — the route only keeps checks that are
    genuinely transport-specific (503 while the brain is still building, the
    live plugin-registry 404, and the defensive Codex/Antigravity branches).
    Previously the route carried its own parallel validation, which could
    drift from the voice path's.
    """
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

    # Fast, specific 404 while the live registry is at hand — the shared logic
    # would only surface an unloadable provider later as switch_not_applied.
    available = []
    if hasattr(brain, "available_providers"):
        try:
            available = list(brain.available_providers())
        except Exception:  # noqa: BLE001
            available = []
    if available and body.provider not in available:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{body.provider}' is not available in the plugin registry",
        )

    # Defensive legacy branches: Codex/Antigravity are ``brain_switchable=False``
    # and rejected by the shared validation below (as "subagent-only", which
    # must stay the primary message). These credential checks are DORMANT until
    # that guard is ever relaxed — then they keep a switch from succeeding
    # nominally and failing on the first turn. Hence the brain_switchable gate.
    spec = get_spec(body.provider)
    if spec is not None and not getattr(spec, "brain_switchable", True):
        pass  # shared validation rejects with the canonical subagent-only 409
    elif spec is not None and spec.id == "codex":
        if not _codex_brain_usable():
            raise HTTPException(
                status_code=409,
                detail=(
                    "Codex can't be a brain yet — add an OpenAI API key (fast) or "
                    "run 'codex login' (ChatGPT subscription, slower CLI path)."
                ),
            )
    elif spec is not None and spec.id == "antigravity":
        # OAuth-only: no API key. Gate on the Google CLI login being present,
        # mirroring the codex branch (the CLI bills the Google subscription).
        from jarvis.google_cli.auth_service import GoogleCliAuthService

        def _antigravity_connected() -> bool:
            status = GoogleCliAuthService().status()
            return status.connected and status.mode == "oauth-personal"

        if not await asyncio.to_thread(_antigravity_connected):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Antigravity isn't connected — sign in with Google (install "
                    "agy or the Gemini CLI and log in), then activate."
                ),
            )

    from jarvis.brain.app_control import apply_provider_switch

    result = await apply_provider_switch(
        "brain",
        body.provider,
        cfg=_resolve_cfg(request),
        persist=body.persist,
        manager=brain,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=_SWITCH_ERROR_STATUS.get(str(result.get("error_kind")), 500),
            detail=result.get("error") or "Switch failed.",
        )
    if body.persist and not result.get("persisted"):
        log.warning(
            "Brain switch to '%s' applied live but persistence to disk FAILED — "
            "the choice will not survive a restart.",
            body.provider,
        )
    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "active": result.get("new_provider", body.provider),
        "persisted": bool(result.get("persisted")),
        "old_provider": result.get("old_provider"),
        "requires_restart": bool(result.get("requires_restart")),
    }


@router.post("/tts/switch")
async def tts_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Switch the active TTS provider without restarting the pipeline.

    The route persists the selection, updates the live configuration, and
    injects a newly built provider into an active SpeechPipeline. If no
    pipeline is running (headless or voice disabled), the persisted selection
    applies on the next start and ``restart_required`` remains true.
    """
    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown provider: {body.provider}"
        )
    if spec.tier != "tts":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' is not a TTS provider (tier={spec.tier})",
        )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider '{body.provider}' has no configured credentials. "
                "Add its API key first."
            ),
        )
    # Same reasoning as the STT switch: a local provider has no credential to
    # check, so readiness must be asked of the disk. Activating a voice whose
    # files are missing would turn every spoken reply into silence.
    from jarvis.brain.app_control import local_readiness_error

    not_installed = local_readiness_error(spec)
    if not_installed:
        raise HTTPException(status_code=409, detail=not_installed)

    if body.persist:
        try:
            from jarvis.core.config_writer import set_tts_provider

            set_tts_provider(body.provider)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    # Best-effort live update lets subscribers observe the value immediately
    # when the app-state Pydantic model is mutable.
    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "tts", None) is not None:
        try:
            cfg.tts.provider = body.provider  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
            log.debug("In-memory TTS provider update skipped: %s", exc)

    # Inject into the active SpeechPipeline. Headless or voice-disabled boots
    # retain the honest ``restart_required=true`` response.
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
                "TTS live switch applied to the active SpeechPipeline: provider=%s",
                body.provider,
            )
        except Exception as exc:  # noqa: BLE001
            # Persistence succeeded, so restart remains an honest recovery.
            # Keep the root cause and stack in the log for diagnosis.
            log.error(
                "TTS live switch failed; restart required: %s: %s",
                type(exc).__name__, exc, exc_info=True,
            )
            restart_required = True

    await _emit(request, SecretConfigured(key="tts.provider", action="set"))

    _invalidate_section_health_state(request)
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
    # The German sentence is deliberately NOT a literal translation of the
    # English one: Gemini's AI-Studio TTS safety filter deterministically
    # blocked the former mirror-translation ("So klingt meine Stimme, wenn
    # ich für dich spreche und dir zuhöre") as PROHIBITED_CONTENT — 0/2 runs  # i18n-allow: forensic quote of the blocked German sample
    # passed, voice-independent, while EN/ES passed (probe 2026-07-17). This
    # wording passed 4/4 runs across three voices.
    "de": (
        "Guten Tag! Ich bin dein "  # i18n-allow: German TTS preview output.
        "persönlicher Assistent. "  # i18n-allow
        "So klingt meine Stimme. "  # i18n-allow
        "Ich freue mich, dir bei deinen Aufgaben zu helfen."  # i18n-allow
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
    ``_apply_tts_selection`` path (config-soll-synced write + a live rebuild of  # i18n-allow: config-soll filename
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
    """Persist the active STT provider in ``jarvis.toml``.

    The SpeechPipeline constructs STT once because loading a Whisper model is
    expensive. The selection therefore applies after the next voice or app
    restart and the response reports ``restart_required: true``.
    """
    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown provider: {body.provider}"
        )
    if spec.tier != "stt":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' is not an STT provider (tier={spec.tier})",
        )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider '{body.provider}' has no configured credentials. "
                "Add its API key first."
            ),
        )
    # A local provider passes the credential check trivially (it has no key), so
    # readiness has to be asked separately — activating an engine that is not
    # installed would leave voice input dead with no explanation. This is the
    # gate the 2026-07-03 card lacked. Shared with the voice/CLI/tool switch
    # path via ``local_readiness_error`` so the two can never disagree.
    from jarvis.brain.app_control import local_readiness_error

    not_installed = local_readiness_error(spec)
    if not_installed:
        raise HTTPException(status_code=409, detail=not_installed)

    if body.persist:
        try:
            from jarvis.core.config_writer import set_stt_provider

            set_stt_provider(body.provider)
            # Pin the checkpoint the card promised and the download fetched.
            # Without this, activating the local card would inherit whatever
            # [stt].model happened to hold (its default is a DIFFERENT Whisper
            # size), so the user would run a model they never chose — and one
            # that may not be downloaded at all.
            from jarvis.speech.local_models import get_local_provider

            local_entry = get_local_provider(body.provider)
            if local_entry is not None and local_entry.runtime == "faster-whisper":
                from jarvis.core.config_writer import set_stt_model

                set_stt_model(local_entry.model_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "stt", None) is not None:
        try:
            cfg.stt.provider = body.provider  # type: ignore[attr-defined]
            # Same pin in memory, so a live restart of the speech pipeline picks
            # up the checkpoint without waiting for a config re-read.
            from jarvis.speech.local_models import get_local_provider

            entry = get_local_provider(body.provider)
            if entry is not None and entry.runtime == "faster-whisper":
                cfg.stt.model = entry.model_id  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
            log.debug("In-memory STT provider update skipped: %s", exc)

    await _emit(request, SecretConfigured(key="stt.provider", action="set"))

    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "active": body.provider,
        "persisted": body.persist,
        "restart_required": True,
    }


@router.post("/realtime/switch")
async def realtime_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Switch the active realtime provider.

    The desktop and browser runtimes resolve ``voice.mode`` plus the selected
    provider whenever a voice session opens. An active desktop call is closed
    and reopened against the new selection; no application restart is required.
    Realtime is cross-family (AP-22), so validation is registry/tier based.

    Activating a realtime provider also makes Realtime the ACTIVE voice mode
    (``[voice].mode``) — the "Active" badge reads ``[voice].mode``, not
    ``[brain.realtime].provider``, so without this the badge could never
    follow an activation (Feature A4).
    """
    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown provider: {body.provider}"
        )
    if spec.tier != "realtime":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{body.provider}' is not a realtime provider "
                f"(tier={spec.tier})"
            ),
        )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider '{body.provider}' has no configured credentials. "
                "Add its API key first."
            ),
        )

    voice_mode_write_ok = True
    if body.persist:
        try:
            from jarvis.core.config_writer import set_realtime_provider

            set_realtime_provider(body.provider)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc
        try:
            from jarvis.core.config_writer import set_voice_mode

            set_voice_mode("realtime")
        except Exception as exc:  # noqa: BLE001 — best-effort, mirrors set_realtime_provider above
            voice_mode_write_ok = False
            log.warning("voice-mode persist failed after realtime switch: %s", exc)

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "brain", None) is not None:
        try:
            realtime_cfg = getattr(cfg.brain, "realtime", None)
            if realtime_cfg is None:
                from jarvis.core.config import BrainTierConfig

                cfg.brain.realtime = BrainTierConfig(provider=body.provider)
            else:
                realtime_cfg.provider = body.provider
        except Exception as exc:  # noqa: BLE001 — frozen/detached cfg is not an error
            log.debug("In-memory realtime.provider update skipped: %s", exc)
    if cfg is not None and getattr(cfg, "voice", None) is not None:
        try:
            cfg.voice.mode = "realtime"  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen/detached cfg is not an error
            log.debug("In-memory voice.mode update skipped: %s", exc)

    await _emit(request, SecretConfigured(key="brain.realtime.provider", action="set"))
    await _emit(request, SecretConfigured(key="voice.mode", action="set"))

    from jarvis.ui.web.voice_runtime import reconnect_realtime

    session_restarted = reconnect_realtime(
        request, reason=f"realtime_provider:{body.provider}"
    )

    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "active": body.provider,
        # True only when persist was requested AND both writes (provider +
        # voice mode) actually landed — previously this reported
        # body.persist unconditionally even when the voice-mode write above
        # failed and was only logged, so the UI showed "persisted" for a
        # switch that silently left [voice].mode stale on disk.
        "persisted": body.persist and voice_mode_write_ok,
        "voice_mode_persisted": voice_mode_write_ok,
        "restart_required": False,
        "session_restarted": session_restarted,
    }


@router.post("/computer-use/switch")
async def computer_use_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Switches the dedicated GLOBAL Computer-Use planner provider.

    Overlay over the brain-tier provider cards (Claude/OpenAI/OpenRouter/
    Gemini), not a new provider tier — mirrors how ``[brain.worker]`` is a
    separate selection over the same brain ids
    (see ``jarvis_agent_switch``), but simpler: the CU planner can only be a
    brain-switchable brain-tier provider (it must be able to receive
    screenshots), so this reuses the generic ``/api/brain/switch``
    validation style instead of the worker route's Codex-specific branch.

    Persists via ``config_writer.set_computer_use_provider`` (3-layer,
    drift-guarded — a TOML-only write would be reverted within minutes) and
    updates ``cfg.brain.computer_use`` in-memory so the very next
    Computer-Use dispatch (``BrainManager._cu_provider`` ->
    ``jarvis.cu.brain_call.call_vision_brain``'s hoist) uses it live — no
    restart required, unlike the worker/realtime switches.
    """
    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown provider: {body.provider}"
        )
    if spec.tier != "brain" or not spec.brain_switchable:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{body.provider}' cannot be the Computer-Use planner "
                f"— it must be a brain-switchable provider that can receive "
                f"screenshots (tier={spec.tier})."
            ),
        )
    if not _is_credential_present(spec):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{spec.label} has no saved API key. Save a key first, "
                "then activate."
            ),
        )

    if body.persist:
        try:
            from jarvis.core.config_writer import set_computer_use_provider

            set_computer_use_provider(body.provider)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    cfg = _resolve_cfg(request)
    if cfg is not None and getattr(cfg, "brain", None) is not None:
        try:
            tier_cfg = getattr(cfg.brain, "tool_model", None) or getattr(
                cfg.brain, "computer_use", None
            )
            if tier_cfg is None:
                from jarvis.core.config import BrainTierConfig

                tier_cfg = BrainTierConfig(provider=body.provider)
            else:
                tier_cfg.provider = body.provider
            cfg.brain.tool_model = tier_cfg
            cfg.brain.computer_use = tier_cfg
        except Exception as exc:  # noqa: BLE001 — frozen/detached cfg is not an error
            log.debug("In-memory computer_use.provider update skipped: %s", exc)

    live_brain = getattr(request.app.state, "brain", None)
    manager_cfg = getattr(live_brain, "_config", None)
    if manager_cfg is not None and manager_cfg is not cfg:
        try:
            from jarvis.core.config import BrainTierConfig

            manager_tier = BrainTierConfig(provider=body.provider)
            manager_cfg.brain.tool_model = manager_tier
            manager_cfg.brain.computer_use = manager_tier
        except Exception as exc:  # noqa: BLE001
            log.debug("Live Tool Model provider update skipped: %s", exc)
    if hasattr(live_brain, "reactivate_provider"):
        live_brain.reactivate_provider(body.provider)

    await _emit(
        request, SecretConfigured(key="brain.computer_use.provider", action="set")
    )

    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "active": body.provider,
        "persisted": body.persist,
        "restart_required": False,
    }


@router.post("/jarvis-agent/switch")
async def jarvis_agent_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
    """Switch the active Jarvis-Agent provider.

    Validation is delegated to ``app_control.apply_provider_switch``, the same
    source used by voice and connected tools. This prevents an auth capability
    recognized by the worker (for example a platform-native Claude login) from
    being rejected by a stale route-local credential check. The mission factory
    re-resolves the persisted provider before every new mission, so a successful
    switch needs no app restart.
    """
    from jarvis.brain.app_control import apply_provider_switch

    cfg = _resolve_cfg(request)
    if cfg is None:
        raise HTTPException(status_code=503, detail="Configuration is not ready.")
    result = await apply_provider_switch(
        "subagent",
        body.provider,
        cfg=cfg,
        persist=body.persist,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=_SWITCH_ERROR_STATUS.get(
                str(result.get("error_kind")), 500
            ),
            detail=result.get("error") or "Agent provider switch failed.",
        )

    await _emit(request, SecretConfigured(key="brain.worker.provider", action="set"))

    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "active": result.get("new_provider", body.provider),
        "persisted": bool(result.get("persisted")),
        "old_provider": result.get("old_provider"),
        "restart_required": bool(result.get("requires_restart")),
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

    _invalidate_section_health_state(request)
    return {
        "ok": True,
        "model": model,
        "persisted": persisted,
        "restart_required": True,
    }

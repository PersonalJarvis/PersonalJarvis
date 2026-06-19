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

import logging
from typing import Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.brain import provider_test as _provider_test
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

from .provider_spec import PROVIDERS, ProviderSpec, get_spec

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["providers"])


# Whitelist aller Keys die gesetzt/gelöscht werden dürfen — exakt die im
# Setup-Wizard deklarierten Slots.
ALLOWED_SECRET_KEYS: frozenset[str] = frozenset(s.key for s in WIZARD_SECRETS)

# Lokale Provider, die im Privacy-Mode erlaubt bleiben.
# Ollama wurde 2026-04-21 entfernt — nur STT (faster-whisper) ist aktuell lokal.
LOCAL_PROVIDERS: frozenset[str] = frozenset({"faster-whisper"})

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
    """True iff an OpenAI API key usable by the Codex *brain* is configured.

    Mirrors ``CodexBrain._ensure_client``: the dedicated codex slot, the general
    OpenAI provider key, or the ``OPENAI_API_KEY`` env fallback. The ChatGPT
    OAuth login is intentionally NOT counted — it powers the codex *subagent*
    (the CLI), not a chat-completions brain. Used to gate Codex-as-brain
    activation so the switch never "succeeds" on OAuth and then fails on the
    first turn.
    """
    return bool(
        cfg_mod.get_secret("codex_openai_api_key")
        or cfg_mod.get_secret("openai_api_key", "OPENAI_API_KEY")
    )


def _codex_brain_usable() -> bool:
    """True iff Codex can serve as a brain: an OpenAI API key (the fast chat-API
    path) OR a ChatGPT login (the slow ``codex exec`` CLI path).

    The CLI path is genuinely slow (~15-20 s per turn) and burns subscription
    tokens, but it IS a working brain (CodexBrain drives ``codex exec`` over the
    OAuth token). So the brain toggle unlocks on OAuth too, not only on a key.
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
        "configured": _is_credential_present(
            spec,
            _codex_binary_path() if spec.id == "codex" else None,
        ),
        "active": active,
        "cli_installed": _cli_installed(spec),
    }
    if codex_status is not None:
        payload["codex_status"] = codex_status
        # Codex IS a selectable brain. It works with an OpenAI API key (fast)
        # OR the ChatGPT login (slow codex-exec CLI path). The UI gates the brain
        # "activate" radio on this — unlocked for either credential.
        payload["codex_brain_ready"] = _codex_brain_usable()
    return payload


def _active_brain(request: Request) -> str | None:
    brain = getattr(request.app.state, "brain", None)
    if brain is None:
        return None
    return getattr(brain, "active_provider", None) or getattr(brain, "name", None)


def _active_tts(request: Request) -> str | None:
    cfg = _resolve_cfg(request)
    return getattr(getattr(cfg, "tts", None), "provider", None) if cfg else None


def _active_stt(request: Request) -> str | None:
    cfg = _resolve_cfg(request)
    return getattr(getattr(cfg, "stt", None), "provider", None) if cfg else None


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


def _apply_sub_jarvis_in_memory(request: Request, provider: str) -> None:
    """Best-effort in-memory update of ``cfg.brain.sub_jarvis.provider``.

    So the next ``/openclaw/status`` reflects the choice immediately (the worker
    itself only re-reads at restart). Frozen / detached cfg is not an error.
    """
    cfg = _resolve_cfg(request)
    if cfg is None or getattr(cfg, "brain", None) is None:
        return
    sub = getattr(cfg.brain, "sub_jarvis", None)
    try:
        if sub is None:
            from jarvis.core.config import BrainTierConfig

            cfg.brain.sub_jarvis = BrainTierConfig(provider=provider)
        else:
            sub.provider = provider
    except Exception as exc:  # noqa: BLE001 — frozen models / detached cfg are not errors
        log.debug("In-memory sub_jarvis.provider update skipped: %s", exc)


def _apply_sub_jarvis_model_in_memory(request: Request, model: str) -> None:
    """Best-effort in-memory update of ``cfg.brain.sub_jarvis.model``.

    Mirrors :func:`_apply_sub_jarvis_in_memory`; a missing ``sub_jarvis``
    block is created with the router primary as provider so the override is
    never silently dropped.
    """
    cfg = _resolve_cfg(request)
    if cfg is None or getattr(cfg, "brain", None) is None:
        return
    sub = getattr(cfg.brain, "sub_jarvis", None)
    try:
        if sub is None:
            from jarvis.core.config import BrainTierConfig

            cfg.brain.sub_jarvis = BrainTierConfig(
                provider=getattr(cfg.brain, "primary", "") or "", model=model,
            )
        else:
            sub.model = model
    except Exception as exc:  # noqa: BLE001 — frozen models / detached cfg are not errors
        log.debug("In-memory sub_jarvis.model update skipped: %s", exc)


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


@router.post("/secrets/{key}")
async def set_secret_value(key: str, body: SecretBody, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unbekannter Secret-Key: {key}")
    if not cfg_mod.set_secret(key, body.value):
        raise HTTPException(status_code=500, detail="Keyring-Write fehlgeschlagen")
    await _emit(request, SecretConfigured(key=key, action="set"))
    return {"ok": True, "key": key}


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
        raise HTTPException(
            status_code=503,
            detail="Brain-Manager nicht verfügbar (vermutlich Headless-Mode)",
        )

    spec = get_spec(body.provider)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unbekannter Provider: {body.provider}")

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
    # Codex-as-BRAIN is special: a chat-completions brain needs an OpenAI API
    # key. The ChatGPT subscription (OAuth) cannot back a chat endpoint — it
    # only powers the Codex *subagent*. Accept ANY OpenAI key CodexBrain can use
    # (codex_openai_api_key / openai_api_key / OPENAI_API_KEY), not just the
    # dedicated codex slot — so activation matches what the brain actually reads,
    # and never "succeeds" on OAuth alone and then fails on the first turn.
    if spec.id == "codex":
        if not _codex_brain_usable():
            raise HTTPException(
                status_code=409,
                detail=(
                    "Codex can't be a brain yet — add an OpenAI API key (fast) or "
                    "run 'codex login' (ChatGPT subscription, slower CLI path)."
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


@router.post("/subagent/switch")
async def subagent_switch(body: SwitchBody, request: Request) -> dict[str, Any]:
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
        JARVIS_TO_OPENCLAW,
        canonical_subagent_provider,
    )

    # Normalize (lower/strip + ``openclaw-claude`` -> ``claude-api``) so the
    # accepted set matches what the UI cards display.
    provider = canonical_subagent_provider(body.provider) or ""

    # Codex is a DIRECT worker (CodexDirectWorker) with no OpenClaw slug — it is
    # not in JARVIS_TO_OPENCLAW. Handle it explicitly: it can be backed by the
    # ChatGPT subscription (OAuth, ``codex login``) OR an OpenAI API key.
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
                from jarvis.core.config_writer import set_sub_jarvis_provider

                set_sub_jarvis_provider(_CODEX_SUBAGENT_CANONICAL)
                persisted = True
            except FileNotFoundError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
                ) from exc
        _apply_sub_jarvis_in_memory(request, _CODEX_SUBAGENT_CANONICAL)
        await _emit(request, SecretConfigured(key="brain.sub_jarvis.provider", action="set"))
        return {
            "ok": True,
            "active": _CODEX_SUBAGENT_CANONICAL,
            "persisted": persisted,
            "restart_required": True,
        }

    if provider not in JARVIS_TO_OPENCLAW:
        known = ", ".join(sorted(JARVIS_TO_OPENCLAW))
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{body.provider}' ist kein Subagent-faehiger Provider. "
                f"Verfuegbar: {known}."
            ),
        )

    # Key-Check — a provider without a stored credential cannot be activated.
    # ``claude-api`` counts the OAuth bearer as present (Claude Max). Reads the
    # same secret store the status endpoint uses for the per-card ``key_set``.
    if not cfg_mod.get_provider_secret(provider):
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
            from jarvis.core.config_writer import set_sub_jarvis_provider

            set_sub_jarvis_provider(provider)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML-Write fehlgeschlagen: {exc}"
            ) from exc

    # Best-effort in-memory update so the next /openclaw/status reflects the
    # choice immediately (the worker itself only re-reads on restart).
    _apply_sub_jarvis_in_memory(request, provider)

    await _emit(request, SecretConfigured(key="brain.sub_jarvis.provider", action="set"))

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


@router.post("/subagent/model")
async def subagent_model(body: SubagentModelBody, request: Request) -> dict[str, Any]:
    """Pin which MODEL the heavy-task sub-agents run (``[brain.sub_jarvis].model``).

    The dedicated subagent LLM, separate from the router brain: the worker
    chain reads it per spawn (``provider_chain._resolve_provider_chain``) and
    ``/openclaw/status`` displays it as ``sub_model_override`` /
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
            from jarvis.core.config_writer import set_sub_jarvis_model

            set_sub_jarvis_model(model)
            persisted = True
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"TOML write failed: {exc}"
            ) from exc

    # Best-effort in-memory update so the next /openclaw/status reflects the
    # choice immediately (workers resolve their chain per spawn from config).
    _apply_sub_jarvis_model_in_memory(request, model)

    await _emit(request, SecretConfigured(key="brain.sub_jarvis.model", action="set"))

    return {
        "ok": True,
        "model": model,
        "persisted": persisted,
        "restart_required": True,
    }


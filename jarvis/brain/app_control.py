"""App-Control service — shared logic behind the brain's App-Control tools.

This module is the single source of truth for three things the brain needs to
both *see* and *change* about the running Desktop App:

1. ``is_credential_present`` — does a provider have a usable credential? (the
   same check ``provider_routes`` uses for the UI cards; imported back there so
   the two never drift — the BUG-008 multi-site-vocab class).
2. ``build_settings_snapshot`` — a complete, read-only, secret-free picture of
   the current configuration (providers, key settings, MCP servers) for the
   ``describe-app-settings`` tool.
3. ``apply_provider_switch`` — switch the active brain/tts/stt/subagent provider,
   reusing the exact 3-layer persist + live-apply path the REST endpoints use,
   via the live runtime references in :mod:`jarvis.core.runtime_refs`.

Security boundary (binding): nothing here ever accepts a raw secret *value* by
voice/chat, and nothing ever returns or logs a *full* secret. Provider switching
only flips *which* provider is active; the target provider's key must already
exist in the Credential Manager. Raw key writes stay UI-only
(``/api/secrets/{key}``) per AP-2 (STT log leak = credential exfil) and the
self-mod ``FORBIDDEN_PATTERNS`` doctrine.

ONE sanctioned read of a secret value lives here: ``masked_secret_preview``
returns ONLY the first 3 + last 3 characters (e.g. ``AIz...xQ2``), never the
middle, never the full value, and never logs the value. This is an explicit
user mandate (2026-05-31): the assistant may *speak* a masked preview when asked
"what is my X key", but must refuse to speak the full key in any language. The
mask leaves 30+ characters hidden on a real API key, so the preview alone is
unusable for an attacker — the GitHub/Stripe "last 4" pattern.

Layering note: this is a brain-layer service. It imports the pure-data provider
catalog (``provider_spec``) and the low-layer config writer / mcp state. The UI
layer (``provider_routes``) imports *down* into this module — never the reverse.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jarvis.core import config as cfg_mod
from jarvis.core import runtime_refs

if TYPE_CHECKING:  # annotations only — `from __future__ import annotations` keeps these lazy
    from jarvis.ui.web.provider_spec import ProviderSpec

log = logging.getLogger(__name__)


def _catalog() -> Any:
    """Lazy import of the provider catalog (a brain module must not import the
    UI layer at module-load time — keeps the dependency direction clean and
    avoids any import cycle with ``provider_routes``)."""
    from jarvis.ui.web import provider_spec

    return provider_spec


def get_spec(provider_id: str) -> Any:
    """Provider spec by id, or ``None`` (thin wrapper over the lazy catalog)."""
    return _catalog().get_spec(provider_id)

# The tiers a provider switch can target. ``brain`` and ``tts`` apply live (no
# restart); ``stt`` and ``subagent`` are wired once at bootstrap and need a
# restart to take effect.
SWITCHABLE_TIERS: frozenset[str] = frozenset({"brain", "tts", "stt", "subagent"})

# Maps a provider id to the credential-manager *provider slot* used by
# ``cfg.get_provider_secret`` — needed where one key backs several provider ids
# (e.g. gemini + gemini-flash-tts share ``gemini_api_key``). Kept in sync with
# ``provider_routes`` by being imported there, not copied.
AUTH_PROVIDER_ALIASES: dict[str, str] = {
    "claude-api": "claude-api",
    "openai": "openai",
    "openai-tts": "openai",
    "openai-api": "openai",
    "openrouter": "openrouter",
    "gemini": "gemini",
    "gemini-flash-tts": "gemini",
    "grok": "grok",
    "grok-voice": "grok",
}

# Local providers that need no credential at all.
_NO_CREDENTIAL_PROVIDERS: frozenset[str] = frozenset({"faster-whisper"})

# A stored secret must be at least this long before we reveal a 3+3 preview.
# Below it, 6 revealed characters would expose too large a fraction of the key,
# so we confirm it is set but show no preview.
_MIN_PREVIEW_LEN: int = 12


def _mask_secret(value: str) -> dict[str, Any]:
    """Build a masked preview of a secret: first 3 + last 3 chars, middle hidden.

    Returns a dict the brain can phrase naturally. NEVER returns the full value
    and NEVER logs it. For values shorter than ``_MIN_PREVIEW_LEN`` the preview
    is ``None`` (set, but too short to reveal safely).
    """
    v = (value or "").strip()
    if len(v) < _MIN_PREVIEW_LEN:
        return {"preview": None, "first3": None, "last3": None, "hidden_chars": len(v)}
    first3, last3 = v[:3], v[-3:]
    return {
        "preview": f"{first3}...{last3}",
        "first3": first3,
        "last3": last3,
        "hidden_chars": len(v) - 6,
    }


def _resolve_secret_value(provider_id: str, spec: Any) -> str:
    """Fetch the stored secret value for a provider (or "" if absent).

    Resolution order mirrors ``is_credential_present``: the provider-slot alias
    first (handles shared keys like gemini + gemini-flash-tts), then the spec's
    declared ``secret_keys``, then the provider id treated as a slot.
    """
    alias = AUTH_PROVIDER_ALIASES.get(provider_id)
    if alias:
        value = cfg_mod.get_provider_secret(alias)
        if value:
            return value
    if spec is not None:
        for key in getattr(spec, "secret_keys", ()):
            value = cfg_mod.get_secret(key)
            if value:
                return value
    return cfg_mod.get_provider_secret(provider_id) or ""


def masked_secret_preview(provider_id: str) -> dict[str, Any]:
    """Masked preview of a provider's stored API key (user mandate 2026-05-31).

    Returns ``{provider, configured, preview, first3, last3, hidden_chars}``.
    The preview is ``AIz...xQ2`` style — first 3 + last 3 only. Never returns
    the full value; logs only the provider name and whether a key was present.
    """
    provider_id = (provider_id or "").strip()
    spec = get_spec(provider_id)
    value = _resolve_secret_value(provider_id, spec)
    if not value:
        log.info("masked_secret_preview: provider=%r has no stored key", provider_id)
        return {"provider": provider_id, "configured": False, "preview": None}

    masked = _mask_secret(value)
    # Privacy: log only that a preview was produced, never the value or the mask.
    log.info(
        "masked_secret_preview: provider=%r configured (preview=%s)",
        provider_id, masked["preview"] is not None,
    )
    return {
        "provider": provider_id,
        "configured": True,
        **masked,
    }


# ----------------------------------------------------------------------
# Credential presence (single source of truth — also used by provider_routes)
# ----------------------------------------------------------------------


def is_credential_present(spec: ProviderSpec, binary_path: str | None = None) -> bool:
    """True iff ``spec``'s provider has a usable stored credential.

    Heuristic per ``auth_mode`` — mirrors the former private check in
    ``provider_routes`` exactly (which now imports this function).
    """
    if spec.auth_mode == "none":
        return True
    if spec.auth_mode == "api_key":
        secret_provider = AUTH_PROVIDER_ALIASES.get(spec.id)
        if secret_provider is not None:
            return bool(cfg_mod.get_provider_secret(secret_provider))
        return all(bool(cfg_mod.get_secret(k)) for k in spec.secret_keys)
    if spec.auth_mode == "codex":
        if any(bool(cfg_mod.get_secret(k)) for k in spec.secret_keys):
            return True
        try:
            from jarvis.codex_auth import CodexAuthService

            return CodexAuthService(binary_path).status().connected
        except Exception:  # noqa: BLE001 — codex CLI absent is just "not present"
            return False
    return False


def _provider_configured(provider_id: str) -> bool:
    spec = get_spec(provider_id)
    if spec is None:
        # subagent providers (e.g. claude-api via OAuth) may not be in the
        # brain/tts/stt catalog — fall back to the provider-secret check.
        return bool(cfg_mod.get_provider_secret(provider_id))
    return is_credential_present(spec)


# ----------------------------------------------------------------------
# Read: complete settings snapshot (secret-free)
# ----------------------------------------------------------------------


def _providers_for_tier(tier: str, active: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for spec in _catalog().PROVIDERS:
        if spec.tier != tier:
            continue
        out.append(
            {
                "id": spec.id,
                "label": spec.label,
                "active": spec.id == active,
                "configured": is_credential_present(spec),
                "needs_credential": spec.auth_mode != "none",
            }
        )
    return out


def _safe(getter: Any, default: Any = None) -> Any:
    try:
        value = getter()
    except Exception:  # noqa: BLE001 — a missing config field is not an error here
        return default
    return default if value is None else value


def build_settings_snapshot(cfg: Any) -> dict[str, Any]:
    """A complete, read-only, secret-free picture of the current config.

    Every value is read defensively (``getattr`` chains) so a config schema that
    is missing a field degrades to ``None`` rather than raising — this is a
    read-only overview tool and must never crash the turn.
    """
    brain = getattr(cfg, "brain", None)
    tts = getattr(cfg, "tts", None)
    stt = getattr(cfg, "stt", None)
    ui = getattr(cfg, "ui", None)
    profile = getattr(cfg, "profile", None)
    wake = getattr(cfg, "wake", None) or getattr(cfg, "wakeword", None)
    autostart = getattr(cfg, "autostart", None)
    computer_use = getattr(cfg, "computer_use", None)
    sub_jarvis = getattr(brain, "sub_jarvis", None) if brain is not None else None

    active_brain = getattr(brain, "primary", None) if brain is not None else None
    # Prefer the *live* active provider when the BrainManager is running — it is
    # the ground truth after a mid-session switch; fall back to config.
    manager = runtime_refs.get_brain_manager()
    if manager is not None:
        live = getattr(manager, "active_provider", None)
        if live:
            active_brain = live

    active_tts = getattr(tts, "provider", None) if tts is not None else None
    active_stt = getattr(stt, "provider", None) if stt is not None else None
    active_sub = getattr(sub_jarvis, "provider", None) if sub_jarvis is not None else None

    providers = {
        "brain": _providers_for_tier("brain", active_brain),
        "tts": _providers_for_tier("tts", active_tts),
        "stt": _providers_for_tier("stt", active_stt),
        "subagent": {
            "active": active_sub,
            "configured": _provider_configured(active_sub) if active_sub else False,
        },
    }

    # Resolved name (the wake phrase is now the single source — there is no
    # separate [persona].name; resolve_assistant_name derives it and is fully
    # defensive, so it is safe inside the read-only snapshot).
    from jarvis.brain.assistant_name import resolve_assistant_name

    settings = {
        "reply_language": _safe(lambda: brain.reply_language),
        "assistant_name": _safe(lambda: resolve_assistant_name(cfg)),
        "wake_phrase": _safe(lambda: wake.phrase),
        "wake_engine": _safe(lambda: wake.engine),
        "autostart_enabled": _safe(lambda: autostart.enabled),
        "ui_theme": _safe(lambda: ui.theme),
        "tts_voice_de": _safe(lambda: tts.voice_de),
        "tts_voice_en": _safe(lambda: tts.voice_en),
        "tts_speed": _safe(lambda: tts.speed),
        "profile_language": _safe(lambda: profile.language),
        "computer_use_step_budget": _safe(lambda: computer_use.step_budget),
    }

    mcp_servers = list_mcp_servers()

    return {
        "providers": providers,
        "settings": settings,
        "mcp_servers": mcp_servers,
    }


# ----------------------------------------------------------------------
# Read: MCP server list
# ----------------------------------------------------------------------


def list_mcp_servers() -> list[dict[str, Any]]:
    """The MCP servers declared in ``mcp.json`` (name, enabled, description)."""
    try:
        from jarvis.mcp import state as mcp_state

        cfg = mcp_state.load_config()
    except Exception as exc:  # noqa: BLE001
        log.debug("list_mcp_servers: load_config failed: %s", exc)
        return []
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
    out: list[dict[str, Any]] = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "name": name,
                "enabled": bool(entry.get("enabled", False)),
                "description": entry.get("description", ""),
                "transport": entry.get("transport", "stdio"),
                "command": entry.get("command"),
            }
        )
    return out


# ----------------------------------------------------------------------
# Write: provider switch (3-layer persist + live apply where supported)
# ----------------------------------------------------------------------


def _current_provider(cfg: Any, tier: str) -> str | None:
    brain = getattr(cfg, "brain", None)
    if tier == "brain":
        return getattr(brain, "primary", None) if brain else None
    if tier == "tts":
        return getattr(getattr(cfg, "tts", None), "provider", None)
    if tier == "stt":
        return getattr(getattr(cfg, "stt", None), "provider", None)
    if tier == "subagent":
        sub = getattr(brain, "sub_jarvis", None) if brain else None
        return getattr(sub, "provider", None) if sub else None
    return None


async def apply_provider_switch(
    tier: str,
    provider: str,
    *,
    cfg: Any,
    persist: bool = True,
) -> dict[str, Any]:
    """Switch the active provider for ``tier`` to ``provider``.

    Returns a result dict with ``ok``; on failure ``error`` + ``error_kind``;
    on success ``old_provider``, ``new_provider``, ``persisted``,
    ``applied_live``, ``requires_restart``.

    Never sets a raw key — only flips which provider is active. The target
    provider must already have a stored credential (checked up-front).
    """
    tier = (tier or "").strip().lower()
    provider = (provider or "").strip()

    if tier not in SWITCHABLE_TIERS:
        return {
            "ok": False,
            "error_kind": "unknown_tier",
            "error": (
                f"Unknown tier {tier!r}. Use one of: "
                f"{', '.join(sorted(SWITCHABLE_TIERS))}."
            ),
        }

    old_provider = _current_provider(cfg, tier)

    if tier == "subagent":
        return await _switch_subagent(provider, cfg=cfg, persist=persist, old=old_provider)

    # brain / tts / stt — validate against the provider catalog.
    spec = get_spec(provider)
    if spec is None:
        return {
            "ok": False,
            "error_kind": "unknown_provider",
            "error": f"Unknown provider {provider!r} for tier {tier!r}.",
        }
    if spec.tier != tier:
        return {
            "ok": False,
            "error_kind": "wrong_tier",
            "error": (
                f"Provider {provider!r} is a {spec.tier} provider, not {tier}. "
                f"Did you mean tier={spec.tier!r}?"
            ),
        }
    if not is_credential_present(spec):
        return {
            "ok": False,
            "error_kind": "missing_credential",
            "error": (
                f"{spec.label} is not configured — its API key is missing. "
                "Add it in the Settings tab first, then switch."
            ),
        }

    if tier == "brain":
        return await _switch_brain(provider, cfg=cfg, persist=persist, old=old_provider)
    if tier == "tts":
        return _switch_tts(provider, cfg=cfg, persist=persist, old=old_provider)
    return _switch_stt(provider, cfg=cfg, persist=persist, old=old_provider)


async def _switch_brain(
    provider: str, *, cfg: Any, persist: bool, old: str | None
) -> dict[str, Any]:
    manager = runtime_refs.get_brain_manager()
    persisted = False
    applied_live = False

    if manager is not None and hasattr(manager, "switch"):
        try:
            await manager.switch(provider, persist=persist)
        except TypeError:
            # Older switch signature without the persist kwarg.
            await manager.switch(provider)
            if persist:
                persisted = _persist_brain_primary(provider)
        except Exception as exc:  # noqa: BLE001
            log.exception("Brain switch to %r failed", provider)
            return {
                "ok": False,
                "error_kind": "switch_failed",
                "error": f"Switch failed: {type(exc).__name__}: {exc}",
            }
        else:
            if persist:
                persisted = bool(getattr(manager, "last_persist_ok", False))
        applied_live = getattr(manager, "active_provider", None) == provider
        if not applied_live:
            return {
                "ok": False,
                "error_kind": "switch_not_applied",
                "error": (
                    f"Switch to {provider!r} was not applied "
                    f"(active is {getattr(manager, 'active_provider', None)!r}). "
                    "Provider may not be loadable."
                ),
            }
    else:
        # No live manager (headless build before bootstrap): persist only.
        if persist:
            persisted = _persist_brain_primary(provider)

    _set_in_memory(cfg, ["brain", "primary"], provider)
    return {
        "ok": True,
        "tier": "brain",
        "old_provider": old,
        "new_provider": provider,
        "persisted": persisted,
        "applied_live": applied_live,
        "requires_restart": not applied_live,
    }


def _switch_tts(provider: str, *, cfg: Any, persist: bool, old: str | None) -> dict[str, Any]:
    persisted = _persist(lambda: _import_writer().set_tts_provider(provider)) if persist else False
    _set_in_memory(cfg, ["tts", "provider"], provider)

    applied_live = False
    pipeline = runtime_refs.get_speech_pipeline()
    tts_cfg = getattr(cfg, "tts", None)
    if pipeline is not None and hasattr(pipeline, "set_tts") and tts_cfg is not None:
        try:
            from jarvis.plugins.tts import build_tts_from_config

            pipeline.set_tts(build_tts_from_config(tts_cfg))
            applied_live = True
        except Exception as exc:  # noqa: BLE001
            log.error("TTS live-switch failed (restart needed): %s", exc, exc_info=True)

    return {
        "ok": True,
        "tier": "tts",
        "old_provider": old,
        "new_provider": provider,
        "persisted": persisted,
        "applied_live": applied_live,
        "requires_restart": not applied_live,
    }


def _switch_stt(provider: str, *, cfg: Any, persist: bool, old: str | None) -> dict[str, Any]:
    persisted = _persist(lambda: _import_writer().set_stt_provider(provider)) if persist else False
    _set_in_memory(cfg, ["stt", "provider"], provider)
    return {
        "ok": True,
        "tier": "stt",
        "old_provider": old,
        "new_provider": provider,
        "persisted": persisted,
        "applied_live": False,
        "requires_restart": True,
    }


async def _switch_subagent(
    provider: str, *, cfg: Any, persist: bool, old: str | None
) -> dict[str, Any]:
    try:
        from jarvis.missions.worker_runtime.provider_map import (
            CODEX_SUBAGENT_CANONICAL,
            CODEX_SUBAGENT_SLUGS,
            JARVIS_TO_OPENCLAW,
            canonical_subagent_provider,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error_kind": "subagent_unavailable",
            "error": f"Subagent provider map unavailable: {exc}",
        }

    canon = canonical_subagent_provider(provider) or ""

    # Codex is a DIRECT worker (no OpenClaw slug) — accept it explicitly, mirroring
    # the REST ``/api/subagent/switch`` path so the two switch sites never drift.
    # Backed by the ChatGPT subscription (OAuth) OR an OpenAI API key.
    if canon in CODEX_SUBAGENT_SLUGS:
        try:
            from jarvis.codex_auth import CodexAuthService

            codex_connected = CodexAuthService().status().connected
        except Exception:  # noqa: BLE001 — codex CLI absent is just "not connected"
            codex_connected = False
        has_key = bool(
            cfg_mod.get_secret("codex_openai_api_key")
            or cfg_mod.get_provider_secret("codex")
        )
        if not (codex_connected or has_key):
            return {
                "ok": False,
                "error_kind": "missing_credential",
                "error": (
                    "Codex is not connected — run 'codex login' or save an OpenAI "
                    "API key first, then switch the subagent."
                ),
            }
        persisted = (
            _persist(
                lambda: _import_writer().set_sub_jarvis_provider(CODEX_SUBAGENT_CANONICAL)
            )
            if persist
            else False
        )
        _set_in_memory(cfg, ["brain", "sub_jarvis", "provider"], CODEX_SUBAGENT_CANONICAL)
        return {
            "ok": True,
            "tier": "subagent",
            "old_provider": old,
            "new_provider": CODEX_SUBAGENT_CANONICAL,
            "persisted": persisted,
            "applied_live": False,
            "requires_restart": True,
        }

    if canon not in JARVIS_TO_OPENCLAW:
        known = ", ".join(sorted(JARVIS_TO_OPENCLAW))
        return {
            "ok": False,
            "error_kind": "unknown_provider",
            "error": f"{provider!r} is not a subagent-capable provider. Available: {known}.",
        }
    if not cfg_mod.get_provider_secret(canon):
        return {
            "ok": False,
            "error_kind": "missing_credential",
            "error": (
                f"{canon} has no stored key. Set the key on the brain provider "
                "first, then switch the subagent."
            ),
        }

    persisted = (
        _persist(lambda: _import_writer().set_sub_jarvis_provider(canon)) if persist else False
    )
    _set_in_memory(cfg, ["brain", "sub_jarvis", "provider"], canon)
    return {
        "ok": True,
        "tier": "subagent",
        "old_provider": old,
        "new_provider": canon,
        "persisted": persisted,
        "applied_live": False,
        "requires_restart": True,
    }


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def resolve_running_cfg() -> Any:
    """The config object the running app actually reads from.

    Prefer the live BrainManager's ``_config`` (the same instance the server
    threaded into ``app.state.config``), so an in-memory provider update is seen
    by other readers (e.g. ``/api/providers``). Falls back to a fresh
    ``load_config()`` for headless / pre-bootstrap callers.
    """
    manager = runtime_refs.get_brain_manager()
    cfg = getattr(manager, "_config", None) if manager is not None else None
    if cfg is not None:
        return cfg
    return cfg_mod.load_config()


def _import_writer() -> Any:
    from jarvis.core import config_writer

    return config_writer


def _persist(fn: Any) -> bool:
    try:
        fn()
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Config persist failed: %s", exc)
        return False


def _persist_brain_primary(provider: str) -> bool:
    return _persist(lambda: _import_writer().set_brain_primary(provider))


def _set_in_memory(cfg: Any, path: list[str], value: Any) -> None:
    """Best-effort in-memory cfg update (frozen models are not an error)."""
    obj = cfg
    try:
        for key in path[:-1]:
            obj = getattr(obj, key, None)
            if obj is None:
                return
        setattr(obj, path[-1], value)
    except Exception as exc:  # noqa: BLE001 — frozen / detached cfg is acceptable
        log.debug("in-memory cfg update skipped (%s): %s", ".".join(path), exc)

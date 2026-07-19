"""Capability- and credential-aware realtime provider resolution.

Realtime plugins are discovered through the ``jarvis.realtime`` entry-point
group. The configured provider and its explicit fallbacks are tried first,
then every other installed provider with a usable credential. No provider name
or model id controls whether the feature is available (AP-21/AP-22).
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import get_secret_any
from jarvis.core.registry import list_plugins, load
from jarvis.realtime.protocol import RealtimeProvider

log = logging.getLogger(__name__)

_GROUP = "jarvis.realtime"


def _configured_provider_ids(cfg: Any) -> list[str]:
    tier = getattr(getattr(getattr(cfg, "brain", None), "realtime", None), "provider", None)
    realtime = getattr(getattr(cfg, "brain", None), "realtime", None)
    preferred = [
        tier,
        getattr(realtime, "fallback_provider", None),
        getattr(realtime, "fallback_provider_2", None),
    ]
    installed = list_plugins(_GROUP)
    ordered: list[str] = []
    for provider_id in [*preferred, *installed]:
        value = str(provider_id or "").strip()
        if value and value in installed and value not in ordered:
            ordered.append(value)
    return ordered


def _provider_candidates(cfg: Any) -> list[Any]:
    """Instantiate every keyed realtime plugin in effective fallback order."""
    candidates: list[Any] = []
    for provider_id in _configured_provider_ids(cfg):
        try:
            provider_cls = load(_GROUP, provider_id, protocol=RealtimeProvider)
            if not bool(getattr(provider_cls, "supports_realtime", False)):
                continue
            credential_candidates = tuple(
                getattr(provider_cls, "credential_candidates", ()) or ()
            )
            api_key = get_secret_any(credential_candidates)
            if not api_key:
                continue
            provider = provider_cls(api_key=api_key)
            if not isinstance(provider, RealtimeProvider):
                log.warning(
                    "Realtime plugin %s does not satisfy the provider contract.",
                    provider_id,
                )
                continue
            candidates.append(provider)
        except Exception as exc:  # noqa: BLE001 — one plugin must not brick others
            log.warning("Realtime plugin %s is unavailable: %s", provider_id, exc)
    return candidates


def _resolve_realtime_provider(cfg: Any) -> Any:
    """Compatibility helper returning the first credential-ready provider."""
    candidates = _provider_candidates(cfg)
    return candidates[0] if candidates else None


def realtime_available_provider(cfg: Any) -> str | None:
    """Return the first credential-ready provider id without opening a socket."""
    provider = _resolve_realtime_provider(cfg)
    return str(getattr(provider, "name", "") or "") or None


def _provider_family(provider_id: str) -> str:
    """Credential/quota family of a provider id (AP-22 diagnostics only)."""
    pid = (provider_id or "").strip().lower()
    aliases = {
        "codex": "openai",
        "openai-api": "openai",
        "openai-realtime": "openai",
        "antigravity": "gemini",
        "gemini-live": "gemini",
        "google": "gemini",
        "claude-api": "anthropic",
        "claude-code": "anthropic",
    }
    return aliases.get(pid, pid.split("-")[0])


def _warn_on_same_family_delegate_chain(cfg: Any, realtime_provider: str) -> None:
    """AP-22 visibility: one quota hit must not kill realtime AND the brain.

    When every configured brain provider resolves to the realtime provider's
    own credential family, a single 429/402 after turn one takes down BOTH
    tiers at once — the provider-down half of the Mac 2026-07-18 self-talk
    loop (BUG-089). Log-only by design: chain resolution stays key-aware and
    realtime-scoped (strict mode separation), so the durable fix is a key of
    another family, added in-app.
    """
    try:
        realtime_family = _provider_family(realtime_provider)
        brain_cfg = getattr(cfg, "brain", None)
        chain = [
            entry
            for entry in (
                str(value or "").strip()
                for value in (
                    getattr(brain_cfg, "primary", None),
                    getattr(brain_cfg, "deep_brain", None),
                    getattr(brain_cfg, "routing_provider", None),
                    getattr(brain_cfg, "local_fallback", None),
                )
            )
            if entry
        ]
        if not realtime_family or not chain:
            return
        if all(_provider_family(entry) == realtime_family for entry in chain):
            log.warning(
                "AP-22: the realtime provider %r and EVERY configured brain "
                "provider (%s) share the %r credential family — one quota or "
                "auth failure silences both tiers at once. Add an API key of "
                "a different family in the API-Keys view to give the "
                "delegate chain a cross-family fallback.",
                realtime_provider,
                ", ".join(sorted(set(chain))),
                realtime_family,
            )
    except Exception:  # noqa: BLE001, S110 — diagnostics must never block the build
        pass


def build_realtime_session(
    *,
    cfg: Any,
    bus: Any,
    session_id: str,
    send_binary: Any,
    send_json: Any,
    half_duplex: bool = False,
    surface: str = "browser",
    brain: Any = None,
):
    """Build a transport-neutral realtime session wrapper.

    Returning ``None`` is an honest request for the caller to use the classic
    pipeline. Actual socket handshakes happen lazily on ``audio_start`` and the
    wrapper tries every candidate in order before failing.
    """
    mode = getattr(getattr(cfg, "voice", None), "mode", "pipeline")
    if mode != "realtime":
        return None
    try:
        providers = _provider_candidates(cfg)
        if not providers:
            log.info("Realtime voice has no credential-ready provider; using pipeline mode.")
            return None
        _warn_on_same_family_delegate_chain(
            cfg, str(getattr(providers[0], "name", "") or "")
        )

        from jarvis.realtime.session import RealtimeVoiceSession

        return RealtimeVoiceSession(
            session_id=session_id,
            send_binary=send_binary,
            send_json=send_json,
            providers=providers,
            config=cfg,
            bus=bus,
            half_duplex=half_duplex,
            surface=surface,
            brain=brain,
        )
    except Exception as exc:  # noqa: BLE001 — unbuildable stack => classic path
        log.warning("Realtime session build failed: %s", exc)
        return None


__all__ = [
    "build_realtime_session",
    "realtime_available_provider",
]

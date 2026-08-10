"""Capability- and credential-aware realtime provider resolution.

Realtime plugins are discovered through the ``jarvis.realtime`` entry-point
group. The configured provider and its explicit fallbacks are tried first,
then every other installed provider with a usable credential when the selected
provider permits implicit usage fallback. No provider name or model id controls
whether the feature is available (AP-21/AP-22).
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import get_secret_any
from jarvis.core.registry import list_plugins, load
from jarvis.realtime.protocol import RealtimeProvider

log = logging.getLogger(__name__)

_GROUP = "jarvis.realtime"


def _explicit_provider_ids(cfg: Any) -> list[str]:
    """Configured primary/fallback ids, without ambient installed plugins."""
    realtime = getattr(getattr(cfg, "brain", None), "realtime", None)
    installed = set(list_plugins(_GROUP))
    ordered: list[str] = []
    for configured in (
        getattr(realtime, "provider", None),
        getattr(realtime, "fallback_provider", None),
        getattr(realtime, "fallback_provider_2", None),
    ):
        provider_id = str(configured or "").strip()
        if provider_id and provider_id in installed and provider_id not in ordered:
            ordered.append(provider_id)
    return ordered


def _configured_provider_ids(cfg: Any) -> list[str]:
    preferred = _explicit_provider_ids(cfg)
    installed = list_plugins(_GROUP)
    allow_ambient = realtime_implicit_usage_fallback_allowed(cfg)
    ordered: list[str] = []
    provider_ids = [*preferred, *(installed if allow_ambient else [])]
    for provider_id in provider_ids:
        value = str(provider_id or "").strip()
        if value and value in installed and value not in ordered:
            ordered.append(value)
    return ordered


def realtime_implicit_usage_fallback_allowed(cfg: Any) -> bool:
    """Whether the selected primary permits ambient usage-billed fallback.

    This reads a provider capability, never a provider id. An absent selection
    and successfully loaded API-backed providers retain the historical
    permissive behavior. A configured but missing/broken primary is
    fail-closed: it cannot silently spend unrelated ambient credentials.
    """
    realtime = getattr(getattr(cfg, "brain", None), "realtime", None)
    primary_id = str(getattr(realtime, "provider", "") or "").strip()
    if not primary_id:
        return True
    installed = list_plugins(_GROUP)
    if primary_id not in installed:
        return False
    try:
        provider_cls = load(_GROUP, primary_id, protocol=RealtimeProvider)
    except Exception as exc:  # noqa: BLE001 - availability still degrades normally
        log.warning("Realtime primary capability probe failed for %s: %s", primary_id, exc)
        return False
    return bool(getattr(provider_cls, "implicit_usage_fallback_allowed", True))


def _identified_provider_candidates(
    cfg: Any, *, defer_external_login_probe: bool = False
) -> list[tuple[str, Any]]:
    """Instantiate every credential-ready plugin in effective fallback order.

    API providers declare ``credential_candidates`` and receive the resolved
    key. Providers backed by an external application login may instead expose
    a synchronous ``external_login_ready`` capability probe and a no-argument
    constructor. The factory never infers either path from a provider name.

    Each candidate is paired with the entry-point id it was loaded from, which
    a caller cannot recover from the instance: ``name`` is a provider-chosen
    label and may differ from the id the registry knows.
    """
    candidates: list[tuple[str, Any]] = []
    explicit_ids = set(_explicit_provider_ids(cfg))
    for provider_id in _configured_provider_ids(cfg):
        try:
            provider_cls = load(_GROUP, provider_id, protocol=RealtimeProvider)
            if not bool(getattr(provider_cls, "supports_realtime", False)):
                continue
            credential_candidates = tuple(
                getattr(provider_cls, "credential_candidates", ()) or ()
            )
            api_key = (
                get_secret_any(credential_candidates)
                if credential_candidates
                else None
            )
            if api_key:
                provider = provider_cls(api_key=api_key)
            else:
                external_login_ready = getattr(
                    provider_cls, "external_login_ready", None
                )
                if not callable(external_login_ready):
                    continue
                if provider_id not in explicit_ids:
                    continue
                # Session construction can happen on an audio/WebSocket loop.
                # The adapter's async can_open_duplex_session/open_session
                # performs the authoritative probe without blocking that loop,
                # but only for providers the user explicitly selected. An
                # ambient installed subscription must not become a candidate
                # merely because its synchronous login probe was deferred.
                if not defer_external_login_probe:
                    try:
                        ready = external_login_ready(cfg)
                    except TypeError:
                        ready = external_login_ready()
                    if not bool(ready):
                        continue
                from_runtime_config = getattr(
                    provider_cls, "from_runtime_config", None
                )
                provider = (
                    from_runtime_config(cfg)
                    if callable(from_runtime_config)
                    else provider_cls()
                )
            if not isinstance(provider, RealtimeProvider):
                log.warning(
                    "Realtime plugin %s does not satisfy the provider contract.",
                    provider_id,
                )
                continue
            candidates.append((provider_id, provider))
        except Exception as exc:  # noqa: BLE001 — one plugin must not brick others
            log.warning("Realtime plugin %s is unavailable: %s", provider_id, exc)
    return candidates


def _provider_candidates(cfg: Any, *, defer_external_login_probe: bool = False) -> list[Any]:
    """Credential-ready provider instances in effective fallback order."""
    return [
        provider
        for _provider_id, provider in _identified_provider_candidates(
            cfg, defer_external_login_probe=defer_external_login_probe
        )
    ]


def _resolve_realtime_provider(cfg: Any) -> Any:
    """Compatibility helper returning the first credential-ready provider."""
    candidates = _provider_candidates(cfg)
    return candidates[0] if candidates else None


def realtime_available_provider(cfg: Any) -> str | None:
    """Return the first credential-ready provider id without opening a socket."""
    provider = _resolve_realtime_provider(cfg)
    return str(getattr(provider, "name", "") or "") or None


def realtime_requires_webrtc_offer(cfg: Any) -> bool:
    """Whether any eligible realtime fallback needs browser SDP signaling.

    This is an adapter capability, never a provider-id check. The offer must be
    prepared before audio starts because an API primary can fail into an
    explicitly configured subscription fallback during the same handshake.
    """
    # Browser SDP preparation is allowed only for an explicitly selected
    # primary/fallback. Installed-but-unselected external-login plugins are
    # neither a billing fallback nor a reason to negotiate WebRTC.
    for provider_id in _explicit_provider_ids(cfg):
        try:
            provider_cls = load(_GROUP, provider_id, protocol=RealtimeProvider)
        except Exception as exc:  # noqa: BLE001 - one broken plugin is skipped
            log.warning("Realtime WebRTC capability probe failed for %s: %s", provider_id, exc)
            continue
        if bool(getattr(provider_cls, "supports_realtime", False)) and bool(
            getattr(provider_cls, "requires_webrtc_offer", False)
        ):
            return True
    return False


def realtime_handshake_budget_s(cfg: Any) -> float:
    """Longest handshake any eligible realtime provider declares it needs.

    A capability read, never a provider-id check (AP-21). ``RealtimeVoiceSession``
    stretches its own handshake deadline to the largest declared budget; a
    SURFACE that gives up earlier throws that away, which is exactly what made
    cold subscription calls unreachable from the browser: the transport is
    allowed 45 s and documents 15-25 s cold starts, while the browser client
    called the attempt dead after 20 s.

    Returns the effective ceiling — never below the shared default — so a
    caller can use the value directly as a timeout budget.

    The budget is read off the INSTANCE the factory would actually open with
    whenever there is one, because a provider may declare it as a ``property``
    whose value depends on its own configuration (a self-hosted card asks for
    more time when it is allowed to revive its server). Reading such a
    declaration off the class yields the descriptor object, not a number.
    Every read stays inside the per-provider guard: one plugin that raises
    must cost only its own budget, never the whole probe — a probe that dies
    leaves the surface on its historical floor while the transport is still
    legitimately negotiating.
    """
    from jarvis.realtime.session import _PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S

    declared = [float(_PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S)]
    instances: dict[str, Any] = {}
    try:
        instances = dict(_identified_provider_candidates(cfg, defer_external_login_probe=True))
    except Exception as exc:  # noqa: BLE001 - class-level reads still work
        log.warning("Realtime handshake-budget candidate build failed: %s", exc)
    for provider_id in _configured_provider_ids(cfg):
        try:
            source = instances.get(provider_id)
            if source is None:
                source = load(_GROUP, provider_id, protocol=RealtimeProvider)
                if not bool(getattr(source, "supports_realtime", False)):
                    continue
            declared.append(float(getattr(source, "handshake_budget_s", 0.0) or 0.0))
        except Exception as exc:  # noqa: BLE001 - one broken plugin is skipped
            log.warning("Realtime handshake-budget probe failed for %s: %s", provider_id, exc)
    return max(declared)


def _realtime_is_the_configured_voice_mode(cfg: Any) -> bool:
    """Whether realtime voice is the mode this install actually runs.

    The same switch ``build_realtime_session`` reads, so warming can never
    prepare a transport the session builder would refuse. Deliberately about
    CONFIGURATION, not about a live call: a warm transport is worth having
    BEFORE the first wake word, which is the entire point of warming.
    """
    # The stable ChatGPT-subscription composition intentionally runs through
    # SpeechPipeline even when an older install still carries
    # ``voice.mode = realtime`` or the legacy realtime-provider pin. Warming
    # that retired duplex adapter starts a second App Server and holds the one
    # subscription-profile lease before the text voice path can answer.
    from jarvis.voice.subscription_profile import (  # noqa: PLC0415
        subscription_voice_selected,
    )

    if subscription_voice_selected(cfg):
        return False
    return getattr(getattr(cfg, "voice", None), "mode", "pipeline") == "realtime"


async def realtime_warm_selected_transports(cfg: Any) -> None:
    """Pre-open the primary transport and only explicitly safe fallbacks.

    A capability probe, never a provider-id check (AP-21): a provider that
    declares no ``warm_transport`` is simply skipped. The primary is always
    eligible. A fallback must additionally declare
    ``eager_warm_as_fallback=True``: preloading multiple native model stacks can
    oversubscribe shared GPU/RAM even though only one call transport is used.
    An installed-but-unselected plugin must never spawn a process or touch a
    credential on its own.

    Warming exists because the Codex subscription adapter otherwise spawns its
    app-server and verifies the account INSIDE the first call's handshake
    (~1.5 s of 3.0 s, measured 2026-08-02). One broken plugin never stops the
    others, and no failure here reaches the caller.

    Skipped entirely when realtime is not the configured voice mode. Warming a
    transport the session builder would refuse anyway is pure cost, and for a
    subscription transport that cost is a spawned process, a live account
    check, and a HELD profile lock at every boot — which is what makes the
    user's own login/logout report "busy" for a feature they switched off.
    """
    if not _realtime_is_the_configured_voice_mode(cfg):
        log.debug(
            "Realtime transport warm skipped: realtime is not the configured "
            "voice mode."
        )
        return
    for position, provider_id in enumerate(_explicit_provider_ids(cfg)):
        try:
            provider_cls = load(_GROUP, provider_id, protocol=RealtimeProvider)
            if position > 0 and not bool(
                getattr(provider_cls, "eager_warm_as_fallback", False)
            ):
                log.debug(
                    "Realtime fallback warm skipped for %s: provider did not "
                    "declare eager_warm_as_fallback.",
                    provider_id,
                )
                continue
            warm = getattr(provider_cls, "warm_transport", None)
            if callable(warm):
                await warm(cfg)
        except Exception as exc:  # noqa: BLE001 - one broken plugin is skipped
            log.warning(
                "Realtime transport warm failed for %s: %s", provider_id, exc
            )


async def realtime_prespawn_transports(cfg: Any) -> None:
    """Fire the spawn-only prestart for every explicitly selected provider.

    Runs BEFORE the warm worker's gates on purpose: the transport that
    declares this capability today is the managed local server, whose models
    load 45-90 s in a SEPARATE process — the earlier that spawn fires, the
    earlier the first call can connect, and nothing on the boot path ever
    waits for it. Unlike eager warming, position does not matter here: a
    prespawn is bounded to starting a process, so an explicitly configured
    FALLBACK is prestarted too — that fallback sitting stone cold is exactly
    what stranded the first call of 2026-08-10, when the subscription primary
    was down and the local fallback had never been started. A capability
    probe, never a provider-id check (AP-21); an installed-but-unselected
    plugin must never spawn a process on its own. Best-effort by contract —
    one broken plugin never stops the others and no failure reaches the
    caller.

    Gated on the same voice-mode switch as warming: prestarting a server for
    a feature this install runs through the classic pipeline is pure cost.
    """
    if not _realtime_is_the_configured_voice_mode(cfg):
        log.debug(
            "Realtime transport prespawn skipped: realtime is not the "
            "configured voice mode."
        )
        return
    for provider_id in _explicit_provider_ids(cfg):
        try:
            provider_cls = load(_GROUP, provider_id, protocol=RealtimeProvider)
            prespawn = getattr(provider_cls, "prespawn_transport", None)
            if callable(prespawn):
                await prespawn(cfg)
        except Exception as exc:  # noqa: BLE001 - one broken plugin is skipped
            log.warning(
                "Realtime transport prespawn failed for %s: %s", provider_id, exc
            )


#: The ChatGPT plan is its own quota pool, not metered platform usage. It is a
#: DISTINCT family from ``openai``: a 429 on one says nothing about the other.
_CHATGPT_SUBSCRIPTION_FAMILY = "openai-chatgpt-subscription"


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


def _brain_credential_family(provider_id: str) -> str:
    """Family a configured BRAIN id will actually bill against.

    ``codex`` is two products behind one id: with an OpenAI API key it is
    metered platform usage (family ``openai``); WITHOUT one it runs on the
    user's ChatGPT plan — the same account and the same quota the subscription
    realtime voice uses. Reporting ``openai`` for both is why a Codex brain
    sitting behind a Codex subscription voice never raised the AP-22 warning,
    even though that is the single configuration where one 429 takes down the
    voice and every action it delegates at the same moment.
    """
    family = _provider_family(provider_id)
    if family != "openai" or (provider_id or "").strip().lower() != "codex":
        return family
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES  # noqa: PLC0415

    candidates = tuple(PROVIDER_SECRET_CANDIDATES.get("codex", ()) or ())
    if candidates and get_secret_any(candidates):
        return "openai"
    return _CHATGPT_SUBSCRIPTION_FAMILY


def _warn_on_same_family_delegate_chain(
    cfg: Any,
    realtime_provider: str,
    *,
    credential_family: str | None = None,
) -> None:
    """AP-22 visibility: one quota hit must not kill realtime AND the brain.

    When every configured brain provider resolves to the realtime provider's
    own credential family, a single 429/402 after turn one takes down BOTH
    tiers at once — the provider-down half of the Mac 2026-07-18 self-talk
    loop (BUG-089). Log-only by design: chain resolution stays key-aware and
    realtime-scoped (strict mode separation), so the durable fix is a key of
    another family, added in-app.
    """
    try:
        realtime_family = str(credential_family or "").strip().lower()
        if not realtime_family:
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
        if all(_brain_credential_family(entry) == realtime_family for entry in chain):
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
    except Exception:  # noqa: BLE001 - diagnostics must never block the build
        log.debug("Realtime credential-family diagnostics failed.", exc_info=True)


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
    if not _realtime_is_the_configured_voice_mode(cfg):
        return None
    try:
        providers = _provider_candidates(cfg, defer_external_login_probe=True)
        if not providers:
            log.info("Realtime voice has no credential-ready provider; using pipeline mode.")
            return None
        primary_provider = providers[0]
        _warn_on_same_family_delegate_chain(
            cfg,
            str(getattr(primary_provider, "name", "") or ""),
            credential_family=str(
                getattr(primary_provider, "credential_family", "") or ""
            ),
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
            allow_classic_fallback=realtime_implicit_usage_fallback_allowed(cfg),
        )
    except Exception as exc:  # noqa: BLE001 — unbuildable stack => classic path
        log.warning("Realtime session build failed: %s", exc)
        return None


__all__ = [
    "build_realtime_session",
    "realtime_available_provider",
    "realtime_handshake_budget_s",
    "realtime_implicit_usage_fallback_allowed",
    "realtime_prespawn_transports",
    "realtime_requires_webrtc_offer",
]

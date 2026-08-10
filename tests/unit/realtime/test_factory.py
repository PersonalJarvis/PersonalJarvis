"""Provider-neutral realtime factory tests (AP-21/AP-22)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jarvis.realtime.factory as factory
from jarvis.core.config import VoiceConfig
from jarvis.realtime.factory import (
    _provider_candidates,
    _resolve_realtime_provider,
    build_realtime_session,
    realtime_available_provider,
    realtime_requires_webrtc_offer,
)


class _BaseProvider:
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, *, api_key=None):
        self.api_key = api_key

    async def can_open_duplex_session(self):
        return bool(self.api_key)

    async def open_session(self, cfg):  # pragma: no cover - factory does not open
        raise NotImplementedError


class _OpenAIProvider(_BaseProvider):
    name = "openai-realtime"
    credential_family = "openai"
    credential_candidates = (("openai_api_key", "OPENAI_API_KEY"),)


class _GeminiProvider(_BaseProvider):
    name = "gemini-live"
    credential_candidates = (("gemini_api_key", "GEMINI_API_KEY"),)


class _AcmeProvider(_BaseProvider):
    name = "acme-realtime"
    credential_candidates = (("acme_api_key", "ACME_API_KEY"),)


class _SubscriptionProvider(_BaseProvider):
    name = "codex-subscription-realtime"
    credential_family = "openai-chatgpt-subscription"
    credential_candidates = ()
    requires_webrtc_offer = True
    implicit_usage_fallback_allowed = False
    login_ready = False

    def __init__(self):
        super().__init__(api_key="external-login")

    @classmethod
    def external_login_ready(cls):
        return cls.login_ready


_PLUGINS = {
    "openai-realtime": _OpenAIProvider,
    "gemini-live": _GeminiProvider,
    "acme-realtime": _AcmeProvider,
    "codex-subscription-realtime": _SubscriptionProvider,
}


def _cfg(
    mode: str = "realtime",
    provider: str = "openai-realtime",
    profile: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        voice=SimpleNamespace(mode=mode, profile=profile),
        brain=SimpleNamespace(
            reply_language="en",
            providers={},
            realtime=SimpleNamespace(
                provider=provider,
                fallback_provider=None,
                fallback_provider_2=None,
            ),
        ),
    )


def _fake_registry(
    monkeypatch, keys: set[str], *, subscription_login: bool = False
) -> None:
    import jarvis.realtime.factory as factory

    monkeypatch.setattr(factory, "list_plugins", lambda _group: list(_PLUGINS))
    monkeypatch.setattr(factory, "load", lambda _group, name, protocol=None: _PLUGINS[name])
    _SubscriptionProvider.login_ready = subscription_login

    def _get_secret(candidates):
        slot = candidates[0][0]
        family = (
            "openai" if slot.startswith("openai")
            else "acme" if slot.startswith("acme")
            else "gemini"
        )
        return f"{family}-key" if family in keys else None

    monkeypatch.setattr(factory, "get_secret_any", _get_secret)


@pytest.mark.parametrize(
    ("configured", "keys", "expected"),
    [
        ("openai-realtime", {"openai"}, "openai-realtime"),
        ("gemini-live", {"gemini"}, "gemini-live"),
        ("openai-realtime", {"gemini"}, "gemini-live"),
        ("gemini-live", {"openai"}, "openai-realtime"),
        ("openai-realtime", {"openai", "gemini"}, "openai-realtime"),
        ("acme-realtime", {"acme"}, "acme-realtime"),
        ("openai-realtime", {"acme"}, "acme-realtime"),
        ("openai-realtime", set(), None),
    ],
)
def test_key_aware_cross_family_resolution(
    monkeypatch, configured, keys, expected
) -> None:
    _fake_registry(monkeypatch, keys)
    config = _cfg(provider=configured)
    resolved = _resolve_realtime_provider(config)
    assert (resolved.name if resolved else None) == expected
    assert realtime_available_provider(config) == expected


def test_explicit_fallback_order_precedes_other_installed_plugins(monkeypatch):
    _fake_registry(monkeypatch, {"openai", "gemini"})
    config = _cfg(provider="openai-realtime")
    config.brain.realtime.fallback_provider = "gemini-live"

    assert [provider.name for provider in _provider_candidates(config)] == [
        "openai-realtime",
        "gemini-live",
    ]


def test_external_login_provider_is_candidate_without_a_fake_api_key(monkeypatch):
    _fake_registry(monkeypatch, set(), subscription_login=True)
    config = _cfg(provider="codex-subscription-realtime")

    providers = _provider_candidates(config)

    assert [provider.name for provider in providers] == [
        "codex-subscription-realtime"
    ]
    assert providers[0].credential_family == "openai-chatgpt-subscription"


def test_subscription_primary_never_uses_ambient_api_fallback(
    monkeypatch,
):
    _fake_registry(monkeypatch, {"openai"}, subscription_login=False)
    config = _cfg(provider="codex-subscription-realtime")

    assert _provider_candidates(config) == []


def test_subscription_and_api_credentials_remain_distinct_fallbacks(monkeypatch):
    _fake_registry(monkeypatch, {"openai"}, subscription_login=True)
    config = _cfg(provider="codex-subscription-realtime")
    config.brain.realtime.fallback_provider = "openai-realtime"

    providers = _provider_candidates(config)

    assert [provider.name for provider in providers] == [
        "codex-subscription-realtime",
        "openai-realtime",
    ]
    assert [provider.credential_family for provider in providers] == [
        "openai-chatgpt-subscription",
        "openai",
    ]
    assert realtime_requires_webrtc_offer(config) is True


def test_api_provider_does_not_request_unneeded_browser_sdp(monkeypatch):
    _fake_registry(monkeypatch, {"openai"}, subscription_login=True)
    config = _cfg(provider="openai-realtime")

    assert realtime_requires_webrtc_offer(config) is False


def test_logged_in_subscription_is_never_an_ambient_candidate(monkeypatch):
    _fake_registry(monkeypatch, set(), subscription_login=True)
    config = _cfg(provider="openai-realtime")

    assert _provider_candidates(config) == []
    assert realtime_available_provider(config) is None


def test_pipeline_mode_never_builds_realtime_session(monkeypatch):
    _fake_registry(monkeypatch, {"openai"})
    assert (
        build_realtime_session(
            cfg=_cfg(mode="pipeline"),
            bus=None,
            session_id="s",
            send_binary=None,
            send_json=None,
        )
        is None
    )


def test_realtime_tool_mode_defaults_to_compact_delegate_execution() -> None:
    assert VoiceConfig().realtime_tool_mode == "delegate"


def test_one_realtime_key_builds_without_a_classic_brain(monkeypatch) -> None:
    _fake_registry(monkeypatch, {"gemini"})

    session = build_realtime_session(
        cfg=_cfg(provider="openai-realtime"),
        bus=None,
        session_id="realtime-only",
        send_binary=lambda _data: None,
        send_json=lambda _message: None,
        brain=None,
    )

    assert session is not None
    assert session._brain is None
    assert session._tool_mode == "delegate"
    assert session._delegate_enabled is False
    assert [provider.name for provider in session._providers] == ["gemini-live"]


def test_realtime_without_any_key_degrades_to_pipeline(monkeypatch):
    _fake_registry(monkeypatch, set())
    assert (
        build_realtime_session(
            cfg=_cfg(),
            bus=None,
            session_id="s",
            send_binary=None,
            send_json=None,
        )
        is None
    )


def test_build_passes_every_keyed_family_for_handshake_fallback(monkeypatch):
    _fake_registry(monkeypatch, {"openai", "gemini"})
    session = build_realtime_session(
        cfg=_cfg(),
        bus=None,
        session_id="s",
        send_binary=lambda _data: None,
        send_json=lambda _message: None,
        half_duplex=True,
    )

    assert session is not None
    assert [provider.name for provider in session._providers] == [
        "openai-realtime",
        "gemini-live",
    ]
    assert session._half_duplex is True


def test_same_family_delegate_chain_logs_ap22_warning(caplog):
    """BUG-089: realtime + whole brain chain on ONE family = one quota hit
    silences both tiers; the build names the risk and the in-app fix."""
    import logging

    from jarvis.realtime.factory import _warn_on_same_family_delegate_chain

    cfg = SimpleNamespace(
        brain=SimpleNamespace(
            primary="gemini",
            deep_brain="antigravity",
            routing_provider="gemini",
            local_fallback="gemini",
        )
    )
    with caplog.at_level(logging.WARNING, logger="jarvis.realtime.factory"):
        _warn_on_same_family_delegate_chain(cfg, "gemini-live")
    assert any("AP-22" in record.message for record in caplog.records)


def test_cross_family_delegate_chain_stays_quiet(caplog):
    import logging

    from jarvis.realtime.factory import _warn_on_same_family_delegate_chain

    cfg = SimpleNamespace(
        brain=SimpleNamespace(
            primary="gemini",
            deep_brain=None,
            routing_provider="claude-api",
            local_fallback="openrouter",
        )
    )
    with caplog.at_level(logging.WARNING, logger="jarvis.realtime.factory"):
        _warn_on_same_family_delegate_chain(cfg, "gemini-live")
    assert not [record for record in caplog.records if "AP-22" in record.message]


class _WarmProbe:
    """Minimal realtime provider exposing the optional warm capability."""

    supports_realtime = True
    eager_warm_as_fallback = True

    def __init__(self) -> None:
        self.calls = 0

    async def warm_transport(self, cfg) -> None:  # noqa: ANN001 - probe shape
        del cfg
        self.calls += 1


class _NoWarmProbe:
    supports_realtime = True


@pytest.mark.asyncio
async def test_warm_selected_transports_skips_providers_without_the_capability(
    monkeypatch,
) -> None:
    """A capability probe, never a provider-id check (AP-21)."""
    warm = _WarmProbe()
    loaded = {"warm-me": warm, "plain": _NoWarmProbe()}
    monkeypatch.setattr(
        factory, "_explicit_provider_ids", lambda _cfg: ["plain", "warm-me"]
    )
    monkeypatch.setattr(
        factory, "load", lambda _group, pid, protocol=None: loaded[pid]
    )

    await factory.realtime_warm_selected_transports(_cfg())

    assert warm.calls == 1


@pytest.mark.asyncio
async def test_warm_selected_transports_does_not_prewarm_a_heavy_fallback(
    monkeypatch,
) -> None:
    """An inactive fallback must not compete with the primary for GPU/RAM."""
    primary = _WarmProbe()
    fallback = _WarmProbe()
    fallback.eager_warm_as_fallback = False
    loaded = {"primary": primary, "heavy-fallback": fallback}
    monkeypatch.setattr(
        factory,
        "_explicit_provider_ids",
        lambda _cfg: ["primary", "heavy-fallback"],
    )
    monkeypatch.setattr(
        factory, "load", lambda _group, pid, protocol=None: loaded[pid]
    )

    await factory.realtime_warm_selected_transports(_cfg())

    assert primary.calls == 1
    assert fallback.calls == 0


class _PrespawnProbe:
    """Minimal realtime provider exposing the spawn-only prestart."""

    supports_realtime = True

    def __init__(self) -> None:
        self.calls = 0

    async def prespawn_transport(self, cfg) -> bool:  # noqa: ANN001 - probe shape
        del cfg
        self.calls += 1
        return True


@pytest.mark.asyncio
async def test_prespawn_reaches_every_explicit_slot(monkeypatch) -> None:
    """Unlike eager warming, the prestart is position-blind: a stone-cold
    explicitly configured FALLBACK is what stranded the 2026-08-10 first call
    when the primary was down. Providers without the capability are skipped
    (AP-21), and one broken plugin never stops the others."""
    primary = _PrespawnProbe()
    fallback = _PrespawnProbe()
    loaded = {"primary": primary, "plain": _NoWarmProbe(), "fallback": fallback}

    def _load(_group, pid, protocol=None):  # noqa: ANN001 - probe shape
        if pid == "broken":
            raise RuntimeError("plugin exploded")
        return loaded[pid]

    monkeypatch.setattr(
        factory,
        "_explicit_provider_ids",
        lambda _cfg: ["primary", "plain", "broken", "fallback"],
    )
    monkeypatch.setattr(factory, "load", _load)

    await factory.realtime_prespawn_transports(_cfg())

    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_prespawn_skips_a_pipeline_mode_install(monkeypatch) -> None:
    """A stale realtime pin must not spawn a server for a disabled feature."""
    probe = _PrespawnProbe()
    monkeypatch.setattr(factory, "_explicit_provider_ids", lambda _cfg: ["probe"])
    monkeypatch.setattr(
        factory, "load", lambda _group, _pid, protocol=None: probe
    )

    await factory.realtime_prespawn_transports(_cfg(mode="pipeline"))

    assert probe.calls == 0


@pytest.mark.asyncio
async def test_prespawn_ignores_unselected_plugins(monkeypatch) -> None:
    """An installed but unselected plugin must never spawn a process."""
    probe = _PrespawnProbe()
    monkeypatch.setattr(factory, "_explicit_provider_ids", lambda _cfg: [])
    monkeypatch.setattr(
        factory, "load", lambda _group, _pid, protocol=None: probe
    )

    await factory.realtime_prespawn_transports(_cfg())

    assert probe.calls == 0


@pytest.mark.asyncio
async def test_warm_selected_transports_survives_one_broken_provider(
    monkeypatch,
) -> None:
    """Warming is advisory: a plugin that explodes must not stop the others,
    and must never reach the caller."""
    warm = _WarmProbe()

    def _load(_group, pid, protocol=None):  # noqa: ANN001 - probe shape
        if pid == "broken":
            raise RuntimeError("plugin exploded")
        return warm

    monkeypatch.setattr(
        factory, "_explicit_provider_ids", lambda _cfg: ["broken", "warm-me"]
    )
    monkeypatch.setattr(factory, "load", _load)

    await factory.realtime_warm_selected_transports(_cfg())

    assert warm.calls == 1


@pytest.mark.asyncio
async def test_warm_selected_transports_ignores_unselected_plugins(
    monkeypatch,
) -> None:
    """An installed but unselected plugin must not spawn a process or touch a
    credential on its own."""
    warm = _WarmProbe()
    monkeypatch.setattr(factory, "_explicit_provider_ids", lambda _cfg: [])
    monkeypatch.setattr(
        factory, "load", lambda _group, _pid, protocol=None: warm
    )

    await factory.realtime_warm_selected_transports(_cfg())

    assert warm.calls == 0


@pytest.mark.asyncio
async def test_warm_selected_transports_skips_a_pipeline_mode_install(
    monkeypatch,
) -> None:
    """A stale realtime pin must not spawn a transport for a disabled feature.

    Warming a subscription transport means spawning a process, running a live
    account check, and HOLDING the profile lock — which is what made the user's
    own Codex login report "busy" on an install whose voice runs the classic
    pipeline. Gate on the same switch ``build_realtime_session`` reads.
    """
    warm = _WarmProbe()
    monkeypatch.setattr(
        factory, "_explicit_provider_ids", lambda _cfg: ["warm-me"]
    )
    monkeypatch.setattr(
        factory, "load", lambda _group, _pid, protocol=None: warm
    )

    await factory.realtime_warm_selected_transports(_cfg(mode="pipeline"))

    assert warm.calls == 0


@pytest.mark.asyncio
async def test_warm_selected_transports_still_warms_the_realtime_install(
    monkeypatch,
) -> None:
    """The mirror: the case warming EXISTS for keeps working.

    Warming is about the cold start before the first wake word, so it must
    depend on the configured mode alone — never on a call being in flight.
    """
    warm = _WarmProbe()
    monkeypatch.setattr(
        factory, "_explicit_provider_ids", lambda _cfg: ["warm-me"]
    )
    monkeypatch.setattr(
        factory, "load", lambda _group, _pid, protocol=None: warm
    )

    await factory.realtime_warm_selected_transports(
        _cfg(mode="realtime", provider="openai-realtime")
    )

    assert warm.calls == 1


@pytest.mark.asyncio
async def test_warm_selected_transports_skips_the_subscription_pipeline_profile(
    monkeypatch,
) -> None:
    """An explicit classic profile must not warm an unused duplex transport."""
    warm = _WarmProbe()
    monkeypatch.setattr(
        factory,
        "_explicit_provider_ids",
        lambda _cfg: ["warm-me"],
    )
    monkeypatch.setattr(
        factory,
        "load",
        lambda _group, _pid, protocol=None: warm,
    )

    await factory.realtime_warm_selected_transports(
        _cfg(
            mode="realtime",
            provider="openai-realtime",
            profile="codex-subscription-voice",
        )
    )

    assert warm.calls == 0


@pytest.mark.asyncio
async def test_warm_selected_transports_keeps_codex_realtime_provider_realtime(
    monkeypatch,
) -> None:
    warm = _WarmProbe()
    monkeypatch.setattr(
        factory,
        "_explicit_provider_ids",
        lambda _cfg: ["codex-subscription-realtime"],
    )
    monkeypatch.setattr(
        factory,
        "load",
        lambda _group, _pid, protocol=None: warm,
    )

    await factory.realtime_warm_selected_transports(
        _cfg(mode="realtime", provider="codex-subscription-realtime")
    )

    assert warm.calls == 1


def test_declared_handshake_budget_reaches_the_surface(monkeypatch) -> None:
    """A surface must not call a start attempt dead inside a declared budget.

    The browser gave every attempt a fixed 20 s while the subscription
    transport declares 45 s and documents 15-25 s cold starts, so a cold
    subscription call could be reported as a timed-out connection while the
    backend was still legitimately negotiating.
    """

    class _SlowStart(_BaseProvider):
        name = "codex-subscription-realtime"
        credential_candidates = ()
        handshake_budget_s = 45.0

        @classmethod
        def external_login_ready(cls):
            return True

    monkeypatch.setattr(factory, "list_plugins", lambda _group: ["codex-subscription-realtime"])
    monkeypatch.setattr(factory, "load", lambda _group, _name, protocol=None: _SlowStart)

    budget = factory.realtime_handshake_budget_s(
        _cfg(provider="codex-subscription-realtime")
    )

    assert budget == 45.0


def test_handshake_budget_never_drops_below_the_shared_default(monkeypatch) -> None:
    """A provider that declares nothing keeps the historical ceiling."""
    _fake_registry(monkeypatch, {"openai"})

    from jarvis.realtime.session import _PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S

    assert factory.realtime_handshake_budget_s(_cfg()) == pytest.approx(
        _PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S
    )


def test_a_subscription_brain_behind_subscription_voice_warns(monkeypatch, caplog) -> None:
    """AP-22: the one chain where a single 429 silences voice AND actions.

    ``codex`` without an OpenAI key runs on the SAME ChatGPT plan as the
    subscription voice. Resolving it to the generic ``openai`` family meant
    this exact configuration never produced the warning it exists for.
    """
    monkeypatch.setattr(factory, "get_secret_any", lambda _candidates: None)
    config = _cfg(provider="codex-subscription-realtime")
    config.brain.primary = "codex"
    config.brain.deep_brain = "codex"
    config.brain.routing_provider = None
    config.brain.local_fallback = None

    with caplog.at_level("WARNING"):
        factory._warn_on_same_family_delegate_chain(
            config,
            "codex-subscription-realtime",
            credential_family="openai-chatgpt-subscription",
        )

    assert any("AP-22" in record.message for record in caplog.records)


def test_a_metered_codex_brain_behind_subscription_voice_does_not_warn(
    monkeypatch, caplog
) -> None:
    """With an OpenAI key the brain bills a DIFFERENT pool — no false alarm."""
    monkeypatch.setattr(factory, "get_secret_any", lambda _candidates: "openai-key")
    config = _cfg(provider="codex-subscription-realtime")
    config.brain.primary = "codex"
    config.brain.deep_brain = None
    config.brain.routing_provider = None
    config.brain.local_fallback = None

    with caplog.at_level("WARNING"):
        factory._warn_on_same_family_delegate_chain(
            config,
            "codex-subscription-realtime",
            credential_family="openai-chatgpt-subscription",
        )

    assert not [record for record in caplog.records if "AP-22" in record.message]

"""The declared-handshake probe must survive every shape of declaration.

``realtime_handshake_budget_s`` is what stops a surface from calling a start
attempt dead while the transport is still legitimately negotiating. It used to
read the budget off the CLASS and outside the per-plugin guard, so a provider
that declares the value as an instance ``property`` (a self-hosted card asking
for its server's warm-up time) made ``float()`` raise on the descriptor object
and killed the whole probe — the browser then silently fell back to its
historical 20 s ceiling.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jarvis.realtime.factory as factory
from jarvis.realtime.session import _PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S


class _FakeProvider:
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000
    credential_candidates = (("fake_api_key", "FAKE_API_KEY"),)

    def __init__(self, *, api_key=None):
        self.api_key = api_key

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):  # pragma: no cover - never opened here
        raise NotImplementedError


class _PlainBudgetProvider(_FakeProvider):
    """Declares the budget as an ordinary class attribute."""

    name = "plain-budget-realtime"
    handshake_budget_s = 30.0


class _PropertyBudgetProvider(_FakeProvider):
    """Declares the budget as an instance property, like the local card."""

    name = "property-budget-realtime"

    @property
    def handshake_budget_s(self) -> float:
        return 135.0


class _SilentProvider(_FakeProvider):
    """Declares no budget at all."""

    name = "silent-realtime"


class _ExplodingProviderLoad(Exception):
    pass


def _cfg(*provider_ids: str, providers: dict | None = None) -> SimpleNamespace:
    ordered = [*provider_ids, None, None, None]
    return SimpleNamespace(
        voice=SimpleNamespace(mode="realtime"),
        brain=SimpleNamespace(
            reply_language="en",
            providers=providers or {},
            realtime=SimpleNamespace(
                provider=ordered[0],
                fallback_provider=ordered[1],
                fallback_provider_2=ordered[2],
            ),
        ),
    )


def _fake_registry(monkeypatch, plugins: dict, *, keys: bool = True) -> None:
    def _load(_group, name, protocol=None):
        provider_cls = plugins[name]
        if isinstance(provider_cls, type) and issubclass(provider_cls, _ExplodingProviderLoad):
            raise provider_cls("plugin import blew up")
        return provider_cls

    monkeypatch.setattr(factory, "list_plugins", lambda _group: list(plugins))
    monkeypatch.setattr(factory, "load", _load)
    monkeypatch.setattr(factory, "get_secret_any", lambda _candidates: "fake-key" if keys else None)


class _BrokenPlugin(_ExplodingProviderLoad):
    pass


def test_property_declaration_is_resolved_not_stringified(monkeypatch) -> None:
    """The bug: ``float()`` on the class attribute got the descriptor object."""
    _fake_registry(monkeypatch, {"property-budget-realtime": _PropertyBudgetProvider})

    assert factory.realtime_handshake_budget_s(_cfg("property-budget-realtime")) == pytest.approx(
        135.0
    )


def test_largest_declared_budget_wins_across_declaration_shapes(monkeypatch) -> None:
    _fake_registry(
        monkeypatch,
        {
            "plain-budget-realtime": _PlainBudgetProvider,
            "property-budget-realtime": _PropertyBudgetProvider,
            "silent-realtime": _SilentProvider,
        },
    )

    budget = factory.realtime_handshake_budget_s(
        _cfg(
            "plain-budget-realtime",
            "property-budget-realtime",
            "silent-realtime",
        )
    )

    assert budget == pytest.approx(135.0)


def test_one_broken_plugin_never_poisons_the_probe(monkeypatch) -> None:
    """A plugin that raises on load costs only its own declaration."""
    _fake_registry(
        monkeypatch,
        {
            "property-budget-realtime": _PropertyBudgetProvider,
            "broken-realtime": _BrokenPlugin,
            "plain-budget-realtime": _PlainBudgetProvider,
        },
    )

    budget = factory.realtime_handshake_budget_s(
        _cfg(
            "property-budget-realtime",
            "broken-realtime",
            "plain-budget-realtime",
        )
    )

    assert budget == pytest.approx(135.0)


def test_class_declaration_still_counts_without_a_usable_instance(
    monkeypatch,
) -> None:
    """No credential means no instance — the class read stays the fallback."""
    _fake_registry(monkeypatch, {"plain-budget-realtime": _PlainBudgetProvider}, keys=False)

    assert factory.realtime_handshake_budget_s(_cfg("plain-budget-realtime")) == pytest.approx(30.0)


def test_budget_never_drops_below_the_shared_default(monkeypatch) -> None:
    _fake_registry(monkeypatch, {"silent-realtime": _SilentProvider})

    assert factory.realtime_handshake_budget_s(_cfg("silent-realtime")) == pytest.approx(
        _PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S
    )


def test_probe_survives_a_registry_that_cannot_build_candidates(
    monkeypatch,
) -> None:
    """Instance construction failing must still leave the class read."""
    _fake_registry(monkeypatch, {"plain-budget-realtime": _PlainBudgetProvider})

    def _boom(_cfg_arg, **_kwargs):
        raise RuntimeError("candidate build failed")

    monkeypatch.setattr(factory, "_identified_provider_candidates", _boom)

    assert factory.realtime_handshake_budget_s(_cfg("plain-budget-realtime")) == pytest.approx(30.0)


def test_installed_local_plugin_declares_a_real_budget() -> None:
    """Against the REAL entry-point registry, not a fake class.

    The local card is the shipped provider that declares its budget as a
    property; a class-level read here returns the descriptor and the probe
    dies. Skipped when that plugin is not installed.
    """
    from jarvis.core.registry import list_plugins

    if "local-realtime" not in list_plugins("jarvis.realtime"):
        pytest.skip("local-realtime plugin is not installed")

    cfg = _cfg(
        "local-realtime",
        providers={
            "local-realtime": SimpleNamespace(
                base_url="http://localhost:8765",
                model="",
                launch_command="",
            )
        },
    )

    budget = factory.realtime_handshake_budget_s(cfg)

    assert budget > _PROVIDER_HANDSHAKE_TOTAL_TIMEOUT_S

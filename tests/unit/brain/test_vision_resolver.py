"""``resolve_vision_brain`` — picking a model that can actually SEE.

The gate is the ``supports_vision`` capability and nothing else (AP-21). That
matters more here than in most resolvers because a text-only provider handed an
image does not fail loudly: it answers about the filename and the sentence around
it, confidently, and the caller ships that as a description of a picture nobody
looked at.
"""
from __future__ import annotations

import pytest

from jarvis.brain import resolver


class _Blind:
    supports_vision = False


class _Seeing:
    supports_vision = True


class _Undeclared:
    """A provider that says nothing about vision at all."""


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    resolver._reset_for_tests()


def _stage(monkeypatch: pytest.MonkeyPatch, chain: list, instances: dict) -> None:
    monkeypatch.setattr(resolver, "_resolve_chain", lambda _cfg: chain)

    class _Registry:
        def instantiate(self, provider: str, **_kwargs: object) -> object:
            made = instances.get(provider)
            if made is None:
                raise RuntimeError(f"{provider} not configured")
            return made()

    monkeypatch.setattr(resolver, "_get_registry", _Registry)


def test_a_blind_provider_is_skipped_for_the_next_one_that_can_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stage(
        monkeypatch,
        [("text-only", "m1"), ("multimodal", "m2")],
        {"text-only": _Blind, "multimodal": _Seeing},
    )

    brain = resolver.resolve_vision_brain(object())

    assert isinstance(brain, _Seeing)


def test_nothing_that_can_see_returns_none_rather_than_a_blind_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stage(monkeypatch, [("text-only", "m1")], {"text-only": _Blind})

    assert resolver.resolve_vision_brain(object()) is None


def test_a_provider_that_does_not_declare_vision_is_treated_as_blind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail-closed: the caller's entire job is describing a picture, and a
    # confident description of an unseen one is worse than an honest "not
    # described".
    _stage(monkeypatch, [("mystery", "m1")], {"mystery": _Undeclared})

    assert resolver.resolve_vision_brain(object()) is None


def test_an_uninstantiable_provider_does_not_stop_the_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stage(
        monkeypatch,
        [("missing-key", None), ("multimodal", "m2")],
        {"multimodal": _Seeing},
    )

    assert isinstance(resolver.resolve_vision_brain(object()), _Seeing)


def test_a_broken_chain_is_none_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_cfg: object) -> list:
        raise RuntimeError("config is a mess")

    monkeypatch.setattr(resolver, "_resolve_chain", _boom)

    assert resolver.resolve_vision_brain(object()) is None


def test_the_choice_is_never_made_by_provider_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name that looks multimodal but declares no vision must not win.

    This is the AP-21 shape: gate on the capability, never on what the provider
    is called — the name-based version breaks for every provider nobody thought
    of when the list was written.
    """
    _stage(
        monkeypatch,
        [("gpt-vision-ultra", "m1"), ("obscure-local-thing", "m2")],
        {"gpt-vision-ultra": _Blind, "obscure-local-thing": _Seeing},
    )

    assert isinstance(resolver.resolve_vision_brain(object()), _Seeing)

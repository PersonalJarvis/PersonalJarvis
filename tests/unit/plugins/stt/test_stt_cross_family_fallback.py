"""Runtime cross-family STT fallback: the resolver half (AP-22).

``_resolve_keyed_stt_provider`` decides at BUILD time and only when a key is
entirely MISSING. A provider that HAS a key and then answers 429 / 402 / 401
mid-session was the end of the transcription — a depleted Groq key bricked
dictation for a whole session even with a valid OpenAI or Gemini key sitting in
the keyring. ``resolve_keyed_stt_fallback`` is the missing primitive: which
provider does this host actually have left, from a family that is not the one
that just failed.

The load-bearing invariant is the FAMILY one. Crossing from a rate-limited
provider to a second id that reads the SAME credential slot is not a fallback,
it is the same 429 twice — the single-provider brick AP-22 names. So "family" is
defined by the credential, never by the provider name (AP-21), and these tests
prove the resolver cannot be tricked by two ids that share a key.

No ``unittest.mock``: credential presence and entry-point registration are both
substituted with plain functions, the way the existing STT factory tests do it.
"""
from __future__ import annotations

from typing import Any

import pytest

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
from jarvis.core.config import ResolvedEndpoint, STTConfig


class _FakeCloudSTT:
    """Stands in for any registered cloud STT entry-point class."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _keys(*slots: str):
    """A ``get_secret_any`` double where ONLY ``slots`` resolve to a credential."""

    def _fake(candidates) -> str | None:
        names = {c[0] for c in candidates}
        return "real-key" if names & set(slots) else None

    return _fake


@pytest.fixture(autouse=True)
def _no_proxy_all_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct mode (no team proxy) and every family registered as an entry-point.

    Both are prerequisites of the thing under test rather than the thing itself:
    the proxy would hand ``groq-api`` a credential it has not got, and an
    unregistered family is skipped for a different, separately tested reason.
    """
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(
            base_url=None, credential=None, via_proxy=False
        ),
    )
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)


# ---------------------------------------------------------------------------
# The family definition
# ---------------------------------------------------------------------------


def test_family_is_the_credential_slot_not_the_provider_name() -> None:
    """Ids that read one keyring entry are ONE family, however they are spelled."""
    assert stt_pkg.stt_family_id("groq-api") == "groq_api_key"
    assert stt_pkg.stt_family_id("openrouter-stt") == "openrouter_api_key"
    assert stt_pkg.stt_family_id("openai-api") == "openai_api_key"
    assert stt_pkg.stt_family_id("gemini-api") == "gemini_api_key"
    # The key-free local engine has no credential to exhaust.
    assert stt_pkg.stt_family_id("faster-whisper") == "local"
    # An unknown / third-party id is its own family: we cannot prove it shares a
    # credential with anything, and dropping it would lose a working provider.
    assert stt_pkg.stt_family_id("some-third-party-stt") == "some-third-party-stt"


def test_the_shipped_cross_family_order_really_is_one_per_family() -> None:
    """A future second id for an existing vendor must not slip into the order.

    Asserted non-empty first, so a table that stopped parsing fails loudly
    instead of passing trivially.
    """
    order = stt_pkg._STT_CROSS_FAMILY_ORDER
    assert len(order) >= 2, "the cross-family order is empty — nothing was checked"

    families = [stt_pkg.stt_family_id(name) for name in order]
    assert len(set(families)) == len(families), (
        f"two shipped STT ids share one credential family: {families}"
    )


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def test_it_crosses_to_the_other_families_the_user_has_a_key_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A depleted Groq key must reach the OpenAI and Gemini keys in the keyring."""
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )

    chain = stt_pkg.resolve_keyed_stt_fallback("groq-api")

    assert chain == ("openai-api", "gemini-api"), (
        "the chain must offer every OTHER keyed family, in the shipped order, "
        "and never the one that just failed"
    )


def test_one_keyed_family_gives_an_honest_empty_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No other key means no fallback — say so, do not invent one.

    An empty tuple is the honest answer: the caller then degrades exactly as it
    does today (an honest message, or the key-free local floor). Promising a
    provider the host cannot authenticate would turn one failure into two.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key"))

    assert stt_pkg.resolve_keyed_stt_fallback("groq-api") == ()


def test_it_never_returns_two_providers_from_one_credential_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ids sharing a key are the same 429 twice — the AP-22 brick.

    Simulated with a second id pointed at the OpenAI credential slot, which is
    exactly the shape a future ``openai-realtime-stt`` (or a renamed provider
    kept as an alias) would take.
    """
    monkeypatch.setitem(
        stt_pkg._STT_SECRET_CANDIDATES,
        "openai-second-id",
        (("openai_api_key", "OPENAI_API_KEY"),),
    )
    monkeypatch.setattr(
        stt_pkg,
        "_STT_CROSS_FAMILY_ORDER",
        ("groq-api", "openai-api", "openai-second-id", "gemini-api"),
    )
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )

    chain = stt_pkg.resolve_keyed_stt_fallback("groq-api")

    assert chain == ("openai-api", "gemini-api")
    families = [stt_pkg.stt_family_id(name) for name in chain]
    assert len(set(families)) == len(families)


def test_excluding_a_family_removes_every_id_that_shares_its_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session that already burned a family must not walk back into it.

    ``exclude_family`` takes a provider id or a family id, because the caller
    knows the provider it just tried, not the keyring slot behind it.
    """
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )

    by_provider_id = stt_pkg.resolve_keyed_stt_fallback(
        "groq-api", exclude_family="openai-api"
    )
    by_family_id = stt_pkg.resolve_keyed_stt_fallback(
        "groq-api", exclude_family=("openai_api_key",)
    )

    assert by_provider_id == ("gemini-api",)
    assert by_family_id == ("gemini-api",)


def test_a_keyed_but_unregistered_family_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never promise a provider we cannot build — the caller would just crash."""
    monkeypatch.setattr(
        stt_pkg,
        "_load_provider_class",
        lambda name: _FakeCloudSTT if name == "gemini-api" else None,
    )
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )

    assert stt_pkg.resolve_keyed_stt_fallback("groq-api") == ("gemini-api",)


def test_the_local_engine_is_not_part_of_this_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """faster-whisper is a floor, not a family.

    It is key-free, so a credential-derived chain would always end on it — but
    it is absent from a base/headless install and slow without a GPU, so it
    belongs at the END of the caller's own ordering
    (``jarvis.speech.stt_fallback.alternate_provider_names``), not in the middle
    of a credential chain.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))

    assert "faster-whisper" not in stt_pkg.resolve_keyed_stt_fallback("groq-api")


def test_an_unknown_current_provider_still_gets_the_whole_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A third-party STT that fails must still reach the user's real keys."""
    monkeypatch.setattr(cfg, "get_secret_any", _keys("openai_api_key"))

    assert stt_pkg.resolve_keyed_stt_fallback("some-third-party-stt") == ("openai-api",)


# ---------------------------------------------------------------------------
# The promise the chain makes: every entry is buildable
# ---------------------------------------------------------------------------


def test_every_chain_entry_can_actually_be_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain is names, not instances (AP-26) — but the names must resolve.

    Building on demand is what keeps a model load off the caller's path; it also
    means a name that cannot be built would only blow up at the worst possible
    moment, mid-failure. So the resolver's registration check is verified here
    against the real build entry point.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))

    chain = stt_pkg.resolve_keyed_stt_fallback("groq-api")
    assert chain, "expected at least one alternate for this key set"

    for name in chain:
        built = stt_pkg.build_named_stt_provider(name, STTConfig(provider="groq-api"))
        assert isinstance(built, _FakeCloudSTT)

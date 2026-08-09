"""Runtime cross-family STT fallback: the resolver half, and both consumers.

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

The resolver was shipped with ONE consumer — the dictation lane — which left
AP-22 half-fixed: a voice turn whose provider answered 429 or 401 still ended in
the spoken apology with two other keyed families sitting untouched in the
keyring. The second half of this file pins the voice lane's crossover, including
the property that made it safe to add at all: a working provider does exactly
what it did before, with no resolution work per turn.

No ``unittest.mock``: credential presence and entry-point registration are both
substituted with plain functions, the way the existing STT factory tests do it.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.config import ResolvedEndpoint, STTConfig
from jarvis.core.protocols import Transcript
from jarvis.speech.pipeline import SpeechPipeline


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


@pytest.fixture(autouse=True)
def _cloud_recognizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the configured recognizer to a CLOUD one for every case by default.

    ``_resolve_stt_fallback_chain`` now asks the polish client's
    ``stt_runs_on_device`` before it crosses automatically, and that predicate
    reads the HOST's config file. Without this pin the whole file would answer
    differently on a contributor who runs faster-whisper — which is the "tests
    pin an arbitrary value, never the host's live configuration" rule this repo
    already applies to the wake word. The privacy floor is the subject of its
    own section at the end, and those cases set the predicate themselves.
    """
    from jarvis.dictation import polish_client

    monkeypatch.setattr(polish_client, "stt_runs_on_device", lambda: False)


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


# ---------------------------------------------------------------------------
# Consumer 2: the VOICE lane's final transcription
# ---------------------------------------------------------------------------


class _HTTPRefusal(RuntimeError):
    """A provider refusal carrying the status the classifier reads.

    ``.status`` is the shape ``jarvis.plugins.stt.errors.STTHTTPError`` exposes,
    which is the first thing ``_stt_error_status`` looks for — so this behaves
    like a real provider error without importing one provider's client library
    into a test about provider independence.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"provider refused with {status}")
        self.status = status


class _RefusingSTT:
    """A configured provider that always answers with the same status."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, **_kwargs: Any) -> Transcript:
        self.calls += 1
        raise _HTTPRefusal(self.status)


class _WorkingSTT:
    """The alternate family: answers, and counts how often it was asked."""

    def __init__(self, text: str = "the words the user actually said") -> None:
        self.text = text
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, **_kwargs: Any) -> Transcript:
        self.calls += 1
        return Transcript(text=self.text, language="en", confidence=0.9)


class _BusyThenWorkingSTT:
    """A native engine whose cosmetic preview still owns its inference lock."""

    def __init__(self, busy_calls: int = 2) -> None:
        self.busy_calls = busy_calls
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, **_kwargs: Any) -> Transcript:
        self.calls += 1
        if self.calls <= self.busy_calls:
            raise RuntimeError("a transcription is already in flight on this model")
        return Transcript(text="the final transcript", language="en", confidence=0.9)


def _voice_pipeline(primary: Any, *, fallback: str = "auto") -> SpeechPipeline:
    """A pipeline with just enough state for ``_transcribe_final``."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = primary
    pipe._stt_final_timeout_s = 2.0
    pipe._config = SimpleNamespace(
        stt=STTConfig(provider="groq-api", fallback=fallback)
    )
    pipe._voice_stt_fallback_chain = None
    pipe._voice_stt_fallback_instances = {}
    return pipe


@pytest.fixture()
def _instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the retry backoff so the ladder does not make the suite wait.

    The delays are the subject of their own tests; here they would only add
    seconds to a case about which PROVIDER answers.
    """
    monkeypatch.setattr(pipeline_mod, "_stt_retry_delay", lambda exc, attempt: 0.0)


@pytest.mark.asyncio
async def test_a_rate_limit_that_survives_the_ladder_crosses_family(
    monkeypatch: pytest.MonkeyPatch, _instant_retries: None
) -> None:
    """The voice half of AP-22, which shipped missing.

    A 429 that outlives the retry ladder used to end the turn in the spoken
    apology — with the user's OpenAI key sitting right there.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))
    alternate = _WorkingSTT()
    built: list[str] = []

    def _build(name: str, _cfg: Any) -> Any:
        built.append(name)
        return alternate

    monkeypatch.setattr(stt_pkg, "build_named_stt_provider", _build)

    primary = _RefusingSTT(429)
    pipe = _voice_pipeline(primary)
    result = await pipe._transcribe_final(b"\x00\x01" * 512)

    assert result is not None, "the turn was lost despite a second keyed family"
    assert result.text == "the words the user actually said"
    assert built == ["openai-api"], built
    # The user's own provider still got the whole ladder before we crossed.
    assert primary.calls == pipeline_mod._STT_FINAL_RETRIES + 1


@pytest.mark.asyncio
async def test_a_busy_native_preview_is_retried_instead_of_dropping_the_turn(
    _instant_retries: None,
) -> None:
    """A cancelled asyncio wrapper does not stop an in-flight native preview."""
    stt = _BusyThenWorkingSTT()
    pipe = _voice_pipeline(stt)

    result = await pipe._transcribe_final(b"\x00\x01" * 512)

    assert result is not None
    assert result.text == "the final transcript"
    assert stt.calls == pipeline_mod._STT_FINAL_RETRIES + 1


@pytest.mark.asyncio
async def test_a_dead_key_crosses_immediately_without_retrying_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """401 is final for THIS key and says nothing about the other families.

    Retrying it is 1.8 s of the user waiting to be told what the first answer
    already said, so the crossover is reached from the fail-fast branch too.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "gemini_api_key"))
    alternate = _WorkingSTT()
    monkeypatch.setattr(
        stt_pkg, "build_named_stt_provider", lambda name, _cfg: alternate
    )

    primary = _RefusingSTT(401)
    pipe = _voice_pipeline(primary)
    result = await pipe._transcribe_final(b"\x00\x01" * 512)

    assert result is not None
    assert primary.calls == 1, "a rejected key must not be asked three times"
    assert alternate.calls == 1


@pytest.mark.asyncio
async def test_a_rejected_request_never_crosses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 means the provider understood the audio and refused it.

    Another provider would refuse the same bytes and charge a second call to
    say so, so this must stay the honest ``None`` the caller apologises for.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))
    alternate = _WorkingSTT()
    monkeypatch.setattr(
        stt_pkg, "build_named_stt_provider", lambda name, _cfg: alternate
    )

    pipe = _voice_pipeline(_RefusingSTT(400))
    assert await pipe._transcribe_final(b"\x00\x01" * 512) is None
    assert alternate.calls == 0


@pytest.mark.asyncio
async def test_one_keyed_family_degrades_exactly_as_it_does_today(
    monkeypatch: pytest.MonkeyPatch, _instant_retries: None
) -> None:
    """No second key means no crossover — and no crash, and no invented one."""
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key"))

    def _never(name: str, _cfg: Any) -> Any:
        raise AssertionError(f"built {name} with no key for it")

    monkeypatch.setattr(stt_pkg, "build_named_stt_provider", _never)

    pipe = _voice_pipeline(_RefusingSTT(429))
    assert await pipe._transcribe_final(b"\x00\x01" * 512) is None


@pytest.mark.asyncio
async def test_the_happy_path_does_no_resolution_work_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property that made this safe to add to the voice lane.

    A working provider must behave exactly as before: no keyring read, no
    entry-point scan, no alternate constructed — the crossover is machinery for
    a turn that has already failed, and a turn that has not must not pay a
    millisecond for it.
    """

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the happy path resolved a fallback chain")

    monkeypatch.setattr(stt_pkg, "resolve_keyed_stt_fallback", _explode)
    monkeypatch.setattr(stt_pkg, "build_named_stt_provider", _explode)
    monkeypatch.setattr(cfg, "get_secret_any", _explode)

    pipe = _voice_pipeline(_WorkingSTT("hello there"))
    result = await pipe._transcribe_final(b"\x00\x01" * 512)

    assert result is not None and result.text == "hello there"
    assert pipe._voice_stt_fallback_chain is None, "the chain was resolved anyway"
    assert pipe._voice_stt_fallback_instances == {}


@pytest.mark.asyncio
async def test_the_chain_is_resolved_once_and_then_remembered(
    monkeypatch: pytest.MonkeyPatch, _instant_retries: None
) -> None:
    """Including the EMPTY answer.

    A single-key install fails every turn while its provider is down; if the
    empty chain were not cached, each of those turns would re-read the keyring
    — which on a host with a locked Secret Service is seconds of blocking on
    the loop the microphone is drained on.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key"))
    resolutions: list[str] = []
    real = stt_pkg.resolve_keyed_stt_fallback

    def _counting(current_id: str, **kwargs: Any):
        resolutions.append(current_id)
        return real(current_id, **kwargs)

    monkeypatch.setattr(stt_pkg, "resolve_keyed_stt_fallback", _counting)

    pipe = _voice_pipeline(_RefusingSTT(429))
    await pipe._transcribe_final(b"\x00\x01" * 512)
    await pipe._transcribe_final(b"\x00\x01" * 512)

    assert resolutions == ["groq-api"], resolutions
    assert pipe._voice_stt_fallback_chain == ()


# ---------------------------------------------------------------------------
# ``[stt].fallback`` — the switch that was documented and never read
# ---------------------------------------------------------------------------


def test_auto_asks_the_key_aware_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )
    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="groq-api", fallback="auto"), "groq-api"
    )
    assert chain == ("openai-api", "gemini-api")


def test_an_empty_fallback_setting_disables_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some people would rather see an error than have their audio sent
    somewhere they did not choose. The setting has to mean that."""
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key")
    )
    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="groq-api", fallback=""), "groq-api"
    )
    assert chain == ()


def test_a_pinned_provider_wins_over_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name the user typed is an instruction, not a suggestion."""
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )
    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="groq-api", fallback="gemini-api"), "groq-api"
    )
    assert chain == ("gemini-api",)


def test_pinning_the_configured_provider_is_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crossing to yourself is the same failure twice."""
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key"))
    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="groq-api", fallback="groq-api"), "groq-api"
    )
    assert chain == ()


# ---------------------------------------------------------------------------
# The privacy floor: an on-device recognizer keeps the AUDIO on the device
# ---------------------------------------------------------------------------
#
# The dictation polish pass already refuses to send a TRANSCRIPT to a cloud
# model when the configured recognizer transcribes here. The crossover was free
# to send the RAW AUDIO of exactly that install to a cloud speech-to-text
# family — the worse half of the same question, since a recording carries the
# voice itself and whatever else was audible in the room. Both lanes now ask
# ONE predicate, so they cannot answer it differently.


@pytest.fixture()
def _on_device_recognizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured recognizer transcribes on this machine."""
    from jarvis.dictation import polish_client

    monkeypatch.setattr(polish_client, "stt_runs_on_device", lambda: True)


def test_an_on_device_recognizer_refuses_the_automatic_crossover(
    monkeypatch: pytest.MonkeyPatch, _on_device_recognizer: None
) -> None:
    """Every id the auto chain can offer is a cloud family, so it offers none.

    The user holds three cloud keys and the resolver would happily list them —
    but they chose a recognizer that never puts their voice on a network, and
    an automatic upload on the first 429 is not a fallback they agreed to.
    """
    monkeypatch.setattr(
        cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key", "gemini_api_key")
    )
    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="faster-whisper", fallback="auto"), "faster-whisper"
    )
    assert chain == ()


def test_an_explicit_pin_still_crosses_from_an_on_device_recognizer(
    monkeypatch: pytest.MonkeyPatch, _on_device_recognizer: None
) -> None:
    """A typed provider id is a decision, and a decision is honoured.

    This is the line between a safe DEFAULT and a policy: refusing to obey a
    setting because we think the user chose badly is how a setting stops being
    a setting — and it is the same line the polish pass draws for a pinned
    cloud family.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("openai_api_key"))
    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="faster-whisper", fallback="openai-api"), "faster-whisper"
    )
    assert chain == ("openai-api",)


def test_an_unanswerable_recognizer_keeps_the_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails CLOSED, because the two mistakes are not symmetric.

    Guessing "local" costs a crossover on a turn the user already heard fail.
    Guessing "cloud" uploads the recording of somebody who picked an on-device
    recognizer to prevent exactly that, and nothing on screen would say so.
    """
    from jarvis.dictation import polish_client

    def _unreadable() -> bool:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(polish_client, "stt_runs_on_device", _unreadable)
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))

    chain = pipeline_mod._resolve_stt_fallback_chain(
        STTConfig(provider="groq-api", fallback="auto"), "groq-api"
    )
    assert chain == ()


def test_both_lanes_ask_one_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the fix: no second reading of "does this run here".

    Pinning the polish client's predicate — and nothing else — must change the
    voice lane's answer, which is only true while the voice lane delegates to
    it instead of re-deriving the same thing from ``[stt].provider``.
    """
    from jarvis.dictation import polish_client

    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))
    stt_cfg = STTConfig(provider="groq-api", fallback="auto")

    monkeypatch.setattr(polish_client, "stt_runs_on_device", lambda: False)
    assert pipeline_mod._resolve_stt_fallback_chain(stt_cfg, "groq-api") == ("openai-api",)

    monkeypatch.setattr(polish_client, "stt_runs_on_device", lambda: True)
    assert pipeline_mod._resolve_stt_fallback_chain(stt_cfg, "groq-api") == ()


@pytest.mark.asyncio
async def test_the_voice_lane_degrades_instead_of_uploading_the_recording(
    monkeypatch: pytest.MonkeyPatch, _on_device_recognizer: None
) -> None:
    """End to end: a dead local turn stays dead rather than crossing to a cloud.

    ``_transcribe_final`` reaches its crossover on a 401 exactly as before; what
    changed is that for an on-device install the chain it gets is empty, so the
    caller speaks its apology and no audio leaves the machine.
    """
    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))

    def _never(name: str, _cfg: Any) -> Any:
        raise AssertionError(f"built {name} — the audio was about to be uploaded")

    monkeypatch.setattr(stt_pkg, "build_named_stt_provider", _never)

    pipe = _voice_pipeline(_RefusingSTT(401))
    pipe._config = SimpleNamespace(
        stt=STTConfig(provider="faster-whisper", fallback="auto")
    )

    assert await pipe._transcribe_final(b"\x00\x01" * 512) is None
    assert pipe._voice_stt_fallback_chain == ()


def test_the_boot_wrap_does_not_arm_a_cloud_ladder_on_an_on_device_install(
    monkeypatch: pytest.MonkeyPatch, _on_device_recognizer: None
) -> None:
    """The earliest and widest of the automatic doors, closed by the same rule.

    ``wrap_stt_with_fallback`` puts a cloud ladder under the utterance provider
    EVERY voice turn uses, and for a locally-configured user every rung of it is
    a cloud family (the local engine is appended only when it is not already the
    configured provider). Closing the last-resort crossover while leaving this
    one open would have fixed the smaller half.
    """
    import jarvis.speech.stt_fallback as stt_fallback

    monkeypatch.setattr(cfg, "get_secret_any", _keys("groq_api_key", "openai_api_key"))

    def _never(provider: Any, stt_cfg: Any) -> Any:
        raise AssertionError("a cloud ladder was armed for an on-device recognizer")

    monkeypatch.setattr(stt_fallback, "wrap_stt_with_fallback", _never)

    # The guard in the constructor, exercised without building a whole pipeline:
    # it is the only thing standing between this config and the wrapper above.
    assert pipeline_mod._stt_crossover_would_leave_the_machine() is True

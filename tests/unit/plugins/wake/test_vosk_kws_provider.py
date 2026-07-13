"""The any-word Vosk grammar KWS provider: confirm contract, gates, firing.

CI has no vosk model (and often no vosk wheel), so a fake ``vosk`` module is
injected into ``sys.modules``; the provider's lazy in-method imports pick it
up. What is pinned here:

- ``sound_confirm`` preserves sound-close ASR mis-hearings while requiring
  independent prefix/core evidence, so unrelated speech cannot confirm a
  grammar hallucination.
- A grammar partial hit fires the keyword only after the free-decode confirm.
- Unverified grammar candidates stay internal and have no UI callback.
- Near-silent candidates are rejected on raw ENERGY (never transcript).
- A confirm infrastructure error fails OPEN (never eats a real wake).
- The wake detector yields the canonical keyword, never transcript text.
- The grammar re-score and the free decode run CONCURRENTLY (spawn-latency
  mission 2026-07-10): they are independent passes over the same audio, so
  paying their SUM instead of their MAX is a pure latency regression.
- Rejected-candidate storms are backpressured so recall-biased grammar cannot
  starve the desktop UI, microphone, or local server, while one immediate
  user retry remains latched instead of landing in a deaf period.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import types
from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider, sound_confirm

# --- sound_confirm ------------------------------------------------------------


def test_sound_confirm_accepts_sound_close_mishearing() -> None:
    assert sound_confirm("hey ruben", "Hey Ruben") is True
    assert sound_confirm("hey oben", "Hey Ruben") is True
    assert sound_confirm("hey nowa", "Hey Nova") is True
    assert sound_confirm("hey joe avis", "Hey Jarvis") is True


@pytest.mark.parametrize(
    ("heard", "phrase"),
    (
        ("herum", "Hey Ruben"),
        ("erhoben", "Hey Ruben"),
        ("henowa", "Hey Nova"),
        ("helatlas", "Hello Atlas"),
    ),
)
def test_sound_confirm_accepts_generic_merged_full_phrase(
    heard: str, phrase: str
) -> None:
    """A one-token ASR merge still carries independent prefix/core evidence."""
    assert sound_confirm(heard, phrase) is True


@pytest.mark.parametrize(
    "heard",
    (
        "age avis",
        "a jarvis",
        "page avis",
        "pay jarvis",
    ),
)
def test_sound_confirm_accepts_live_garbled_prefix_wakes(heard: str) -> None:
    """A free decoder may preserve the core but lose the short prefix."""
    assert sound_confirm(heard, "Hey Jarvis") is True


def test_sound_confirm_rejects_unrelated_speech() -> None:
    assert sound_confirm("vielen dank", "Hey Ruben") is False  # i18n-allow: utterance under test


@pytest.mark.parametrize(
    "heard",
    (
        "hi servers sichern",  # i18n-allow: forensic STT transcript
        "ein jahr bis",  # i18n-allow: forensic STT transcript
        "k passgenau ein paar beispiele was nicht",  # i18n-allow: forensic STT transcript
        "heiss services",  # i18n-allow: forensic STT transcript
    ),
)
def test_sound_confirm_rejects_live_false_wake_transcripts(heard: str) -> None:
    """Production negatives that the old flattened 0.55 comparison accepted."""
    assert sound_confirm(heard, "Hey Jarvis") is False


def test_alternative_known_prefix_still_needs_a_matching_core() -> None:
    assert sound_confirm("hi jarvis", "Hey Jarvis") is True
    assert sound_confirm("hallo jarvis", "Hey Jarvis") is True
    assert sound_confirm("hi servers", "Hey Jarvis") is False
    assert sound_confirm("hey room", "Hey Ruben") is False


def test_garbled_prefix_rescue_does_not_fish_the_core_from_longer_speech() -> None:
    assert sound_confirm("we discussed jarvis today", "Hey Jarvis") is False


def test_merged_phrase_rescue_never_accepts_the_bare_core() -> None:
    """The configured full phrase remains mandatory after a one-token merge."""
    assert sound_confirm("ruben", "Hey Ruben") is False
    assert sound_confirm("atlas", "Hello Atlas") is False


def test_unprefixed_wake_requires_strong_core_evidence() -> None:
    assert sound_confirm("joseph", "Joseph") is True
    assert sound_confirm("das ist etwas", "Joseph") is False  # i18n-allow: utterance under test


def test_sound_confirm_rejects_empty_free_transcript() -> None:
    # The free ear heard NOTHING — the grammar hit was noise, not speech.
    assert sound_confirm("", "Hey Ruben") is False


def test_sound_confirm_finds_phrase_inside_longer_speech() -> None:
    # i18n-allow: German utterance under test
    assert sound_confirm("ich sagte hey ruben gerade eben", "Hey Ruben") is True


# --- fake vosk runtime ----------------------------------------------------------


def _timed_words(text: str, conf: float, start: float = 0.5) -> list[dict]:
    out = []
    t = start
    for w in text.split():
        out.append({"word": w, "start": t, "end": t + 0.3, "conf": conf})
        t += 0.35
    return out


class _FakeRecognizer:
    """Scriptable KaldiRecognizer stand-in.

    Grammar mode (grammar arg passed): after ``fire_after`` chunks the partial
    contains the phrase; FinalResult re-hears the phrase with timed words at
    ``model.grammar_conf`` (the verify re-score input). Free mode (no
    grammar): FinalResult returns ``model.free_text`` with timed words in the
    same span — the knob the confirm-path tests turn.
    """

    def __init__(self, model, rate, grammar=None):  # noqa: ANN001
        self._model = model
        self._grammar = grammar
        self._chunks = 0

    def SetWords(self, flag):  # noqa: ANN001, N802
        pass

    def Reset(self):  # noqa: N802 — consumed by the prewarm factory
        self.was_reset = True
        self._chunks = 0

    def AcceptWaveform(self, pcm):  # noqa: ANN001, N802
        self._chunks += 1
        return False  # partial path only — finals are exercised via partials

    def PartialResult(self):  # noqa: N802
        if self._grammar is not None and self._chunks >= self._model.fire_after:
            return json.dumps({"partial": self._model.phrase.lower()})
        return json.dumps({"partial": ""})

    def Result(self):  # noqa: N802
        return json.dumps({"text": ""})

    def FinalResult(self):  # noqa: N802
        if self._grammar is not None:
            phrase = self._model.phrase.lower()
            return json.dumps({
                "text": phrase,
                "result": _timed_words(phrase, self._model.grammar_conf),
            })
        return json.dumps({
            "text": self._model.free_text,
            "result": _timed_words(self._model.free_text, 0.9),
        })


class _FakeModel:
    def __init__(self, path):  # noqa: ANN001
        self.path = path
        self.phrase = "hey nova"
        self.free_text = "hey nova"
        self.grammar_conf = 0.95
        self.fire_after = 3


@pytest.fixture()
def fake_vosk(monkeypatch):
    mod = types.ModuleType("vosk")
    # "model" = the most recently built model (single-model tests);
    # "models" = every model by path (multi-model tests).
    state = {"model": None, "models": {}}

    def _model_factory(path):  # noqa: ANN001
        state["model"] = _FakeModel(path)
        state["models"][path] = state["model"]
        return state["model"]

    mod.Model = _model_factory
    mod.KaldiRecognizer = _FakeRecognizer
    mod.SetLogLevel = lambda *_a: None
    monkeypatch.setitem(sys.modules, "vosk", mod)
    return state


def _chunk(value: int = 6000, n: int = 1600) -> AudioChunk:
    arr = np.full(n, value, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


def _silent_chunk(n: int = 1600) -> AudioChunk:
    return AudioChunk(pcm=b"\x00\x00" * n, sample_rate=16000, timestamp_ns=0)


async def _run_detect(provider: VoskKwsProvider, chunks: list[AudioChunk]) -> list[str]:
    async def _iter() -> AsyncIterator[AudioChunk]:
        for c in chunks:
            yield c

    fired: list[str] = []

    async def _drive() -> None:
        async for kw in provider.detect(_iter()):
            fired.append(kw)

    await asyncio.wait_for(_drive(), timeout=5.0)
    return fired


# --- detection loop -------------------------------------------------------------


async def test_partial_hit_with_confirm_fires_the_keyword(fake_vosk) -> None:
    # partial hit at chunk 3, then 0.6 s (6 chunks) of tail land in the ring
    # before the confirm runs — the free decoder must see the WHOLE phrase.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == ["nova"]
    assert p.stats()["fired"] == 1


async def test_confirm_rejection_suppresses_the_fire(fake_vosk) -> None:
    # The free ear hears unrelated speech — grammar pulled noise onto the phrase.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    fake_vosk_model_free_text = "das ist etwas ganz anderes"  # i18n-allow: utterance under test
    # model instance is created lazily inside detect; patch via the factory state
    fired: list[str] = []

    async def _late_set() -> None:
        # wait until the model exists, then set its free text before the hit
        for _ in range(50):
            if fake_vosk["model"] is not None:
                fake_vosk["model"].free_text = fake_vosk_model_free_text
                return
            await asyncio.sleep(0.01)

    async def _drive() -> None:
        async def _iter() -> AsyncIterator[AudioChunk]:
            for _ in range(12):
                await asyncio.sleep(0.005)
                yield _chunk()

        async for kw in p.detect(_iter()):
            fired.append(kw)

    await asyncio.wait_for(asyncio.gather(_late_set(), _drive()), timeout=5.0)
    assert fired == []
    assert p.stats()["suppressed_confirm"] >= 1


async def test_near_silent_candidate_is_gated_on_energy(fake_vosk) -> None:
    # AP-27: silence suppression happens on raw RMS at the match site — even
    # though the fake grammar "hears" the phrase, an all-zero window must gate.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    fired = await _run_detect(p, [_silent_chunk() for _ in range(12)])
    assert fired == []
    assert p.stats()["gated_rms"] >= 1


# --- early-candidate boundary (mini-verify gated) ------------------------------


async def test_raw_stage_one_hit_never_reaches_the_early_listener(fake_vosk) -> None:
    """Stage-one grammar hits stay internal (revert 5fe5c4d2): the ONLY
    outward candidate signal is the mini-verify-gated early candidate.

    A weak re-score (conf 0.2) means the mini-verify rejects — the listener
    must hear NOTHING even though stage one fired. This replaces the older
    "no callback parameter exists" guard: the boundary is behavioural now —
    unverified candidates never cross, verified ones may.
    """
    events: list[bool] = []

    async def _listener(active: bool) -> None:
        events.append(active)

    p = VoskKwsProvider(
        "Hey Nova", model_path="fake", keyword="nova",
        early_candidate_listener=_listener,
    )
    p._ensure_model()
    fake_vosk["model"].grammar_conf = 0.2  # weak re-hear -> mini-verify rejects
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == []
    assert events == []


async def test_early_candidate_fires_after_mini_verify_then_wake(fake_vosk) -> None:
    # Calibration 2026-07-11 (400 pos / 2500 neg real windows, "hey jarvis"):
    # conf-gate alone leaks 0.84-0.92% of room-speech windows to the bar, the
    # mini-verify (re-score conf + span RMS + localized sound confirm on the
    # TRUNCATED ring) leaks 0 of 2500. The early candidate therefore runs the
    # full mini-verify, and a passing one shows the bar BEFORE the 0.6s
    # confirm tail + authoritative verify complete.
    events: list[bool] = []

    async def _listener(active: bool) -> None:
        events.append(active)

    p = VoskKwsProvider(
        "Hey Nova", model_path="fake", keyword="nova",
        early_candidate_listener=_listener,
    )
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == ["nova"]
    assert events == [True]  # shown once; the wake event supersedes (no retract)
    assert p.stats()["early_shown"] == 1
    assert p.stats()["early_retracted"] == 0
    # The shown flag survives the fire for the pipeline to CONSUME (it needs
    # to retract the bar if it silently drops this wake), then resets.
    assert p.consume_early_candidate() is True
    assert p.early_candidate_active is False
    assert p.consume_early_candidate() is False


async def test_early_candidate_retracts_when_confirm_rejects(
    fake_vosk, monkeypatch
) -> None:
    # The bar must never stay stuck on a candidate the authoritative verify
    # rejects: shown -> retracted, in that order.
    events: list[bool] = []

    async def _listener(active: bool) -> None:
        events.append(active)

    p = VoskKwsProvider(
        "Hey Nova", model_path="fake", keyword="nova",
        early_candidate_listener=_listener,
    )
    monkeypatch.setattr(p, "_early_check", lambda window, model_path=None: True)
    p._ensure_model()
    fake_vosk["model"].free_text = "das ist etwas ganz anderes"  # i18n-allow: utterance under test
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == []
    assert events == [True, False]
    assert p.stats()["early_shown"] == 1
    assert p.stats()["early_retracted"] == 1


def test_early_check_fails_closed_on_infra_error(fake_vosk, monkeypatch) -> None:
    # Polarity contract: the AUTHORITATIVE confirm fails OPEN (never eat a
    # real wake), the VISUAL-ONLY early check fails CLOSED (never flash the
    # bar on a broken verifier).
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")

    def _boom():
        raise RuntimeError("confirm infra down")

    monkeypatch.setattr(p, "_ensure_model", _boom)
    assert p._early_check(np.full(1600, 0.2, dtype=np.float32)) is False
    assert p._verify_candidate(np.full(1600, 0.2, dtype=np.float32)) is True


def test_confirm_infrastructure_error_fails_open(fake_vosk, monkeypatch) -> None:
    # A broken confirm must never eat a real wake (mirrors the echo-confirm
    # contract on the stt_match path): _verify_candidate returns True on ANY
    # infrastructure exception.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")

    def _boom():
        raise RuntimeError("confirm infra down")

    monkeypatch.setattr(p, "_ensure_model", _boom)
    assert p._verify_candidate(np.full(1600, 0.2, dtype=np.float32)) is True


def test_low_rescore_confidence_suppresses(fake_vosk) -> None:
    # Live forensic 2026-07-06: the streaming PARTIAL has no confidence (its
    # 1.00 placeholder passed the gate), so room speech fired constantly.
    # The verify re-score must supply a REAL confidence and gate on it.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    p._ensure_model()
    fake_vosk["model"].grammar_conf = 0.2  # weak re-hear -> not a wake
    assert p._verify_candidate(np.full(48000, 0.2, dtype=np.float32)) is False


def test_free_words_outside_the_span_cannot_confirm(fake_vosk) -> None:
    # The sound confirm must judge what was said AT the candidate's position;
    # a sound-close pair elsewhere in the 3 s window must not count.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    p._ensure_model()
    m = fake_vosk["model"]
    m.free_text = "ganz andere worte hier hey nowa"  # i18n-allow: utterance under test
    # free words start at 0.5s and stride 0.35s -> "hey nowa" sits ~1.9-2.6s,
    # far outside the grammar span (0.5-1.15s +-0.3) -> localised confirm
    # sees only unrelated words and rejects.
    assert p._verify_candidate(np.full(48000, 0.2, dtype=np.float32)) is False


async def test_multi_model_second_model_fires_when_first_is_deaf(fake_vosk) -> None:
    """Union recall (measured +38% on the fixture corpus, 2026-07-11): a
    phrase the primary language model cannot hear ("Hey Jarvis" on the de
    model → 'hey [unk]') must still fire through a sibling model that can.
    The verify runs against the model that HEARD the candidate.
    """
    p = VoskKwsProvider(
        "Hey Nova",
        model_path="deaf-model",
        model_paths=["deaf-model", "hearing-model"],
        keyword="nova",
    )
    p._ensure_model("deaf-model")
    p._ensure_model("hearing-model")
    fake_vosk["models"]["deaf-model"].fire_after = 10**9  # never hears
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == ["nova"]
    assert p.stats()["fired"] == 1


async def test_sibling_rescue_fires_when_primary_verify_rejects(fake_vosk) -> None:
    """First-hit-wins must not eat the union: when the model that heard the
    candidate cannot VERIFY it, the other models get to try over the same
    ring audio before the candidate is suppressed."""
    p = VoskKwsProvider(
        "Hey Nova",
        model_path="primary",
        model_paths=["primary", "sibling"],
        keyword="nova",
    )
    p._ensure_model("primary")
    p._ensure_model("sibling")
    # The primary hears the candidate but its free ear contradicts it; the
    # sibling's free ear confirms the phrase.
    fake_vosk["models"]["primary"].free_text = "das ist ganz anders"  # i18n-allow: test utterance
    fake_vosk["models"]["sibling"].fire_after = 10**9  # never stage-1-hits
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == ["nova"]
    assert p.stats()["fired"] == 1
    assert p.stats()["suppressed_confirm"] == 0


async def test_cooldown_suppresses_immediate_refire(fake_vosk) -> None:
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova", cooldown_s=60.0)
    fired = await _run_detect(p, [_chunk() for _ in range(24)])
    assert fired == ["nova"]  # second grammar hit lands inside the cooldown
    assert p.stats()["suppressed_cooldown"] >= 1


async def test_rejected_candidate_storm_is_backpressured(
    fake_vosk, monkeypatch
) -> None:
    """Room speech can make recall-biased grammar hit continuously.

    One rejection may re-arm one cheap recognizer set to retain a user retry,
    but the first candidate it hears is latched without another expensive
    verifier call. Rebuilding on every skipped hit would preserve the
    process-wide CPU storm even if full verification were rate-limited.
    """
    p = VoskKwsProvider(
        "Hey Nova",
        model_path="fake",
        keyword="nova",
        confirm_tail_s=0.0,
        rejected_candidate_backoff_s=60.0,
    )
    p._ensure_model()
    fake_vosk["model"].free_text = "unrelated room speech"

    verify_calls = 0
    fresh_rec_calls = 0
    original_verify = p._verify_candidate
    original_fresh_recs = p._fresh_recs

    def _counted_verify(window, model_path=None):  # noqa: ANN001
        nonlocal verify_calls
        verify_calls += 1
        return original_verify(window, model_path)

    def _counted_fresh_recs():
        nonlocal fresh_rec_calls
        fresh_rec_calls += 1
        return original_fresh_recs()

    monkeypatch.setattr(p, "_verify_candidate", _counted_verify)
    monkeypatch.setattr(p, "_fresh_recs", _counted_fresh_recs)

    fired = await _run_detect(p, [_chunk() for _ in range(200)])

    assert fired == []
    assert verify_calls == 1
    assert fresh_rec_calls == 2  # initial set + one bounded retry listener
    assert p.stats()["backpressure_windows"] == 1
    assert p.stats()["backpressure_chunks"] > 100


@pytest.mark.parametrize(
    ("phrase", "keyword"),
    (
        ("Hey Nova", "nova"),
        ("Computer", "computer"),
        ("Good Morning Atlas", "atlas"),
    ),
)
async def test_immediate_retry_is_latched_during_reject_backpressure(
    fake_vosk, monkeypatch, phrase: str, keyword: str
) -> None:
    """A clean false-negative must not make any configured phrase deaf.

    The expensive verifier remains limited to one call per backpressure
    window. Stage one retains the next complete call and releases it at the
    deadline, so rapid human retries are not discarded.
    """
    p = VoskKwsProvider(
        phrase,
        model_path="fake",
        keyword=keyword,
        confirm_tail_s=0.0,
        rejected_candidate_backoff_s=0.5,
    )
    p._ensure_model()
    fake_vosk["model"].phrase = phrase.lower()
    fake_vosk["model"].free_text = phrase.lower()

    now = 0.0
    verify_calls = 0

    def _clock() -> float:
        return now

    def _verify(_window, _model_path=None):  # noqa: ANN001
        nonlocal verify_calls
        verify_calls += 1
        return verify_calls > 1

    monkeypatch.setattr(p, "_monotonic", _clock)
    monkeypatch.setattr(p, "_verify_candidate", _verify)

    async def _iter() -> AsyncIterator[AudioChunk]:
        nonlocal now
        # First candidate at t=0.3 rejects. The freshly re-armed recognizer
        # hears the retry at t=0.6, inside the t=0.8 deadline. On the first
        # chunk after that deadline the retained retry is verified. The old
        # deaf-window implementation only begins listening again there and
        # therefore cannot accumulate the three chunks needed for a wake.
        for _ in range(9):
            now += 0.1
            yield _chunk()

    fired = [wake async for wake in p.detect(_iter())]

    assert fired == [keyword]
    assert verify_calls == 2
    assert p.stats()["candidates"] == 2
    assert p.stats()["backpressure_windows"] == 1


# --- prewarmed verify-recognizer stock ------------------------------------------


def test_verify_stock_prewarms_and_hands_out_exclusive_recognizers(fake_vosk) -> None:
    """The factory pre-pays Kaldi's ~400ms lazy first-decode init: stocked
    recognizers are silence-prewarmed (Reset() called after the throwaway
    decode) and every taker gets EXCLUSIVE ownership (AP-24: never shared;
    reuse is banned — it drifted 2/600 real decisions). An empty stock falls
    back to a cold fresh build instead of blocking or sharing."""
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    p._ensure_model()
    p._replenish_stock()
    key = ("fake", "grammar")
    assert len(p._rec_stock[key]) == 2
    assert all(getattr(r, "was_reset", False) for r in p._rec_stock[key])
    r1 = p._take_verify_rec("fake", "grammar")
    r2 = p._take_verify_rec("fake", "grammar")
    assert r1 is not r2
    assert len(p._rec_stock[key]) == 0
    r3 = p._take_verify_rec("fake", "grammar")  # empty stock -> cold build
    assert r3 is not None and r3 is not r1 and r3 is not r2


# --- latency: concurrent grammar re-score + free decode ------------------------


def test_verify_candidate_runs_grammar_and_free_decode_concurrently(monkeypatch) -> None:
    """Deterministic concurrency proof via a 2-party barrier — NOT a
    wall-clock margin (those flake under CI/system load; measured directly
    during this mission's benchmarking).

    Each fake recognizer's ``FinalResult()`` blocks on a barrier that only
    releases once BOTH the grammar-rescore call and the free-decode call have
    reached it. If ``_verify_candidate`` ran them sequentially, the first
    call would block forever waiting for a second party that can only arrive
    after the first one already returned — a deadlock, which the barrier
    turns into a hard, fast-failing ``BrokenBarrierError`` instead of a
    flaky timing assertion.
    """
    barrier = threading.Barrier(2, timeout=2.0)

    class _BarrierRecognizer:
        def __init__(self, model, rate, grammar=None):  # noqa: ANN001
            self._model = model
            self._grammar = grammar

        def SetWords(self, flag):  # noqa: ANN001, N802
            pass

        def AcceptWaveform(self, pcm):  # noqa: ANN001, N802
            return False

        def PartialResult(self):  # noqa: N802
            return json.dumps({"partial": ""})

        def Result(self):  # noqa: N802
            return json.dumps({"text": ""})

        def FinalResult(self):  # noqa: N802
            barrier.wait()
            if self._grammar is not None:
                phrase = self._model.phrase.lower()
                return json.dumps({
                    "text": phrase,
                    "result": _timed_words(phrase, self._model.grammar_conf),
                })
            return json.dumps({
                "text": self._model.free_text,
                "result": _timed_words(self._model.free_text, 0.9),
            })

    mod = types.ModuleType("vosk")
    state = {"model": None}

    def _model_factory(path):  # noqa: ANN001
        state["model"] = _FakeModel(path)
        return state["model"]

    mod.Model = _model_factory
    mod.KaldiRecognizer = _BarrierRecognizer
    mod.SetLogLevel = lambda *_a: None
    monkeypatch.setitem(sys.modules, "vosk", mod)

    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    p._ensure_model()  # noqa: SLF001
    window = np.full(48000, 0.2, dtype=np.float32)

    result = p._verify_candidate(window)  # noqa: SLF001
    # A sequential regression breaks the barrier (only one party ever
    # arrives at a time -> BrokenBarrierError after the 2 s timeout), which
    # ``_verify_candidate``'s fail-open exception handler would otherwise
    # silently turn into a passing ``True`` too — so the barrier's own
    # broken-state is the real assertion, not just the return value.
    assert barrier.broken is False, (
        "the barrier never filled — grammar re-score and free decode ran "
        "SEQUENTIALLY, not concurrently"
    )
    assert result is True

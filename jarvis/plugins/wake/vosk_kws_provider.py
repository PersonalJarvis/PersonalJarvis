"""Universal any-word wake detector via Vosk grammar keyword spotting.

The one-identical-system-everywhere wake engine
(docs/superpowers/specs/2026-07-05-universal-wake-kws-design.md): a small
per-language Vosk model (Apache-2.0, official CPU wheels for
Windows/macOS/Linux x86+ARM, torch-free) streams microphone audio through a
GRAMMAR-constrained recognizer that only knows the configured wake phrase(s)
plus ``[unk]``. Any freely chosen word is pure configuration — no per-user
training, no cloud, no GPU.

Spike-measured on 5250 real captured windows (2026-07-05): grammar mode hears
the hard German-spoken name at 96 % where pretrained en/zh KWS models sit at
0-4 %; the two-stage arrangement below lands at 79-100 % recall with
1/0/0 % false accepts (ambient/quiet/silence) at ~0.05x realtime CPU.

Two-stage detection, AP-27-safe:

1. **Streaming grammar detector** — partial results fire DURING the phrase
   (measured t=1.3 s into a 1.8 s utterance), finals carry per-word
   confidence. The grammar forces every utterance onto the nearest phrase, so
   stage 1 alone false-accepts on ambient speech (34 % measured) and must
   never fire unconfirmed.
2. **Structured free-decode sound confirm** — ONE unconstrained pass over the
   ring-buffered candidate audio. For a prefixed phrase, the free transcript
   must contain a real wake prefix immediately followed by a sound-close core.
   This keeps ASR spelling/splitting tolerance (for example, "joe avis" for
   "Jarvis") without letting a high-confidence grammar hallucination turn
   unrelated room speech into a wake. Infrastructure errors fail OPEN so a
   broken confirm cannot eat a real wake.

A raw-energy gate (word-agnostic RMS at the match site, AP-27) rejects
near-silent candidates before the confirm. The detector never emits
transcript text — its only output is the fired keyword (design criterion:
user speech must never double-enter the pipeline through the wake path).

**Early candidate (visual-only, mini-verify gated).** A PARTIAL hit waits
``confirm_tail_s`` before the authoritative verify, so the bar used to react
~0.85-1.0 s after the phrase. The early candidate runs the SAME three verify
checks immediately over the audio heard so far (truncated ring) in a worker
thread and, only when ALL pass, tells ``early_candidate_listener`` to show
the bar — typically ~0.1-0.25 s after the partial, i.e. around phrase end.
Calibrated 2026-07-11 on real captured windows (400 pos / 2500 neg per
phrase): a conf gate alone leaks 0.84-12 % of room-speech windows (the
flicker that got the plain candidate reveal reverted, 5fe5c4d2); the full
mini-verify leaks 0/2500 ("hey jarvis") and 1-2/2500 (worst-case single
word) while early-revealing ~a third of eventual fires. A rejected or
superseded candidate retracts; infrastructure errors fail CLOSED here
(visual-only — the opposite polarity of the authoritative confirm, which
fails open so it can never eat a real wake).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.wake_constants import (
    WAKE_PREFIXES,
    normalize_phrase_for_match,
    phrase_core_for_match,
    sound_fold,
)

log = logging.getLogger("jarvis.wake.vosk")

# Minimum per-word grammar confidence for the verify RE-SCORE over the ring
# window. This is the precision anchor (live forensic 2026-07-06, "Hey Ruben"
# fired on plain room speech): genuine wakes re-score at ~1.0 (spike
# distribution p25..max = 1.0), room speech pulled onto the phrase re-scores
# lower. Calibrated on 6 min of judged continuous room speech vs the
# hardest common-sound phrase. Later production audio exposed a second class:
# unrelated speech could still re-score at 1.0, so confidence remains necessary
# but is now paired with the structured prefix/core confirm below.
_MIN_FINAL_CONF = 0.9

# Legacy whole-phrase sound-similarity floor. The structured confirm below
# treats this as the caller's minimum, then applies stronger prefix/core floors
# that close the live false-wake class where unrelated speech such as
# "hi servers sichern" scored above 0.55 against "Hey Jarvis".
_CONFIRM_RATIO = 0.55

# A configured wake prefix is a strong independent anchor. Once it is present,
# the following core may stay tolerant enough for an ASR token split
# ("Jarvis" -> "joe avis"), but unrelated words must not be subsidised by the
# matching prefix. A phrase with no prefix has no independent anchor and needs
# the stricter bare-core floor.
_PREFIXED_CORE_RATIO = 0.70
_UNPREFIXED_CORE_RATIO = 0.80

# Word-agnostic energy floor for a candidate window (mirrors the stt_match
# path's RollingWhisperWake._match_min_rms — AP-27: silence is gated on raw
# energy, never on transcript content).
_MATCH_MIN_RMS = 0.006

# Ring buffer length for the confirm pass — long enough to hold the full
# spoken phrase plus lead-in at the moment the partial trigger fires.
_RING_SECONDS = 3.0

# Refractory period after a fired wake.
_COOLDOWN_S = 5.0

# How much audio to let land in the ring AFTER a PARTIAL candidate before the
# confirm pass runs. A partial fires DURING the phrase (that is its virtue),
# but confirming at that instant hands the free decoder a truncated utterance
# ("hey lu…") which it then rejects — measured E2E: 50 % recall on "Hey Luca"
# vs 100 % when the confirm sees the whole phrase. 0.6 s covers the tail of a
# short core word; final candidates skip the wait (the endpoint already
# passed).
_CONFIRM_TAIL_S = 0.6

def _folded(text: str) -> str:
    return " ".join(sound_fold(t) for t in normalize_phrase_for_match(text))


def sound_confirm(free_text: str, phrase: str, *, ratio: float = _CONFIRM_RATIO) -> bool:
    """Return whether the free transcript supports the configured phrase.

    The old implementation compared one flattened phrase-sized string at a
    permissive 0.55 threshold. That let ordinary speech confirm forced grammar
    hits: production examples including ``"hi servers sichern"`` and
    ``"ein jahr bis"`` both matched ``"Hey Jarvis"`` and fired. Prefix and
    core are now independent evidence. A prefixed wake needs a real known
    prefix at the candidate position plus a sound-close core; an unprefixed
    wake needs a stricter core match because it has no second anchor.

    Core candidates may use one extra/fewer token so ASR splits and merges do
    not require exact spelling or tokenisation. Empty text always rejects.
    """
    if not free_text:
        return False
    phrase_tokens = normalize_phrase_for_match(phrase)
    core_tokens = phrase_core_for_match(phrase)
    if not phrase_tokens or not core_tokens:
        return False

    words = [sound_fold(t) for t in normalize_phrase_for_match(free_text)]
    target_core = "".join(sound_fold(t) for t in core_tokens)
    if not words or not target_core:
        return False

    prefix_count = len(phrase_tokens) - len(core_tokens)
    has_prefix = prefix_count > 0
    known_prefixes = {sound_fold(token) for token in WAKE_PREFIXES}
    core_token_count = max(1, len(core_tokens))
    core_sizes = range(max(1, core_token_count - 1), core_token_count + 2)
    core_floor = max(
        float(ratio),
        _PREFIXED_CORE_RATIO if has_prefix else _UNPREFIXED_CORE_RATIO,
    )

    for start in range(len(words)):
        if has_prefix:
            heard_prefix = words[start]
            if heard_prefix not in known_prefixes:
                continue
            core_start = start + 1
        else:
            core_start = start

        for size in core_sizes:
            core_end = core_start + size
            if core_end > len(words):
                continue
            heard_core = "".join(words[core_start:core_end])
            core_score = SequenceMatcher(None, target_core, heard_core).ratio()
            if core_score >= core_floor:
                return True
    return False


class VoskKwsProvider:
    """Any-word wake detector — structurally compatible with `WakeWordProvider`.

    ``phrase`` is the user's wake phrase; ``keyword`` is the canonical value
    yielded on a hit (the pipeline's trigger key). ``model_path`` points at an
    extracted Vosk model directory for the configured language.
    """

    name = "vosk_kws"

    def __init__(
        self,
        phrase: str,
        model_path: str,
        *,
        # ALL model dirs to listen on (primary first). None/empty = just
        # model_path. A phrase and the speaker language routinely diverge
        # ("Hey Jarvis": English name, German speaker — live forensic
        # 2026-07-11: the de model re-heard 'hey [unk]' and ate real wakes),
        # so every installed language model streams its own grammar and the
        # candidate is verified against the model that heard it.
        model_paths: Sequence[str] | None = None,
        keyword: str | None = None,
        sample_rate: int = 16_000,
        min_final_conf: float = _MIN_FINAL_CONF,
        confirm_ratio: float = _CONFIRM_RATIO,
        match_min_rms: float = _MATCH_MIN_RMS,
        cooldown_s: float = _COOLDOWN_S,
        confirm_tail_s: float = _CONFIRM_TAIL_S,
        # Production poll-loop parity: peak-normalize the confirm window to
        # -3 dBFS (gain capped at 40 dB) exactly like the other wake paths.
        target_peak: float = 0.7079,
        max_gain: float = 100.0,
        # Visual-only early candidate: awaited with True when the mini-verify
        # passes at PARTIAL time, False when a shown candidate is retracted.
        # Never carries transcript text; never fires unverified (see module
        # docstring + tests). None = feature off (default).
        early_candidate_listener: Callable[[bool], Awaitable[None]] | None = None,
    ) -> None:
        self._phrase = phrase.strip()
        self._keyword = keyword or "_".join(normalize_phrase_for_match(phrase)) or "wake"
        self._model_path = model_path
        paths = [p for p in (model_paths or ()) if p]
        if model_path and model_path not in paths:
            paths.insert(0, model_path)
        self._model_paths: list[str] = paths or [model_path]
        self._sample_rate = sample_rate
        self._min_final_conf = float(min_final_conf)
        phrase_tokens = normalize_phrase_for_match(self._phrase)
        core_tokens = phrase_core_for_match(self._phrase)
        has_prefix = len(core_tokens) < len(phrase_tokens)
        structural_floor = (
            _PREFIXED_CORE_RATIO if has_prefix else _UNPREFIXED_CORE_RATIO
        )
        self._confirm_ratio = max(float(confirm_ratio), structural_floor)
        self._match_min_rms = float(match_min_rms)
        self._cooldown_s = float(cooldown_s)
        self._confirm_tail_bytes = int(float(confirm_tail_s) * sample_rate) * 2
        self._target_peak = float(target_peak)
        self._max_gain = float(max_gain)
        self._models: dict[str, Any] = {}
        # Pre-warmed ONE-SHOT verify recognizers, keyed (model_path, kind).
        # Why: a fresh KaldiRecognizer's FIRST decode pays ~400ms lazy init
        # on top of ~100ms construction (measured 2026-07-11: fresh verify
        # p50 523ms vs 104ms warm). REUSING recognizers is not an option —
        # Kaldi adaptation survives Reset() and flipped 2/600 real decisions
        # (conf drift up to 0.55). A silence decode + Reset() however leaves
        # every decision unchanged (0/200 mismatches, conf jitter <=0.09),
        # so a background factory pre-pays the init and each verify consumes
        # a prewarmed recognizer EXCLUSIVELY, once (AP-24: never shared).
        # Empty stock (candidate burst) falls back to a cold fresh build —
        # exactly today's behaviour, just slower.
        self._rec_stock: dict[tuple[str, str], list[Any]] = {}
        self._stock_lock = threading.Lock()
        self._stock_target = 2
        self._replenish_task: asyncio.Task[None] | None = None
        self._grammar_words = [w for w in self._phrase.lower().split() if w]
        # Duck-typing parity with OpenWakeWordProvider: the pipeline's ready
        # log reads ``_keywords`` and ``_threshold`` off whatever detector is
        # armed. The confirm ratio is the closest analogue of a threshold.
        self._keywords = (self._keyword,)
        self._threshold = self._confirm_ratio
        # Ring buffer of raw int16 PCM bytes for the confirm pass.
        self._ring: deque[bytes] = deque()
        self._ring_len = 0
        self._ring_max = int(_RING_SECONDS * sample_rate) * 2  # bytes
        # Early-candidate state: the listener, whether a candidate is shown,
        # the in-flight mini-verify task, and a generation counter so a LATE
        # mini-verify completion can never show a candidate the authoritative
        # verify already resolved.
        self._early_listener = early_candidate_listener
        self._early_active = False
        self._early_task: asyncio.Task[None] | None = None
        self._pending_gen = 0
        # Session stats (parity with OpenWakeWordProvider.stats()).
        self._stat_chunks = 0
        self._stat_candidates = 0
        self._stat_gated_rms = 0
        self._stat_suppressed_confirm = 0
        self._stat_suppressed_cooldown = 0
        self._stat_fired = 0
        self._stat_early_shown = 0
        self._stat_early_retracted = 0

    # -- lifecycle -----------------------------------------------------------

    def _ensure_model(self, path: str | None = None) -> Any:
        key = path or self._model_path
        model = self._models.get(key)
        if model is None:
            from vosk import Model, SetLogLevel  # lazy: keep base import light

            SetLogLevel(-1)
            t0 = time.perf_counter()
            model = Model(key)
            self._models[key] = model
            log.info(
                "vosk-kws: model loaded in %.1f s (%s)",
                time.perf_counter() - t0,
                key,
            )
        return model

    def _new_grammar_rec(self, path: str | None = None) -> Any:
        from vosk import KaldiRecognizer

        grammar = json.dumps([self._phrase.lower(), "[unk]"])
        rec = KaldiRecognizer(self._ensure_model(path), self._sample_rate, grammar)
        rec.SetWords(True)
        return rec

    def _build_verify_rec(self, path: str | None, kind: str, *, prewarm: bool) -> Any:
        """A fresh one-shot verify recognizer; optionally silence-prewarmed.

        The prewarm decodes 0.3s of silence and Resets — it pre-pays Kaldi's
        lazy first-decode init without touching decisions (parity-measured).
        A prewarm error degrades to the cold recognizer, never fails a verify.
        """
        from vosk import KaldiRecognizer

        if kind == "grammar":
            rec = self._new_grammar_rec(path)
        else:
            rec = KaldiRecognizer(self._ensure_model(path), self._sample_rate)
            rec.SetWords(True)
        if prewarm:
            try:
                silence = np.zeros(
                    int(0.3 * self._sample_rate), dtype=np.int16
                ).tobytes()
                rec.AcceptWaveform(silence)
                rec.FinalResult()
                rec.Reset()
            except Exception as exc:  # noqa: BLE001 — cold rec still works
                log.debug("vosk-kws: prewarm skipped (%s/%s): %s", path, kind, exc)
        return rec

    def _take_verify_rec(self, path: str | None, kind: str) -> Any:
        """Pop a prewarmed one-shot recognizer, or build cold on empty stock.

        The taker owns the recognizer exclusively and discards it after ONE
        use (AP-24: no sharing, no reuse — reuse drifts decisions).
        """
        key = (path or self._model_path, kind)
        with self._stock_lock:
            stack = self._rec_stock.get(key)
            if stack:
                return stack.pop()
        return self._build_verify_rec(path, kind, prewarm=False)

    def _replenish_stock(self) -> None:
        """Top the prewarmed stock back up to target (worker thread only)."""
        for path in self._model_paths:
            for kind in ("grammar", "free"):
                key = (path, kind)
                while True:
                    with self._stock_lock:
                        if len(self._rec_stock.setdefault(key, [])) >= self._stock_target:
                            break
                    try:
                        rec = self._build_verify_rec(path, kind, prewarm=True)
                    except Exception as exc:  # noqa: BLE001 — broken model:
                        # takers fall back to cold builds which fail the same
                        # way and are handled at the verify layer.
                        log.debug(
                            "vosk-kws: stock replenish failed (%s/%s): %s",
                            path, kind, exc,
                        )
                        break
                    with self._stock_lock:
                        self._rec_stock[key].append(rec)

    def _kick_replenish(self) -> None:
        """Fire-and-forget stock top-up off the hot path (needs a loop)."""
        if self._replenish_task is not None and not self._replenish_task.done():
            return
        with contextlib.suppress(RuntimeError):  # no running loop (sync tests)
            asyncio.get_running_loop()
            self._replenish_task = asyncio.create_task(
                asyncio.to_thread(self._replenish_stock)
            )

    def _fresh_recs(self) -> dict[str, Any]:
        """One streaming grammar recognizer per LOADABLE model path.

        Taken from the prewarmed stock when available: a rebuilt streaming
        recognizer otherwise pays Kaldi's ~400ms lazy first-decode init
        INLINE in the detect loop (it decodes chunks on the event loop) —
        after every candidate reset, per model. A corrupt/missing model dir
        must never brick the working ones — it is skipped with a warning and
        detection continues on the rest.
        """
        recs: dict[str, Any] = {}
        for path in self._model_paths:
            try:
                recs[path] = self._take_verify_rec(path, "grammar")
            except Exception as exc:  # noqa: BLE001 — isolate a broken model
                log.warning("vosk-kws: model %s unusable (%s) — skipped.", path, exc)
        self._kick_replenish()
        return recs

    async def start(self) -> None:
        """Pre-load every model and FILL the prewarmed recognizer stock.

        Replaces the former throwaway warm-up decode: the stock recognizers
        ARE the warm-up now (their silence decode pre-pays Kaldi's lazy
        first-decode init), and unlike the throwaways they are kept and
        consumed by the first real detect/verify. Fail-closed: errors must
        never break boot — takers fall back to cold builds.
        """
        for path in self._model_paths:
            try:
                await asyncio.to_thread(self._ensure_model, path)
            except Exception as exc:  # noqa: BLE001 — a broken model must not
                # brick the working ones; _fresh_recs skips it too.
                log.warning("vosk-kws: model %s failed to load (%s).", path, exc)
        await asyncio.to_thread(self._replenish_stock)

    async def stop(self) -> None:
        # Invalidate any in-flight early check and retract a shown candidate
        # so a detector swap can never leave the bar stuck "candidate".
        self._pending_gen += 1
        if self._early_task is not None:
            self._early_task.cancel()
            self._early_task = None
        await self._notify_early(False)
        if self._replenish_task is not None:
            self._replenish_task.cancel()
            self._replenish_task = None
        with self._stock_lock:
            self._rec_stock.clear()
        self._models.clear()
        self._ring.clear()
        self._ring_len = 0

    def stats(self) -> dict[str, int]:
        return {
            "chunks": self._stat_chunks,
            "candidates": self._stat_candidates,
            "gated_rms": self._stat_gated_rms,
            "suppressed_confirm": self._stat_suppressed_confirm,
            "suppressed_cooldown": self._stat_suppressed_cooldown,
            "fired": self._stat_fired,
            "early_shown": self._stat_early_shown,
            "early_retracted": self._stat_early_retracted,
        }

    # -- early candidate (visual-only) ----------------------------------------

    def set_early_candidate_listener(
        self, listener: Callable[[bool], Awaitable[None]] | None
    ) -> None:
        """Wire/unwire the visual candidate listener after construction."""
        self._early_listener = listener

    @property
    def early_candidate_active(self) -> bool:
        """Whether a shown early candidate is currently outstanding."""
        return self._early_active

    def consume_early_candidate(self) -> bool:
        """Hand the shown-candidate state to the pipeline (reset, no event).

        Called once per yielded keyword: the pipeline needs to know whether
        the bar is already visible so a silently DROPPED wake (post-hangup
        echo lock, app not activatable) retracts it instead of leaving it
        stuck "candidate" with no session behind it. Resetting here also
        re-arms the show guard for the next candidate.
        """
        was_shown = self._early_active
        self._early_active = False
        return was_shown

    async def _notify_early(self, active: bool) -> None:
        if self._early_listener is None or active == self._early_active:
            return
        self._early_active = active
        if active:
            self._stat_early_shown += 1
        else:
            self._stat_early_retracted += 1
        try:
            await self._early_listener(active)
        except Exception as exc:  # noqa: BLE001 — a UI listener error must
            # never break wake detection.
            log.debug("early-candidate listener failed: %s", exc)

    async def _run_early_check(
        self, window: np.ndarray, gen: int, model_path: str | None = None
    ) -> None:
        """Mini-verify the truncated ring; show the candidate only if the
        pending candidate that spawned this check is still unresolved."""
        try:
            ok = await asyncio.to_thread(self._early_check, window, model_path)
        except Exception as exc:  # noqa: BLE001 — visual-only, never disrupt
            log.debug("early-candidate check errored: %s", exc)
            return
        if ok and gen == self._pending_gen:
            log.info(
                "vosk-kws: EARLY candidate shown for %r (mini-verify passed)",
                self._phrase,
            )
            await self._notify_early(True)

    # -- internals -----------------------------------------------------------

    def _ring_push(self, pcm: bytes) -> None:
        self._ring.append(pcm)
        self._ring_len += len(pcm)
        while self._ring_len > self._ring_max and self._ring:
            dropped = self._ring.popleft()
            self._ring_len -= len(dropped)

    def _ring_window(self) -> np.ndarray:
        if not self._ring:
            return np.empty(0, dtype=np.float32)
        raw = b"".join(self._ring)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    def _grammar_hit(self, rec: Any, pcm: bytes) -> tuple[bool, float] | None:
        """Feed one chunk; return (is_final, min_conf) on a phrase hit else None."""
        if rec.AcceptWaveform(pcm):
            res = json.loads(rec.Result())
            text = res.get("text", "")
            if self._phrase.lower() in text:
                words = [
                    w for w in res.get("result", [])
                    if w.get("word") in self._grammar_words
                ]
                conf = min((w.get("conf", 0.0) for w in words), default=0.0)
                return (True, conf)
            return None
        partial = json.loads(rec.PartialResult()).get("partial", "")
        if partial and self._phrase.lower() in partial:
            return (False, 1.0)  # partials carry no conf; the confirm decides
        return None

    def _verify_candidate(
        self, window: np.ndarray, model_path: str | None = None
    ) -> bool:
        """Authoritative confirm — fails OPEN (never eat a real wake).

        ``model_path`` selects the model that HEARD the candidate; the verify
        must use the same acoustics that produced the hit, never a sibling.
        """
        return self._verify_window(window, fail_open=True, model_path=model_path)

    def _early_check(
        self, window: np.ndarray, model_path: str | None = None
    ) -> bool:
        """Visual-only mini-verify on the truncated ring — fails CLOSED
        (never flash the bar on a broken verifier)."""
        try:
            return self._verify_window(
                window, fail_open=False, model_path=model_path
            )
        except Exception:  # noqa: BLE001 — belt and braces for the UI path
            return False

    def _verify_window(
        self,
        window: np.ndarray,
        *,
        fail_open: bool,
        model_path: str | None = None,
    ) -> bool:
        """Three checks over the given window; ALL must pass before a fire.

        Why this shape (live forensic 2026-07-06, "Hey Ruben" fired on plain
        room speech every few minutes): the streaming PARTIAL that makes the
        detector fast carries no confidence — its 1.00 placeholder sailed
        through the conf gate — and the old confirm compared the phrase
        against the BEST two-word window anywhere in ~3 s of speech, so for a
        phrase built from common German sounds SOME pair ("herr oben",
        "bei ihm") always eventually cleared 0.55. Both holes were
        word-dependent — exactly the class this engine exists to kill.

        1. **Grammar re-score**: a fresh grammar pass over the window must
           re-hear the phrase as a FINAL — yielding a real per-word confidence
           (gate: ``min_final_conf``) and the phrase's TIME SPAN.
        2. **Energy**: the word-agnostic RMS floor is measured over that span
           (not the whole ring, where surrounding silence dilutes it — AP-27:
           silence gates on energy, never transcript).
        3. **Localised sound confirm**: the free decode keeps only words
           OVERLAPPING the span (±0.3 s) — the permissive sound match then
           judges what was said AT the candidate's position instead of
           fishing the best pair out of three seconds of conversation.

        On infrastructure errors the ``fail_open`` polarity decides: the
        authoritative confirm accepts (a broken confirm must never eat a real
        wake), the visual-only early check rejects. A clean "the phrase is
        not there" is always a rejection.

        Latency (spawn-latency mission, 2026-07-10): the grammar re-score and
        the free decode are independent Kaldi passes over the SAME audio —
        the free decode does not consume the re-score's output, only the
        LATER span-filtering step does. Measured on the real German small
        model (data/wake_models/vosk/de/vosk-model-small-de-0.15): the free
        pass costs 3-5x the grammar pass (e.g. 235ms vs 70ms over a 3 s
        window), so running them sequentially pays their SUM even though the
        wall-clock floor is only their MAX. They run concurrently in two
        worker threads against ONE shared, read-only ``Model`` — Vosk's
        documented multi-client pattern (one Model, many independent
        KaldiRecognizer sessions decoding concurrently), not the AP-24 hazard
        (that guards a single recognizer's mutable per-call state shared
        across concurrent callers; here each thread owns its own fresh
        recognizer). This changes only wall-clock time, never the decision:
        both passes decode the identical ``pcm`` and every downstream
        threshold/comparison is untouched.
        """
        try:
            peak = float(np.max(np.abs(window))) if len(window) else 0.0
            if peak > 1e-6:
                window = np.clip(
                    window * min(self._target_peak / peak, self._max_gain), -1.0, 1.0
                )
            pcm = (window * 32767.0).astype(np.int16).tobytes()

            # 1) grammar re-score (real confidence + time span) and
            # 3) free decode (unconstrained) run CONCURRENTLY — see the
            # latency note above. One attempt each over the full ring,
            # deliberately: a second grammar try over a shorter cut
            # measurably HELPED room speech more than genuine calls (FA
            # matrix 3 -> 7 with a last-1.8 s retry), because the grammar
            # happily forces any short speech snippet onto the phrase.
            def _grammar_pass() -> dict:
                g = self._take_verify_rec(model_path, "grammar")
                g.AcceptWaveform(pcm)
                return json.loads(g.FinalResult())

            def _free_pass() -> dict:
                f = self._take_verify_rec(model_path, "free")
                f.AcceptWaveform(pcm)
                return json.loads(f.FinalResult())

            with ThreadPoolExecutor(max_workers=2) as pool:
                grammar_future = pool.submit(_grammar_pass)
                free_future = pool.submit(_free_pass)
                gres = grammar_future.result()
                fres = free_future.result()

            gwords = [
                w for w in gres.get("result", [])
                if w.get("word") in self._grammar_words
            ]
            if self._phrase.lower() not in gres.get("text", "") or not gwords:
                log.info(
                    "vosk-kws: verify SUPPRESSED — re-score did not re-hear "
                    "%r (heard %r)",
                    self._phrase, gres.get("text", "")[:60],
                )
                return False
            conf = min(w.get("conf", 0.0) for w in gwords)
            if conf < self._min_final_conf:
                log.info(
                    "vosk-kws: verify SUPPRESSED — re-score conf %.2f < %.2f "
                    "for %r", conf, self._min_final_conf, self._phrase,
                )
                return False
            start_s = min(w.get("start", 0.0) for w in gwords)
            end_s = max(w.get("end", 0.0) for w in gwords)
            span_a = start_s - 0.3
            span_b = end_s + 0.3

            # 2) word-agnostic energy over the phrase span
            a = max(0, int(span_a * self._sample_rate))
            b = min(len(window), int(span_b * self._sample_rate))
            segment = window[a:b] if b > a else window
            rms = float(np.sqrt(np.mean(segment * segment) + 1e-12)) if len(segment) else 0.0
            if rms < self._match_min_rms:
                self._stat_gated_rms += 1
                log.info(
                    "vosk-kws: verify SUPPRESSED — span rms %.4f < %.4f "
                    "(silence can never fire)", rms, self._match_min_rms,
                )
                return False

            # localise the (already-decoded) free words to the phrase span
            local = [
                w.get("word", "") for w in fres.get("result", [])
                if w.get("end", 0.0) >= span_a and w.get("start", 0.0) <= span_b
            ]
            free_local = " ".join(local)
        except Exception as exc:  # noqa: BLE001 — polarity via fail_open
            log.warning(
                "vosk-kws: verify failed (%s) — %s.",
                exc,
                "accepting" if fail_open else "rejecting (visual-only)",
            )
            return fail_open
        ok = sound_confirm(free_local, self._phrase, ratio=self._confirm_ratio)
        log.info(
            "vosk-kws: verify %s — free ear heard %r at the candidate span "
            "(conf=%.2f) vs phrase %r",
            "OK" if ok else "SUPPRESSED",
            free_local[:60],
            conf,
            self._phrase,
        )
        return ok

    # -- detection loop --------------------------------------------------------

    async def detect(self, chunks: AsyncIterator[AudioChunk]) -> AsyncIterator[str]:
        """Consume audio chunks, yield the keyword on a confirmed hit.

        The grammar recognizer streams chunk-by-chunk (measured ~0.02x
        realtime — a 100 ms chunk costs ~2 ms, safe inline). The confirm pass
        (~0.1-0.2 s) runs in a worker thread only on candidates.

        The stage-one grammar is intentionally noisy (it forces speech onto
        the configured phrase), so candidates remain private to this method.
        Only the keyword yielded after the full verify pass may cross into the
        pipeline or become visible in the overlay.
        """
        for path in self._model_paths:
            try:
                await asyncio.to_thread(self._ensure_model, path)
            except Exception as exc:  # noqa: BLE001 — skip a broken model
                log.warning("vosk-kws: model %s failed to load (%s).", path, exc)
        # One streaming grammar per installed model — a phrase whose language
        # differs from the speaker's still has a model that can spell it
        # (union recall measured +38% on the fixture corpus, 2026-07-11).
        recs = self._fresh_recs()
        last_fire_t = 0.0
        # Pending candidate: a PARTIAL hit waits for ``confirm_tail_s`` more
        # audio before the confirm pass so the free decoder sees the WHOLE
        # phrase, not a truncated one (E2E-measured recall trap). Finals skip
        # the wait — their endpoint already passed. ``hit_path`` pins the
        # model that heard the candidate: verify and early check must use the
        # same acoustics, never a sibling model.
        pending: tuple[bool, float, str] | None = None
        pending_tail = 0
        async for chunk in chunks:
            self._stat_chunks += 1
            pcm = chunk.pcm
            self._ring_push(pcm)
            if pending is not None:
                pending_tail += len(pcm)
                # Keep every model's stream fed during the tail wait so their
                # decode state stays aligned with the ring.
                for r in recs.values():
                    self._grammar_hit(r, pcm)
                if pending_tail < self._confirm_tail_bytes:
                    continue
                is_final, conf, hit_path = pending
                pending = None
            else:
                # Feed EVERY model; first hit wins the candidate slot.
                hit: tuple[bool, float] | None = None
                hit_path = ""
                for path, r in recs.items():
                    h = self._grammar_hit(r, pcm)
                    if h is not None and hit is None:
                        hit, hit_path = h, path
                if hit is None:
                    continue
                is_final, conf = hit
                now = time.time()
                if now - last_fire_t < self._cooldown_s:
                    self._stat_suppressed_cooldown += 1
                    recs = self._fresh_recs()
                    continue
                self._stat_candidates += 1
                if is_final and conf < self._min_final_conf:
                    recs = self._fresh_recs()
                    continue
                if not is_final and self._confirm_tail_bytes > 0:
                    pending = (is_final, conf, hit_path)
                    pending_tail = 0
                    # Visual-only early candidate: mini-verify the audio heard
                    # SO FAR in a worker thread while the confirm tail keeps
                    # accumulating. Typically done ~0.1-0.25 s later — the bar
                    # reacts around phrase end instead of ~0.85 s after it.
                    if self._early_listener is not None:
                        self._pending_gen += 1
                        self._early_task = asyncio.create_task(
                            self._run_early_check(
                                self._ring_window(), self._pending_gen, hit_path
                            )
                        )
                        # The early check just consumed prewarmed recognizers;
                        # top the stock back up before the authoritative
                        # verify needs its own pair (~0.6s from now).
                        self._kick_replenish()
                    continue
            now = time.time()
            window = self._ring_window()
            confirmed = await asyncio.to_thread(
                self._verify_candidate, window, hit_path
            )
            fired_path = hit_path
            if not confirmed:
                # Sibling rescue: the model that HEARD the candidate could not
                # verify it, but the ring still holds the phrase — let every
                # other model try (union recall, measured +38%: 'Hey Jarvis'
                # de-spoken garbles on the de model yet verifies on en).
                # Fail-CLOSED via _early_check: an opportunistic rescue must
                # never fire off a broken sibling (the fail-open contract
                # protects only the primary confirm).
                for other in self._model_paths:
                    if other == hit_path or other not in recs:
                        continue
                    if await asyncio.to_thread(self._early_check, window, other):
                        confirmed = True
                        fired_path = other
                        break
            if not confirmed:
                self._stat_suppressed_confirm += 1
                # Resolve the pending candidate: let an in-flight early check
                # finish FIRST (its show is still legitimate — the retract
                # right below keeps the bar honest either way), THEN
                # invalidate the generation and retract. Bumping before the
                # await races a slow early check into dropping its show while
                # the retract below no-ops — bar states must be deterministic.
                if self._early_task is not None:
                    with contextlib.suppress(Exception):
                        await self._early_task
                    self._early_task = None
                self._pending_gen += 1
                await self._notify_early(False)
                recs = self._fresh_recs()
                continue
            # Confirmed: let a still-running early check finish first (in
            # production it completed long ago — the tail wait dwarfs it) so
            # show-then-wake ordering is deterministic. The shown flag stays
            # set for the pipeline to CONSUME: it must know the bar is visible
            # when it silently drops this wake (echo lock) and retract it.
            if self._early_task is not None:
                with contextlib.suppress(Exception):
                    await self._early_task
                self._early_task = None
            self._pending_gen += 1
            self._stat_fired += 1
            last_fire_t = now
            log.info(
                "vosk-kws: WAKE fired for %r (%s candidate, model %s)",
                self._phrase,
                "final" if is_final else "partial",
                fired_path,
            )
            yield self._keyword
            recs = self._fresh_recs()


def vosk_model_supports_phrase(model_path: str, phrase: str) -> bool:
    """True when every core word of ``phrase`` exists in the model lexicon.

    Vosk drops out-of-vocabulary grammar words with a stderr warning and then
    silently builds a grammar WITHOUT them (live 2026-07-08: 'Hey Billionar'
    → "Ignoring word missing in vocabulary: 'billionar'" → the phrase could
    never be heard, and ``SetLogLevel(-1)`` hid the warning). The small models
    ship no readable word list, so the warning is the only signal — capture it
    at the OS fd level (portable on Windows and POSIX) around a throwaway
    recognizer build. NEVER raises: any failure means "cannot prove it's
    unsupported" → returns True (fail-open, never eat a usable wake word). This
    is DELIBERATELY off the boot path (it loads the model, ~1.5 s) — call it
    from a user action (self-test) or a background task, never in ``_run_backend``.
    """
    core = phrase_core_for_match(phrase)
    if not core:
        return False
    import contextlib
    import os
    import tempfile

    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except Exception:  # noqa: BLE001 — no vosk → cannot disprove support
        return True
    tmp = None
    old = None
    try:
        SetLogLevel(0)  # surface the OOV warning the app normally hides
        tmp = tempfile.TemporaryFile(mode="w+")
        old = os.dup(2)
        os.dup2(tmp.fileno(), 2)
        KaldiRecognizer(Model(model_path), 16_000, json.dumps([phrase.lower(), "[unk]"]))
    except Exception:  # noqa: BLE001 — probe failure must not reject a real word
        return True
    finally:
        if old is not None:
            with contextlib.suppress(Exception):
                os.dup2(old, 2)
                os.close(old)
        SetLogLevel(-1)  # restore the app's quiet default
    tmp.seek(0)
    return "missing in vocabulary" not in tmp.read().lower()


__all__ = ["VoskKwsProvider", "sound_confirm", "vosk_model_supports_phrase"]

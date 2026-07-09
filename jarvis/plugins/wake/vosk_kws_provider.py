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
2. **Permissive free-decode sound confirm** — ONE unconstrained pass over the
   ring-buffered candidate audio. The folded free transcript must merely be
   SOUND-CLOSE to the phrase (SequenceMatcher on sound-folded tokens): a
   genuine "Hey Ruben" free-decodes to "hey ruben"/"hey room"/"herum" (all
   close), ambient "vielen dank" does not. NEVER require the free pass to
   spell the word (AP-27: that kills recall for hard names); infrastructure
   errors fail OPEN so a broken confirm cannot eat a real wake.

A raw-energy gate (word-agnostic RMS at the match site, AP-27) rejects
near-silent candidates before the confirm. The detector never emits
transcript text — its only output is the fired keyword (design criterion:
user speech must never double-enter the pipeline through the wake path).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.wake_constants import (
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
# hardest common-sound phrase: conf>=0.9 + the permissive 0.55 sound ratio =
# 0 false fires at Ruben 20/24 / Luca 8/8 recall; conf 0.5 leaked
# 0.84 fires/min. Raising the sound ratio instead costs recall (AP-27) —
# keep the ratio permissive and let the REAL confidence carry precision.
_MIN_FINAL_CONF = 0.9

# Sound-similarity floor for the free-decode confirm. Spike sweep: 0.55 keeps
# 79-100 % recall at 1/0/0 % false accepts; 0.45 lifts recall ~8 points but
# false accepts rise to ~5 % — the wrong trade for an always-on listener.
_CONFIRM_RATIO = 0.55

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
    """True when the free transcript is SOUND-CLOSE to ``phrase`` (permissive).

    Empty free text means the unconstrained ear heard nothing at all — the
    grammar hit was noise pulled onto the phrase, reject. Otherwise slide a
    phrase-sized window over the folded free tokens and accept the best
    SequenceMatcher ratio >= ``ratio``.
    """
    if not free_text:
        return False
    target = _folded(phrase)
    if not target:
        return False
    words = _folded(free_text).split()
    n = max(1, len(target.split()))
    best = 0.0
    for i in range(max(1, len(words) - n + 1)):
        window = " ".join(words[i : i + n])
        best = max(best, SequenceMatcher(None, target, window).ratio())
    return best >= ratio


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
    ) -> None:
        self._phrase = phrase.strip()
        self._keyword = keyword or "_".join(normalize_phrase_for_match(phrase)) or "wake"
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._min_final_conf = float(min_final_conf)
        self._confirm_ratio = float(confirm_ratio)
        self._match_min_rms = float(match_min_rms)
        self._cooldown_s = float(cooldown_s)
        self._confirm_tail_bytes = int(float(confirm_tail_s) * sample_rate) * 2
        # Short-core hardening (calibrated 2026-07-06): phrases whose longest
        # sound-folded token is very short ("Karl", "Anton") are cheap for
        # room speech to imitate — the 10-word x 3-min matrix leaked only on
        # the shortest cores. Tightening the RE-SCORE confidence instead
        # deafened short words (synthetic recall dropped), so the localised
        # sound confirm carries the extra strictness: a short core must match
        # its span at 0.62 instead of the permissive 0.55.
        tokens = normalize_phrase_for_match(self._phrase)
        longest = max((len(sound_fold(t)) for t in tokens), default=0)
        if longest < 6:
            self._confirm_ratio = max(self._confirm_ratio, 0.62)
        self._target_peak = float(target_peak)
        self._max_gain = float(max_gain)
        self._model: Any = None
        self._grammar_words = [w for w in self._phrase.lower().split() if w]
        # Duck-typing parity with OpenWakeWordProvider: the pipeline's ready
        # log reads ``_keywords`` and ``_threshold`` off whatever detector is
        # armed. The confirm ratio is the closest analogue of a threshold.
        self._keywords = (self._keyword,)
        self._threshold = float(confirm_ratio)
        # Ring buffer of raw int16 PCM bytes for the confirm pass.
        self._ring: deque[bytes] = deque()
        self._ring_len = 0
        self._ring_max = int(_RING_SECONDS * sample_rate) * 2  # bytes
        # Session stats (parity with OpenWakeWordProvider.stats()).
        self._stat_chunks = 0
        self._stat_candidates = 0
        self._stat_gated_rms = 0
        self._stat_suppressed_confirm = 0
        self._stat_suppressed_cooldown = 0
        self._stat_fired = 0

    # -- lifecycle -----------------------------------------------------------

    def _ensure_model(self) -> Any:
        if self._model is None:
            from vosk import Model, SetLogLevel  # lazy: keep base import light

            SetLogLevel(-1)
            t0 = time.perf_counter()
            self._model = Model(self._model_path)
            log.info(
                "vosk-kws: model loaded in %.1f s (%s)",
                time.perf_counter() - t0,
                self._model_path,
            )
        return self._model

    def _new_grammar_rec(self) -> Any:
        from vosk import KaldiRecognizer

        grammar = json.dumps([self._phrase.lower(), "[unk]"])
        rec = KaldiRecognizer(self._ensure_model(), self._sample_rate, grammar)
        rec.SetWords(True)
        return rec

    async def start(self) -> None:
        """Pre-load the model off the event loop (never on the boot path)."""
        await asyncio.to_thread(self._ensure_model)

    async def stop(self) -> None:
        self._model = None
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
        }

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

    def _verify_candidate(self, window: np.ndarray) -> bool:
        """Three checks over the ring window; ALL must pass before a fire.

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

        Fail-OPEN only on infrastructure errors (a broken confirm must never
        eat a real wake); a clean "the phrase is not there" is a rejection.
        """
        try:
            from vosk import KaldiRecognizer

            peak = float(np.max(np.abs(window))) if len(window) else 0.0
            if peak > 1e-6:
                window = np.clip(
                    window * min(self._target_peak / peak, self._max_gain), -1.0, 1.0
                )
            pcm = (window * 32767.0).astype(np.int16).tobytes()

            # 1) grammar re-score: real confidence + time span for the phrase.
            # One attempt over the full ring, deliberately: a second try over
            # a shorter cut measurably HELPED room speech more than genuine
            # calls (FA matrix 3 -> 7 with a last-1.8 s retry), because the
            # grammar happily forces any short speech snippet onto the phrase.
            g = self._new_grammar_rec()
            g.AcceptWaveform(pcm)
            gres = json.loads(g.FinalResult())
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

            # 3) free decode, localised to the phrase span
            f = KaldiRecognizer(self._ensure_model(), self._sample_rate)
            f.SetWords(True)
            f.AcceptWaveform(pcm)
            fres = json.loads(f.FinalResult())
            local = [
                w.get("word", "") for w in fres.get("result", [])
                if w.get("end", 0.0) >= span_a and w.get("start", 0.0) <= span_b
            ]
            free_local = " ".join(local)
        except Exception as exc:  # noqa: BLE001 — fail-open, never eat a wake
            log.warning("vosk-kws: verify failed (%s) — accepting.", exc)
            return True
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
        """
        await asyncio.to_thread(self._ensure_model)
        rec = self._new_grammar_rec()
        last_fire_t = 0.0
        # Pending candidate: a PARTIAL hit waits for ``confirm_tail_s`` more
        # audio before the confirm pass so the free decoder sees the WHOLE
        # phrase, not a truncated one (E2E-measured recall trap). Finals skip
        # the wait — their endpoint already passed.
        pending: tuple[bool, float] | None = None
        pending_tail = 0
        async for chunk in chunks:
            self._stat_chunks += 1
            pcm = chunk.pcm
            self._ring_push(pcm)
            if pending is not None:
                pending_tail += len(pcm)
                if pending_tail < self._confirm_tail_bytes:
                    continue
                is_final, conf = pending
                pending = None
            else:
                hit = self._grammar_hit(rec, pcm)
                if hit is None:
                    continue
                is_final, conf = hit
                now = time.time()
                if now - last_fire_t < self._cooldown_s:
                    self._stat_suppressed_cooldown += 1
                    rec = self._new_grammar_rec()
                    continue
                self._stat_candidates += 1
                if is_final and conf < self._min_final_conf:
                    rec = self._new_grammar_rec()
                    continue
                if not is_final and self._confirm_tail_bytes > 0:
                    pending = (is_final, conf)
                    pending_tail = 0
                    continue
            now = time.time()
            window = self._ring_window()
            confirmed = await asyncio.to_thread(self._verify_candidate, window)
            if not confirmed:
                self._stat_suppressed_confirm += 1
                rec = self._new_grammar_rec()
                continue
            self._stat_fired += 1
            last_fire_t = now
            log.info(
                "vosk-kws: WAKE fired for %r (%s candidate)",
                self._phrase,
                "final" if is_final else "partial",
            )
            yield self._keyword
            rec = self._new_grammar_rec()


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

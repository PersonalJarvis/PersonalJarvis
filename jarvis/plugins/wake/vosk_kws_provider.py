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
   unrelated room speech into a wake. A shape-only acceptance (the free ear
   could not spell the word but what it heard looks like a wake call) must
   additionally win the acoustic competition against an explicit
   "<prefix> [unk]" grammar alternative, or a call of a DIFFERENT name fires
   too (live 2026-07-17). Infrastructure errors fail OPEN so a broken confirm
   cannot eat a real wake.

A raw-energy gate (word-agnostic RMS at the match site, AP-27) rejects
near-silent candidates before the confirm. The detector never emits
transcript text — its only output is the fired keyword (design criterion:
user speech must never double-enter the pipeline through the wake path).

**Candidate-prefix verify + early visual.** A PARTIAL hit waits
``confirm_tail_s`` before the fallback verify, so the bar used to react
~0.85-1.0 s after the phrase. The early check runs the SAME three verify
checks immediately over the audio heard so far (truncated ring) in a worker
thread. A positive result is authoritative for that candidate and also tells
``early_candidate_listener`` to show the bar — typically ~0.1-0.25 s after
the partial, i.e. around phrase end. Later audio is session speech and cannot
revoke that already-verified prefix. If the truncated check rejects, the
normal full-window verify still runs after the tail, so early truncation never
eats a wake.
Calibrated 2026-07-11 on real captured windows (400 pos / 2500 neg per
phrase): a conf gate alone leaks 0.84-12 % of room-speech windows (the
flicker that got the plain candidate reveal reverted, 5fe5c4d2); the full
mini-verify leaks 0/2500 ("hey jarvis") and 1-2/2500 (worst-case single
word) while early-confirming ~a third of eventual fires. Infrastructure errors
fail CLOSED in the prefix check — the opposite polarity of the fallback
full-window confirm, which fails open so it can never eat a real wake.
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

# One-shot latch so a native JSON glitch is diagnosable without log-spamming
# a busy wake loop (the parse runs many times per second).
_MALFORMED_RESULT_LOGGED = False


def _parse_recognizer_json(raw: str, *, where: str) -> dict:
    """Parse a KaldiRecognizer JSON payload; malformed output is a no-hit.

    libvosk builds its result JSON in native code. A malformed payload
    (observed in the field on macOS 2026-07-17: one bad ``Result()`` put the
    whole parallel wake stack into a crash-loop, "Wake loop failed:
    Expecting property name…" every ~20 s — wake effectively deaf) must
    degrade to "heard nothing" instead of raising out of the wake loop
    (AD-6). The first occurrence logs the raw payload so the native cause
    stays diagnosable.
    """
    global _MALFORMED_RESULT_LOGGED
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        if not _MALFORMED_RESULT_LOGGED:
            _MALFORMED_RESULT_LOGGED = True
            log.warning(
                "vosk-kws: recognizer returned malformed JSON at %s (%s); "
                "treating as no-hit. Raw payload: %r",
                where,
                exc,
                raw[:400] if isinstance(raw, str) else raw,
            )
        else:
            log.debug("vosk-kws: malformed recognizer JSON at %s (%s)", where, exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}

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
_PREFIXED_CORE_RATIO = 2.0 / 3.0
_UNPREFIXED_CORE_RATIO = 0.80

# A free decoder often preserves the wake core while garbling the short
# prefix completely (production examples for "Hey Jarvis": "age avis",
# "a jarvis", "page avis", "pay jarvis").  Requiring the prefix to be one of
# WAKE_PREFIXES therefore makes the detector deaf even though the grammar
# pass re-heard the full phrase at high confidence.  The rescue remains
# narrow: the localised transcript must contain exactly one prefix-shaped
# token plus the core, and the core itself must clear this stronger floor.
# This does not revive the old whole-window fuzzy match that accepted
# unrelated multi-word speech.
_GARBLED_PREFIX_CORE_RATIO = 0.80
_GARBLED_PREFIX_PHRASE_RATIO = 0.62

# Some free decoders merge the complete prefixed phrase into one word. Real
# captures include "Hey Ruben" -> "herum" / "erhoben"; treating those as a
# missing prefix would either reject genuine calls or accidentally accept the
# bare core. The rescue therefore requires independent similarity to the
# prefix, core, and complete merged phrase, plus a bounded length ratio. A
# 100-positive / 500-negative real-window calibration on 2026-07-13 lifted
# recall without adding a negative acceptance. These rules are derived from
# the configured tokens and apply equally to every supported phrase.
_MERGED_PREFIX_RATIO = 0.40
_MERGED_CORE_RATIO = 0.40
_MERGED_PHRASE_RATIO = 0.53
_MERGED_MIN_LENGTH_RATIO = 0.55
_MERGED_MAX_LENGTH_RATIO = 1.20

# Word-agnostic energy floor for a candidate window (mirrors the stt_match
# path's RollingWhisperWake._match_min_rms — AP-27: silence is gated on raw
# energy, never on transcript content).
_MATCH_MIN_RMS = 0.006

# --- word-agnostic candidate shape (AP-27, forensic 2026-07-13) -------------
# Everything above asks the free decoder to SPELL the wake word. An offline
# small model has no arbitrary proper noun in its lexicon, so it CANNOT.
# Replaying 159 real captured "Hey Ruben" calls (data/wake_debug) through this
# detector: the free decoder spelled the phrase in only 28 % of genuine calls
# and otherwise produced sound-alike garbage — "herum", "erhoben", "hey room",
# "hey oben", "heroes". Rejecting on that garbage ate 38 % of all real wakes
# (end-to-end recall 32 %; the user had to repeat the phrase four or five
# times) while false accepts sat at 0/400. And no spelling threshold can close
# it: the free transcript "herr oben" was produced BOTH by a genuine call and
# by room chatter. Spelling cannot discriminate an out-of-vocabulary word — and
# EVERY wake word is out-of-vocabulary for some installed language model. That
# is precisely AP-27, reappearing in the Vosk path.
#
# What DOES discriminate, without ever asking how the wake word is written, is
# the SHAPE of what the free ear heard AT the candidate span:
#   * a wake call is short and stands alone    (measured: 0.72 s, 2 words)
#   * room speech is a longer word stream the  (measured: 1.29 s, 5 words,
#     decoder confidently recognises            top word confidence ~1.0)
# Both bounds are derived from the CONFIGURED phrase, never from its spelling,
# so they hold for any phrase in any supported language. ``sound_confirm``
# remains a BONUS path that may only ACCEPT (a free ear that did spell the
# phrase still fires instantly), so this can never make the detector deaf.
#
# Calibrated on 250 positive / 1650 negative real captured windows: verify
# pass-rate on genuine calls 55 % -> 74 %, at 3 false accepts (two of which are
# genuine calls the corpus labels negative: "ey ruben", "hei ruben").
_SHAPE_MAX_VOICED_S_PER_TOKEN = 0.65
_SHAPE_MAX_OTHER_WORD_CONF = 0.98

# The shape bounds above describe a SHORT, ISOLATED utterance — which a bare
# interjection also is. Live false wake (2026-07-13 11:05, first hour after the
# shape gate shipped): "hey ho" confirmed for "Hey Ruben". The free ear had
# heard the prefix plus a 0.12 s grunt: the NAME was never spoken and the
# grammar had stretched a bare "hey" onto the phrase.
#
# Neither spelling nor sound-similarity can catch that — measured on the real
# captures, room speech scores HIGHER against "ruben" (`den genie ring` 0.50,
# `simple frage brauchen` 0.62) than genuine calls do (`hey room` 0.25,
# `hey ho` 0.25). Any similarity floor that rejects the false wake also rejects
# real ones. <!-- the AP-27 trap, one level down -->
#
# The word-agnostic question that DOES separate them: was anything NAME-SIZED
# uttered where the name belongs? Strip the known wake prefixes from what the
# free ear heard and measure the voiced duration of what REMAINS — the core
# body. It never asks how the name is spelled, only that a name was spoken.
# Real captures: genuine calls carry a 0.48 s median core body (p10 0.30 s);
# 9/159 carry none at all — that is exactly the false-wake class. A 0.20 s floor
# (a single syllable) drops those 9 and costs 1.2 points of recall.
_SHAPE_MIN_CORE_BODY_S = 0.20
# No slack: the free ear may not hear MORE words at the span than the phrase
# itself has. Allowing one extra token to absorb an ASR split ("Jarvis" ->
# "joe avis") measurably let compact room speech through (5 vs 3 false accepts
# on 1650 real negative windows) — and it is not needed: a split core is
# exactly what ``sound_confirm``'s core_sizes tolerance already accepts, so the
# split case is covered by the spelling path and must not be paid for twice.
_SHAPE_TOKEN_SLACK = 0

# --- acoustic competition for the SHAPE path (live forensic 2026-07-17) -----
# The shape gate accepts anything that LOOKS like a wake call — which a call of
# a DIFFERENT name also does. Live: "hey nova" confirmed (shape) for the phrase
# "Hey Jarvis", "hey ruben" for "Hey Nova": every "<prefix> <other name>" call
# has exactly the shape of a genuine wake, so shape alone cannot separate them
# — and per AP-27 no spelling rule may reject either (measured again on this
# corpus: the free-decode confidences of adversarial calls are indistinguishable
# from genuine garbles, 0.45–0.88 both).
#
# What DOES separate them, without ever reading a spelling, is an ACOUSTIC
# competition: re-score the window with a grammar that offers, next to the
# configured phrase, an explicit "<prefix> [unk]" alternative. The decoder then
# chooses whether the name slot is better explained by the configured wake word
# or by ANY other word. A shape-only acceptance must win that competition; the
# spelling path stays accept-only and never consults it (a free ear that wrote
# the phrase down is stronger evidence than the competition is).
#
# Replay-calibrated on 918 real captured windows embedded in live-like ring
# context (2026-07-17, three phrases x foreign-name calls + ambient): kills
# every shape-path foreign-name fire (2 -> 0) at a 1-2 % genuine-recall cost,
# where gating the WHOLE verify on the competition (not just the shape path)
# cost 13 % of genuine "Hey Ruben" calls — that variant was rejected.
# Only a PREFIXED phrase has this competition: an unprefixed phrase ("Computer")
# already competes against the bare "[unk]" in the normal re-score grammar and
# offers no prefix anchor to build the alternative from.
_COMPETITION_KIND = "competition"

# Ring buffer length for the confirm pass — long enough to hold the full
# spoken phrase plus lead-in at the moment the partial trigger fires.
_RING_SECONDS = 3.0

# Refractory period after a fired wake.
_COOLDOWN_S = 5.0

# A rejected stage-one candidate is expected occasionally because grammar mode
# deliberately favours recall.  It must not immediately launch another pair of
# full-window decoders: production forensics on 2026-07-13 captured 99 verify
# passes in 140 seconds, enough to starve the WebView, overlay, microphone, and
# local HTTP listener in the shared desktop process.  A short reject-only
# backoff bounds that work on every OS while leaving the first candidate (and
# therefore the normal quiet-room wake path) at full speed.  Stage one keeps
# listening during this window and latches one retry; otherwise a user's
# immediate second call would land in a two-second deaf period.
_REJECTED_CANDIDATE_BACKOFF_S = 2.0

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

    # Recall rescue for an ASR-garbled prefix.  Do not slide this over longer
    # speech: exact local token coverage is what keeps a mention of the core
    # inside an ordinary sentence from becoming a wake.  Split/merged core
    # spellings still work through ``core_sizes``.
    if has_prefix:
        target_phrase = " ".join(sound_fold(token) for token in phrase_tokens)
        for size in core_sizes:
            if len(words) != 1 + size:
                continue
            heard_core = "".join(words[1:])
            core_score = SequenceMatcher(None, target_core, heard_core).ratio()
            heard_phrase = " ".join(words)
            phrase_score = SequenceMatcher(
                None, target_phrase, heard_phrase
            ).ratio()
            if (
                core_score >= max(float(ratio), _GARBLED_PREFIX_CORE_RATIO)
                and phrase_score >= _GARBLED_PREFIX_PHRASE_RATIO
            ):
                return True

        # The unconstrained decoder may merge the complete spoken phrase into
        # one token. Require that one token to carry separate prefix AND core
        # evidence; this keeps a bare core from satisfying a configured full
        # phrase while recovering generic ASR merges.
        if len(words) == 1:
            heard = words[0]
            target_prefix = "".join(
                sound_fold(token) for token in phrase_tokens[:prefix_count]
            )
            target_merged = "".join(sound_fold(token) for token in phrase_tokens)
            length_ratio = len(heard) / max(1, len(target_merged))
            if (
                _MERGED_MIN_LENGTH_RATIO
                <= length_ratio
                <= _MERGED_MAX_LENGTH_RATIO
                and SequenceMatcher(None, target_prefix, heard).ratio()
                >= _MERGED_PREFIX_RATIO
                and SequenceMatcher(None, target_core, heard).ratio()
                >= _MERGED_CORE_RATIO
                and SequenceMatcher(None, target_merged, heard).ratio()
                >= _MERGED_PHRASE_RATIO
            ):
                return True
    return False


def candidate_shape_ok(
    local_words: Sequence[dict],
    phrase: str,
    *,
    max_voiced_s_per_token: float = _SHAPE_MAX_VOICED_S_PER_TOKEN,
    max_other_word_conf: float = _SHAPE_MAX_OTHER_WORD_CONF,
    min_core_body_s: float = _SHAPE_MIN_CORE_BODY_S,
) -> bool:
    """Does the free ear's output AT the candidate span look like a wake call?

    Word-agnostic by construction (AP-27): it reads only how much was said at
    the span and how sure the free decoder was — never how the wake word is
    spelled. ``local_words`` are the free decode's word dicts (``word``,
    ``start``, ``end``, ``conf``) already localised to the phrase span.

    Four word-agnostic questions, all scaled by the configured phrase:

    1. **Not more words than the phrase has.** A wake call is the phrase; a
       forced grammar hit on conversation carries the surrounding words too.
       (A free decoder that SPLITS the name into two tokens — "Jarvis" ->
       "joe avis" — is already accepted by ``sound_confirm``'s core_sizes
       tolerance, so this gate does not need to pay for that case with the
       extra false accepts a token slack costs.)
    2. **Not spoken for longer than the phrase could be.** The grammar happily
       stretches the phrase across flowing speech; a real call cannot last
       longer than its own tokens do.
    3. **The free ear is not SURE it heard another core word.** This is the
       positive signal an out-of-vocabulary wake word leaves behind: the free
       decoder does not know the core, so it guesses and its confidence drops.
       Ordinary speech ("engineering", "google") it recognises outright. A
       known wake prefix is deliberately excluded from this confidence check.
    4. **A name was actually spoken.** Strip the known wake prefixes and the
       REMAINING voiced duration — the core body — must be at least a syllable.
       Without this, the three bounds above describe a bare interjection just
       as well as a wake call, and "hey ho" fires (live 2026-07-13). Note this
       still never reads the name's SPELLING: it only asks whether a
       name-sized sound exists where the name belongs.

    Empty input rejects: the grammar claimed the phrase where the free ear
    heard no speech at all.
    """
    if not local_words:
        return False
    n_tokens = max(1, len(normalize_phrase_for_match(phrase)))
    if len(local_words) > n_tokens + _SHAPE_TOKEN_SLACK:
        return False
    voiced_s = sum(
        max(0.0, float(w.get("end", 0.0)) - float(w.get("start", 0.0)))
        for w in local_words
    )
    if voiced_s > max_voiced_s_per_token * n_tokens:
        return False
    # A confidently recognised wake prefix is expected evidence, not proof the
    # decoder heard some OTHER word. Apply the confidence discriminator only
    # to the unknown core body. Otherwise a perfect ``hey`` confidence rejects
    # a genuine arbitrary name that the free decoder necessarily guesses.
    known_prefixes = {sound_fold(p) for p in WAKE_PREFIXES}
    phrase_tokens = normalize_phrase_for_match(phrase)
    phrase_is_all_prefix = all(
        sound_fold(t) in known_prefixes for t in phrase_tokens
    )
    core_words = [
        w
        for w in local_words
        if sound_fold(str(w.get("word", ""))) not in known_prefixes
    ]
    confidence_words = local_words if phrase_is_all_prefix else core_words
    if not confidence_words:
        return False
    top_conf = max(float(w.get("conf", 0.0)) for w in confidence_words)
    if top_conf > max_other_word_conf:
        return False
    # A phrase that IS nothing but prefixes ("Hey", "Hallo") has no core to
    # demand, so it must not be gated on a core duration.
    if phrase_is_all_prefix:
        return True
    core_body_s = sum(
        max(0.0, float(w.get("end", 0.0)) - float(w.get("start", 0.0)))
        for w in core_words
    )
    return core_body_s >= min_core_body_s


class VoskKwsProvider:
    """Any-word wake detector — structurally compatible with `WakeWordProvider`.

    ``phrase`` is the user's wake phrase; ``keyword`` is the canonical value
    yielded on a hit (the pipeline's trigger key). ``model_path`` points at an
    extracted Vosk model directory for the configured language.
    """

    name = "vosk_kws"

    # Catch-up contract with the pipeline fanout (``_queue_iter``): Kaldi
    # streams arbitrary buffer sizes, so when this detector falls behind live
    # audio (busy desktop CPU — live forensic 2026-07-21: the fanout queue sat
    # pinned at 50 x 100 ms chunks and every wake was heard ~5 s late), the
    # pipeline may coalesce up to this many backlogged chunks into ONE
    # AudioChunk. The per-chunk state machine below is byte-based
    # (``pending_tail``, ring) and decision-identical either way; batches only
    # ever form while catching up, never on the caught-up hot path.
    coalesce_catchup_chunks = 10

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
        rejected_candidate_backoff_s: float = _REJECTED_CANDIDATE_BACKOFF_S,
        confirm_tail_s: float = _CONFIRM_TAIL_S,
        # Production poll-loop parity: peak-normalize the confirm window to
        # -3 dBFS (gain capped at 40 dB) exactly like the other wake paths.
        target_peak: float = 0.7079,
        max_gain: float = 100.0,
        # Early visual side effect for the candidate-prefix verify: awaited
        # with True when the strict check passes at PARTIAL time, False when a
        # shown candidate is retracted by lifecycle teardown. Never carries
        # transcript text; never exposes an unverified hit. None = no visual.
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
        # Acoustic-competition grammar for shape-only acceptances: the phrase
        # must beat an explicit "<prefix> [unk]" alternative (None for an
        # unprefixed phrase — its normal re-score already competes with the
        # bare "[unk]" and there is no prefix anchor to build this from).
        raw_tokens = [t for t in self._phrase.lower().split() if t]
        self._competition_grammar: str | None = None
        if has_prefix and raw_tokens:
            self._competition_grammar = json.dumps(
                [self._phrase.lower(), f"{raw_tokens[0]} [unk]", "[unk]"]
            )
        self._match_min_rms = float(match_min_rms)
        self._cooldown_s = float(cooldown_s)
        self._rejected_candidate_backoff_s = max(
            0.0, float(rejected_candidate_backoff_s)
        )
        self._confirm_tail_bytes = int(float(confirm_tail_s) * sample_rate) * 2
        self._target_peak = float(target_peak)
        self._max_gain = float(max_gain)
        self._models: dict[str, Any] = {}
        # Set once ``start()`` has attempted every model load — the honest
        # "warm" floor even when a broken model directory never loads.
        self._load_attempted = False
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
        # Candidate-prefix state: the listener, whether a candidate is shown,
        # the in-flight strict verify task, and a generation counter so a LATE
        # completion can never show a candidate another lifecycle edge already
        # resolved.
        self._early_listener = early_candidate_listener
        self._early_active = False
        self._early_task: asyncio.Task[bool] | None = None
        self._pending_gen = 0
        # Instance-local clock hook keeps reject/backpressure state-machine
        # tests deterministic without replacing Python's process-global
        # monotonic clock (which asyncio itself also consumes).
        self._monotonic = time.monotonic
        # Session stats (parity with OpenWakeWordProvider.stats()).
        self._stat_chunks = 0
        self._stat_candidates = 0
        self._stat_gated_rms = 0
        self._stat_suppressed_confirm = 0
        self._stat_suppressed_cooldown = 0
        self._stat_backpressure_windows = 0
        self._stat_backpressure_chunks = 0
        self._stat_fired = 0
        self._stat_early_shown = 0
        self._stat_early_retracted = 0
        self._stat_suppressed_shape_competition = 0

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
        elif kind == _COMPETITION_KIND:
            if self._competition_grammar is None:  # unprefixed phrase
                rec = self._new_grammar_rec(path)
            else:
                rec = KaldiRecognizer(
                    self._ensure_model(path),
                    self._sample_rate,
                    self._competition_grammar,
                )
                rec.SetWords(True)
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
        kinds = ("grammar", "free") if self._competition_grammar is None else (
            "grammar", "free", _COMPETITION_KIND,
        )
        for path in self._model_paths:
            for kind in kinds:
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

    @property
    def is_warm(self) -> bool:
        """True once every configured model directory has finished loading.

        Consumed by the desktop wake-model priority gate: the heavy backend
        boot storm (brain/MCP/mission init) is held until the ACTIVE wake
        engine is warm so the model load runs uncontended. Gating on the
        utterance STT instead let the storm start mid-load and stretched a
        few-second Vosk model load to 30+ s (live forensic 2026-07-17).

        A broken model directory must not wedge the gate: once ``start()``
        has ATTEMPTED every load, warm is honest-true even if a load failed
        (detection continues on the working models; the gate's job — an
        uncontended load window — is over either way).
        """
        if self._load_attempted:
            return True
        return all(path in self._models for path in self._model_paths)

    async def start(self) -> None:
        """Pre-load every model and FILL the prewarmed recognizer stock.

        Replaces the former throwaway warm-up decode: the stock recognizers
        ARE the warm-up now (their silence decode pre-pays Kaldi's lazy
        first-decode init), and unlike the throwaways they are kept and
        consumed by the first real detect/verify. Fail-closed: errors must
        never break boot — takers fall back to cold builds.

        The per-language models load CONCURRENTLY: each is an independent
        Kaldi object (no shared engine, AP-24-safe) and the load is mostly
        native disk/CPU work, so overlapping them cuts multi-language boot
        from the sum of the loads to the slowest one (live forensic
        2026-07-17: en 34 s + de 24 s sequential under a boot storm).
        """
        async def _load_one(path: str) -> None:
            try:
                await asyncio.to_thread(self._ensure_model, path)
            except Exception as exc:  # noqa: BLE001 — a broken model must not
                # brick the working ones; _fresh_recs skips it too.
                log.warning("vosk-kws: model %s failed to load (%s).", path, exc)

        await asyncio.gather(*(_load_one(path) for path in self._model_paths))
        self._load_attempted = True
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
        self._load_attempted = False
        self._ring.clear()
        self._ring_len = 0

    def stats(self) -> dict[str, int]:
        return {
            "chunks": self._stat_chunks,
            "candidates": self._stat_candidates,
            "gated_rms": self._stat_gated_rms,
            "suppressed_confirm": self._stat_suppressed_confirm,
            "suppressed_cooldown": self._stat_suppressed_cooldown,
            "backpressure_windows": self._stat_backpressure_windows,
            "backpressure_chunks": self._stat_backpressure_chunks,
            "fired": self._stat_fired,
            "early_shown": self._stat_early_shown,
            "early_retracted": self._stat_early_retracted,
            "suppressed_shape_competition": (
                self._stat_suppressed_shape_competition
            ),
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
    ) -> bool:
        """Strictly verify the candidate prefix and optionally show the bar.

        ``True`` is an authoritative positive for this generation. The later
        fallback window may contain the first words of the user's command, so
        it must not overwrite a clean verdict over the candidate audio itself.
        """
        try:
            ok = await asyncio.to_thread(self._early_check, window, model_path)
        except Exception as exc:  # noqa: BLE001 — fallback verify remains
            log.debug("early-candidate check errored: %s", exc)
            return False
        if not ok or gen != self._pending_gen:
            return False
        log.info("vosk-kws: candidate prefix verified for %r", self._phrase)
        await self._notify_early(True)
        return True

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
            res = _parse_recognizer_json(rec.Result(), where="grammar_hit.Result")
            text = res.get("text", "")
            if self._phrase.lower() in text:
                words = [
                    w for w in res.get("result", [])
                    if w.get("word") in self._grammar_words
                ]
                conf = min((w.get("conf", 0.0) for w in words), default=0.0)
                return (True, conf)
            return None
        partial = _parse_recognizer_json(
            rec.PartialResult(), where="grammar_hit.PartialResult"
        ).get("partial", "")
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
        """Strict prefix verify on the truncated ring — fails CLOSED.

        A positive may confirm the wake; a negative only defers to the later
        fail-open full-window check and therefore cannot make the path deaf.
        """
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
                return _parse_recognizer_json(
                    g.FinalResult(), where="verify.grammar_final"
                )

            def _free_pass() -> dict:
                f = self._take_verify_rec(model_path, "free")
                f.AcceptWaveform(pcm)
                return _parse_recognizer_json(
                    f.FinalResult(), where="verify.free_final"
                )

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
                log.debug(
                    "vosk-kws: verify SUPPRESSED — re-score did not re-hear "
                    "%r (heard %r)",
                    self._phrase, gres.get("text", "")[:60],
                )
                return False
            conf = min(w.get("conf", 0.0) for w in gwords)
            if conf < self._min_final_conf:
                log.debug(
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
                log.debug(
                    "vosk-kws: verify SUPPRESSED — span rms %.4f < %.4f "
                    "(silence can never fire)", rms, self._match_min_rms,
                )
                return False

            # localise the (already-decoded) free words to the phrase span
            local_words = [
                w for w in fres.get("result", [])
                if w.get("end", 0.0) >= span_a and w.get("start", 0.0) <= span_b
            ]
            free_local = " ".join(w.get("word", "") for w in local_words)
        except Exception as exc:  # noqa: BLE001 — polarity via fail_open
            log.warning(
                "vosk-kws: verify failed (%s) — %s.",
                exc,
                "accepting" if fail_open else "rejecting (visual-only)",
            )
            return fail_open
        # Two INDEPENDENT ways to confirm, either of which is sufficient:
        #   (a) the free ear spelled the phrase        -> sound_confirm
        #   (b) it could not spell it (an offline model cannot spell an
        #       arbitrary proper noun) but what it heard at the span has the
        #       SHAPE of a wake call -> candidate_shape_ok (word-agnostic)
        # (a) alone was the AP-27 recall trap: it ate 38 % of real wakes,
        # because the wake word is out-of-vocabulary for the very decoder being
        # asked to write it down. (b) can never depend on the phrase's
        # spelling, so it holds for every wake word in every language.
        #
        # (b) alone was the PRECISION trap one level up (live 2026-07-17): a
        # call of a DIFFERENT name has exactly the shape of a wake call, so
        # "hey nova" fired for "Hey Jarvis". A shape-only acceptance therefore
        # must additionally WIN the acoustic competition — a purely acoustic
        # judgement that never reads a spelling, so (a)'s recall contract is
        # untouched: a free ear that spelled the phrase still fires instantly.
        ok = sound_confirm(free_local, self._phrase, ratio=self._confirm_ratio)
        by_shape = False
        if not ok and candidate_shape_ok(local_words, self._phrase):
            ok = by_shape = self._shape_competition_ok(pcm, model_path)
            if not ok:
                self._stat_suppressed_shape_competition += 1
        log_method = log.info if ok else log.debug
        log_method(
            "vosk-kws: verify %s (%s) — free ear heard %r at the candidate span "
            "(conf=%.2f) vs phrase %r",
            "OK" if ok else "SUPPRESSED",
            ("shape" if by_shape else "spelled") if ok else "neither",
            free_local[:60],
            conf,
            self._phrase,
        )
        return ok

    def _shape_competition_ok(self, pcm: bytes, model_path: str | None) -> bool:
        """Must the shape-only acceptance stand? Purely acoustic, fail-OPEN.

        Re-scores the window with a grammar that offers the configured phrase
        AND an explicit "<prefix> [unk]" competitor: the decoder itself decides
        whether the name slot is better explained by the configured wake word
        or by any other word. The window already passed the normal re-score,
        the energy gate, and the shape gate — this only breaks the tie the
        forced no-alternative grammar could not express (replay-calibrated
        2026-07-17: kills every shape-path foreign-name fire at a 1-2 %
        genuine-recall cost; see the _COMPETITION_KIND note).

        An unprefixed phrase has no competitor to offer — it always stands.
        Infrastructure errors accept (a broken extra check must never make the
        detector deaf; the spelling path never consults this at all).
        """
        if self._competition_grammar is None:
            return True
        try:
            rec = self._take_verify_rec(model_path, _COMPETITION_KIND)
            rec.AcceptWaveform(pcm)
            res = json.loads(rec.FinalResult())
            if self._phrase.lower() not in res.get("text", ""):
                log.debug(
                    "vosk-kws: shape acceptance lost the acoustic competition "
                    "— competitor grammar heard %r, not %r",
                    res.get("text", "")[:60],
                    self._phrase,
                )
                return False
            words = [
                w for w in res.get("result", [])
                if w.get("word") in self._grammar_words
            ]
            conf = min((w.get("conf", 0.0) for w in words), default=0.0)
            if conf < self._min_final_conf:
                log.debug(
                    "vosk-kws: shape acceptance lost the acoustic competition "
                    "— re-heard %r only at conf %.2f",
                    self._phrase,
                    conf,
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001 — extra check fails open
            log.warning(
                "vosk-kws: shape competition errored (%s) — accepting.", exc
            )
            return True

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
        verify_not_before = 0.0
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
            # Full verification remains rate-limited after a clean rejection,
            # but the cheap streaming grammar must keep listening.  Completely
            # pausing stage one made this a deaf period: a genuine immediate
            # retry was discarded before it could ever reach the verifier.
            # During the window we latch at most one candidate, stop stage-one
            # work once latched, and verify it only when the same deadline
            # expires.  This preserves the BUG-045 load bound without dropping
            # a user's retry.
            now_mono = self._monotonic()
            backpressure_active = now_mono < verify_not_before
            if backpressure_active:
                self._stat_backpressure_chunks += 1
            if not recs:
                recs = self._fresh_recs()
            if pending is not None:
                pending_tail += len(pcm)
                if backpressure_active:
                    # The retry is already latched.  Advancing the ring is
                    # enough; do not spend stage-one or verifier work yet.
                    continue
                # Keep every model's stream fed during the tail wait so their
                # decode state stays aligned with the ring.
                for r in recs.values():
                    self._grammar_hit(r, pcm)
                # A POSITIVE early check is already authoritative for this
                # candidate (later audio belongs to the session and cannot
                # revoke it — see _run_early_check). Waiting out the rest of
                # the confirm tail after that verdict is pure dead time on
                # every early-confirmed wake (~0.3-0.5 s of perceived spawn
                # latency), so fire as soon as the verdict lands. A pending,
                # failed, or negative early task changes nothing: the normal
                # tail wait + full-window verify below still decide.
                early_positive = (
                    self._early_task is not None
                    and self._early_task.done()
                    and not self._early_task.cancelled()
                    and self._early_task.exception() is None
                    and self._early_task.result() is True
                )
                if pending_tail < self._confirm_tail_bytes and not early_positive:
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
                if backpressure_active:
                    # One user retry (or one more room-speech candidate) is
                    # retained without launching any expensive decode.  A
                    # final already includes its endpoint; a partial keeps
                    # accumulating the normal confirmation tail in the ring.
                    pending = (is_final, conf, hit_path)
                    pending_tail = self._confirm_tail_bytes if is_final else 0
                    continue
                if is_final and conf < self._min_final_conf:
                    self._stat_backpressure_windows += 1
                    verify_not_before = (
                        self._monotonic() + self._rejected_candidate_backoff_s
                    )
                    recs = self._fresh_recs()
                    continue
                if not is_final and self._confirm_tail_bytes > 0:
                    pending = (is_final, conf, hit_path)
                    pending_tail = 0
                    # Strictly verify the audio heard SO FAR in a worker thread
                    # while the confirm tail keeps accumulating. A positive is
                    # enough to confirm this candidate and may reveal the bar;
                    # a negative still falls through to the later full window.
                    self._pending_gen += 1
                    self._early_task = asyncio.create_task(
                        self._run_early_check(
                            self._ring_window(), self._pending_gen, hit_path
                        )
                    )
                    continue
            # The candidate-prefix check and fallback decision must not fan out
            # two decoder pairs at once on a weak CPU. The 0.6 s tail normally
            # lets the prefix check finish already, so this is a no-cost
            # ordering guard in the common path. Most importantly, a positive
            # prefix verdict is monotonic: the newly captured tail belongs to
            # the user's command and cannot turn a verified wake back off.
            early_confirmed = False
            if self._early_task is not None:
                try:
                    early_confirmed = bool(await self._early_task)
                except Exception as exc:  # noqa: BLE001 — fallback verify remains
                    log.debug(
                        "candidate-prefix task failed; using full-window fallback: %s",
                        exc,
                    )
                finally:
                    self._early_task = None
            now = time.time()
            window = self._ring_window()
            fired_path = hit_path
            if early_confirmed:
                confirmed = True
                log.info(
                    "vosk-kws: retaining verified candidate prefix for %r; "
                    "following audio belongs to the voice session",
                    self._phrase,
                )
            else:
                confirmed = await asyncio.to_thread(
                    self._verify_candidate, window, hit_path
                )
            if not confirmed:
                # Sibling rescue: the model that HEARD the candidate could not
                # verify it, but the ring still holds the phrase — let every
                # other model try (union recall, measured +38%: 'Hey Jarvis'
                # de-spoken garbles on the de model yet verifies on en).
                # Fail-CLOSED via _early_check: an opportunistic rescue must
                # never fire off a broken sibling (the fail-open contract
                # protects only the primary confirm). The siblings verify
                # CONCURRENTLY — each owns its fresh one-shot recognizers
                # (AP-24-safe: nothing mutable is shared), and the sequential
                # form paid the SUM of two full verifies right on the fire
                # path of exactly the phrases the primary model garbles. The
                # decision is unchanged: the first sibling in configured
                # order that verifies still wins.
                others = [
                    other
                    for other in self._model_paths
                    if other != hit_path and other in recs
                ]
                if others:
                    rescues = await asyncio.gather(
                        *(
                            asyncio.to_thread(self._early_check, window, other)
                            for other in others
                        )
                    )
                    for other, rescued in zip(others, rescues, strict=True):
                        if rescued:
                            confirmed = True
                            fired_path = other
                            break
            if not confirmed:
                self._stat_suppressed_confirm += 1
                # Invalidate this generation and defensively retract any stale
                # visual state. A positive prefix task cannot reach this branch;
                # it was consumed as the monotonic verdict above.
                self._pending_gen += 1
                await self._notify_early(False)
                self._stat_backpressure_windows += 1
                verify_not_before = (
                    self._monotonic() + self._rejected_candidate_backoff_s
                )
                if (
                    self._stat_backpressure_windows == 1
                    or self._stat_backpressure_windows % 25 == 0
                ):
                    log.warning(
                        "vosk-kws: rejected-candidate backpressure active "
                        "(pause %.1fs, windows=%d, skipped_chunks=%d) — "
                        "protecting desktop responsiveness.",
                        self._rejected_candidate_backoff_s,
                        self._stat_backpressure_windows,
                        self._stat_backpressure_chunks,
                    )
                # Re-arm one cheap streaming recognizer set now.  It may latch
                # one retry during backpressure, but no second full verify can
                # run before the deadline above.
                recs = self._fresh_recs()
                continue
            # The shown flag stays set for the pipeline to CONSUME: it must know
            # the bar is visible when it silently drops this wake (echo lock)
            # and retract it.
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


__all__ = [
    "VoskKwsProvider",
    "candidate_shape_ok",
    "sound_confirm",
    "vosk_model_supports_phrase",
]

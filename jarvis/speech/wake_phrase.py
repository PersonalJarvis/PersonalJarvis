"""Configurable wake-word phrase matching + engine resolution.

This is the heart of the custom-wake-word feature (see
docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md). Two public pieces:

``compile_wake_matcher(phrase)`` -> :class:`WakeMatcher`
    A matcher that **duck-types ``re.Pattern.search``** (returns an object with
    ``.group(0)``) so it is a drop-in replacement everywhere the wake paths
    currently thread a compiled regex. For the default "Hey Jarvis" phrase it
    delegates to the canonical strict pattern (legacy behaviour preserved). For
    an arbitrary phrase it fuzzy-matches a noisy STT transcript.

``resolve_wake_plan(cfg, *, local_whisper_available)`` -> :class:`WakeWordPlan`
    Turns the user's ``[trigger.wake_word]`` config into a concrete engine
    choice, honouring the cloud-first doctrine: a phrase with no pretrained
    model needs local Whisper, and on a box without it we degrade gracefully to
    the bundled "Hey Rhasspy" model with a clear English message — never a
    silent dead listener. Users who type "Hey Jarvis" still get the hey_jarvis
    offline model (it is in ``KNOWN_OWW_MODELS``); "Hey Rhasspy" is just the
    neutral out-of-box shipped fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from jarvis.plugins.wake.openwakeword_provider import PRODUCTION_WAKE_THRESHOLD
from jarvis.speech.wake_constants import (
    DEFAULT_WAKE_PHRASE,
    JARVIS_WAKE_PATTERN,
    WAKE_ENGINES,
    WAKE_PREFIXES,
    match_known_oww_model,
    normalize_phrase_for_match,
    phrase_core,
    phrase_core_for_match,
    resolve_oww_model_path,
    sound_fold,
)

log = logging.getLogger("jarvis.wake.phrase")

# Lower threshold floor (most sensitive) and upper ceiling (least sensitive)
# for the sensitivity->threshold mapping. The midpoint (sensitivity 0.5) is
# pinned to the data-driven PRODUCTION_WAKE_THRESHOLD so the default is
# unchanged and the BUG-009 floor reasoning still holds at the default.
_THRESHOLD_CEILING = 0.30  # sensitivity 0.0
_THRESHOLD_FLOOR = 0.06    # sensitivity 1.0


def sensitivity_to_threshold(sensitivity: float) -> float:
    """Map a 0..1 sensitivity onto an openWakeWord activation threshold.

    Anchored piecewise-linear: 0.0 -> 0.30, 0.5 -> PRODUCTION_WAKE_THRESHOLD,
    1.0 -> 0.06. Higher sensitivity => lower threshold => easier to trigger.
    """
    s = max(0.0, min(1.0, float(sensitivity)))
    if s <= 0.5:
        span = PRODUCTION_WAKE_THRESHOLD - _THRESHOLD_CEILING
        return _THRESHOLD_CEILING + span * (s / 0.5)
    span = _THRESHOLD_FLOOR - PRODUCTION_WAKE_THRESHOLD
    return PRODUCTION_WAKE_THRESHOLD + span * ((s - 0.5) / 0.5)


# stt_match responsiveness: the openWakeWord threshold above is meaningless for
# the local-Whisper transcript path (it does not score against a threshold), so
# a custom-phrase user moving the Sensitivity slider felt NOTHING. Map the same
# 0..1 slider onto how OFTEN that path re-transcribes the rolling window: a
# higher sensitivity polls more often, so a spoken wake is picked up sooner.
# Both ends are deliberately FAST — the user's mandate is "always as low as
# possible" (2026-07-02). The slider still trims a little, but even its lowest
# position must not feel slow, so the slow end is 0.12 s, not a lazy 0.20 s.
_POLL_INTERVAL_SLOW = 0.12   # sensitivity 0.0 — still snappy
_POLL_INTERVAL_FAST = 0.08   # sensitivity 1.0 — snappiest reaction


def sensitivity_to_poll_interval(sensitivity: float) -> float:
    """Map a 0..1 sensitivity onto the stt_match wake poll interval (seconds).

    Linear: 0.0 -> 0.12 s, 1.0 -> 0.08 s. Both ends are fast on purpose (the
    "always as low as possible" mandate); higher sensitivity just trims a little
    more. The dominant latency floor is the ~0.5 s transcription itself, so the
    poll interval is a minor knob — the real win is skipping the second
    confirming transcription on a clearly-loud wake (unconditional).
    """
    s = max(0.0, min(1.0, float(sensitivity)))
    return _POLL_INTERVAL_SLOW + (_POLL_INTERVAL_FAST - _POLL_INTERVAL_SLOW) * s


class _FuzzyMatch:
    """Minimal ``re.Match`` stand-in: only ``.group(0)`` is contracted."""

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        self._text = text

    def group(self, index: int = 0) -> str:
        if index != 0:
            raise IndexError("WakeMatcher only exposes group(0)")
        return self._text

    def __bool__(self) -> bool:  # pragma: no cover - defensive
        return True


class WakeMatcher:
    """Phrase matcher that duck-types ``re.Pattern.search(text)``.

    - jarvis-default: wraps the canonical :data:`JARVIS_WAKE_PATTERN`.
    - arbitrary phrase: fuzzy token-window match against a normalised
      transcript using ``difflib.SequenceMatcher``. When the configured phrase
      starts with a known wake prefix ("Hey Fable"), the matched window must be
      IMMEDIATELY preceded by a prefix token too (``require_known_prefix``) —
      the bare core word inside ordinary speech must NOT activate (user mandate
      2026-07-02; measured 71.7 % false accepts on real bare-core windows
      before this). Any known prefix counts, not just the configured one, so an
      STT that writes "Hallo Fable" for a spoken "Hey Fable" still matches.
    """

    def __init__(
        self,
        *,
        pattern: Any | None = None,
        core_tokens: list[str] | None = None,
        fuzzy_ratio: float = 0.8,
        is_jarvis_default: bool = False,
        require_known_prefix: bool = False,
    ) -> None:
        self._pattern = pattern
        # Sound-fold the core so a sound-equivalent ASR spelling of the wake word
        # ("Nico" -> "Niko"/"Nicko"/"Nikko") compares equal (see ``sound_fold``).
        self._core = [sound_fold(c) for c in (core_tokens or [])]
        self._ratio = fuzzy_ratio
        self._is_jarvis = is_jarvis_default
        self._require_prefix = require_known_prefix
        # Folded prefix family: the window before the core must fuzzy-match ANY
        # of these when the configured phrase itself carries a prefix.
        self._folded_prefixes = tuple({sound_fold(p) for p in WAKE_PREFIXES})

    @property
    def is_jarvis_default(self) -> bool:
        return self._is_jarvis

    def _effective_ratio(self, core_token: str) -> float:
        """The match ratio required for one core token.

        Short proper-noun wake words ("Neko", "Mia", "Leo") are penalised
        hardest by ``SequenceMatcher``: a single STT mishearing ("Neko" ->
        "Niko") is a one-character substitution, which on a 4-char token drops
        the ratio to ~0.75 — just under the 0.8 default, so the word "never
        works". For short cores we therefore relax the bar to allow ~one
        character of drift (never below an absolute 0.6 floor so it cannot
        become a hair-trigger). A core of 6+ chars keeps the configured ratio
        unchanged, so a longer word — or an explicitly strict matcher on one —
        stays as strict as before (the configurable-ratio contract is intact).
        """
        n = len(core_token)
        if n >= 6:
            return self._ratio
        # ratio of an n-char token vs the same token with one char substituted.
        one_sub_ratio = (n - 1) / n if n else 1.0
        return min(self._ratio, max(0.6, one_sub_ratio - 0.01))

    def _is_prefix_token(self, folded_token: str) -> bool:
        """True when ``folded_token`` fuzzy-matches ANY known wake prefix."""
        return any(
            SequenceMatcher(None, p, folded_token).ratio()
            >= self._effective_ratio(p)
            for p in self._folded_prefixes
        )

    def search(self, text: str) -> Any | None:
        """Return a match object (``.group(0)``) or ``None``. Never raises."""
        if not text:
            return None
        if self._pattern is not None:
            return self._pattern.search(text)
        # Compare on sound-FOLDED tokens so a sound-equivalent ASR spelling of the
        # wake word lines up with the (also folded) core, but return the ORIGINAL
        # heard text as the match so the yielded keyword / logs are unchanged.
        orig = normalize_phrase_for_match(text)
        folded = [sound_fold(t) for t in orig]
        n = len(self._core)
        if n == 0 or len(folded) < n:
            return None
        # EVERY core token must individually clear its own length-aware bar
        # (short names tolerate ~one character of ASR drift, longer tokens keep
        # the configured ratio). Averaging across tokens is deliberately NOT
        # used: with the prefix in the window a perfect "hey" would subsidise a
        # half-matching core word — exactly the loosening the 2026-07-02
        # fire-only-on-the-phrase mandate forbids.
        for i in range(0, len(folded) - n + 1):
            window = folded[i : i + n]
            ok = all(
                SequenceMatcher(None, c, w).ratio() >= self._effective_ratio(c)
                for c, w in zip(self._core, window, strict=False)
            )
            if not ok:
                continue
            if self._require_prefix:
                # The configured phrase has a wake prefix ("Hey Fable") -> the
                # core must be IMMEDIATELY preceded by a prefix token. The bare
                # core word inside ordinary speech ("1 Fable Pro", "Nico, mein
                # Barsch.") stays silent — the 2026-07-02 user mandate that
                # REVERSED the 2026-06-29 "prefix optional" trade-off. Any
                # known prefix counts ("Hallo Fable" still wakes a "Hey Fable"
                # config; ASR often localises the greeting).
                if i == 0 or not self._is_prefix_token(folded[i - 1]):
                    continue
                return _FuzzyMatch(" ".join(orig[i - 1 : i + n]))
            return _FuzzyMatch(" ".join(orig[i : i + n]))
        return None


def compile_wake_matcher(phrase: str, *, fuzzy_ratio: float = 0.8) -> WakeMatcher:
    """Build a :class:`WakeMatcher` for ``phrase``.

    The default "Hey Jarvis" (and any jarvis-only phrase) is matched by the
    strict legacy pattern. Every other phrase fuzzy-matches on its CORE tokens;
    when the configured phrase itself starts with a known wake prefix ("Hey
    Fable"), the prefix is REQUIRED immediately before the core (any known
    prefix counts — "Hallo Fable" still wakes a "Hey Fable" config).

    History: 2026-06-29 deliberately made the prefix OPTIONAL ("lieber leichter
    triggern als schwer") because the slow poll cadence (~1.7 s vs a 1.8 s
    window) split the phrase across snapshots. That trade-off is REVERSED by
    explicit user instruction (2026-07-02): the bare core word in ordinary /
    dictated speech kept activating Jarvis (live: 'WAKE matched fable in
    "1 Fable Pro"', 'nico in "Nico, mein Barsch."'; bench: 71.7 % false accepts
    on real bare-core windows). Fire ONLY on the configured phrase. The
    split-window concern is addressed by the faster transcription cadence
    (2026-07-02 wake-latency work) — consecutive windows overlap by more than a
    spoken two-word phrase, so a genuine wake still lands whole in at least one
    window. A single-word phrase ("Computer") has no prefix to require; the
    word itself is the phrase, matched anywhere (inherent to one-word wakes).
    """
    tokens = normalize_phrase_for_match(phrase)
    core = phrase_core_for_match(phrase)
    if core == ["jarvis"]:
        return WakeMatcher(pattern=JARVIS_WAKE_PATTERN, is_jarvis_default=True)
    # A leading known prefix was stripped from the core -> the user's phrase
    # carries one -> demand it in the transcript too.
    has_prefix = len(core) < len(tokens)
    return WakeMatcher(
        core_tokens=core,
        fuzzy_ratio=fuzzy_ratio,
        require_known_prefix=has_prefix,
    )


def _canonical_keyword(phrase: str) -> str:
    """A stable lower_snake keyword for a phrase (logging / yield value)."""
    known = match_known_oww_model(phrase)
    if known:
        return known
    core = phrase_core(phrase)
    return "_".join(core) if core else "wake"


def custom_model_matches_phrase(model_path: str, phrase: str) -> bool:
    """True when a trained wake-model FILE belongs to ``phrase``.

    The trainer names models after their phrase (``hey_nico.onnx`` for
    "Hey Nico"), so ownership is decided by comparing the sound-folded core
    tokens of the phrase against the sound-folded tokens of the file stem.
    Sound-folding keeps spelling variants of the same word together
    ("Hey Niko" still owns ``hey_nico.onnx``). An empty phrase owns nothing.

    Why this exists (live bug 2026-07-02): ``custom_model_path`` stays in the
    config when the user changes the wake phrase in Settings, and the resolver
    used to let ANY custom path win — the stale model then kept detecting the
    OLD word and the new phrase was deaf ("only Hey Nico still works").
    """
    core = [sound_fold(t) for t in phrase_core_for_match(phrase)]
    if not core:
        return False
    stem_tokens = {sound_fold(t) for t in normalize_phrase_for_match(Path(model_path).stem)}
    return all(token in stem_tokens for token in core)


# ---------------------------------------------------------------------------
# Engine resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WakeWordPlan:
    """Concrete, resolved wake configuration the pipeline can act on.

    Note: in the graceful-degrade case (an arbitrary phrase on a box without
    local Whisper) ``phrase`` keeps the user's *requested* word (e.g. "Computer")
    while ``engine``/``oww_keyword``/``matcher`` are all "Hey Rhasspy" and
    ``degraded`` is True — so ``phrase != oww_keyword`` is a valid, intentional
    state. ``phrase`` is what to show the user; ``oww_keyword`` is what fires.
    """

    phrase: str
    engine: str                  # resolved concrete engine (never "auto")
    oww_model_path: str | None   # absolute ONNX path for openwakeword/custom_onnx
    oww_keyword: str             # canonical key OWW reports / trigger keyword
    threshold: float             # OWW activation threshold (from sensitivity)
    matcher: WakeMatcher         # phrase matcher for the verifier + rolling-whisper
    needs_local_whisper: bool    # True only for the stt_match path
    degraded: bool               # True if we could not honour the request
    message: str                 # English status string for logs + UI
    # Whether an OpenWakeWord hit needs the second-stage STT prefix check.
    # True for the jarvis family (the hey_jarvis model also fires on bare
    # "Jarvis" — BUG-009) AND for user-trained custom_onnx models (live
    # forensic 2026-07-01: a few-shot/synthetic-data model scored breath,
    # ambient noise and arbitrary speech up to 1.000 — several false
    # activations per minute; the verify transcript, matched against the
    # phrase's own sound-folded fuzzy matcher, is the real discriminator).
    # False only for a specific PRETRAINED model (alexa/mycroft/rhasspy):
    # those are trained on large curated datasets, ARE their own
    # discriminator, and the STT would mis-transcribe the foreign brand word
    # and wrongly reject valid hits.
    verify_prefix: bool


def _read(cfg: Any, name: str, default: Any) -> Any:
    value = getattr(cfg, name, default)
    return default if value is None else value


def resolve_wake_plan(cfg: Any, *, local_whisper_available: bool) -> WakeWordPlan:
    """Resolve a ``[trigger.wake_word]`` config into a :class:`WakeWordPlan`.

    ``cfg`` is duck-typed: any object exposing ``phrase``, ``engine``,
    ``custom_model_path``, ``sensitivity``, ``fuzzy_match_ratio``.
    """
    phrase = str(_read(cfg, "phrase", DEFAULT_WAKE_PHRASE)).strip()
    engine_pref = str(_read(cfg, "engine", "auto")).strip().lower()
    if engine_pref not in WAKE_ENGINES:
        log.warning("Unknown wake engine %r — coercing to 'auto'.", engine_pref)
        engine_pref = "auto"
    custom_path = str(_read(cfg, "custom_model_path", "")).strip()
    sensitivity = float(_read(cfg, "sensitivity", 0.5))
    fuzzy = float(_read(cfg, "fuzzy_match_ratio", 0.8))

    threshold = sensitivity_to_threshold(sensitivity)
    # A TRAINED custom_onnx model outputs well-calibrated 0..1 scores — a genuine
    # wake lands ~0.85-0.95 and breath/ambient/other-words well below — NOT the
    # 0.15-0.23 band the pretrained-openWakeWord 0.06-0.30 sensitivity map was
    # tuned for. Using that low band for a custom model false-fires on breathing
    # and random words (live 2026-07-01). So a custom model uses a higher band:
    # 0.0 -> 0.70 (strict), 0.5 -> 0.50 (balanced default), 1.0 -> 0.30 (sensitive).
    custom_threshold = 0.70 - 0.40 * max(0.0, min(1.0, sensitivity))
    matcher = compile_wake_matcher(phrase, fuzzy_ratio=fuzzy)
    canonical = _canonical_keyword(phrase)

    # 1. Custom ONNX model. Auto-adopted ONLY when it belongs to the
    # configured phrase; an explicit engine="custom_onnx" still forces it
    # regardless of its filename (the user's own training, their own naming).
    # Live bug 2026-07-02: the phrase was changed to "Hey Fable" in Settings,
    # but the stale hey_nico.onnx path left in config used to win here
    # unconditionally — the NICO model stayed the detector and the new phrase
    # was deaf. A stale model falls through to the normal any-phrase path and
    # is kept in config so switching the phrase back re-adopts it.
    custom_missing = False
    custom_stale = False
    if custom_path:
        if not Path(custom_path).is_file():
            custom_missing = True
            log.warning("Custom wake ONNX not found: %s", custom_path)
            # fall through — try STT match, else degrade.
        elif engine_pref == "custom_onnx" or custom_model_matches_phrase(
            custom_path, phrase
        ):
            return WakeWordPlan(
                phrase=phrase,
                engine="custom_onnx",
                oww_model_path=custom_path,
                oww_keyword=canonical,
                threshold=custom_threshold,
                matcher=matcher,
                needs_local_whisper=False,
                degraded=False,
                message=f"Custom ONNX wake model: {custom_path}",
                # ALWAYS verify custom-model hits with the STT prefix gate.
                # Live forensic 2026-07-01: trusting the trained model alone
                # ("it IS its own discriminator") caused a false-positive storm
                # — scores up to 1.000 on breath/ambient/other speech. The
                # verifier matches against this phrase's sound-folded fuzzy
                # matcher, so it works for ANY configured wake word and
                # tolerates ASR spelling drift ("Niko" for "Nico").
                verify_prefix=True,
            )
        else:
            custom_stale = True
            log.info(
                "Custom wake model '%s' belongs to a different phrase — "
                "resolving '%s' through the normal engine chain.",
                Path(custom_path).name,
                phrase,
            )

    # 2. Known pretrained openWakeWord model (CPU, instant, offline).
    known = match_known_oww_model(phrase)
    if engine_pref in ("auto", "openwakeword") and known and (
        not custom_path or custom_stale
    ):
        model_path = resolve_oww_model_path(known)
        if model_path is not None:
            return WakeWordPlan(
                phrase=phrase,
                engine="openwakeword",
                oww_model_path=model_path,
                oww_keyword=known,
                threshold=threshold,
                matcher=matcher,
                needs_local_whisper=False,
                degraded=False,
                message=f"Pretrained openWakeWord model '{known}'.",
                verify_prefix=matcher.is_jarvis_default,
            )
        log.warning("Pretrained model '%s' not found on disk.", known)

    # 3. Arbitrary phrase via local-Whisper transcript match.
    want_stt = (
        engine_pref in ("auto", "stt_match")
        or bool(custom_path)                       # custom file missing -> best effort
        or (engine_pref == "openwakeword" and not known)
    )
    if want_stt and local_whisper_available:
        # A STALE custom model (belongs to another phrase) is NOT a degrade:
        # the transcript match IS the regular path for the new phrase, and the
        # model stays configured for when the user switches back.
        degraded = custom_missing or (engine_pref == "openwakeword" and not known)
        if custom_missing:
            message = (
                f"Custom ONNX not found ({custom_path}); "
                "using local-Whisper transcript match instead."
            )
        elif custom_stale:
            message = (
                f"Custom wake model '{Path(custom_path).name}' belongs to a "
                f"different phrase; '{phrase}' uses the local-Whisper "
                "transcript match."
            )
        else:
            message = f"Custom phrase '{phrase}' via local-Whisper transcript match."
        return WakeWordPlan(
            phrase=phrase,
            engine="stt_match",
            oww_model_path=None,
            oww_keyword=canonical,
            threshold=threshold,
            matcher=matcher,
            needs_local_whisper=True,
            degraded=degraded,
            message=message,
            verify_prefix=matcher.is_jarvis_default,
        )

    # 4. Graceful degrade — fall back to the bundled hey_rhasspy model (neutral
    # offline fallback that ships without any product-name association). The
    # hey_rhasspy model is its own discriminator, so the German-pinned STT prefix
    # verifier must NOT run (verify_prefix=False): applying it would reject valid
    # hits because the STT transcribes the rhasspy sound differently from "Jarvis".
    rhasspy_path = resolve_oww_model_path("hey_rhasspy")
    _phrase_label = phrase or "Hey Rhasspy"
    return WakeWordPlan(
        phrase=phrase,
        engine="openwakeword",
        oww_model_path=rhasspy_path,
        oww_keyword="hey_rhasspy",
        threshold=threshold,
        matcher=compile_wake_matcher("Hey Rhasspy"),
        needs_local_whisper=False,
        degraded=True,
        message=(
            f"Wake phrase '{_phrase_label}' needs the local-Whisper extra ([desktop])"
            " or a custom ONNX model; falling back to the bundled 'Hey Rhasspy'"
            " offline model. Install [desktop] or supply a custom .onnx to use a"
            " different phrase."
        ),
        verify_prefix=False,  # rhasspy model IS the discriminator; no prefix gate
    )


__all__ = [
    "WakeMatcher",
    "WakeWordPlan",
    "compile_wake_matcher",
    "custom_model_matches_phrase",
    "resolve_wake_plan",
    "sensitivity_to_poll_interval",
    "sensitivity_to_threshold",
]

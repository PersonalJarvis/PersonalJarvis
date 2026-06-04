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
    "Hey Jarvis" with a clear English message — never a silent dead listener.
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
    match_known_oww_model,
    normalize_phrase,
    phrase_core,
    resolve_oww_model_path,
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
        return _THRESHOLD_CEILING + (PRODUCTION_WAKE_THRESHOLD - _THRESHOLD_CEILING) * (s / 0.5)
    return PRODUCTION_WAKE_THRESHOLD + (_THRESHOLD_FLOOR - PRODUCTION_WAKE_THRESHOLD) * ((s - 0.5) / 0.5)


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
      transcript using ``difflib.SequenceMatcher``.
    """

    def __init__(
        self,
        *,
        pattern: Any | None = None,
        core_tokens: list[str] | None = None,
        fuzzy_ratio: float = 0.8,
        is_jarvis_default: bool = False,
    ) -> None:
        self._pattern = pattern
        self._core = core_tokens or []
        self._ratio = fuzzy_ratio
        self._is_jarvis = is_jarvis_default

    @property
    def is_jarvis_default(self) -> bool:
        return self._is_jarvis

    def search(self, text: str) -> Any | None:
        """Return a match object (``.group(0)``) or ``None``. Never raises."""
        if not text:
            return None
        if self._pattern is not None:
            return self._pattern.search(text)
        tokens = normalize_phrase(text)
        n = len(self._core)
        if n == 0 or len(tokens) < n:
            return None
        for i in range(0, len(tokens) - n + 1):
            window = tokens[i : i + n]
            score = sum(
                SequenceMatcher(None, c, w).ratio() for c, w in zip(self._core, window)
            ) / n
            if score >= self._ratio:
                return _FuzzyMatch(" ".join(window))
        return None


def compile_wake_matcher(phrase: str, *, fuzzy_ratio: float = 0.8) -> WakeMatcher:
    """Build a :class:`WakeMatcher` for ``phrase``.

    The default "Hey Jarvis" (and any jarvis-only phrase) is matched by the
    strict legacy pattern; everything else is fuzzy-matched on its core tokens.
    """
    core = phrase_core(phrase)
    if core == ["jarvis"]:
        return WakeMatcher(pattern=JARVIS_WAKE_PATTERN, is_jarvis_default=True)
    return WakeMatcher(core_tokens=core, fuzzy_ratio=fuzzy_ratio)


def _canonical_keyword(phrase: str) -> str:
    """A stable lower_snake keyword for a phrase (logging / yield value)."""
    known = match_known_oww_model(phrase)
    if known:
        return known
    core = phrase_core(phrase)
    return "_".join(core) if core else "wake"


# ---------------------------------------------------------------------------
# Engine resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WakeWordPlan:
    """Concrete, resolved wake configuration the pipeline can act on.

    Note: in the graceful-degrade case (an arbitrary phrase on a box without
    local Whisper) ``phrase`` keeps the user's *requested* word (e.g. "Computer")
    while ``engine``/``oww_keyword``/``matcher`` are all "Hey Jarvis" and
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
    # Whether an OpenWakeWord hit needs the second-stage STT prefix check. This
    # is ONLY for the jarvis family (the hey_jarvis model also fires on bare
    # "Jarvis" — BUG-009). For a specific pretrained model (alexa/mycroft/
    # rhasspy) or a custom model, the model IS the discriminator and the German-
    # pinned STT would otherwise mis-transcribe the wake word and reject valid
    # hits. So verify_prefix is True only when the matcher is the jarvis default.
    verify_prefix: bool


def _read(cfg: Any, name: str, default: Any) -> Any:
    value = getattr(cfg, name, default)
    return default if value is None else value


def resolve_wake_plan(cfg: Any, *, local_whisper_available: bool) -> WakeWordPlan:
    """Resolve a ``[trigger.wake_word]`` config into a :class:`WakeWordPlan`.

    ``cfg`` is duck-typed: any object exposing ``phrase``, ``engine``,
    ``custom_model_path``, ``sensitivity``, ``fuzzy_match_ratio``.
    """
    phrase = str(_read(cfg, "phrase", DEFAULT_WAKE_PHRASE)).strip() or DEFAULT_WAKE_PHRASE
    engine_pref = str(_read(cfg, "engine", "auto")).strip().lower()
    if engine_pref not in WAKE_ENGINES:
        log.warning("Unknown wake engine %r — coercing to 'auto'.", engine_pref)
        engine_pref = "auto"
    custom_path = str(_read(cfg, "custom_model_path", "")).strip()
    sensitivity = float(_read(cfg, "sensitivity", 0.5))
    fuzzy = float(_read(cfg, "fuzzy_match_ratio", 0.8))

    threshold = sensitivity_to_threshold(sensitivity)
    matcher = compile_wake_matcher(phrase, fuzzy_ratio=fuzzy)
    canonical = _canonical_keyword(phrase)

    # 1. Explicit custom ONNX (or any custom path on a non-stt engine).
    if custom_path:
        if Path(custom_path).is_file():
            return WakeWordPlan(
                phrase=phrase,
                engine="custom_onnx",
                oww_model_path=custom_path,
                oww_keyword=canonical,
                threshold=threshold,
                matcher=matcher,
                needs_local_whisper=False,
                degraded=False,
                message=f"Custom ONNX wake model: {custom_path}",
                verify_prefix=matcher.is_jarvis_default,
            )
        log.warning("Custom wake ONNX not found: %s", custom_path)
        # fall through — try STT match, else degrade.

    # 2. Known pretrained openWakeWord model (CPU, instant, offline).
    known = match_known_oww_model(phrase)
    if engine_pref in ("auto", "openwakeword") and known and not custom_path:
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
        degraded = bool(custom_path) or (engine_pref == "openwakeword" and not known)
        if custom_path:
            message = (
                f"Custom ONNX not found ({custom_path}); "
                "using local-Whisper transcript match instead."
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

    # 4. Graceful degrade — fall back to bundled "Hey Jarvis", explain why.
    jarvis_path = resolve_oww_model_path("hey_jarvis")
    return WakeWordPlan(
        phrase=phrase,
        engine="openwakeword",
        oww_model_path=jarvis_path,
        oww_keyword="hey_jarvis",
        threshold=threshold,
        matcher=compile_wake_matcher(DEFAULT_WAKE_PHRASE),
        needs_local_whisper=False,
        degraded=True,
        message=(
            f"Wake word '{phrase}' needs the local-Whisper extra ([desktop]) or a "
            "custom ONNX model; falling back to 'Hey Jarvis'."
        ),
        verify_prefix=True,  # we fell back to hey_jarvis, which needs the gate
    )


__all__ = [
    "WakeMatcher",
    "WakeWordPlan",
    "compile_wake_matcher",
    "resolve_wake_plan",
    "sensitivity_to_threshold",
]

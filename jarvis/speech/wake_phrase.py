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
    choice through a fully GENERIC chain (design 2026-07-07 — the product
    ships no named wake model and never resolves a phrase against one):
    user-trained custom .onnx -> any-word Vosk keyword spotting ->
    local-Whisper transcript match -> honest hotkey-only degrade
    (``wake_available=False`` with a clear English message — never a silent
    dead listener, never a substituted fallback word).
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
    WAKE_ENGINES,
    WAKE_PREFIXES,
    normalize_phrase_for_match,
    phrase_core,
    phrase_core_for_match,
    resolve_vosk_model_path,
    resolve_vosk_model_paths,
    sound_fold,
)

log = logging.getLogger("jarvis.wake.phrase")

# The user-facing Sensitivity slider was removed (2026-07-10 mandate: "remove
# the control; always spawn at maximum speed on every OS" — identically on
# Windows/macOS/Linux, no per-user tuning). Every wake path now runs its
# calibrated-reliable value unconditionally instead of a slider-derived one:
#
# - stt_match poll interval: pinned to the fast end that used to be
#   sensitivity=1.0 — the "always as fast as possible" (2026-07-02) mandate
#   made the slow end a formality anyway.
WAKE_POLL_INTERVAL_S = 0.08

# - openWakeWord pretrained-model threshold: pinned to the data-driven
#   PRODUCTION_WAKE_THRESHOLD (imported above) directly, not the old
#   slider-scaled midpoint. This is a recall/false-positive tradeoff, not a
#   speed knob — the calibrated production value IS the fastest *reliable*
#   trigger; pinning to the old sensitive extreme (0.06) would re-open the
#   ghost-wake war (BUG-009/BUG-037/AP-27 history).

# - custom_onnx model threshold: pinned to the "balanced default" of the
#   calibrated 0.70 (strict) / 0.50 (balanced) / 0.30 (sensitive) band. A
#   TRAINED custom_onnx model outputs well-calibrated 0..1 scores; the
#   sensitive extreme false-fires on breathing and random words (live
#   2026-07-01 forensic — see resolve_wake_plan below).
CUSTOM_ONNX_THRESHOLD = 0.50


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

    Every phrase gets the same treatment (no special-cased word — design
    2026-07-07): fuzzy token-window match against a normalised
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
        require_known_prefix: bool = False,
    ) -> None:
        self._pattern = pattern
        # Sound-fold the core so a sound-equivalent ASR spelling of the wake word
        # ("Nico" -> "Niko"/"Nicko"/"Nikko") compares equal (see ``sound_fold``).
        self._core = [sound_fold(c) for c in (core_tokens or [])]
        self._ratio = fuzzy_ratio
        self._require_prefix = require_known_prefix
        # Folded prefix family: the window before the core must fuzzy-match ANY
        # of these when the configured phrase itself carries a prefix.
        self._folded_prefixes = tuple({sound_fold(p) for p in WAKE_PREFIXES})

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

    Every phrase — including "Hey Jarvis" — fuzzy-matches on its CORE tokens
    (no special-cased word ships; design 2026-07-07). When the configured
    phrase itself starts with a known wake prefix ("Hey Fable"), the prefix is
    REQUIRED immediately before the core (any known prefix counts — "Hallo
    Fable" still wakes a "Hey Fable" config), so the bare core word in
    ordinary speech stays silent (BUG-009 for prefixed phrases).

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
    a usable local engine) ``phrase`` keeps the user's *requested* word
    (e.g. "Computer") while ``engine`` is "none" and ``degraded`` is True.
    ``phrase`` is what to show the user; ``oww_keyword`` is what fires.
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
    # True for user-trained custom_onnx models (live forensic 2026-07-01: a
    # few-shot/synthetic-data model scored breath, ambient noise and arbitrary
    # speech up to 1.000 — several false activations per minute; the verify
    # transcript, matched against the phrase's own sound-folded fuzzy matcher,
    # is the real discriminator). False for vosk_kws — its permissive
    # free-decode sound confirm is built into the provider itself.
    verify_prefix: bool
    # Product rule (2026-07-04): a wake word REQUIRES a local model that matches
    # the user's OWN chosen word. When no such model is available (no custom
    # ONNX, no local Whisper) we do NOT silently substitute a branded fallback
    # model — we return wake_available=False so the app arms NO detector and the
    # honest alternative is Call-shortcut activation. True for every
    # real engine (custom_onnx / vosk_kws / stt_match).
    wake_available: bool = True
    # Extracted Vosk model directory for the vosk_kws engine (None otherwise).
    vosk_model_path: str | None = None
    # ALL installed Vosk model dirs, primary language first (vosk_kws only).
    # The provider streams a grammar per model and fires on whichever model's
    # verify confirms — a phrase and the speaker language routinely diverge
    # ("Hey Jarvis": English name, German speaker; live forensic 2026-07-11),
    # and the single language-matched model eats genuine wakes it cannot
    # spell. Empty tuple = fall back to the single vosk_model_path.
    vosk_model_paths: tuple[str, ...] = ()


def _read(cfg: Any, name: str, default: Any) -> Any:
    value = getattr(cfg, name, default)
    return default if value is None else value


def resolve_wake_plan(
    cfg: Any,
    *,
    local_whisper_available: bool,
    language: str | None = None,
    vosk_available: bool | None = None,
) -> WakeWordPlan:
    """Resolve a ``[trigger.wake_word]`` config into a :class:`WakeWordPlan`.

    ``cfg`` is duck-typed: any object exposing ``phrase``, ``engine``,
    ``custom_model_path``, ``fuzzy_match_ratio``. ``sensitivity`` is no longer
    read (the user-facing slider was removed 2026-07-10 — every wake path now
    runs its calibrated-reliable value unconditionally; a legacy config may
    still carry the field, it is simply ignored here).

    ``language`` selects the per-language Vosk model for the ``vosk_kws``
    engine (falls back to the first installed model on ``auto``/None).
    ``vosk_available`` overrides the vosk import probe (tests); None probes
    ``importlib.util.find_spec("vosk")``.
    """
    phrase = str(_read(cfg, "phrase", DEFAULT_WAKE_PHRASE)).strip()
    engine_pref = str(_read(cfg, "engine", "auto")).strip().lower()
    if engine_pref not in WAKE_ENGINES:
        log.warning("Unknown wake engine %r — coercing to 'auto'.", engine_pref)
        engine_pref = "auto"
    custom_path = str(_read(cfg, "custom_model_path", "")).strip()
    fuzzy = float(_read(cfg, "fuzzy_match_ratio", 0.8))

    threshold = PRODUCTION_WAKE_THRESHOLD
    custom_threshold = CUSTOM_ONNX_THRESHOLD
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

    # (The former step 2 — "known pretrained openWakeWord model" — was removed
    # 2026-07-07: the product ships no named wake model and never resolves a
    # phrase against the openwakeword package's pretrained third-party models.
    # engine="openwakeword" in an old config simply falls through this chain.)

    # 2. Any-word Vosk grammar keyword spotting — the one-identical-system-
    # everywhere engine (design spec 2026-07-05): per-language model, CPU-only,
    # no training, no cloud, no GPU; spike-measured 79-100 % recall at
    # 1/0/0 % false accepts where the transcription path is machine- and
    # word-dependent. Requires the vosk package (base dep) AND a per-language
    # model directory; missing either falls through to the stt_match chain
    # below (graceful, message says why).
    #
    # LANGUAGE-AWARE (2026-07-09): a Vosk model is acoustically language-SPECIFIC
    # — an English model cannot hear a German-spoken name even when the word is
    # in its lexicon (live: 'Hey Ruben' spoken de on the en model free-decoded to
    # 'hey of whom' and EVERY verify suppressed → a silent dead listener). So
    # vosk_kws is trusted only when its language provably matches the speaker:
    # (a) engine explicitly forced, or (b) a CONCRETE language is resolved and a
    # model for THAT language exists. Under an ambiguous "auto" language we do NOT
    # gamble on the first-installed model — we prefer the multilingual,
    # open-vocabulary stt_match path whenever local Whisper is available (it
    # transcribes ANY word in ANY language). Callers pass a concrete language via
    # wake_model_fetch.resolve_wake_language(cfg); "auto"/None only survives here
    # from legacy call sites, and then vosk is a best-effort last resort on a box
    # with no local Whisper.
    lang_norm = (language or "auto").strip().lower().split("-")[0]
    lang_is_concrete = bool(lang_norm) and lang_norm != "auto"
    vosk_model = None
    if phrase and engine_pref in ("auto", "vosk_kws"):
        if vosk_available is None:
            import importlib.util as _ilu

            vosk_available = _ilu.find_spec("vosk") is not None
        if vosk_available:
            if engine_pref == "vosk_kws":
                # Explicit force: honour the user's choice, any installed model.
                vosk_model = resolve_vosk_model_path(language)
            elif lang_is_concrete:
                # auto engine + a concrete language: trust vosk only for a model
                # in exactly that language (never a mismatched fallback).
                vosk_model = resolve_vosk_model_path(lang_norm)
            elif not local_whisper_available:
                # auto engine + ambiguous language + NO multilingual Whisper:
                # vosk (first-installed) is the only local option — best effort.
                vosk_model = resolve_vosk_model_path(language)
            # else: auto + ambiguous language + Whisper present → fall through to
            # the multilingual stt_match path below (the universal answer).
        if vosk_model is not None and (
            engine_pref == "vosk_kws"
            or not custom_path
            or custom_stale
            or custom_missing
        ):
            return WakeWordPlan(
                phrase=phrase,
                engine="vosk_kws",
                oww_model_path=None,
                oww_keyword=canonical,
                threshold=threshold,
                matcher=matcher,
                needs_local_whisper=False,
                degraded=False,
                message=(
                    f"Any-word Vosk keyword spotting for '{phrase}' "
                    f"(model: {vosk_model})."
                ),
                verify_prefix=False,  # the provider's sound confirm is built in
                vosk_model_path=vosk_model,
                # Every installed model, primary first: the provider listens
                # on all of them so a phrase whose language differs from the
                # speaker's ("Hey Jarvis" de-spoken) still has a model that
                # can spell it. Verified per model — precision gates intact.
                vosk_model_paths=tuple(
                    resolve_vosk_model_paths(
                        lang_norm if lang_is_concrete else language
                    )
                ),
            )

    # 3. Arbitrary phrase via local-Whisper transcript match.
    want_stt = (
        engine_pref in ("auto", "stt_match", "vosk_kws")
        or bool(custom_path)                       # custom file missing -> best effort
        or engine_pref == "openwakeword"           # legacy engine value, no model ships
    )
    if want_stt and local_whisper_available:
        if custom_missing:
            # A configured custom model file is gone. Best-effort fallback to
            # transcript match, but this IS a degrade — the user's trained
            # model no longer applies.
            degraded = True
            message = (
                f"Custom ONNX not found ({custom_path}); "
                "using local-Whisper transcript match instead."
            )
        elif custom_stale:
            # A STALE custom model (belongs to another phrase) is NOT a
            # degrade: the transcript match IS the regular path for the new
            # phrase, and the model stays configured for when the user
            # switches back.
            degraded = False
            message = (
                f"Custom wake model '{Path(custom_path).name}' belongs to a "
                f"different phrase; '{phrase}' uses the local-Whisper "
                "transcript match."
            )
        else:
            # A custom word served ONLY by the transcribe-and-match path is
            # UNRELIABLE for hard proper nouns (AP-27): the base model garbles
            # the name and matched stays 0. This is a LOUD degrade, not silent
            # success — point the user at the reliable any-word engine.
            degraded = True
            _lang = language or "the configured language"
            message = (
                f"Custom phrase '{phrase}' is on the local-Whisper transcript "
                f"match — this is UNRELIABLE for a hard name. Download the Vosk "
                f"model for {_lang} to make it reliable (Settings -> Wake word -> "
                "'Download wake model')."
            )
            log.warning(
                "Wake word '%s' resolved to stt_match only (no Vosk model, no "
                "custom ONNX) — recognition will be unreliable for a hard name. "
                "Provision the Vosk model for %s.",
                phrase,
                _lang,
            )
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
            # The rolling-whisper path already matches the phrase itself; no
            # second-stage prefix verification (that gate exists for weak
            # custom_onnx models — see WakeWordPlan.verify_prefix).
            verify_prefix=False,
        )

    # 4. No local model for the user's OWN word. The product rule (2026-07-04)
    # forbids silently substituting a branded fallback (the retired degrade
    # made the app listen for a word the user never says). Instead we
    # return wake_available=False: the app arms NO wake detector, and the honest
    # alternative is Call-shortcut activation. The user's requested phrase
    # is preserved; installing the local speech pack (works for ANY word) or
    # supplying a custom .onnx is what makes the wake word actually fire.
    _phrase_label = phrase or "your wake word"
    return WakeWordPlan(
        phrase=phrase,
        engine="none",
        oww_model_path=None,
        oww_keyword=canonical,
        threshold=threshold,
        matcher=matcher,
        needs_local_whisper=False,
        degraded=True,
        message=(
            f"Wake word '{_phrase_label}' needs a local model. Install the local "
            "speech pack (works for any word) or supply a custom .onnx to enable "
            "it. Until then the wake word is off — use the Call shortcut "
            "to start a voice turn."
        ),
        verify_prefix=False,
        wake_available=False,
    )


__all__ = [
    "CUSTOM_ONNX_THRESHOLD",
    "WAKE_POLL_INTERVAL_S",
    "WakeMatcher",
    "WakeWordPlan",
    "compile_wake_matcher",
    "custom_model_matches_phrase",
    "resolve_wake_plan",
]

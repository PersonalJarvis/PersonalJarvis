"""Cross-layer parity guard for the transcription-failure vocabulary (AP-4).

``jarvis.speech.stt_failure.STT_FAILURE_REASONS`` is the single source of truth
for "why did this transcription fail". The value is written by the speech
pipeline, carried on ``DictationCompleted.error``, stored in the history
sidecar, serialised by the REST layer and finally turned into a sentence by the
UI — the same five-layer shape that has drifted four times in this repo
(BUG-008), and the reason the sibling ``test_outcome_parity`` exists.

This one additionally pins the behaviour that made the vocabulary necessary: the
history row must never carry a provider's raw error text again. What the user
saw under their own words was a Python exception class, a vendor endpoint and a
link to an HTTP specification — untranslatable, provider-identifying, and no
answer to "what do I do now".

Every parsed set is asserted NON-EMPTY before comparison, so a regex that stops
matching fails loudly instead of going trivially green against an empty set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.speech.stt_failure import (
    CROSSABLE_REASONS,
    STT_FAILURE_REASONS,
    classify_stt_failure,
    is_crossable_failure,
    stt_failure_message,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src"
_DICTATION_TS = _FRONTEND / "hooks" / "useDictation.ts"
_HISTORY_GROUP_TSX = _FRONTEND / "views" / "voice" / "DictationHistoryGroup.tsx"
_LOCALES = _FRONTEND / "i18n" / "locales"

SUPPORTED_LOCALES = ("de", "en", "es")

#: The live 429 the maintainer was shown, verbatim from the log. Kept as a
#: literal so the classifier is pinned against a REAL provider error rather than
#: a tidy invented one.
LIVE_GROQ_429 = (
    "HTTPStatusError: Client error '429 Too Many Requests' for url "
    "'https://api.groq.com/openai/v1/audio/transcriptions' For more information "
    "check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429"
)


def _ts_reasons() -> list[str]:
    """Members of ``export const STT_FAILURE_REASONS = [...] as const``."""
    assert _DICTATION_TS.exists(), f"frontend hook missing: {_DICTATION_TS}"
    source = _DICTATION_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const STT_FAILURE_REASONS\s*=\s*\[(.*?)\]\s*as const",
        source,
        re.DOTALL,
    )
    assert match is not None, (
        f"STT_FAILURE_REASONS array not found in {_DICTATION_TS.name}"
    )
    return re.findall(r'"([a-z_]+)"', match.group(1))


def _locale(name: str) -> dict:
    path = _LOCALES / f"{name}.json"
    assert path.exists(), f"locale file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_python_vocabulary_is_a_usable_set() -> None:
    assert STT_FAILURE_REASONS, "STT_FAILURE_REASONS is empty"
    assert len(set(STT_FAILURE_REASONS)) == len(STT_FAILURE_REASONS)
    assert "unknown" in STT_FAILURE_REASONS, "the catch-all must stay a member"


def test_ts_reason_array_mirrors_the_python_vocabulary() -> None:
    members = _ts_reasons()
    assert members, f"parsed no reasons from {_DICTATION_TS.name}"
    assert len(members) == len(set(members)), members
    assert set(members) == set(STT_FAILURE_REASONS)


def test_every_reason_has_a_sentence_in_every_locale() -> None:
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("failure", {})
        assert isinstance(table, dict) and table, (
            f"{name}.json: dictation.failure missing"
        )
        for reason in STT_FAILURE_REASONS:
            value = table.get(reason)
            assert isinstance(value, str) and value.strip(), (
                f"{name}.json: dictation.failure.{reason}"
            )


def test_no_locale_carries_a_reason_the_backend_never_emits() -> None:
    """A stale key is drift in the other direction — dead copy nobody maintains."""
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("failure", {})
        assert set(table) == set(STT_FAILURE_REASONS), f"{name}.json: {sorted(table)}"


def test_no_locale_sentence_names_a_provider() -> None:
    """The message must read the same whichever key the downloader configured.

    A sentence that says "Groq" is wrong for the ~99.9 % of installs that are
    not the maintainer's (§3), and it is also just untrue as soon as the runtime
    fallback chain moves the work to a different provider mid-dictation.
    """
    vendors = ("groq", "openai", "gemini", "openrouter", "whisper", "anthropic")
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("failure", {})
        for reason, sentence in table.items():
            lowered = sentence.lower()
            for vendor in vendors:
                assert vendor not in lowered, f"{name}.json {reason}: {sentence}"
            assert "http" not in lowered, f"{name}.json {reason} leaks a URL"


def test_the_renderer_derives_its_known_set_from_the_shared_array() -> None:
    """The UI must not keep a second, hand-written copy of the vocabulary."""
    assert _HISTORY_GROUP_TSX.exists(), f"missing: {_HISTORY_GROUP_TSX}"
    source = _HISTORY_GROUP_TSX.read_text(encoding="utf-8")
    assert "new Set(STT_FAILURE_REASONS)" in source, (
        f"{_HISTORY_GROUP_TSX.name} no longer derives its known-failure set "
        "from the shared array"
    )


def test_the_renderer_never_prints_a_raw_error_string() -> None:
    """The whole point: an unknown value falls back to a sentence, not the value.

    This is the regression that was actually reported — a raw
    ``{entry.error}`` in the JSX, which is how a Python exception class and a
    vendor URL ended up rendered under a user's dictated text.
    """
    source = _HISTORY_GROUP_TSX.read_text(encoding="utf-8")
    assert "{entry.error}" not in source, (
        f"{_HISTORY_GROUP_TSX.name} renders the raw error string again"
    )
    assert "failureLabel(t, entry.error)" in source, _HISTORY_GROUP_TSX.name


def test_the_live_rate_limit_classifies_as_rate_limited() -> None:
    """The exact string from the reported incident, not a tidied-up stand-in."""
    assert classify_stt_failure(LIVE_GROQ_429) == "rate_limited"


def test_the_local_engines_busy_error_is_named_rather_than_generic() -> None:
    """``TranscribeBusy`` is a real recurring shape here (AP-24 / BUG-036)."""
    from jarvis.plugins.stt.fwhisper import TranscribeBusy

    busy = TranscribeBusy("a transcription is already in flight on this model")
    assert classify_stt_failure(busy) == "engine_busy"
    # NOT crossable: the non-blocking lock means "try again in a moment", and
    # spending a cloud request to skip a millisecond wait is the wrong trade.
    assert is_crossable_failure("engine_busy") is False


def test_normalization_is_idempotent_and_keeps_none_as_none() -> None:
    """The backstop at the store: a code survives, raw text is converted."""
    from jarvis.speech.stt_failure import normalize_stt_failure

    for reason in STT_FAILURE_REASONS:
        assert normalize_stt_failure(reason) == reason, reason
    assert normalize_stt_failure(LIVE_GROQ_429) == "rate_limited"
    # "no failure" must stay distinguishable from "failed, reason unknown".
    assert normalize_stt_failure(None) is None
    assert normalize_stt_failure("") is None
    assert normalize_stt_failure("   ") is None


def test_every_reason_has_an_english_sentence() -> None:
    """The log / CLI half. An identifier there helps nobody either."""
    for reason in STT_FAILURE_REASONS:
        message = stt_failure_message(reason)
        assert message and not message.startswith(reason), reason
        assert message.endswith("."), reason
    # Unknown input degrades to a sentence rather than echoing the raw value.
    assert stt_failure_message("something_from_a_newer_install") == (
        stt_failure_message("unknown")
    )
    assert stt_failure_message(None) == stt_failure_message("unknown")


def test_crossable_reasons_are_a_real_subset_that_excludes_a_refusal() -> None:
    """``rejected`` must never cross: another provider refuses the same bytes."""
    assert CROSSABLE_REASONS < set(STT_FAILURE_REASONS)
    assert "rejected" not in CROSSABLE_REASONS
    assert "unknown" not in CROSSABLE_REASONS
    for reason in STT_FAILURE_REASONS:
        assert is_crossable_failure(reason) is (reason in CROSSABLE_REASONS), reason
    assert is_crossable_failure(None) is False


def test_classification_never_raises_on_the_shapes_it_actually_sees() -> None:
    """It runs on the path already handling a failure; it may not add one."""
    assert classify_stt_failure(None) == "unknown"
    assert classify_stt_failure("") == "unknown"
    assert classify_stt_failure("   ") == "unknown"
    assert classify_stt_failure(RuntimeError("something nobody predicted")) == "unknown"
    for reason in (classify_stt_failure(x) for x in (None, "", TimeoutError())):
        assert reason in STT_FAILURE_REASONS

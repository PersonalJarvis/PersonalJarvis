"""The speech pipeline must honor the ``brain.reply_language`` pin for EVERY
spoken layer — not just the deep-brain reply.

Forensic 2026-06-18 (session 13:55, "Mask it up."): a German utterance was
mis-transcribed by STT as clean English text, so the per-turn language was
detected as English and the ack preamble, the answer and the TTS voice all went
English. The deep brain already honored ``brain.reply_language``; the speech
pipeline did NOT — it re-derived the turn language from text/STT alone. These
tests pin the unified contract: ``SpeechPipeline._output_language`` resolves the
turn's output language through ``resolve_output_language``, so a selected
language reaches the ack preamble, the canned phrases and the TTS voice, while
``auto`` still mirrors the input. See CLAUDE.md "Runtime Output Language".
"""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.speech.pipeline import SpeechPipeline


def _pipe(
    *, brain_pin: object = None, config_pin: object = "auto", conversation: str = ""
) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    if brain_pin is _MISSING:
        pipe._brain = SimpleNamespace()  # production brain without the attr
    else:
        pipe._brain = SimpleNamespace(
            reply_language=brain_pin, conversation_language=conversation
        )
    pipe._config = SimpleNamespace(brain=SimpleNamespace(reply_language=config_pin))
    return pipe


_MISSING = object()


def test_explicit_pin_overrides_mis_transcribed_input() -> None:
    # THE bug: German speech mis-heard as English text. The German pin must win.
    pipe = _pipe(brain_pin="de")
    assert pipe._output_language("english", "Mask it up.") == "de"


def test_spanish_pin_wins() -> None:
    pipe = _pipe(brain_pin="es")
    assert pipe._output_language("german", "Wie ist das Wetter?") == "es"


def test_auto_mirrors_detected_input() -> None:
    pipe = _pipe(brain_pin="auto")
    assert pipe._output_language("german", "Turn on the lights") == "en"
    assert pipe._output_language("english", "Mach das Licht an") == "de"


def test_live_brain_pin_takes_priority_over_stale_config() -> None:
    # The live pin lives on the BrainManager (hot-reloaded); config may lag.
    pipe = _pipe(brain_pin="es", config_pin="de")
    assert pipe._output_language("english", "Mask it up.") == "es"


def test_falls_back_to_config_pin_when_brain_lacks_attr() -> None:
    pipe = _pipe(brain_pin=_MISSING, config_pin="es")
    assert pipe._output_language("english", "Mask it up.") == "es"


def test_thin_interjection_inherits_conversation_language() -> None:
    # THE 16:05 bug at the pipeline layer: a one-word English "Now" in a German
    # conversation must keep the ack/phrases/TTS in German.
    pipe = _pipe(brain_pin="auto", conversation="de")
    assert pipe._output_language("english", "Now.") == "de"


def test_substantive_turn_still_switches_in_auto() -> None:
    pipe = _pipe(brain_pin="auto", conversation="de")
    assert pipe._output_language("german", "What is the weather tomorrow there") == "en"

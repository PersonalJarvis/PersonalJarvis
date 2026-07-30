"""Every STT provider filters its transcript, and none of them loses the raw one.

Two failure modes this pins, both of which are invisible until a user reports
"it behaves differently since I switched providers":

1. **Coverage.** A provider that skips the filter hands hesitation sounds,
   decoder loops and stutters to the intent router while its neighbours do not,
   so command accuracy silently depends on which key someone happens to have —
   the exact provider-shaped divergence AP-21/22 exist to prevent.
2. **The raw string.** Dictation and wake verification must both read what the
   recognizer actually emitted. A provider that filters without keeping
   ``raw_text`` turns the user's filler switch into a no-op (AP-31) and makes
   wake judge an edited transcript (AP-27).

The German fixtures are the speech under test (CLAUDE.md §1, category 4).
"""
from __future__ import annotations

import dataclasses
import importlib
import pkgutil
from types import SimpleNamespace

import pytest

import jarvis.plugins.stt as stt_package
from jarvis.plugins.stt import gemini_api, groq_api, openai_api, openrouter_stt

#: One payload carrying three artifact classes at once: an outer quote pair the
#: model added, a hesitation sound, and a decoder repetition loop.
DIRTY_TEXT = '"Umm, turn on the light. Thank you. Thank you. Thank you."'
CLEAN_TEXT = "Turn on the light. Thank you."

#: Modules in the package that are not providers.
_NON_PROVIDER_MODULES = frozenset({"errors", "transcript_filter"})


def _discover_provider_modules() -> tuple[object, ...]:
    """Every provider module in the package, found rather than listed.

    Listing them by hand is what let the Nemotron engine ship past the first
    rollout of this filter: it was added in a parallel session, the list did not
    know about it, and the parity test went green while one provider quietly
    behaved differently from the other five. Discovery makes the NEXT provider
    fail loudly instead.

    A module counts as a provider when it defines a ``Transcript`` — that is the
    thing this file has an opinion about. A module that cannot be imported (an
    optional SDK missing on this host) is skipped, not failed: the point of the
    plugin layout is that a provider you have no dependencies for stays absent.
    """
    found: list[object] = []
    for info in pkgutil.iter_modules(stt_package.__path__):
        if info.name.startswith("_") or info.name in _NON_PROVIDER_MODULES:
            continue
        try:
            module = importlib.import_module(f"{stt_package.__name__}.{info.name}")
        except Exception:  # noqa: BLE001, S112 — see below
            # Deliberately silent: on a base install the absent SDKs are the
            # EXPECTED case, and logging one line per skipped provider would
            # bury the run in noise about modules that are working as designed.
            continue
        if hasattr(module, "Transcript"):
            found.append(module)
    return tuple(found)


#: Providers that turn a vendor JSON payload into a Transcript.
PAYLOAD_PROVIDERS = (openrouter_stt, groq_api, openai_api)

#: Every provider module in the package, discovered at collection time.
ALL_PROVIDER_MODULES = _discover_provider_modules()


def test_discovery_actually_found_the_known_providers() -> None:
    """A guard on the guard: a broken discovery would make everything below
    vacuously pass by finding nothing at all."""
    names = {m.__name__.rsplit(".", 1)[-1] for m in ALL_PROVIDER_MODULES}
    assert {"openrouter_stt", "groq_api", "openai_api", "gemini_api"} <= names
    assert len(ALL_PROVIDER_MODULES) >= 4


@pytest.mark.parametrize(
    "module", PAYLOAD_PROVIDERS, ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_a_payload_provider_cleans_its_text_and_keeps_the_raw_one(module) -> None:
    transcript = module._payload_to_transcript(
        {"text": DIRTY_TEXT, "language": "English"}
    )
    assert transcript.text == CLEAN_TEXT
    assert transcript.raw_text == DIRTY_TEXT
    # A filter that removed words must never read as "nothing was said".
    assert transcript.confidence > 0.0


def test_gemini_cleans_its_response_and_keeps_the_raw_one() -> None:
    transcript = gemini_api._response_to_transcript(
        SimpleNamespace(text=DIRTY_TEXT), "en"
    )
    assert transcript.text == CLEAN_TEXT
    assert transcript.raw_text == DIRTY_TEXT
    assert transcript.confidence == 1.0


async def test_the_local_engine_cleans_its_joined_text_and_keeps_the_raw_one() -> None:
    """faster-whisper joins segment texts; the join is what gets filtered.

    The per-segment dicts stay exactly as decoded — they carry timings and
    probabilities, and re-aligning them to an edited string would mean guessing
    where the edits landed.
    """
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    # i18n-allow: German speech under test (§1 list #4)
    pieces = ("Ähm, mach ", "das Licht an. ", "Danke. Danke. Danke.")  # i18n-allow

    class _SegmentedModel:
        def transcribe(self, audio, **_kwargs):  # noqa: ANN001, ANN003
            segments = [
                SimpleNamespace(
                    start=float(i), end=float(i + 1), text=piece, avg_logprob=-0.2
                )
                for i, piece in enumerate(pieces)
            ]
            return iter(segments), SimpleNamespace(language="de")

    prov = FasterWhisperProvider(device="cpu", compute_type="int8")
    prov._model = _SegmentedModel()  # noqa: SLF001 — skip the real model load

    transcript = await prov.transcribe_pcm(b"\x00\x00" * 16_000)

    assert transcript.text == "Mach das Licht an. Danke."  # i18n-allow: fixture (§1 list #4)
    assert transcript.raw_text == "".join(pieces).strip()
    assert tuple(s["text"] for s in transcript.segments) == pieces
    assert transcript.confidence > 0.0


@pytest.mark.parametrize(
    "module", ALL_PROVIDER_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_every_provider_transcript_carries_an_optional_raw_text(module) -> None:
    """A new provider cannot ship a Transcript without the field and stay green.

    The default has to be empty rather than required: consumers construct empty
    Transcripts on silence and on an aborted call, and a required field would
    turn every one of those into a TypeError at the worst possible moment.
    """
    fields = {f.name: f for f in dataclasses.fields(module.Transcript)}
    assert "raw_text" in fields, f"{module.__name__} lost the raw transcript"
    assert fields["raw_text"].default == ""


@pytest.mark.parametrize(
    "module", ALL_PROVIDER_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_every_provider_module_runs_the_filter(module) -> None:
    """Source-level, on purpose.

    A behavioural test is better and this file has one wherever it is possible
    — but it is not possible for every provider: some need a vendor SDK that a
    base install does not carry, and one needs an on-device model measured in
    gigabytes. Those providers are exactly the ones nobody notices drifting, so
    the check that covers ALL of them is the one that reads the source. It
    proves the call is wired, not that it works; the behavioural tests above
    prove the rest for the providers that can be driven here.
    """
    import inspect

    source = inspect.getsource(module)
    assert "clean_stt_text" in source, (
        f"{module.__name__} builds a Transcript without running the cleanup "
        "filter — its users would get hesitation sounds and decoder loops that "
        "every other provider removes."
    )


def test_the_shared_protocol_transcript_carries_it_too() -> None:
    """The local engine returns the protocol type, not a plugin-local copy."""
    from jarvis.core.protocols import Transcript as ProtocolTranscript

    fields = {f.name: f for f in dataclasses.fields(ProtocolTranscript)}
    assert fields["raw_text"].default == ""

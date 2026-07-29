"""On-disk truth about the OPTIONAL local speech engines and their models.

Why this module exists — the 2026-07-03 lesson. A user-selectable
"Faster-Whisper (local)" STT card already shipped once and had to be REMOVED
(v1.0.1) for two reasons, both of which were the same defect wearing different
clothes: **the UI asserted a readiness nobody had verified.** The card reported
"ready" on a base install where the engine package was never installed, and its
model picker listed every Whisper checkpoint regardless of what had actually
been downloaded. Picking it produced a provider that could not transcribe a
single word.

So the local providers get exactly one gate, and this is it. Nothing may claim a
local engine is usable except by asking here, and every answer separates the two
independent facts a local path needs:

* **engine** — is the inference runtime importable in THIS interpreter
  (``faster_whisper`` / ``sherpa_onnx``)? Both are opt-in extras the cloud-first
  base install deliberately omits (CLAUDE.md section 3), so on a fresh install
  the honest answer is "no".
* **model** — are the weights on disk? An installed engine with no weights is
  not readiness; it is a download away from it, and saying so is the whole
  point.

Design contract, mirroring ``jarvis.setup.model_report``:

- **Read-only and network-free.** Probing must never download: the Whisper probe
  uses ``local_files_only``, the sherpa probe is a directory check. A status
  route polls these, so a probe that reached the network would turn a UI refresh
  into a multi-gigabyte surprise.
- **Best-effort, never raising.** Every probe degrades to "absent". An uncertain
  answer is "not ready" — that is the fail-closed direction, because claiming
  readiness we cannot prove is the exact bug this module exists to prevent.
- **Capability, never hardware or provider names** (AP-21). Nothing here asks
  what GPU is installed or which vendor a provider belongs to.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger("jarvis.speech.local_models")

#: Import names of the two optional local inference runtimes. These are module
#: names for ``find_spec``, NOT pip requirement strings — the installable
#: specifier lives with the install route that pins it to the ``[local-voice]``
#: extra, so the two never drift apart in opposite directions.
FASTER_WHISPER_MODULE = "faster_whisper"
SHERPA_ONNX_MODULE = "sherpa_onnx"

Runtime = Literal["faster-whisper", "sherpa-onnx"]

#: Runtime id -> module to probe. A runtime the app does not know about is
#: reported absent rather than guessed at.
_RUNTIME_MODULES: dict[str, str] = {
    "faster-whisper": FASTER_WHISPER_MODULE,
    "sherpa-onnx": SHERPA_ONNX_MODULE,
}


@dataclass(frozen=True, slots=True)
class LocalModelStatus:
    """Whether one local provider can actually run right now, and if not, why.

    ``ready`` is deliberately the conjunction of both facts rather than a third
    stored flag: a single source of truth cannot contradict itself.
    """

    #: Which inference runtime this provider needs.
    runtime: str
    #: The runtime package is importable in this interpreter.
    engine_installed: bool
    #: The weights this provider would load are on disk.
    model_present: bool
    #: Human-readable model name, for the UI and for log lines.
    model_label: str
    #: One honest English sentence explaining the state — shown verbatim in the
    #: provider card, so it must read as an answer, not a status code.
    detail: str

    @property
    def ready(self) -> bool:
        """True only when the engine AND its weights are both usable."""
        return self.engine_installed and self.model_present


def runtime_installed(runtime: str) -> bool:
    """True when *runtime*'s inference package is importable here.

    ``find_spec`` rather than a real import: this runs on status polls and on
    the provider-list path, and importing ctranslate2/onnxruntime eagerly would
    put a heavy load on a route that only asked a question (AP-26).
    """
    module = _RUNTIME_MODULES.get(runtime)
    if module is None:
        log.debug("Unknown local runtime %r -- reporting absent.", runtime)
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except Exception as exc:  # noqa: BLE001 — a broken sys.path is "not installed"
        log.debug("Probe for local runtime %r failed: %s", runtime, exc)
        return False


def whisper_model_cached(name: str) -> bool:
    """True when faster-whisper checkpoint *name* is already in the HF cache.

    Pure cache lookup — ``local_files_only`` guarantees no network call. Any
    failure (not cached, engine missing, unfamiliar cache layout) reads as
    "not present", which is the fail-closed direction for a readiness claim.
    """
    if not name.strip():
        return False
    try:
        from faster_whisper.utils import (  # type: ignore[import-not-found,import-untyped]
            download_model,
        )

        download_model(name, local_files_only=True)
        return True
    except Exception as exc:  # noqa: BLE001 — an uncertain cache is not ready
        log.debug("Whisper checkpoint %r is not cached locally: %s", name, exc)
        return False


def models_root(data_dir: str | None = None) -> Path:
    """Directory holding the locally downloaded speech models.

    Honours the same ``JARVIS__MEMORY__DATA_DIR`` seam as the wake-model
    fetcher, so a headless install that relocated its data dir (ADR-0027) keeps
    every model in one place instead of scattering them across two conventions.
    """
    base = data_dir or os.environ.get("JARVIS__MEMORY__DATA_DIR") or "data"
    return Path(base) / "models"


def sherpa_model_dir(model_id: str, *, data_dir: str | None = None) -> Path:
    """Where the sherpa-onnx bundle for *model_id* is unpacked."""
    return models_root(data_dir) / model_id


def sherpa_model_present(
    model_id: str,
    *,
    data_dir: str | None = None,
    required_files: tuple[str, ...] = (),
) -> bool:
    """True when *model_id*'s ONNX bundle is unpacked and complete on disk.

    A partially-extracted bundle (interrupted download, killed process) is the
    realistic failure here, and it must not read as ready — an incomplete
    directory would fail at model-load time with a stack trace instead of an
    honest "download it again". So presence means: the directory exists AND
    every file the loader needs is in it. With no ``required_files`` given, the
    weaker "directory holds at least one .onnx file" test applies.
    """
    directory = sherpa_model_dir(model_id, data_dir=data_dir)
    try:
        if not directory.is_dir():
            return False
        if required_files:
            # ``exists``, not ``is_file``: a Piper voice's ``espeak-ng-data`` is
            # a DIRECTORY of phoneme rules, and requiring a file here reported
            # every correctly-downloaded voice as missing.
            missing = [f for f in required_files if not (directory / f).exists()]
            if missing:
                log.debug(
                    "Local model %r is incomplete -- missing %s.",
                    model_id,
                    ", ".join(missing),
                )
                return False
            return True
        return any(directory.glob("*.onnx"))
    except Exception as exc:  # noqa: BLE001 — an unreadable dir is not ready
        log.debug("Probe for local model %r failed: %s", model_id, exc)
        return False


def whisper_status(model: str) -> LocalModelStatus:
    """Readiness of the local Whisper voice-input path for checkpoint *model*.

    The three states a user can actually be in, each with the sentence that
    tells them what to do next.
    """
    engine = runtime_installed("faster-whisper")
    cached = whisper_model_cached(model) if engine else False
    if not engine:
        detail = (
            "The local speech engine is not installed yet. Install it from here "
            "to transcribe on this machine without an API key."
        )
    elif not cached:
        detail = (
            f"The engine is installed, but the {model} model still has to be "
            "downloaded (about 3 GB, once)."
        )
    else:
        detail = f"Ready — {model} runs entirely on this machine."
    return LocalModelStatus(
        runtime="faster-whisper",
        engine_installed=engine,
        model_present=cached,
        model_label=model,
        detail=detail,
    )


def bundle_present(model_id: str, *, data_dir: str | None = None) -> bool:
    """True when the catalogued bundle *model_id* is complete on disk."""
    bundle = SHERPA_BUNDLES.get(model_id)
    if bundle is None:
        return False
    return sherpa_model_present(
        model_id, data_dir=data_dir, required_files=bundle.required_files
    )


def sherpa_status(
    model_ids: tuple[str, ...],
    *,
    model_label: str,
    download_size: str,
    data_dir: str | None = None,
) -> LocalModelStatus:
    """Readiness of a sherpa-onnx backed local provider (STT or TTS).

    ALL of *model_ids* must be present. For the local voice that means one
    voice per supported language: a provider that can only answer in German is
    not ready for an assistant whose reply language follows the conversation.
    """
    engine = runtime_installed("sherpa-onnx")
    present = (
        all(bundle_present(mid, data_dir=data_dir) for mid in model_ids)
        if engine and model_ids
        else False
    )
    if not engine:
        detail = (
            "The local speech runtime is not installed yet. Install it from here "
            "to run this model on your own machine."
        )
    elif not present:
        detail = (
            f"The runtime is installed, but {model_label} still has to be "
            f"downloaded ({download_size}, once)."
        )
    else:
        detail = f"Ready — {model_label} runs entirely on this machine."
    return LocalModelStatus(
        runtime="sherpa-onnx",
        engine_installed=engine,
        model_present=present,
        model_label=model_label,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# The catalog — which local providers exist and what each one needs on disk
# ---------------------------------------------------------------------------
# ONE declaration shared by the provider card, the activation gate, the download
# route and the plugins themselves. A second list anywhere would be the BUG-008
# shape (the same fact spelled twice, drifting apart), and here it would drift
# into precisely the lie this module exists to prevent: a card offering a model
# the downloader never fetches.


@dataclass(frozen=True, slots=True)
class SherpaBundle:
    """One downloadable ONNX model bundle: where it comes from, what it is.

    Two shapes, because the upstreams differ: an ASR model is a handful of loose
    files in a model repo, while a Piper voice is a tar.bz2 whose payload
    includes a 390-file phoneme-data directory that would be absurd to fetch one
    file at a time. Exactly one of ``archive_url`` / ``hf_repo`` is set.
    """

    #: Directory name under ``data/models/`` — also how every other layer names
    #: this bundle.
    model_id: str
    #: Human-readable name for the UI.
    label: str
    #: Files that must be present for the bundle to count as complete. Doubles
    #: as the download list for ``hf_repo`` bundles, so a bundle can never be
    #: declared ready against a shorter list than the one that was fetched.
    required_files: tuple[str, ...]
    #: A tar.bz2 whose single top-level directory is stripped on extraction.
    archive_url: str | None = None
    #: A HuggingFace repo whose ``required_files`` are fetched individually.
    hf_repo: str | None = None
    #: Voice bundles only: the language it speaks and the voice's gender, so the
    #: per-turn voice pick can stay consistent across a language switch (a voice
    #: that changes sex mid-conversation is BUG-086's shape).
    language: str = ""
    gender: str = ""


@dataclass(frozen=True, slots=True)
class LocalProvider:
    """A provider that runs on this machine, and what it needs to do so."""

    #: Matches the ``jarvis.stt`` / ``jarvis.tts`` entry-point name AND the
    #: ProviderSpec id, so no layer has to translate between two vocabularies.
    provider_id: str
    #: "stt" or "tts" — which tier the card belongs to.
    tier: str
    #: Which inference runtime must be importable.
    runtime: str
    #: Whisper checkpoint name, or the directory name of an unpacked sherpa
    #: bundle under ``data/models/``.
    model_id: str
    #: What to call the model in the UI.
    model_label: str
    #: Download size, phrased for a human ("about 3 GB").
    download_size: str
    #: The pip requirement that provides ``runtime``. Declared HERE, next to the
    #: model it powers, so the in-app installer and the ``[local-voice]`` extra
    #: in pyproject.toml can be checked against one another instead of drifting
    #: apart in two files that never meet.
    pip_package: str
    #: Bundle ids this provider needs on disk, in download order. Usually one;
    #: a local TTS needs one voice PER LANGUAGE, because a Piper voice speaks
    #: exactly one — and a provider that can only answer in German is not a
    #: usable voice for a multilingual assistant. Empty for Whisper, whose
    #: engine manages its own cache.
    bundles: tuple[str, ...] = ()


#: The Whisper checkpoint the local voice-input card offers. ONE checkpoint, not
#: a picker over all seven: the removed 2026-07-03 card listed every size
#: regardless of what was downloaded, and a list of mostly-absent options is a
#: worse answer than one option that is actually there. ``large-v3`` is the
#: accuracy pick (the multilingual full model) — the wake word keeps its own,
#: much smaller checkpoint via ``[stt].wake_model`` and is untouched by this.
WHISPER_VOICE_MODEL = "large-v3"

#: pip requirement for the CTranslate2-backed Whisper engine. Torch-free,
#: cross-platform wheels; the cloud-first base install omits it on purpose
#: (CLAUDE.md section 3), which is why it can be installed from inside the app.
FASTER_WHISPER_PACKAGE = "faster-whisper>=1.0"

#: pip requirement for the ONNX-Runtime speech stack (Apache-2.0, torch-free,
#: wheels for Windows/macOS/Linux). The FLOOR IS LOAD-BEARING: multilingual
#: Nemotron needs the ``prompt_index`` encoder input that landed in 1.13.3
#: (PR #3671, released 2026-06-15). An older wheel loads the same files and
#: then transcribes every language as English — a silent wrong answer, which is
#: worse than a refusal, so the version is pinned rather than hoped for.
SHERPA_ONNX_PACKAGE = "sherpa-onnx>=1.13.3"

#: The multilingual Nemotron 3.5 streaming bundle, int8-quantised at a 560 ms
#: chunk. Choices worth stating: MULTILINGUAL (the ``-en-`` sibling would
#: mangle German into English words, the same trap the English-only Whisper
#: checkpoints carry), int8 (about a third of the size, and this must run on a
#: machine with no GPU), and 560 ms (the middle of the offered 80–1120 ms
#: chunk sizes: low enough to feel live, long enough that accuracy holds).
NEMOTRON_MODEL_ID = "nemotron-3.5-asr-streaming-0.6b-560ms-int8"
NEMOTRON_HF_REPO = (
    "csukuangfj2/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
)

#: Where the sherpa-onnx project publishes its converted TTS voices.
_PIPER_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"


def _piper_bundle(name: str, *, label: str, language: str, gender: str) -> SherpaBundle:
    """One Piper voice, named the way the upstream release names it.

    Each archive carries its own copy of ``espeak-ng-data`` (the phoneme rules
    Piper needs to turn text into sounds), which is why a 30 MB voice arrives as
    a ~67 MB download.
    """
    voice = name.removeprefix("vits-piper-")
    return SherpaBundle(
        model_id=name,
        label=label,
        required_files=(f"{voice}.onnx", "tokens.txt", "espeak-ng-data"),
        archive_url=f"{_PIPER_RELEASE}{name}.tar.bz2",
        language=language,
        gender=gender,
    )


#: Every model bundle the local providers can use, keyed by id.
#:
#: The voices are picked for CONSISTENCY across languages rather than one at a
#: time: the same perceived speaker in German, English and Spanish, so switching
#: language mid-conversation does not switch who is talking (BUG-086). A second,
#: feminine set is listed so a user can change voice without losing that
#: property — it is downloadable, not downloaded by default.
SHERPA_BUNDLES: dict[str, SherpaBundle] = {
    NEMOTRON_MODEL_ID: SherpaBundle(
        model_id=NEMOTRON_MODEL_ID,
        label="Nemotron 3.5 (streaming)",
        required_files=(
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ),
        hf_repo=NEMOTRON_HF_REPO,
    ),
    "vits-piper-de_DE-thorsten-medium": _piper_bundle(
        "vits-piper-de_DE-thorsten-medium",
        label="Thorsten (German)",
        language="de",
        gender="masculine",
    ),
    "vits-piper-en_US-ryan-medium": _piper_bundle(
        "vits-piper-en_US-ryan-medium",
        label="Ryan (English)",
        language="en",
        gender="masculine",
    ),
    "vits-piper-es_ES-davefx-medium": _piper_bundle(
        "vits-piper-es_ES-davefx-medium",
        label="Dave (Spanish)",
        language="es",
        gender="masculine",
    ),
    "vits-piper-de_DE-ramona-low": _piper_bundle(
        "vits-piper-de_DE-ramona-low",
        label="Ramona (German)",
        language="de",
        gender="feminine",
    ),
    "vits-piper-en_US-amy-medium": _piper_bundle(
        "vits-piper-en_US-amy-medium",
        label="Amy (English)",
        language="en",
        gender="feminine",
    ),
    "vits-piper-es_ES-sharvard-medium": _piper_bundle(
        "vits-piper-es_ES-sharvard-medium",
        label="Sharvard (Spanish)",
        language="es",
        gender="feminine",
    ),
}

#: The voices the local TTS install fetches — one per supported language, all
#: the same gender. Anything less would leave the assistant mute in a language
#: the rest of the app fully supports.
PIPER_DEFAULT_VOICES: tuple[str, ...] = (
    "vits-piper-de_DE-thorsten-medium",
    "vits-piper-en_US-ryan-medium",
    "vits-piper-es_ES-davefx-medium",
)

LOCAL_PROVIDERS: tuple[LocalProvider, ...] = (
    LocalProvider(
        provider_id="faster-whisper",
        tier="stt",
        runtime="faster-whisper",
        model_id=WHISPER_VOICE_MODEL,
        model_label=f"Whisper {WHISPER_VOICE_MODEL}",
        download_size="about 3 GB",
        pip_package=FASTER_WHISPER_PACKAGE,
    ),
    LocalProvider(
        provider_id="nemotron-local",
        tier="stt",
        runtime="sherpa-onnx",
        model_id=NEMOTRON_MODEL_ID,
        model_label="Nemotron 3.5 (streaming)",
        download_size="about 690 MB",
        pip_package=SHERPA_ONNX_PACKAGE,
        bundles=(NEMOTRON_MODEL_ID,),
    ),
    LocalProvider(
        provider_id="piper-local",
        tier="tts",
        runtime="sherpa-onnx",
        model_id=PIPER_DEFAULT_VOICES[0],
        model_label="Piper voices",
        download_size="about 200 MB",
        pip_package=SHERPA_ONNX_PACKAGE,
        bundles=PIPER_DEFAULT_VOICES,
    ),
)


def get_local_provider(provider_id: str) -> LocalProvider | None:
    """The catalog entry for *provider_id*, or None when it is not a local one.

    Callers branch on the RESULT (does this provider need a local runtime?),
    never on the provider's name — the AP-21 capability rule applied to
    "is this thing local".
    """
    for entry in LOCAL_PROVIDERS:
        if entry.provider_id == provider_id:
            return entry
    return None


def local_status(
    provider_id: str,
    *,
    model_override: str | None = None,
    data_dir: str | None = None,
) -> LocalModelStatus | None:
    """Readiness of local provider *provider_id*, or None if it is not local.

    ``model_override`` lets a caller ask about the checkpoint the CONFIG names
    rather than the catalog default, so a user who pinned another Whisper size
    in ``jarvis.toml`` sees the truth about THEIR model instead of a reassuring
    answer about one they never selected.
    """
    entry = get_local_provider(provider_id)
    if entry is None:
        return None
    if entry.runtime == "faster-whisper":
        return whisper_status(model_override or entry.model_id)
    return sherpa_status(
        entry.bundles,
        model_label=entry.model_label,
        download_size=entry.download_size,
        data_dir=data_dir,
    )


__all__ = [
    "LOCAL_PROVIDERS",
    "WHISPER_VOICE_MODEL",
    "LocalModelStatus",
    "LocalProvider",
    "get_local_provider",
    "local_status",
    "models_root",
    "runtime_installed",
    "sherpa_model_dir",
    "sherpa_model_present",
    "sherpa_status",
    "whisper_model_cached",
    "whisper_status",
]

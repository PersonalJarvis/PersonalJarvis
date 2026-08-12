"""User-selectable model profiles for the managed local realtime stack.

Ollama owns the language-model catalog. This module owns the speech catalog:
TTS checkpoints use backend-specific runtimes, so an arbitrary Hugging Face
model cannot safely be treated like an Ollama tag. Runtime-compatible profiles
are selectable and still have to pass the transactional voice smoke test.
Current frontier projects that are not yet supported by the pinned runtime stay
browsable with an honest compatibility verdict and their official source.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    id: str
    label: str
    backend: str
    model: str
    languages: tuple[str, ...]
    selectable: bool
    recommended: bool = False
    note: str = ""
    speaker: str = ""
    pocket_language: str = ""
    release_date: str = ""
    license: str = ""
    source_url: str = ""
    streaming: bool = False
    frontier: bool = False
    size_gb: float = 0.0


_QWEN_LANGUAGES = ("de", "en", "es", "fr", "it", "pt", "zh", "ja", "ko", "ru")
_POCKET_SOURCE = "https://github.com/kyutai-labs/pocket-tts"
_POCKET_RELEASE = "2026-05"


def _pocket_profile(
    *,
    profile_id: str,
    label: str,
    language: str,
    language_code: str,
    speaker: str,
    size_gb: float,
) -> VoiceProfile:
    return VoiceProfile(
        id=profile_id,
        label=label,
        backend="pocket",
        model=f"kyutai/pocket-tts:{language}",
        languages=(language_code,),
        selectable=True,
        note=(
            "Current CPU-first streaming model. This profile is language-specific; "
            "choose Qwen3-TTS when automatic language switching is required."
        ),
        speaker=speaker,
        pocket_language=language,
        release_date=_POCKET_RELEASE,
        license="MIT",
        source_url=_POCKET_SOURCE,
        streaming=True,
        frontier=True,
        size_gb=size_gb,
    )


# Selectable means the pinned speech-to-speech server has a shipped adapter for
# the profile. ``voice_catalog`` separately reports whether that adapter is
# already installed in the managed venv. New frontier families remain visible
# without pretending their checkpoints can be passed to a different backend.
VOICE_PROFILES: tuple[VoiceProfile, ...] = (
    VoiceProfile(
        id="qwen3-tts-1.7b",
        label="Qwen3-TTS 1.7B",
        backend="qwen3",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        languages=_QWEN_LANGUAGES,
        selectable=True,
        recommended=True,
        note="Best multilingual quality in the tested managed profile.",
        speaker="Aiden",
        release_date="2026-01",
        license="Apache-2.0",
        source_url="https://github.com/QwenLM/Qwen3-TTS",
        streaming=True,
        frontier=True,
        size_gb=3.4,
    ),
    VoiceProfile(
        id="qwen3-tts-0.6b",
        label="Qwen3-TTS 0.6B",
        backend="qwen3",
        model="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        languages=_QWEN_LANGUAGES,
        selectable=True,
        note="Smaller multilingual voice model; first use downloads its weights.",
        speaker="Aiden",
        release_date="2026-01",
        license="Apache-2.0",
        source_url="https://github.com/QwenLM/Qwen3-TTS",
        streaming=True,
        frontier=True,
        size_gb=1.4,
    ),
    _pocket_profile(
        profile_id="pocket-tts-de-24l",
        label="Pocket TTS 2.1 · German HQ",
        language="german_24l",
        language_code="de",
        speaker="juergen",
        size_gb=1.0,
    ),
    _pocket_profile(
        profile_id="pocket-tts-en",
        label="Pocket TTS 2.1 · English",
        language="english",
        language_code="en",
        speaker="alba",
        size_gb=0.5,
    ),
    _pocket_profile(
        profile_id="pocket-tts-fr-24l",
        label="Pocket TTS 2.1 · French HQ",
        language="french_24l",
        language_code="fr",
        speaker="estelle",
        size_gb=1.0,
    ),
    _pocket_profile(
        profile_id="pocket-tts-es-24l",
        label="Pocket TTS 2.1 · Spanish HQ",
        language="spanish_24l",
        language_code="es",
        speaker="lola",
        size_gb=1.0,
    ),
    _pocket_profile(
        profile_id="pocket-tts-it",
        label="Pocket TTS 2.1 · Italian HQ",
        language="italian_24l",
        language_code="it",
        speaker="giovanni",
        size_gb=1.0,
    ),
    _pocket_profile(
        profile_id="pocket-tts-pt",
        label="Pocket TTS 2.1 · Portuguese HQ",
        language="portuguese_24l",
        language_code="pt",
        speaker="rafael",
        size_gb=1.0,
    ),
    VoiceProfile(
        id="kokoro-82m",
        label="Kokoro-82M",
        backend="kokoro",
        model="hexgrad/Kokoro-82M",
        languages=("en", "ja", "zh", "fr", "it", "pt", "es", "hi"),
        selectable=False,
        note=(
            "The upstream adapter is available, but its optional package has "
            "not passed the managed voice test."
        ),
        release_date="2025-01",
        license="Apache-2.0",
        source_url="https://huggingface.co/hexgrad/Kokoro-82M",
        streaming=True,
        size_gb=0.4,
    ),
    VoiceProfile(
        id="chattts",
        label="ChatTTS 0.2.5",
        backend="chatTTS",
        model="2Noise/ChatTTS",
        languages=("en", "zh"),
        selectable=False,
        note=(
            "The model is non-commercial and the optional adapter has not "
            "passed the managed voice test."
        ),
        release_date="2026-04",
        license="CC-BY-NC-4.0",
        source_url="https://github.com/2noise/ChatTTS",
        streaming=True,
        size_gb=2.0,
    ),
    VoiceProfile(
        id="mms-tts",
        label="MMS TTS",
        backend="facebookMMS",
        model="facebook/mms-tts-*",
        languages=("single-language",),
        selectable=False,
        note=(
            "The upstream adapter uses one checkpoint per language and cannot "
            "follow automatic per-turn language switching yet."
        ),
        release_date="2023-05",
        license="CC-BY-NC-4.0",
        source_url="https://huggingface.co/facebook/mms-tts",
        size_gb=0.15,
    ),
    VoiceProfile(
        id="chatterbox-multilingual-v3",
        label="Chatterbox Multilingual V3",
        backend="chatterbox",
        model="ResembleAI/Chatterbox-Multilingual (v3)",
        languages=(
            "ar",
            "da",
            "de",
            "el",
            "en",
            "es",
            "fi",
            "fr",
            "he",
            "hi",
            "it",
            "ja",
            "ko",
            "ms",
            "nl",
            "no",
            "pl",
            "pt",
            "ru",
            "sv",
            "sw",
            "tr",
            "zh",
        ),
        selectable=False,
        note="Frontier candidate; the pinned realtime server has no Chatterbox adapter yet.",
        release_date="2026-06",
        license="MIT",
        source_url="https://github.com/resemble-ai/chatterbox",
        frontier=True,
        size_gb=3.2,
    ),
    VoiceProfile(
        id="fun-cosyvoice3-0.5b",
        label="Fun-CosyVoice 3.0 0.5B",
        backend="cosyvoice",
        model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        languages=("zh", "en", "ja", "ko", "de", "es", "fr", "it", "ru"),
        selectable=False,
        note=(
            "Frontier candidate; its bi-streaming runtime is not integrated "
            "with the managed server."
        ),
        release_date="2025-12",
        license="Apache-2.0",
        source_url="https://github.com/FunAudioLLM/CosyVoice",
        streaming=True,
        frontier=True,
        size_gb=2.0,
    ),
    VoiceProfile(
        id="moss-tts-nano-100m",
        label="MOSS-TTS-Nano 100M",
        backend="moss",
        model="OpenMOSS-Team/MOSS-TTS-Nano-100M",
        languages=(
            "zh",
            "en",
            "de",
            "es",
            "fr",
            "ja",
            "it",
            "hu",
            "ko",
            "ru",
            "fa",
            "ar",
            "pl",
            "pt",
            "cs",
        ),
        selectable=False,
        note=(
            "New CPU-friendly frontier candidate; the managed realtime "
            "adapter is not available yet."
        ),
        release_date="2026-04",
        license="Apache-2.0",
        source_url="https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M",
        streaming=True,
        frontier=True,
        size_gb=0.6,
    ),
    VoiceProfile(
        id="vibevoice-realtime-0.5b",
        label="VibeVoice Realtime 0.5B",
        backend="vibevoice",
        model="microsoft/VibeVoice-Realtime-0.5B",
        languages=("en",),
        selectable=False,
        note=(
            "Frontier candidate; multilingual output is experimental and the "
            "managed adapter is not available yet."
        ),
        release_date="2026-07",
        license="MIT",
        source_url="https://huggingface.co/microsoft/VibeVoice-Realtime-0.5B",
        streaming=True,
        frontier=True,
        size_gb=2.0,
    ),
)

_PROFILE_BY_ID = {profile.id: profile for profile in VOICE_PROFILES}
_TTS_FLAG = re.compile(r"(?<!\S)--tts(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)
_QWEN_MODEL_FLAG = re.compile(
    r"(?<!\S)--qwen3_tts_model_name(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_QWEN_SPEAKER_FLAG = re.compile(
    r"(?<!\S)--qwen3_tts_speaker(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_POCKET_VOICE_FLAG = re.compile(
    r"(?<!\S)--pocket_tts_voice(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_POCKET_DEVICE_FLAG = re.compile(
    r"(?<!\S)--pocket_tts_device(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)


def get_voice_profile(profile_id: str) -> VoiceProfile | None:
    """Return a known profile without accepting arbitrary command fragments."""
    return _PROFILE_BY_ID.get((profile_id or "").strip())


def current_voice_profile(command: str) -> str:
    """Resolve the effective managed TTS profile from a launch command."""
    backend = _flag_value(command, "--tts") or "qwen3"
    model = _flag_value(command, "--qwen3_tts_model_name")
    if backend.lower() == "qwen3" and not model:
        return "qwen3-tts-1.7b"
    if backend.lower() == "pocket":
        voice = _flag_value(command, "--pocket_tts_voice")
        language, _speaker = _decode_pocket_voice(voice)
        for profile in VOICE_PROFILES:
            if profile.backend == "pocket" and profile.pocket_language == language:
                return profile.id
        return "pocket-tts-en"
    for profile in VOICE_PROFILES:
        if profile.backend.lower() != backend.lower():
            continue
        if not profile.model or profile.model == model:
            return profile.id
    return ""


def _profile_runtime_ready(profile: VoiceProfile) -> bool:
    if not profile.selectable:
        return False
    from jarvis.realtime.local_server.install import _site_packages

    site_packages = _site_packages()
    if profile.backend == "qwen3":
        return (site_packages / "speech_to_speech" / "TTS" / "qwen3_tts_handler.py").is_file()
    if profile.backend == "pocket":
        from jarvis.realtime.local_server.patching import pocket_language_patch_state

        return (site_packages / "pocket_tts").is_dir() and pocket_language_patch_state(
            site_packages
        ) == "patched"
    return False


def voice_catalog(command: str) -> dict[str, object]:
    """JSON-shaped speech model catalog with compatibility metadata."""
    current = current_voice_profile(command)
    models: list[dict[str, object]] = []
    for profile in VOICE_PROFILES:
        item = asdict(profile)
        item["languages"] = list(profile.languages)
        item["current"] = profile.id == current
        item["runtime_ready"] = _profile_runtime_ready(profile)
        item["platform"] = "macOS / Linux / Windows"
        models.append(item)
    return {
        "current": current,
        "models": models,
        "hearing": {
            "id": "parakeet-tdt",
            "label": "Parakeet TDT",
            "note": "Speech-to-text for the call. The wake-word model is configured separately.",
        },
    }


def apply_voice_profile(command: str, profile_id: str) -> str:
    """Return ``command`` with one validated, selectable TTS profile applied."""
    profile = get_voice_profile(profile_id)
    if profile is None:
        raise ValueError(f"unknown voice model: {profile_id}")
    if not profile.selectable:
        raise ValueError(profile.note or f"voice model {profile_id} is not selectable")
    rewritten = _replace_flag(command, _TTS_FLAG, f"--tts {profile.backend}")
    if profile.backend == "qwen3":
        rewritten = _replace_flag(
            rewritten,
            _QWEN_MODEL_FLAG,
            f"--qwen3_tts_model_name {profile.model}",
        )
        if profile.speaker:
            rewritten = _replace_flag(
                rewritten,
                _QWEN_SPEAKER_FLAG,
                f"--qwen3_tts_speaker {profile.speaker}",
            )
    elif profile.backend == "pocket":
        encoded_voice = f"@{profile.pocket_language}:{profile.speaker}"
        rewritten = _replace_flag(
            rewritten,
            _POCKET_VOICE_FLAG,
            f"--pocket_tts_voice {encoded_voice}",
        )
        rewritten = _replace_flag(
            rewritten,
            _POCKET_DEVICE_FLAG,
            "--pocket_tts_device cpu",
        )
    return rewritten


def _decode_pocket_voice(value: str) -> tuple[str, str]:
    if value.startswith("@") and ":" in value:
        language, speaker = value[1:].split(":", 1)
        return language, speaker
    return "english", value or "alba"


def _replace_flag(command: str, pattern: re.Pattern[str], rendered: str) -> str:
    without_old = pattern.sub("", command or "").strip()
    return f"{without_old} {rendered}".strip()


def _flag_value(command: str, flag: str) -> str:
    try:
        import shlex

        tokens = shlex.split(command or "", posix=os.name != "nt")
    except ValueError:
        # Unbalanced quotes in a stored command — treat as "flag not found".
        return ""
    value = ""
    lowered_flag = flag.lower()
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if lowered == lowered_flag and index + 1 < len(tokens):
            value = tokens[index + 1].strip("\"'")
        elif lowered.startswith(f"{lowered_flag}="):
            value = token.split("=", 1)[1].strip("\"'")
    return value

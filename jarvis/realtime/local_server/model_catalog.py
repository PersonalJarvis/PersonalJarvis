"""User-selectable model profiles for the managed local realtime stack.

Ollama owns the language-model catalog.  This module owns the smaller speech
catalog because TTS checkpoints are not Ollama models and pretending otherwise
would make the picker lie.  Only profiles exercised by the pinned managed
runtime are selectable; upstream backends remain visible with an honest reason
until their dependency set and end-to-end audio path pass the same smoke gate.
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


_QWEN_LANGUAGES = ("de", "en", "es", "fr", "it", "pt", "zh", "ja", "ko", "ru")

# The two CustomVoice checkpoints use the same pinned qwen-tts runtime and
# speaker contract.  The remaining entries are capabilities of the pinned
# speech-to-speech server, but their optional dependency/profile bake-off has
# not passed Jarvis's managed audio gate yet.
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
    ),
    VoiceProfile(
        id="kokoro-82m",
        label="Kokoro-82M",
        backend="kokoro",
        model="hexgrad/Kokoro-82M",
        languages=("en", "ja", "zh", "fr", "it", "pt"),
        selectable=False,
        note="Visible upstream option; its optional package has not passed the managed voice test.",
    ),
    VoiceProfile(
        id="pocket-tts",
        label="Pocket TTS",
        backend="pocket",
        model="kyutai/pocket-tts",
        languages=("en", "fr"),
        selectable=False,
        note=(
            "Visible upstream option; its optional package conflicts with the "
            "current audio-enhancement stack."
        ),
    ),
    VoiceProfile(
        id="chattts",
        label="ChatTTS",
        backend="chatTTS",
        model="2Noise/ChatTTS",
        languages=("en", "zh"),
        selectable=False,
        note="Visible upstream option; it is not yet validated by the managed realtime smoke test.",
    ),
    VoiceProfile(
        id="mms-tts",
        label="MMS TTS",
        backend="facebookMMS",
        model="facebook/mms-tts-*",
        languages=("single-language",),
        selectable=False,
        note=(
            "Visible upstream option; each language needs a separate model, so "
            "it cannot follow automatic language switching yet."
        ),
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


def get_voice_profile(profile_id: str) -> VoiceProfile | None:
    """Return a known profile without accepting arbitrary command fragments."""
    return _PROFILE_BY_ID.get((profile_id or "").strip())


def current_voice_profile(command: str) -> str:
    """Resolve the effective managed TTS profile from a launch command."""
    backend = _flag_value(command, "--tts") or "qwen3"
    model = _flag_value(command, "--qwen3_tts_model_name")
    if backend.lower() == "qwen3" and not model:
        return "qwen3-tts-1.7b"
    for profile in VOICE_PROFILES:
        if profile.backend.lower() != backend.lower():
            continue
        if not profile.model or profile.model == model:
            return profile.id
    return ""


def voice_catalog(command: str) -> dict[str, object]:
    """JSON-shaped speech model catalog with the persisted selection."""
    current = current_voice_profile(command)
    models: list[dict[str, object]] = []
    for profile in VOICE_PROFILES:
        item = asdict(profile)
        item["languages"] = list(profile.languages)
        item["current"] = profile.id == current
        # Kokoro's built-in status differs by platform, but it stays
        # unselectable until its Jarvis smoke profile passes on every OS.
        item["platform"] = (
            "macOS / Linux / Windows"
            if profile.id != "kokoro-82m"
            else ("built in on macOS; optional package elsewhere")
        )
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
    return rewritten


def _replace_flag(command: str, pattern: re.Pattern[str], rendered: str) -> str:
    without_old = pattern.sub("", command or "").strip()
    return f"{without_old} {rendered}".strip()


def _flag_value(command: str, flag: str) -> str:
    try:
        import shlex

        tokens = shlex.split(command or "", posix=os.name != "nt")
    except ValueError:
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

"""Packaging guards for the one-official-full-install decision (spec 2026-07-07).

[full] must carry local-voice so the advertised install path ships the local
Whisper wake/STT runtime; pvporcupine (dead, proprietary-keyed, branded
built-in keywords) must be gone from the dependency surface entirely.
"""
from pathlib import Path

import tomllib

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _extras() -> dict[str, list[str]]:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]


def test_full_extra_includes_local_voice():
    full = " ".join(_extras()["full"])
    assert "local-voice" in full, "[full] must include the local-voice extra"


def test_local_voice_ships_faster_whisper():
    names = " ".join(_extras()["local-voice"])
    assert "faster-whisper" in names


def test_pvporcupine_is_gone_everywhere():
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    everything = list(data["project"]["dependencies"])
    for extra in data["project"]["optional-dependencies"].values():
        everything.extend(extra)
    assert not any("pvporcupine" in item for item in everything)

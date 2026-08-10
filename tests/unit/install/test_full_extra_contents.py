"""Packaging guards for the one-official-full-install decision (spec 2026-07-07).

[full] must carry desktop and local-voice so the advertised install path ships
the native window, tray, and local Whisper wake/STT runtime. Pvporcupine (dead,
proprietary-keyed, branded built-in keywords) must be gone from the dependency
surface entirely.
"""
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _extras() -> dict[str, list[str]]:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]


def test_full_extra_includes_local_voice():
    full = " ".join(_extras()["full"])
    assert "local-voice" in full, "[full] must include the local-voice extra"


def test_full_extra_includes_desktop():
    full_requirements = [Requirement(item) for item in _extras()["full"]]
    included_extras = {
        extra.casefold()
        for requirement in full_requirements
        for extra in requirement.extras
    }
    assert "desktop" in included_extras, "[full] must include the desktop extra"


def test_desktop_extra_ships_native_shell_dependencies():
    dependency_names = {
        Requirement(item).name.casefold() for item in _extras()["desktop"]
    }
    assert {"pystray", "pywebview"} <= dependency_names


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

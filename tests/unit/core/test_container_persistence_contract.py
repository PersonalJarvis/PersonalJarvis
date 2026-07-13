"""Static contract for the non-root headless container's writable state."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = PurePosixPath("/app/data")
_CONFIG_PATH = _DATA_DIR / "jarvis.toml"
_HOME_DIR = _DATA_DIR / "home"


def test_non_root_image_keeps_config_inside_the_persisted_writable_volume() -> None:
    """The runtime user must never need to write into the application tree."""
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert f"JARVIS_CONFIG={_CONFIG_PATH.as_posix()}" in dockerfile
    assert f"JARVIS_DATA_DIR={_DATA_DIR.as_posix()}" in dockerfile
    assert f"HOME={_HOME_DIR.as_posix()}" in dockerfile
    assert _CONFIG_PATH.parent == _DATA_DIR
    assert f"jarvis-data:{_DATA_DIR.as_posix()}" in compose

    chown_data = "chown -R jarvis:jarvis /app/data"
    non_root_user = "USER jarvis"
    assert chown_data in dockerfile
    assert "mkdir -p /app/data/home" in dockerfile
    assert "libportaudio2 curl git" in dockerfile
    assert non_root_user in dockerfile
    assert dockerfile.index(chown_data) < dockerfile.index(non_root_user)

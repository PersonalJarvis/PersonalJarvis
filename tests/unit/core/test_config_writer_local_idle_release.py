"""``[voice] local_idle_release_minutes`` — the local voice stack's idle window."""

from __future__ import annotations

import tomllib

from jarvis.core.config_writer import set_local_idle_release_minutes


def _minutes(path) -> int:
    return tomllib.loads(path.read_text(encoding="utf-8"))["voice"]["local_idle_release_minutes"]


def test_idle_release_is_written_under_voice(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text('[voice]\nmode = "realtime"\n', encoding="utf-8")

    set_local_idle_release_minutes(45, path=path)
    block = tomllib.loads(path.read_text(encoding="utf-8"))["voice"]
    assert block == {"mode": "realtime", "local_idle_release_minutes": 45}

    set_local_idle_release_minutes(-3, path=path)
    assert _minutes(path) == 0

"""Boot measurements must not draw a second desktop companion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.core.config import load_config
from jarvis.core.instance import InstanceIdentity
from jarvis.ui.desktop_app import boot_overlay_style
from jarvis.ui.overlay_styles import OVERLAY_STYLES
from scripts.measure_boot import _bench_env


@pytest.mark.parametrize("style", OVERLAY_STYLES)
@pytest.mark.parametrize("voice", (False, True), ids=("window-only", "voice-ready"))
def test_benchmark_disables_companion_without_changing_user_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, style: str, voice: bool
) -> None:
    for key in tuple(os.environ):
        if key.startswith("JARVIS_"):
            monkeypatch.delenv(key)
    config_file = tmp_path / "jarvis.toml"
    saved = f'[ui]\norb_style = "{style}"\n[overlay]\nenabled = true\n'
    config_file.write_text(saved, encoding="utf-8")
    # An inherited preference must not defeat the benchmark's isolation either.
    monkeypatch.setenv("JARVIS__UI__ORB_STYLE", style)
    parent_env = dict(os.environ)

    env = _bench_env(49152)
    if voice:
        # The desktop TTU harness re-enables speech after building this env.
        env["JARVIS_VOICE"] = "1"
    assert dict(os.environ) == parent_env
    with monkeypatch.context() as child:
        for key, value in env.items():
            child.setenv(key, value)
        config = load_config(config_file=config_file, profile="default")
        assert boot_overlay_style(
            config.ui.orb_style, instance=InstanceIdentity("default")
        ) == "none"
        assert os.environ["JARVIS_VOICE"] == ("1" if voice else "0")

    assert config_file.read_text(encoding="utf-8") == saved
    assert dict(os.environ) == parent_env

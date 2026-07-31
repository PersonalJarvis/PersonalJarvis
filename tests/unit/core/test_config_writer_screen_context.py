"""Atomic configuration writes for Screen Context privacy settings."""

from __future__ import annotations

import tomllib

import pytest

from jarvis.core.config_writer import set_screen_context_settings


def test_screen_context_patch_is_written_as_one_table_update(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text("[screen_context]\nenabled = false\n", encoding="utf-8")

    set_screen_context_settings(
        {
            "enabled": True,
            "denylist": ["Password Manager"],
            "sensitive_patterns": [r"customer:CUST-[0-9]+"],
        },
        path=path,
    )

    block = tomllib.loads(path.read_text(encoding="utf-8"))["screen_context"]
    assert block == {
        "enabled": True,
        "denylist": ["Password Manager"],
        "sensitive_patterns": [r"customer:CUST-[0-9]+"],
    }


def test_screen_context_patch_rejects_unknown_keys_before_writing(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    original = "[screen_context]\nenabled = true\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="unknown screen_context"):
        set_screen_context_settings({"typo": True}, path=path)

    assert path.read_text(encoding="utf-8") == original

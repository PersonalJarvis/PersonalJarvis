"""``packaging/pyinstaller_rthook_frozen.py`` — the frozen path redirection.

Why this file matters: the hook is what stops a natively installed app from
keeping the user's ``jarvis.toml`` and whole data tree inside its own program
directory, where an upgrade would overwrite the settings and an uninstall would
delete the memory. It cannot import ``jarvis`` (a runtime-hook exception kills a
windowed process before any logging exists), so it re-states the app-directory
convention — and the first test below is what stops that copy from drifting.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from jarvis.core.paths import user_data_dir

HOOK_PATH = Path(__file__).resolve().parents[3] / "packaging" / "pyinstaller_rthook_frozen.py"


def _load_hook() -> ModuleType:
    """Import the hook as a module. Its top-level ``_apply()`` is a no-op here.

    The hook returns immediately when ``sys.frozen`` is absent, which it is
    under pytest, so importing it has no side effects.
    """
    spec = importlib.util.spec_from_file_location("jarvis_rthook_frozen", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook() -> ModuleType:
    return _load_hook()


@pytest.fixture
def frozen_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Pretend to be a PyInstaller bundle with a per-user app directory."""
    meipass = tmp_path / "bundle" / "_internal"
    meipass.mkdir(parents=True)
    home = tmp_path / "AppData" / "Local"
    home.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    monkeypatch.delenv("JARVIS_CONFIG", raising=False)
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    return meipass, home / "Jarvis"


def test_hook_directory_matches_jarvis_core_paths(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The hook duplicates the convention on purpose; this is the gate that keeps
    # the duplicate honest. If jarvis.core.paths ever moves the app directory,
    # this fails instead of the frozen app quietly writing to the old place.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert hook._user_data_dir() == user_data_dir()

    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert hook._user_data_dir() == user_data_dir()


def test_apply_points_config_and_data_at_the_user_directory(
    hook: ModuleType, frozen_bundle: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    meipass, home = frozen_bundle
    (meipass / "jarvis.toml").write_text("[app]\nname = 'bundled'\n", encoding="utf-8")

    hook._apply()

    import os

    assert Path(os.environ["JARVIS_CONFIG"]) == home / "jarvis.toml"
    assert Path(os.environ["JARVIS_DATA_DIR"]) == home / "data"
    # First run seeds the config from the copy inside the bundle, so the app
    # starts on the shipped defaults instead of an empty file.
    assert (home / "jarvis.toml").read_text(encoding="utf-8") == ("[app]\nname = 'bundled'\n")


def test_apply_never_overwrites_an_existing_user_config(
    hook: ModuleType, frozen_bundle: tuple[Path, Path]
) -> None:
    meipass, home = frozen_bundle
    (meipass / "jarvis.toml").write_text("bundled\n", encoding="utf-8")
    home.mkdir(parents=True)
    (home / "jarvis.toml").write_text("the user's own settings\n", encoding="utf-8")

    hook._apply()

    # This is the whole point: an in-place upgrade reinstalls the bundled file
    # and must not touch what the user configured.
    assert (home / "jarvis.toml").read_text(encoding="utf-8") == ("the user's own settings\n")


def test_apply_respects_an_explicit_override(
    hook: ModuleType, frozen_bundle: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    meipass, home = frozen_bundle
    (meipass / "jarvis.toml").write_text("bundled\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", "D:/scratch/jarvis.toml")
    monkeypatch.setenv("JARVIS_DATA_DIR", "D:/scratch/data")

    hook._apply()

    import os

    assert os.environ["JARVIS_CONFIG"] == "D:/scratch/jarvis.toml"
    assert os.environ["JARVIS_DATA_DIR"] == "D:/scratch/data"
    # No seeding happens for an overridden path.
    assert not (home / "jarvis.toml").exists()


def test_apply_does_nothing_outside_a_frozen_bundle(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delenv("JARVIS_CONFIG", raising=False)
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)

    hook._apply()

    import os

    # A dev checkout keeps resolving its own paths; the hook is inert there.
    assert "JARVIS_CONFIG" not in os.environ
    assert "JARVIS_DATA_DIR" not in os.environ


def test_apply_still_sets_the_path_when_seeding_fails(
    hook: ModuleType, frozen_bundle: tuple[Path, Path]
) -> None:
    meipass, home = frozen_bundle
    # No jarvis.toml inside the bundle: nothing to copy, but the app must still
    # be pointed at the writable location (config_writer creates the file).
    hook._apply()

    import os

    assert Path(os.environ["JARVIS_CONFIG"]) == home / "jarvis.toml"
    assert not (home / "jarvis.toml").exists()

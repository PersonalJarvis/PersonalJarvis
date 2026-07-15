"""Unit tests for the cross-platform uninstaller (jarvis/setup/uninstall.py).

Safety: every test drives tmp_path directories or fakes — NOTHING here deletes a
real install, the real keyring, or the real autostart entry. The removal helpers
are monkeypatched to recorders whenever run_uninstall() is exercised.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis.setup import uninstall
from jarvis.setup.uninstall import UninstallPlan


def _fake_plan(
    tmp: Path, *, is_jarvis: bool = True, keys: list[str] | None = None
) -> UninstallPlan:
    return UninstallPlan(
        install_dir=tmp,
        is_jarvis_install=is_jarvis,
        autostart_supported=True,
        autostart_entry=str(tmp / "autostart.entry"),
        keyring_keys=list(keys or []),
    )


def _use_plan(monkeypatch: pytest.MonkeyPatch, plan: UninstallPlan) -> None:
    monkeypatch.setattr(uninstall, "build_plan", lambda: plan)


def _record_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the four removal helpers with recorders; return the shared log."""
    called: list[str] = []
    monkeypatch.setattr(
        uninstall,
        "_remove_desktop_registration",
        lambda: called.append("desktop"),
    )
    monkeypatch.setattr(uninstall, "_remove_autostart", lambda: called.append("autostart"))
    monkeypatch.setattr(uninstall, "_remove_keys", lambda k: called.append("keys"))
    monkeypatch.setattr(uninstall, "_remove_folder", lambda p: called.append("folder"))
    return called


# ---------------------------------------------------------------- guard
def test_looks_like_jarvis_install_true(tmp_path: Path) -> None:
    (tmp_path / "jarvis").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='personal-jarvis'\n", encoding="utf-8"
    )
    assert uninstall._looks_like_jarvis_install(tmp_path) is True


def test_looks_like_jarvis_install_false_for_random_dir(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    assert uninstall._looks_like_jarvis_install(tmp_path) is False


def test_build_plan_reflects_real_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep it hermetic: no real keyring / autostart probing.
    monkeypatch.setattr(uninstall, "_autostart_state", lambda: (False, None))
    monkeypatch.setattr(uninstall, "_keyring_keys_present", lambda: [])
    plan = uninstall.build_plan()
    from jarvis.core import config as cfg

    assert plan.install_dir == Path(cfg.PROJECT_ROOT).resolve()
    # The repo we run from IS a Jarvis install.
    assert plan.is_jarvis_install is True
    assert plan.config_file == plan.install_dir / "jarvis.toml"
    assert plan.data_dir == plan.install_dir / "data"


# ---------------------------------------------------------------- refusal
def test_run_uninstall_refuses_non_jarvis_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_plan(monkeypatch, _fake_plan(tmp_path, is_jarvis=False))
    called = _record_steps(monkeypatch)

    rc = uninstall.run_uninstall(assume_yes=True)
    assert rc == 2
    assert called == []  # refused before touching anything


# ---------------------------------------------------------------- dry run
def test_dry_run_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_plan(monkeypatch, _fake_plan(tmp_path, keys=["openai_api_key"]))
    called = _record_steps(monkeypatch)

    rc = uninstall.run_uninstall(dry_run=True)
    assert rc == 0
    assert called == []


# ---------------------------------------------------------------- confirmation
def test_cancel_at_prompt_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_plan(monkeypatch, _fake_plan(tmp_path))
    monkeypatch.setattr(uninstall, "_confirm", lambda: False)
    called = _record_steps(monkeypatch)

    rc = uninstall.run_uninstall(assume_yes=False)
    assert rc == 1
    assert called == []


@pytest.mark.parametrize(
    "answer,expected",
    [("yes", True), ("y", True), ("no", False), ("", False)],
)
def test_confirm_requires_yes(monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": answer)
    assert uninstall._confirm() is expected


# ---------------------------------------------------------------- happy path
def test_assume_yes_runs_all_four_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_plan(monkeypatch, _fake_plan(tmp_path, keys=["openai_api_key"]))
    called = _record_steps(monkeypatch)

    rc = uninstall.run_uninstall(assume_yes=True)
    assert rc == 0
    assert called == [
        "desktop",
        "autostart",
        "keys",
        "folder",
    ]  # order: outside-the-folder first


def test_keep_keys_skips_key_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_plan(monkeypatch, _fake_plan(tmp_path, keys=["openai_api_key"]))
    called = _record_steps(monkeypatch)

    uninstall.run_uninstall(assume_yes=True, keep_keys=True)
    assert "keys" not in called
    assert "folder" in called


def test_keep_folder_skips_folder_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_plan(monkeypatch, _fake_plan(tmp_path, keys=["openai_api_key"]))
    called = _record_steps(monkeypatch)

    uninstall.run_uninstall(assume_yes=True, keep_folder=True)
    assert "folder" not in called
    assert "keys" in called


def test_bootstrap_fallbacks_remove_registration_without_the_venv() -> None:
    root = Path(__file__).resolve().parents[3]
    windows = (root / "install" / "uninstall.ps1").read_text(encoding="utf-8")
    posix = (root / "install" / "uninstall.sh").read_text(encoding="utf-8")

    assert "CurrentVersion\\Uninstall\\PersonalJarvis" in windows
    assert "Start Menu\\Programs\\Personal Jarvis.lnk" in windows
    assert "$HOME/Applications/Personal Jarvis.app" in posix
    assert "applications/personal-jarvis.desktop" in posix


# ---------------------------------------------------------------- key removal
def test_remove_keys_deletes_each_present_key(monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(uninstall.cfg, "delete_secret", lambda key: (deleted.append(key) or True))
    n = uninstall._remove_keys(["openai_api_key", "gemini_api_key"])
    assert n == 2
    assert deleted == ["openai_api_key", "gemini_api_key"]


# ---------------------------------------------------------------- folder removal
def test_remove_folder_direct_delete(tmp_path: Path) -> None:
    target = tmp_path / "install"
    target.mkdir()
    (target / "file.txt").write_text("x", encoding="utf-8")
    # The running interpreter is NOT inside tmp_path, so this is a direct rmtree
    # on every OS (no self-delete branch).
    assert uninstall._remove_folder(target) is True
    assert not target.exists()


def test_running_inside_detects_self_host() -> None:
    exe_dir = Path(sys.executable).resolve().parent
    assert uninstall._running_inside(exe_dir) is True


def test_running_inside_false_for_unrelated_dir(tmp_path: Path) -> None:
    assert uninstall._running_inside(tmp_path) is False


def test_windows_self_deleter_writes_batch_and_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args, **kwargs):  # noqa: ANN001, ANN003
            spawned.append(args)

    monkeypatch.setattr(uninstall.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(uninstall.tempfile, "gettempdir", lambda: str(tmp_path))

    target = tmp_path / "somewhere" / ".personal-jarvis"
    uninstall._spawn_windows_self_deleter(target)

    # A batch was written and cmd was invoked on it.
    bats = list(tmp_path.glob("jarvis_uninstall_*.bat"))
    assert len(bats) == 1
    body = bats[0].read_text(encoding="utf-8")
    assert str(target) in body
    assert "rmdir /s /q" in body
    assert spawned and spawned[0][0] == "cmd"

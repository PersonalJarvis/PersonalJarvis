"""Post-exit update transaction coverage for the detached relauncher."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.ui import relauncher


def _write_pending(root: Path) -> tuple[str, str]:
    previous = "a" * 40
    target = "b" * 40
    (root / relauncher.PENDING_UPDATE_FILENAME).write_text(
        json.dumps(
            {
                "schema": 1,
                "previous_revision": previous,
                "target_revision": target,
                "profile": "full",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return previous, target


def _managed_root(root: Path) -> tuple[str, str]:
    (root / ".git").mkdir()
    (root / relauncher.MANAGED_MARKER).write_text("{}\n", encoding="utf-8")
    return _write_pending(root)


def test_pending_manifest_validation_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / relauncher.PENDING_UPDATE_FILENAME
    path.write_text('{"schema": 1, "target_revision": "main"}\n', encoding="utf-8")
    assert relauncher._read_pending_update(tmp_path) is None

    previous, target = _write_pending(tmp_path)
    payload = relauncher._read_pending_update(tmp_path)
    assert payload is not None
    assert payload["previous_revision"] == previous
    assert payload["target_revision"] == target


@pytest.mark.parametrize(
    ("profile", "flag"),
    (("full", "--with-desktop"), ("headless", "--headless")),
)
def test_installer_command_preserves_managed_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    profile: str,
    flag: str,
) -> None:
    monkeypatch.setattr(relauncher, "_managed_python", lambda _root: "python")
    command = relauncher._installer_command(tmp_path, profile)
    assert command[-1] == flag
    assert "--no-launch" in command


def test_ui_bundle_requires_a_tracked_javascript_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "jarvis" / "ui" / "web" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>'
        '<link rel="stylesheet" href="/assets/app.css">',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("export {};\n", encoding="utf-8")
    (assets / "app.css").write_text("body {}\n", encoding="utf-8")
    tracked: list[list[str]] = []

    def _tracked(cmd, *, root, timeout):
        tracked.append(cmd)
        return 0

    monkeypatch.setattr(relauncher, "_run_update_command", _tracked)
    assert relauncher._ui_bundle_ready(tmp_path) is True
    assert len(tracked) == 3

    (assets / "app.js").unlink()
    assert relauncher._ui_bundle_ready(tmp_path) is False


def test_successful_update_runs_full_installer_after_target_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    previous, target = _managed_root(tmp_path)
    calls: list[list[str]] = []

    def _run(cmd, *, root, timeout):
        calls.append(cmd)
        return 0

    monkeypatch.setattr(relauncher, "_run_update_command", _run)
    monkeypatch.setattr(relauncher, "_ui_bundle_ready", lambda _root: True)
    monkeypatch.setattr(
        relauncher,
        "_installer_command",
        lambda _root, profile: ["installer", profile],
    )

    assert relauncher.finalize_pending_update(tmp_path) is True
    assert calls == [
        ["git", "reset", "--hard", target],
        ["installer", "full"],
    ]
    assert not (tmp_path / relauncher.PENDING_UPDATE_FILENAME).exists()
    result = json.loads(
        (tmp_path / relauncher.UPDATE_RESULT_FILENAME).read_text(encoding="utf-8")
    )
    assert result["ok"] is True
    assert result["rolled_back"] is False
    assert result["previous_revision"] == previous


def test_failed_target_install_rolls_back_and_repairs_previous_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    previous, target = _managed_root(tmp_path)
    calls: list[list[str]] = []

    def _run(cmd, *, root, timeout):
        calls.append(cmd)
        if cmd == ["installer", "full"] and calls.count(cmd) == 1:
            return 1
        return 0

    monkeypatch.setattr(relauncher, "_run_update_command", _run)
    monkeypatch.setattr(relauncher, "_ui_bundle_ready", lambda _root: True)
    monkeypatch.setattr(
        relauncher,
        "_installer_command",
        lambda _root, profile: ["installer", profile],
    )

    assert relauncher.finalize_pending_update(tmp_path) is False
    assert calls == [
        ["git", "reset", "--hard", target],
        ["installer", "full"],
        ["git", "reset", "--hard", previous],
        ["installer", "full"],
    ]
    result = json.loads(
        (tmp_path / relauncher.UPDATE_RESULT_FILENAME).read_text(encoding="utf-8")
    )
    assert result["ok"] is False
    assert result["rolled_back"] is True


def test_relauncher_finalizes_before_spawning_new_app(tmp_path: Path) -> None:
    order: list[str] = []

    def _spawn(_cmd, **_kwargs):
        order.append("spawn")
        return SimpleNamespace(pid=99)

    rc = relauncher.main(
        ["42", str(tmp_path)],
        _wait=lambda _pid, **_kwargs: True,
        _spawn=_spawn,
        _sleep=lambda _seconds: None,
        _alive=lambda pid: pid == 42,
        _settled=lambda *_args, **_kwargs: True,
        _finalize_update=lambda _cwd: order.append("finalize") or True,
    )

    assert rc == 0
    assert order == ["finalize", "spawn"]

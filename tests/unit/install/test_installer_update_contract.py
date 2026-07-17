"""Installer contracts reused by the post-exit in-app updater."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "installer_update_contract", REPO / "install" / "installer.py"
)
assert _SPEC is not None and _SPEC.loader is not None
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)


@pytest.mark.parametrize(
    ("with_desktop", "profile"),
    ((True, "full"), (False, "headless")),
)
def test_managed_marker_persists_install_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    with_desktop: bool,
    profile: str,
) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    installer.write_managed_marker(with_desktop=with_desktop)

    payload = json.loads(
        (tmp_path / ".jarvis-managed-install").read_text(encoding="utf-8")
    )
    assert payload["profile"] == profile
    assert payload["desktop"] is with_desktop


def test_desktop_dependency_plan_is_complete_and_verified(capsys) -> None:
    installer.step_pip_install(
        with_desktop=True,
        with_voice_local=False,
        dry_run=True,
    )
    output = capsys.readouterr().out
    assert "--require-hashes" in output
    assert ".[full]" in output
    assert "dependency consistency check" in output


def test_metadata_repair_removes_only_incomplete_records(tmp_path: Path) -> None:
    valid = tmp_path / "example-1.0.dist-info"
    valid.mkdir()
    (valid / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: example\nVersion: 1.0\n",
        encoding="utf-8",
    )
    broken = tmp_path / "old_update-0.9.dist-info"
    broken.mkdir()
    (broken / "RECORD").write_text("", encoding="utf-8")
    licenses = broken / "licenses"
    licenses.mkdir()
    os.chmod(licenses, stat.S_IREAD)
    os.chmod(broken, stat.S_IREAD)

    assert installer.repair_distribution_metadata(site_packages=tmp_path) is True
    assert valid.is_dir()
    assert not broken.exists()


def test_metadata_repair_dry_run_does_not_remove_records(tmp_path: Path) -> None:
    broken = tmp_path / "old_update-0.9.dist-info"
    broken.mkdir()

    assert installer.repair_distribution_metadata(
        site_packages=tmp_path, dry_run=True
    ) is True
    assert broken.is_dir()


def test_ui_bundle_check_rejects_missing_javascript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "jarvis" / "ui" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>\n", encoding="utf-8")
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)

    assert installer.step_ui_bundle_check() is False


def test_ui_bundle_check_accepts_complete_entry_set(
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
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)

    assert installer.step_ui_bundle_check() is True


def test_desktop_registration_failure_is_install_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(installer, "venv_python", lambda: tmp_path / "python")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"ok": false, "attempted": true}',
            stderr="",
        ),
    )

    assert installer.step_desktop_integration(enabled=True, dry_run=False) is False


def test_desktop_registration_failure_surfaces_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(installer, "venv_python", lambda: tmp_path / "python")
    stderr = "\n".join(f"stderr-line-{index}" for index in range(20))
    report = json.dumps(
        {
            "ok": False,
            "attempted": True,
            "warnings": ["bundle identity probe failed"],
        }
    )
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=report,
            stderr=stderr,
        ),
    )

    assert installer.step_desktop_integration(enabled=True, dry_run=False) is False

    out = capsys.readouterr().out
    assert "bundle identity probe failed" in out
    assert "stderr-line-19" in out
    assert "stderr-line-5" in out
    # Only the last ~15 stderr lines are echoed to the console.
    assert "stderr-line-4" not in out
    assert "--headless" in out
    # The full path may be folded across lines by the console width.
    assert "full log:" in out
    log_path = tmp_path / "data" / "logs" / "install-desktop-integration.log"
    text = log_path.read_text(encoding="utf-8")
    assert "stderr-line-0" in text
    assert "bundle identity probe failed" in text


def test_desktop_registration_success_still_writes_full_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(installer, "venv_python", lambda: tmp_path / "python")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"ok": true, "attempted": true}',
            stderr="probe chatter\n",
        ),
    )

    assert installer.step_desktop_integration(enabled=True, dry_run=False) is True

    text = (
        tmp_path / "data" / "logs" / "install-desktop-integration.log"
    ).read_text(encoding="utf-8")
    assert '{"ok": true, "attempted": true}' in text
    assert "probe chatter" in text

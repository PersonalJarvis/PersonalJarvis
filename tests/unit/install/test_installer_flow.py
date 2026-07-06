"""Installer flow: no wizard, explanatory steps, launch is the LAST action."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("installer", REPO / "install" / "installer.py")
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)


def test_no_wizard_invocation_anywhere() -> None:
    source = (REPO / "install" / "installer.py").read_text(encoding="utf-8")
    assert "--wizard" not in source.replace("--no-wizard", "")


def test_dry_run_order_launch_last(monkeypatch, capsys) -> None:
    monkeypatch.setattr(installer, "write_managed_marker", lambda: None)
    rc = installer.main(["--dry-run", "--headless"])
    out = capsys.readouterr().out
    assert rc == 0
    # The launch step must come after every prepare step AND after the summary.
    assert out.rindex("Launch") > out.rindex("Voice models")
    assert out.rindex("Launch") > out.rindex("Done")


def test_update_run_is_detected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    assert installer.is_update_run() is False
    (tmp_path / ".jarvis-managed-install").write_text("{}", encoding="utf-8")
    assert installer.is_update_run() is True


def test_update_summary_promises_no_reonboarding(monkeypatch, capsys, tmp_path) -> None:
    (tmp_path / ".jarvis-managed-install").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(installer, "write_managed_marker", lambda: None)
    rc = installer.main(["--dry-run", "--headless", "--no-launch"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no re-onboarding" in out

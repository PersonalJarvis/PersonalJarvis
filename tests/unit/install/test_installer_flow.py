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
    # The launch line must come after every prepare phase AND after the summary.
    assert out.rindex("Launching") > out.rindex("Voice models")
    assert out.rindex("Launching") > out.rindex("Personal Jarvis is ready")


def test_dry_run_prints_the_numbered_journey(monkeypatch, capsys) -> None:
    """Design 2026-07-09: one six-phase journey; this stage owns 4/6..6/6 in order."""
    monkeypatch.setattr(installer, "write_managed_marker", lambda: None)
    rc = installer.main(["--dry-run", "--headless"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.index("4/6") < out.index("5/6") < out.index("6/6")
    assert "Dependencies" in out
    assert "Voice models" in out
    assert "Finish & launch" in out


def test_installer_prompts_only_inside_missing_prerequisite_flow() -> None:
    """Design amendment 2026-07-11: Stage 1 may ask only when Python or Git
    is missing. The normal path and all of Stage 2 remain prompt-free."""
    sh = (REPO / "install" / "install.sh").read_text(encoding="utf-8")
    ps1 = (REPO / "install" / "install.ps1").read_text(encoding="utf-8")
    py = (REPO / "install" / "installer.py").read_text(encoding="utf-8")

    sh_begin = sh.index("# --- prerequisite-bootstrap begin")
    sh_end = sh.index("# --- prerequisite-bootstrap end")
    sh_outside = sh[:sh_begin] + sh[sh_end:]
    assert "read -r" not in sh_outside
    assert "read -p" not in sh
    assert sh[sh_begin:sh_end].count("read -r") == 2
    assert all(
        "</dev/tty" in line
        for line in sh[sh_begin:sh_end].splitlines()
        if "read -r" in line
    )

    ps1_begin = ps1.index("# --- prerequisite-bootstrap begin")
    ps1_end = ps1.index("# --- prerequisite-bootstrap end")
    ps1_outside = ps1[:ps1_begin] + ps1[ps1_end:]
    assert "Read-Host" not in ps1_outside
    assert "$Host.UI.Prompt" not in ps1
    ps1_prompt_lines = [
        line
        for line in ps1[ps1_begin:ps1_end].splitlines()
        if "Read-Host" in line and not line.lstrip().startswith("#")
    ]
    assert len(ps1_prompt_lines) == 2

    for forbidden in ("input(", "Confirm.ask", "Prompt.ask", "getpass"):
        assert forbidden not in py


def test_update_run_is_detected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    assert installer.is_update_run() is False
    (tmp_path / ".jarvis-managed-install").write_text("{}", encoding="utf-8")
    assert installer.is_update_run() is True


def test_default_pip_plan_installs_full_extra(capsys) -> None:
    """Design 2026-07-07: the one advertised install path installs .[full]."""
    installer.step_pip_install(with_desktop=True, with_voice_local=False, dry_run=True)
    out = capsys.readouterr().out
    assert ".[full]" in out
    assert ".[desktop]" not in out
    assert ".[local-voice]" not in out


def test_headless_pip_plan_stays_base_floor(capsys) -> None:
    """--headless keeps the torch-free base floor: no extras at all."""
    installer.step_pip_install(with_desktop=False, with_voice_local=False, dry_run=True)
    out = capsys.readouterr().out
    assert ".[full]" not in out
    assert ".[desktop]" not in out


def test_update_summary_promises_no_reonboarding(monkeypatch, capsys, tmp_path) -> None:
    (tmp_path / ".jarvis-managed-install").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(installer, "write_managed_marker", lambda: None)
    rc = installer.main(["--dry-run", "--headless", "--no-launch"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no re-onboarding" in out

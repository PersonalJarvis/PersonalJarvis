"""Bare ``jarvis`` must start the desktop app — the README's headline promise.

For a long time it did not. ``main()`` ended in ``_run_tray_app``: a tray icon
with no backend, no window and no voice, which blocks forever and prints
nothing useful. Typing ``jarvis`` therefore looked exactly like a hang, and the
desktop app was reachable only through ``run.bat`` or the Start-Menu shortcut —
while README.md advertised ``jarvis  # full desktop: window + voice + Orb
overlay``. These tests pin the entry point to that documented contract on every
OS, since nothing about the dispatch is platform-specific.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import jarvis.__main__ as main_mod


class _Recorder:
    """Records that a start path ran, without booting anything real."""

    def __init__(self, rv: int = 0) -> None:
        self.calls: list[object] = []
        self.rv = rv

    def __call__(self, *args: object, **kwargs: object) -> int:
        self.calls.append((args, kwargs))
        return self.rv


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace the desktop launcher with a recorder; never open a window."""
    rec = _Recorder()
    monkeypatch.setattr(main_mod, "_run_desktop", rec)
    return rec


@pytest.fixture
def tray(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace the tray loop with a recorder; the real one blocks forever."""
    rec = _Recorder()

    async def _fake_tray(**kwargs: object) -> int:
        rec(**kwargs)
        return 0

    monkeypatch.setattr(main_mod, "_run_tray_app", _fake_tray)
    return rec


def test_bare_jarvis_runs_the_desktop_app(desktop: _Recorder, tray: _Recorder) -> None:
    assert main_mod.main([]) == 0
    assert desktop.calls, "bare `jarvis` must start the desktop app"
    assert not tray.calls, "bare `jarvis` must not fall into the tray-only loop"


def test_tray_only_behind_its_own_flag(desktop: _Recorder, tray: _Recorder) -> None:
    """The tray loop survives as an explicit opt-in, not as the default."""
    assert main_mod.main(["--tray"]) == 0
    assert tray.calls, "--tray must still reach the tray loop"
    assert not desktop.calls


def test_serve_stays_headless(desktop: _Recorder, tray: _Recorder) -> None:
    """`jarvis serve` is the window-less path and must not grow a window."""
    rec = _Recorder()
    import jarvis.ui.web.launcher as launcher_mod

    original = launcher_mod.main
    launcher_mod.main = rec  # type: ignore[assignment]
    try:
        assert main_mod.main(["serve"]) == 0
    finally:
        launcher_mod.main = original  # type: ignore[assignment]
    assert rec.calls and rec.calls[0][0] == (["--headless"],)
    assert not desktop.calls and not tray.calls


def test_debug_travels_as_env_not_as_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher's parser has no --debug; forwarding the flag would abort it.

    run.bat and run.sh both set JARVIS_DEBUG for exactly this reason, and the
    entry point has to use the same channel.
    """
    monkeypatch.delenv("JARVIS_DEBUG", raising=False)
    seen: dict[str, object] = {}

    def _fake_launcher_main(argv: list[str]) -> int:
        seen["argv"] = argv
        seen["env"] = main_mod.os.environ.get("JARVIS_DEBUG")
        return 0

    import jarvis.ui.web.launcher as launcher_mod

    monkeypatch.setattr(launcher_mod, "main", _fake_launcher_main)
    monkeypatch.setattr(main_mod, "_missing_desktop_dependency", lambda: None)

    assert main_mod._run_desktop(debug=True) == 0
    assert seen["argv"] == [], "no --debug on argv; the launcher would reject it"
    assert seen["env"] == "1"


def test_missing_pywebview_explains_itself_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A window-less base install must get a sentence, not a traceback.

    ``pip install personal-jarvis`` ships without pywebview on purpose, so an
    arbitrary downloader typing ``jarvis`` legitimately lands here.
    """
    monkeypatch.setattr(
        main_mod, "_missing_desktop_dependency", lambda: "pywebview is missing"
    )
    rc = main_mod._run_desktop(debug=False)
    assert rc == 4, "a refused start must not report success"
    assert "pywebview is missing" in capsys.readouterr().err


def test_dependency_probe_passes_on_this_install() -> None:
    """Sanity: wherever pywebview IS installed, the probe must not block boot."""
    import importlib.util

    have_webview = importlib.util.find_spec("webview") is not None
    result = main_mod._missing_desktop_dependency()
    assert (result is None) is have_webview


def test_readme_still_documents_bare_jarvis_as_the_desktop_entry() -> None:
    """Guard the contract from the other side: docs and code must agree."""
    readme = Path(__file__).resolve().parents[2] / "README.md"
    if not readme.is_file():
        pytest.skip("README absent (slim checkout)")
    text = readme.read_text(encoding="utf-8")
    assert "jarvis          # full desktop" in text, (
        "README no longer advertises bare `jarvis` as the desktop entry — "
        "if that changed on purpose, update this test and __main__.main()"
    )


def test_tray_import_is_not_on_the_desktop_boot_path() -> None:
    """The tray toolkit must not be imported for the normal start (AP-26).

    ``jarvis.ui.tray`` pulls in pystray/PIL. Now that the tray is a rarely used
    opt-in, paying for it on every desktop launch is dead weight on the boot
    critical path. Measured in a fresh interpreter rather than read out of the
    source: a ``TYPE_CHECKING`` import reads like an import but never runs, and
    ``sys.modules`` in this process is polluted by whatever ran before us.
    """
    import subprocess

    probe = (
        "import sys; import jarvis.__main__; "
        "print('LOADED' if 'jarvis.ui.tray' in sys.modules else 'CLEAN')"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert out.returncode == 0, f"probe failed: {out.stderr}"
    assert out.stdout.strip() == "CLEAN", (
        "importing jarvis.__main__ pulls in jarvis.ui.tray — move that import "
        "into _run_tray_app so the desktop path does not pay for pystray"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw is Windows-only")
def test_windows_start_paths_agree_on_the_launcher_module() -> None:
    """The Start-Menu shortcut and the CLI must start the SAME thing.

    The shortcut runs ``pythonw -m jarvis.ui.web.launcher``; bare ``jarvis``
    now routes to ``launcher.main`` as well. If those two ever drift apart, one
    of the two documented ways to start the app breaks without a test noticing.
    """
    from jarvis.ui.icon_utils import _LAUNCHER_MODULE

    assert _LAUNCHER_MODULE == "jarvis.ui.web.launcher"
    import inspect

    assert "from jarvis.ui.web import launcher" in inspect.getsource(
        main_mod._run_desktop
    )

"""WindowsAutostart PowerShell-script assembly (pure → CI-provable anywhere).

The actual .lnk creation/read-back needs a real Windows host + WScript.Shell and
is covered by live sign-off; here we prove the script we *would* run is correct.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.autostart.protocol import LaunchSpec
from jarvis.autostart.windows import build_create_script, build_read_script


def _spec(minimized: bool = True) -> LaunchSpec:
    return LaunchSpec(
        program=r"C:\Python\pythonw.exe",
        args=("-m", "jarvis.ui.web.launcher"),
        working_dir=r"C:\Users\u\Personal Jarvis",
        minimized=minimized,
    )


def test_create_script_sets_target_args_workdir() -> None:
    script = build_create_script(Path(r"C:\startup\Personal Jarvis.lnk"), _spec())
    assert r"$sc.TargetPath = 'C:\Python\pythonw.exe'" in script
    assert "$sc.Arguments = '-m jarvis.ui.web.launcher'" in script
    assert r"$sc.WorkingDirectory = 'C:\Users\u\Personal Jarvis'" in script
    assert "$sc.Save()" in script


def test_minimized_maps_to_windowstyle_7() -> None:
    assert "$sc.WindowStyle = 7" in build_create_script(Path("x.lnk"), _spec(minimized=True))


def test_non_minimized_maps_to_windowstyle_1() -> None:
    assert "$sc.WindowStyle = 1" in build_create_script(Path("x.lnk"), _spec(minimized=False))


def test_read_script_emits_three_sentinel_lines() -> None:
    script = build_read_script(Path(r"C:\startup\Personal Jarvis.lnk"))
    assert script.count("Write-Output") == 3
    assert "$sc.TargetPath" in script
    assert "$sc.Arguments" in script
    assert "$sc.WorkingDirectory" in script


def test_create_script_embeds_icon_when_given() -> None:
    """The autostart .lnk must carry the Jarvis icon so the taskbar button is
    branded from first paint — not the bare pythonw.exe Python logo."""
    ico = r"C:\Users\u\Personal Jarvis\jarvis\assets\icons\jarvis.ico"
    script = build_create_script(Path(r"C:\startup\Personal Jarvis.lnk"), _spec(), icon=ico)
    assert f"$sc.IconLocation = '{ico},0'" in script


def test_create_script_omits_icon_line_without_icon() -> None:
    """Back-compat: no icon given → no IconLocation line (never an empty ',0')."""
    script = build_create_script(Path(r"C:\startup\Personal Jarvis.lnk"), _spec())
    assert "IconLocation" not in script


# --------------------------------------------------------------------------- #
# Apostrophes in paths (a login like O'Brien, a folder like "Ruben's Jarvis")  #
# --------------------------------------------------------------------------- #

_APOSTROPHE_SPEC = LaunchSpec(
    program=r"C:\Users\O'Brien\.venv\Scripts\pythonw.exe",
    args=("-m", "jarvis.ui.web.launcher"),
    working_dir=r"C:\Users\O'Brien\Ruben's Jarvis",
)


def _quotes_balanced(script: str) -> bool:
    """Every line closes each single-quoted literal it opens."""
    return all(line.count("'") % 2 == 0 for line in script.splitlines())


def test_create_script_escapes_apostrophes_in_paths() -> None:
    # An unescaped apostrophe terminates the single-quoted PowerShell literal
    # early, so the whole script fails to parse and autostart silently never
    # installs for that user.
    script = build_create_script(
        Path(r"C:\Users\O'Brien\Startup\Personal Jarvis.lnk"),
        _APOSTROPHE_SPEC,
        icon=r"C:\Users\O'Brien\jarvis.ico",
    )
    assert r"$sc.TargetPath = 'C:\Users\O''Brien\.venv\Scripts\pythonw.exe'" in script
    assert r"Ruben''s Jarvis" in script
    assert r"$sc.IconLocation = 'C:\Users\O''Brien\jarvis.ico,0'" in script
    assert _quotes_balanced(script)


def test_read_script_escapes_apostrophes_in_the_link_path() -> None:
    script = build_read_script(Path(r"C:\Users\O'Brien\Startup\Personal Jarvis.lnk"))
    assert r"O''Brien" in script
    assert _quotes_balanced(script)

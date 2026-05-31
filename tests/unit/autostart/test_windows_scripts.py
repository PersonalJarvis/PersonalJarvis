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

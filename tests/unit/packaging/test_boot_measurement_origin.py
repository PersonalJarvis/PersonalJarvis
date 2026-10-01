"""A boot measurement must load the candidate, not another editable checkout."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

ROOT = Path(__file__).resolve().parents[3]


def test_bench_environment_overrides_foreign_checkout(monkeypatch, tmp_path):
    foreign = tmp_path / "foreign"
    package = foreign / "jarvis"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("FOREIGN = True\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(foreign))
    bench = runpy.run_path(str(ROOT / "scripts" / "measure_boot.py"))
    result = subprocess.run(
        [sys.executable, "-c", "import jarvis; print(jarvis.__file__)"],
        cwd=ROOT / "scripts",
        env=bench["_bench_env"](47899),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=True,
    )
    assert Path(result.stdout.strip()).resolve() == ROOT / "jarvis" / "__init__.py"


def test_standalone_driver_pins_its_own_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    env = dict(os.environ, BOOT_ORIGIN_DRIVER=str(ROOT / "scripts" / "_desktop_boot_driver.py"))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,runpy; runpy.run_path(os.environ['BOOT_ORIGIN_DRIVER']); "
            "import jarvis; print(jarvis.__file__)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=True,
    )
    assert Path(result.stdout.strip()).resolve() == ROOT / "jarvis" / "__init__.py"

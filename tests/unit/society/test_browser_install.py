"""Managed installation failures must preserve the previously verified runtime."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jarvis.society.browser import install


@pytest.fixture
def installer(monkeypatch, tmp_path):
    root = install.install_root(tmp_path)
    launches = []
    failure = {"stage": ""}

    def run(cmd, **kwargs):
        if "venv" in cmd:
            runtime = Path(cmd[-1])
            executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test runtime")
            launches.append(runtime)
        if failure["stage"] and failure["stage"] in cmd:
            raise RuntimeError("Interrupted test download")
        if "--probe" in cmd:
            binary = root / "browsers" / "chrome"
            binary.parent.mkdir(exist_ok=True)
            binary.write_bytes(b"test browser")
            return json.dumps({"kind": "probe", "ok": True, "executable": str(binary),
                               "version": "test", "packages": [{"name": "test", "license": "MIT"}]})
        return ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "managed_python_request", lambda *_: "3.12")
    monkeypatch.setattr(install.sys, "frozen", False, raising=False)
    yield tmp_path, root, launches, failure
    install._reset_for_tests()


@pytest.mark.parametrize("stage", ["ensurepip", "pip", "playwright", "--probe"])
def test_failed_repair_keeps_the_verified_environment(installer, stage):
    data, root, launches, failure = installer
    install.ensure_installed(data)
    original = (root / "installed.json").read_bytes()
    previous_python = install.venv_python(data)
    failure["stage"] = stage
    with pytest.raises(RuntimeError, match="Interrupted"):
        install.ensure_installed(data, repair=True)
    assert (root / "installed.json").read_bytes() == original
    assert install.venv_python(data) == previous_python
    assert install.is_installed(data)
    assert install.snapshot(data)["retry_at"] > 0
    failure["stage"] = ""
    install.ensure_installed(data, repair=True)
    assert install.venv_python(data) != previous_python
    assert previous_python.is_file()
    assert install.is_installed(data)


def test_simultaneous_installers_promote_only_one_runtime(installer):
    data, root, launches, _ = installer
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(install.ensure_installed, [data, data]))
    assert all(row["installed"] for row in outcomes)
    assert len(launches) == 1


def test_missing_binary_and_corrupt_marker_trigger_repair(installer):
    data, root, launches, _ = installer
    install.ensure_installed(data)
    install.browser_executable(data).unlink()
    assert not install.is_installed(data)
    install.ensure_installed(data)
    assert install.is_installed(data)
    (root / "installed.json").write_text("{partial", encoding="utf-8")
    assert not install.is_installed(data)
    install.ensure_installed(data)
    assert install.is_installed(data)
    assert len(launches) == 3

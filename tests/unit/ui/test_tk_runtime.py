"""Relocated launchers must find their own Tcl/Tk script libraries."""

import os
import sys
from types import SimpleNamespace

import pytest

from jarvis.ui import tk_runtime


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setitem(sys.modules, "_tkinter", SimpleNamespace(
        __file__=str(tmp_path / "loaded" / "DLLs" / "_tkinter.pyd"),
        TCL_VERSION="8.6", TK_VERSION="8.6",
    ))
    # Restore both process-local variables after every case, including ones
    # where production code fills an initially missing value.
    monkeypatch.setenv("TCL_LIBRARY", "")
    monkeypatch.setenv("TK_LIBRARY", "")
    return tmp_path


def libraries(root, folder="tcl", version="8.6"):
    tcl = root / folder / f"tcl{version}"
    tk = root / folder / f"tk{version}"
    tcl.mkdir(parents=True)
    tk.mkdir(parents=True)
    (tcl / "init.tcl").write_text("# Tcl test data", encoding="utf-8")
    (tk / "tk.tcl").write_text("# Tk test data", encoding="utf-8")
    return str(tcl), str(tk)


def test_relocated_launcher_uses_loaded_extension_installation(runtime):
    expected = libraries(runtime / "loaded")
    libraries(runtime / "base")
    tk_runtime.prepare_tk_runtime()
    assert (os.environ["TCL_LIBRARY"], os.environ["TK_LIBRARY"]) == expected


@pytest.mark.parametrize("folder", ["tcl", "lib"])
def test_base_installation_is_a_valid_fallback(runtime, folder):
    expected = libraries(runtime / "base", folder=folder)
    tk_runtime.prepare_tk_runtime()
    assert (os.environ["TCL_LIBRARY"], os.environ["TK_LIBRARY"]) == expected


def test_explicit_environment_overrides_are_preserved(runtime, monkeypatch):
    libraries(runtime / "loaded")
    monkeypatch.setenv("TCL_LIBRARY", "custom-tcl")
    monkeypatch.setenv("TK_LIBRARY", "custom-tk")
    tk_runtime.prepare_tk_runtime()
    assert os.environ["TCL_LIBRARY"] == "custom-tcl"
    assert os.environ["TK_LIBRARY"] == "custom-tk"


def test_incomplete_or_wrong_version_data_is_not_selected(runtime):
    libraries(runtime / "loaded", version="9.0")
    (runtime / "base" / "tcl" / "tcl8.6").mkdir(parents=True)
    tk_runtime.prepare_tk_runtime()
    assert os.environ["TCL_LIBRARY"] == ""
    assert os.environ["TK_LIBRARY"] == ""


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_other_platforms_need_no_tk_import_or_environment_change(runtime, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(sys.modules, "_tkinter", None)
    tk_runtime.prepare_tk_runtime()
    assert os.environ["TCL_LIBRARY"] == ""
    assert os.environ["TK_LIBRARY"] == ""

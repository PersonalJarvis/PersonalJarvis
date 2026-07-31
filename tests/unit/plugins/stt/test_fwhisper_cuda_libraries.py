"""The GPU device must not be lost to a CUDA library sitting off the search path.

A pip-installed torch keeps ``cublas64_12.dll`` inside its own package
directory, which is not on Windows' DLL search path. ctranslate2 then fails with
``Library cublas64_12.dll is not found or cannot be loaded`` on a machine whose
CUDA stack demonstrably works, and the provider's ``cuda -> cpu`` self-heal
quietly takes the local recogniser from ~7.9x realtime down to ~1.1x — an 8 s
dictation segment going from 1.05 s to a full 8 s.

These tests pin the CONTRACT, not the platform: the helper must be a silent
no-op wherever it cannot help, must never raise, and must not import torch.
"""

from __future__ import annotations

import sys

import pytest

from jarvis.plugins.stt import fwhisper


@pytest.fixture(autouse=True)
def _reset_module_flag(monkeypatch):
    """Each test starts with the helper un-run."""
    monkeypatch.setattr(fwhisper, "_cuda_dll_path_prepared", False)
    monkeypatch.setattr(fwhisper, "_cuda_dll_directory_handles", [])
    monkeypatch.setattr(fwhisper, "_cuda_dll_library_handles", [])


class TestItNeverCostsAnything:
    def test_it_is_a_no_op_where_there_is_no_dll_search_path(self, monkeypatch):
        """POSIX has no ``os.add_dll_directory`` — and needs none."""
        monkeypatch.setattr(fwhisper.os, "name", "posix")
        monkeypatch.delattr(fwhisper.os, "add_dll_directory", raising=False)

        fwhisper.ensure_cuda_libraries_findable()

        assert fwhisper._cuda_dll_path_prepared is True, (
            "the no-op still counts as done, so it is not retried per model build"
        )

    def test_a_missing_package_is_an_ordinary_state(self, monkeypatch):
        """A base install has neither torch nor the nvidia wheels."""
        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", lambda _p: None, raising=False
        )
        monkeypatch.setattr(
            "importlib.util.find_spec", lambda _name: None
        )

        fwhisper.ensure_cuda_libraries_findable()  # must not raise

        assert fwhisper._cuda_dll_path_prepared is False, (
            "nothing was loaded, so a later build must be free to try again"
        )

    def test_a_stubbed_module_does_not_end_the_search(self, monkeypatch):
        """``inference_only_import_shield`` stubs torch as None in sys.modules.

        ``find_spec`` raises ValueError for that, which must be survivable —
        otherwise the one helper that repairs the GPU path is defeated by the
        one shield that runs right next to it.
        """
        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", lambda _p: None, raising=False
        )

        def _raise(name: str):
            raise ValueError(f"{name}.__spec__ is None")

        monkeypatch.setattr("importlib.util.find_spec", _raise)

        fwhisper.ensure_cuda_libraries_findable()  # must not raise

        assert fwhisper._cuda_dll_path_prepared is False

    def test_it_does_not_import_torch(self, monkeypatch):
        """Importing torch here would cost seconds and fight the import shield."""
        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", lambda _p: None, raising=False
        )
        monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
        had_torch = "torch" in sys.modules

        fwhisper.ensure_cuda_libraries_findable()

        if not had_torch:
            assert "torch" not in sys.modules


class TestItOnlyClaimsSuccessWhenItLoadedTheOne:
    def test_a_directory_without_cublas_is_skipped(self, tmp_path, monkeypatch):
        """cuDNN alone does not unblock ctranslate2 — cublas is the blocker."""
        package = tmp_path / "torch"
        (package / "lib").mkdir(parents=True)
        (package / "lib" / "cudnn64_9.dll").write_bytes(b"")
        (package / "__init__.py").write_text("")

        added: list[str] = []
        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", added.append, raising=False
        )
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: (
                type("S", (), {"origin": str(package / "__init__.py")})()
                if name == "torch"
                else None
            ),
        )

        fwhisper.ensure_cuda_libraries_findable()

        assert fwhisper._cuda_dll_path_prepared is False
        assert added == [], "a directory with no cublas is not worth adding"

    def test_the_search_path_entry_accompanies_the_load(self, tmp_path, monkeypatch):
        """Both halves are load-bearing, and measurement is why.

        ``add_dll_directory`` ALONE did not fix the failure — it reproduced with
        the directory added. Loading by name did. The directory entry is what
        then lets the loaded libraries resolve their own dependencies, so a
        version that keeps only one of the two is a regression either way.
        """
        package = tmp_path / "torch"
        lib = package / "lib"
        lib.mkdir(parents=True)
        for name in ("cublasLt64_12.dll", "cublas64_12.dll"):
            (lib / name).write_bytes(b"")
        (package / "__init__.py").write_text("")

        added: list[str] = []
        loaded: list[str] = []
        directory_token = object()

        def _add(path: str):
            added.append(path)
            return directory_token

        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", _add, raising=False
        )
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: (
                type("S", (), {"origin": str(package / "__init__.py")})()
                if name == "torch"
                else None
            ),
        )
        # ctypes.WinDLL does not exist off Windows, so it is injected rather
        # than patched — the test states the contract on every platform.
        fake_ctypes = type(
            "C", (), {"WinDLL": staticmethod(lambda path: loaded.append(path))}
        )
        monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

        fwhisper.ensure_cuda_libraries_findable()

        assert added == [str(lib)], "the directory must be on the search path"
        assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in loaded] == [
            "cublasLt64_12.dll",
            "cublas64_12.dll",
        ], "cublasLt must load before the cublas that depends on it"
        assert fwhisper._cuda_dll_path_prepared is True
        assert fwhisper._cuda_dll_directory_handles == [directory_token]
        assert len(fwhisper._cuda_dll_library_handles) == 2

    def test_namespace_nvidia_package_is_searched_by_full_submodule(
        self, tmp_path, monkeypatch
    ):
        """PEP-420 ``nvidia`` has no origin; its cublas child owns ``bin``."""
        package = tmp_path / "nvidia" / "cublas"
        lib = package / "bin"
        lib.mkdir(parents=True)
        (lib / "cublas64_12.dll").write_bytes(b"")

        searched: list[str] = []

        def _find_spec(name: str):
            searched.append(name)
            if name == "nvidia.cublas":
                return type(
                    "S",
                    (),
                    {
                        "origin": None,
                        "submodule_search_locations": [str(package)],
                    },
                )()
            return None

        monkeypatch.setattr("importlib.util.find_spec", _find_spec)
        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", lambda _path: object(), raising=False
        )
        monkeypatch.setitem(
            sys.modules,
            "ctypes",
            type("C", (), {"WinDLL": staticmethod(lambda _path: object())}),
        )

        fwhisper.ensure_cuda_libraries_findable()

        assert "nvidia.cublas" in searched
        assert fwhisper._cuda_dll_path_prepared is True

    def test_a_library_that_will_not_load_is_survived(self, tmp_path, monkeypatch):
        """A present-but-broken DLL falls through to the cuda->cpu self-heal."""
        package = tmp_path / "torch"
        lib = package / "lib"
        lib.mkdir(parents=True)
        (lib / "cublas64_12.dll").write_bytes(b"")
        (package / "__init__.py").write_text("")

        monkeypatch.setattr(
            fwhisper.os, "add_dll_directory", lambda _p: None, raising=False
        )
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: (
                type("S", (), {"origin": str(package / "__init__.py")})()
                if name == "torch"
                else None
            ),
        )

        def _boom(_path):
            raise OSError("not a valid Win32 application")

        monkeypatch.setitem(
            sys.modules, "ctypes", type("C", (), {"WinDLL": staticmethod(_boom)})
        )

        fwhisper.ensure_cuda_libraries_findable()  # must not raise

        assert fwhisper._cuda_dll_path_prepared is False


def test_the_cpu_device_never_pays_for_this(monkeypatch):
    """A CPU-only host must not walk site-packages on every model build."""
    calls: list[int] = []
    monkeypatch.setattr(
        fwhisper, "ensure_cuda_libraries_findable", lambda: calls.append(1)
    )
    monkeypatch.setattr(
        fwhisper, "inference_only_import_shield", lambda: __import__(
            "contextlib"
        ).nullcontext()
    )
    fake = type("M", (), {})
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        type("FW", (), {"WhisperModel": staticmethod(lambda *a, **k: fake)}),
    )

    fwhisper._new_whisper_model("tiny", "cpu", "int8")

    assert calls == [], "the CUDA repair is for GPU devices only"

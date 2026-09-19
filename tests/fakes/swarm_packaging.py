"""Platform-independent PyInstaller input recorder for the frozen contract."""

import json
import os
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import PurePosixPath


class BundleHooks:
    def __init__(self, missing: str | None = None, native: str = "wasmtime.dll") -> None:
        self.missing = missing
        self.native = native
        self.metadata: list[str] = []
        self.modules: list[str] = []
        self.data: list[str] = []

    def copy_metadata(self, name: str) -> list[tuple[str, str]]:
        if name == self.missing:
            raise PackageNotFoundError(name)
        self.metadata.append(name)
        return [(f"{name}.dist-info/METADATA", f"{name}.dist-info")]

    def collect_submodules(self, name: str, *, on_error: str) -> list[str]:
        assert on_error == "raise"
        self.modules.append(name)
        return [name]

    def collect_data_files(self, name: str) -> list[tuple[str, str]]:
        self.data.append(name)
        return [(f"{name}/data", name)]

    def collect_dynamic_libs(
        self,
        name: str,
        *,
        search_patterns: list[str] | tuple[str, ...] = ("*.dll", "*.dylib", "lib*.so"),
    ) -> list[tuple[str, str]]:
        source = PurePosixPath(name) / self.native
        if any(source.match(pattern) for pattern in search_patterns):
            return [(str(source), str(source.parent))]
        return []


class BinaryWheel:
    def __init__(self, *files: str, version: str = "9999") -> None:
        self.files = [PurePosixPath(file) for file in files]
        self.version = version

    def locate_file(self, file: PurePosixPath) -> str:
        return str(PurePosixPath("wheel") / file)


def descendant_probe(pid_path, *, parent_exits: bool) -> list[str]:
    """A harmless child survives its launcher unless the caller owns the tree."""
    child = (
        "import os, sys, time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); time.sleep(60)"
    )
    parent = f"""
import subprocess, sys, time
from pathlib import Path
pid_path = Path({json.dumps(str(pid_path))})
subprocess.Popen(
    [sys.executable, '-c', {child!r}, str(pid_path)],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    encoding='utf-8', creationflags={0x08000000 if os.name == "nt" else 0},
)
deadline = time.monotonic() + 10
while not pid_path.is_file():
    if time.monotonic() >= deadline:
        raise RuntimeError('Probe child did not start')
    time.sleep(0.01)
{"pass" if parent_exits else "time.sleep(60)"}
"""
    return [sys.executable, "-c", parent]


class ObservedJob:
    """Record whether a real suspended target executed before assignment."""

    def __init__(self, actual, marker, *, refuse=False):
        self.actual, self.marker, self.refuse = actual, marker, refuse
        self.child = None
        self.saw_suspended_target = False

    def assign_and_resume(self, child):
        self.child = child
        self.saw_suspended_target = not self.marker.exists()
        if self.refuse:
            raise OSError("Synthetic job assignment failure")
        self.actual.assign_and_resume(child)

    def terminate_and_wait(self, deadline):
        self.actual.terminate_and_wait(deadline)

    def close(self):
        self.actual.close()

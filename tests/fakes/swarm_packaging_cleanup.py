"""Native smoke protocol fakes and a real Windows image-lock probe."""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path


class CompletedSmoke:
    """Two healthy API rounds, without invoking a machine-wide installer."""

    team = {"id": "team", "lead_id": "lead", "goal": "retained", "limits": {}, "policy": {}}

    def __init__(self):
        self.installations = 0

    def install(self, installer, root, env):
        self.installations += 1
        return root / "jarvis.exe"

    @contextmanager
    def running_app(self, executable, root, env, log_path, *, live=False):
        assert not live, "Cleanup-only verification must not start provider requests"
        yield self

    def wait_ready(self, api, child):
        assert api is self and child is self

    def request(self, path, body=None):
        if path == "/api/swarm/capabilities":
            return {"local": True, "sandbox": {"available": True, "kind": "wasmtime-quickjs"}}
        if path == "/api/swarm/teams" and body is None:
            return [self.team]
        assert path in {"/api/swarm/teams", "/api/swarm/teams/team"}
        return self.team


@contextmanager
def mapped_extension(root: Path):
    """Keep a copied Python extension image mapped until explicitly released."""
    if os.name != "nt":
        yield None
        return

    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    origin = importlib.util.find_spec("_socket").origin
    target = root / "mask.pyd"
    shutil.copy2(origin, target)
    module = ctypes.WinDLL(str(target))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel.FreeLibrary.restype = wintypes.BOOL
    lock = threading.Lock()
    released = False

    def release():
        nonlocal released
        with lock:
            if not released:
                if not kernel.FreeLibrary(module._handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                released = True

    try:
        yield release
    finally:
        release()

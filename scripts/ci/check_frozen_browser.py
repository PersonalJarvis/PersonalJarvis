"""Exercise automatic provisioning and real frames in the shipped frozen app.

Run after packaging, with --executable pointing at the built launcher. Two
headless boots share a fresh scratch profile; no source imports, account keys,
desktop input, or existing user state are passed to the child application.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
from PIL import Image
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.core.config_writer import _WRITE_LOCK, _atomic_write  # noqa: E402
from jarvis.core.instance import DEV_PORT_OFFSET  # noqa: E402
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402


def isolated_env(profile: Path, port: int, key: str) -> dict[str, str]:
    """Keep OS launch essentials, excluding inherited account/config secrets."""
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    for name in (
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    ):
        env[name] = str(profile)
    config = profile / "jarvis.toml"
    with _WRITE_LOCK:
        _atomic_write(config, f"[ui]\nadmin_api_port = {port - DEV_PORT_OFFSET}\n")
    env.update(
        JARVIS_INSTANCE="dev",
        JARVIS_CONFIG=str(config),
        JARVIS_DATA_DIR=str(profile / "data"),
        JARVIS_CONTROL_API_KEY=key,
        PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
        PYTHONIOENCODING="utf-8",
    )
    return env


def stop_owned_tree(process: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        return  # The application may have exited on its own.
    owned = [*reversed(parent.children(recursive=True)), parent]
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass  # Browser containment can close descendants concurrently.
    _, alive = psutil.wait_procs(owned, timeout=8)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass  # A pending graceful shutdown may win the race.
    _, alive = psutil.wait_procs(alive, timeout=5)
    if alive:
        raise RuntimeError("Frozen app left owned processes running")
    process.wait(timeout=5)


class ProbeHTTPError(RuntimeError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"Frozen probe request failed: HTTP {code}")


def request_json(port: int, path: str, key: str) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Authorization": f"Bearer {key}"})
        response = connection.getresponse()
        if response.status != 200:
            raise ProbeHTTPError(response.status)
        return json.loads(response.read())
    finally:
        connection.close()


def capture_frame(port: int, key: str, output: Path) -> dict:
    started = time.monotonic()
    with connect(
        f"ws://127.0.0.1:{port}/api/society/agents/jarvis/browser/live",
        additional_headers={"Authorization": f"Bearer {key}"},
        open_timeout=10,
    ) as viewer:
        while time.monotonic() - started < 60:
            event = json.loads(viewer.recv(timeout=15))
            if event.get("kind") in {"error", "disconnected"}:
                raise RuntimeError("Frozen browser stream failed before its first frame")
            if event.get("kind") != "frame":
                continue
            content = base64.b64decode(event["data"], validate=True)
            with Image.open(io.BytesIO(content)) as frame:
                frame.load()
                width, height = frame.size
                if width < 320 or height < 200:
                    raise RuntimeError("Frozen browser frame is too small")
            output.write_bytes(content)
            return {
                "first_frame_seconds": round(time.monotonic() - started, 3),
                "width": width,
                "height": height,
                "full_window": bool(event.get("full_window")),
            }
    raise TimeoutError("Frozen browser did not produce a frame within 60 seconds")


def boot(executable: Path, profile: Path, output: Path, key: str, timeout: float) -> dict:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = isolated_env(profile, port, key)
    started = time.monotonic()
    report: dict = {}
    with output.with_suffix(".log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(executable), "serve"],
            cwd=profile,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        try:
            last_phase = None
            while time.monotonic() - started < timeout:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Frozen app exited before browser readiness: {process.returncode}"
                    )
                try:
                    health = request_json(port, "/api/health", key)
                    if health.get("ok") and "healthy_seconds" not in report:
                        if health.get("instance") != "dev":
                            raise RuntimeError("Frozen probe reached the wrong app instance")
                        report["healthy_seconds"] = round(time.monotonic() - started, 3)
                    status = request_json(port, "/api/society/browser/status", key)
                except ProbeHTTPError as exc:
                    if exc.code not in {503, 404}:
                        raise RuntimeError(
                            f"Frozen probe request failed: HTTP {exc.code}"
                        ) from None
                    time.sleep(1)
                    continue
                except (OSError, http.client.HTTPException):
                    time.sleep(1)
                    continue
                phase = status.get("phase")
                if phase != last_phase:
                    print(f"{output.name}: browser phase={phase}", flush=True)
                    last_phase = phase
                if status.get("error"):
                    raise RuntimeError(
                        "Frozen app automatic browser provisioning failed; inspect its log"
                    )
                if status.get("installed") and "healthy_seconds" in report:
                    report["browser_ready_seconds"] = round(time.monotonic() - started, 3)
                    report.update(capture_frame(port, key, output.with_suffix(".jpg")))
                    return report
                time.sleep(1)
            raise TimeoutError(
                "Frozen app did not automatically prepare its browser before the deadline"
            )
        finally:
            stop_owned_tree(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # A fresh path is mandatory: accidentally reusing a prior runtime would
    # turn a failed provisioning path into a passing warm-start test.
    profile = output / "profile"
    profile.mkdir(exist_ok=False)
    key = "jctl_" + secrets.token_urlsafe(32)
    report = {"platform": sys.platform, "passed": False, "boots": []}
    try:
        for name in ("first-install", "restart"):
            row = boot(executable, profile, output / name, key, args.timeout)
            report["boots"].append({"name": name, **row})
        report["passed"] = True
        return 0
    except Exception as exc:
        report["failure"] = type(exc).__name__
        print(f"Frozen browser smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

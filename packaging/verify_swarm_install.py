"""Exercise a native installer and its frozen Swarm API in a disposable CI profile.

This is a packaging check, not a production test mode. It starts the shipped
``jarvis serve`` entry point and uses only normal authenticated HTTP routes.
The same installer is applied twice: this proves native replacement and durable
state preservation, not an upgrade from a previous public release. No model
request is made, so a passing result does not claim provider qualification.

Run only on a disposable GitHub Actions runner: Inno Setup registers the
installation in that runner's account even with an explicit temporary target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import runpy
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from jarvis.core.config_writer import _atomic_write
from jarvis.core.installer_update import apply_installer

_process_support = runpy.run_path(str(Path(__file__).with_name("_native_process.py")))
contained_process = _process_support["contained_process"]
ContainmentError = _process_support["ContainmentError"]


def isolated_environment(root: Path, port: int, control_key: str) -> dict[str, str]:
    """Pass OS essentials, never inherited provider credentials or Python paths."""
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    for key, directory in {
        "HOME": "home",
        "USERPROFILE": "home",
        "APPDATA": "home/roaming",
        "LOCALAPPDATA": "home/local",
        "XDG_CONFIG_HOME": "home/config",
        "XDG_DATA_HOME": "home/data",
        "XDG_CACHE_HOME": "home/cache",
        "TMP": "tmp",
        "TEMP": "tmp",
        "TMPDIR": "tmp",
    }.items():
        path = root / directory
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(
        {
            "JARVIS_CONFIG": str(root / "jarvis.toml"),
            "JARVIS_DATA_DIR": str(root / "data"),
            "JARVIS_VOICE": "0",
            "JARVIS_CONTROL_API_KEY": control_key,
            "JARVIS__UI__ADMIN_API_PORT": str(port),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "APPIMAGE_EXTRACT_AND_RUN": "1",
            "JARVIS_APPIMAGE_NO_BROWSER": "1",
        }
    )
    return env


class NativeRunner:
    """Execute the updater's commands, with an owned Inno destination override."""

    def __init__(self, target: Path, env: dict[str, str]) -> None:
        self.target, self.env = target, env

    def run(self, argv, *, timeout_s):
        with contained_process(
            argv,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as child:
            stdout, stderr = child.communicate(timeout=timeout_s)
            return child.returncode, stdout, stderr

    def spawn_detached(self, argv) -> None:
        # apply_installer's Windows handover is normally detached. The CI edge
        # waits for completion and suppresses launch, shortcuts and PATH edits.
        if sys.platform != "win32":
            raise RuntimeError("The native smoke must never relaunch an installer")
        command = [arg for arg in argv if arg not in {"/RESTARTAPPLICATIONS", "/SILENT"}]
        command += [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTARTAPPLICATIONS",
            "/NOICONS",
            "/TASKS=",
            f"/DIR={self.target}",
        ]
        rc, _out, err = self.run(command, timeout_s=600)
        if rc:
            raise RuntimeError(f"Native installer failed ({rc}): {err[:300]}")


def install(installer: Path, root: Path, env: dict[str, str]) -> Path:
    """Install or replace the same temporary destination using the real updater."""
    if sys.platform == "win32":
        target = root / "application"
        apply_installer(installer, runner=NativeRunner(target, env), relaunch=False)
        executable = target / "jarvis.exe"
    elif sys.platform == "darwin":
        target = root / "Personal Jarvis.app"
        apply_installer(
            installer,
            app_path=target,
            runner=NativeRunner(target, env),
            relaunch=False,
        )
        executable = target / "Contents" / "MacOS" / "jarvis"
    elif sys.platform.startswith("linux"):
        executable = root / "PersonalJarvis.AppImage"
        apply_installer(installer, appimage_path=executable, relaunch=False)
    else:
        raise RuntimeError("No native installer is supported on this platform")
    if not executable.is_file():
        raise RuntimeError("The installed console entry point is missing")
    return executable


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("The isolated Swarm API must not redirect its control key")


class SwarmApi:
    def __init__(self, port: int, key: str) -> None:
        self.base, self.key = f"http://127.0.0.1:{port}", key
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(self, path: str, body: dict | None = None) -> Any:
        request = Request(  # noqa: S310 - fixed loopback base; redirects are rejected
            self.base + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=10) as response:
            payload = response.read(1_048_577)
        if len(payload) > 1_048_576:
            raise RuntimeError("The native smoke response exceeded its bound")
        return json.loads(payload)


def verify_capabilities(result: dict) -> None:
    if result.get("local") is not True or result.get("sandbox", {}).get("available") is not True:
        raise RuntimeError("The installed native WASM execution probe failed")
    if result.get("sandbox", {}).get("kind") != "wasmtime-quickjs":
        raise RuntimeError("The native API did not use the production execution sandbox")


def verify_identity(before: dict, after: dict) -> None:
    for field in ("id", "lead_id", "goal", "name", "limits", "policy"):
        if before.get(field) != after.get(field):
            raise RuntimeError(f"Installer replacement changed durable team field {field}")


@contextmanager
def running_app(executable: Path, root: Path, env: dict[str, str], log_path: Path):
    """Contain only this smoke's child tree, including the AppImage runtime."""
    with log_path.open("w", encoding="utf-8") as output:
        with contained_process(
            [str(executable), "serve"],
            cwd=root,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        ) as child:
            yield child


def wait_ready(api: SwarmApi, child: subprocess.Popen, *, timeout_s: float = 180) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(
                f"The installed application exited before readiness ({child.returncode})"
            )
        try:
            # The bootstrap health endpoint can answer before the full app is
            # ready. This ordinary authenticated route waits for real wiring.
            api.request("/api/swarm/teams")
            return
        except (URLError, TimeoutError):
            time.sleep(0.25)  # Startup is allowed a bounded unavailable interval.
    raise RuntimeError("The installed application did not become ready")


@contextmanager
def smoke_workspace():
    """Remove our temporary files only after every process owner confirms shutdown."""
    root = Path(tempfile.mkdtemp(prefix="jarvis-native-smoke-")).resolve(strict=True)
    removable = True
    try:
        yield root
    except ContainmentError as exc:
        removable = False
        raise RuntimeError(f"Native process cleanup failed; workspace retained at {root}") from exc
    finally:
        if removable:
            shutil.rmtree(root)


def run(installer: Path, report: Path) -> dict:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError(
            "Native installer verification requires a disposable GitHub Actions runner"
        )
    installer = installer.resolve(strict=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with smoke_workspace() as root:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        key = secrets.token_urlsafe(32)
        env = isolated_environment(root, port, key)
        # The normal config writer owns even this disposable configuration.
        _atomic_write(root / "jarvis.toml", f"[ui]\nadmin_api_port = {port}\n")
        api = SwarmApi(port, key)
        original = None
        rounds = []
        for index in range(2):
            executable = install(installer, root, env)
            log_path = report.with_name(f"native-smoke-{index + 1}.log")
            with running_app(executable, root, env, log_path) as child:
                wait_ready(api, child)
                verify_capabilities(api.request("/api/swarm/capabilities"))
                if original is None:
                    original = api.request(
                        "/api/swarm/teams",
                        {
                            "name": "Native installation persistence probe",
                            "goal": "Preserve this unstarted team across installer replacement.",
                            "request_key": "native-installation-probe",
                            "policy": {"internet": False, "allow_dependencies": False},
                        },
                    )
                    if not original.get("id") or not original.get("lead_id"):
                        raise RuntimeError(
                            "The installed API did not create a persistent team and lead"
                        )
                current = api.request(f"/api/swarm/teams/{original['id']}")
                verify_identity(original, current)
                teams = api.request("/api/swarm/teams")
                if len(teams) != 1 or teams[0]["id"] != original["id"]:
                    raise RuntimeError("Installer replacement duplicated or lost the isolated team")
                rounds.append({"native_wasm": "pass", "persistent_team_and_lead": "pass"})
        with installer.open("rb") as payload:
            digest = hashlib.file_digest(payload, "sha256").hexdigest()
        result = {
            "platform": sys.platform,
            "machine": platform.machine(),
            "installer": installer.name,
            "sha256": digest,
            "fresh_install": rounds[0],
            "same_artifact_replacement": rounds[1],
            "profile": "disposable",
            "provider_requests": 0,
            "previous_public_release_upgrade": "not exercised",
        }
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.installer, args.report), indent=2))


if __name__ == "__main__":
    main()

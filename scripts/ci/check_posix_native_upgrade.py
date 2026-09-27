"""Exercise a real previous native install, failed update, and successful update."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import httpx

from jarvis.agent_chat.store import AgentChatStore
from jarvis.core.branding import KEYRING_SERVICE_NAME
from jarvis.core.config import _FileCredStore
from jarvis.core.config_writer import _WRITE_LOCK, _atomic_write
from jarvis.core.http_pool import SyncHttpClientPool
from jarvis.core.installer_update import (
    InstallerUpdateError,
    SubprocessCommandRunner,
    run_native_update_supervisor,
)
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_probe_pool = SyncHttpClientPool(timeout_s=2.0)


class TrackingRunner(SubprocessCommandRunner):
    def __init__(self, evidence_dir: Path, profile: Path, base_env: dict[str, str]) -> None:
        super().__init__()
        self.evidence_dir = evidence_dir
        self.profile = profile
        self.base_env = base_env
        self.spawned: list[int] = []

    def spawn_detached(self, command, *, env=None):
        child_env = self.base_env.copy()
        child_env.update(env or {})
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        log_path = self.evidence_dir / f"child-{len(self.spawned) + 1:02d}.log"
        with log_path.open("wb") as output:
            process = subprocess.Popen(
                list(command),
                cwd=self.profile,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        pid = process.pid
        self._children[pid] = process
        self.spawned.append(pid)
        return pid

    def stop_spawned(self) -> None:
        for pid in reversed(self.spawned):
            try:
                self.terminate_group(pid)
            except (OSError, ValueError, InstallerUpdateError):
                # A child already stopped by the supervisor needs no second stop.
                pass

    def record(self, phase: str) -> None:
        statuses = [
            {
                "pid": pid,
                "returncode": self._children[pid].poll() if pid in self._children else "reaped",
            }
            for pid in self.spawned
        ]
        (self.evidence_dir / f"{phase}-children.json").write_text(
            json.dumps(statuses, indent=2), encoding="utf-8"
        )


def _isolated_env(profile: Path, config: Path, data: Path) -> dict[str, str]:
    """Keep only OS launch requirements and this smoke's controlled profile."""
    allowed = {
        "PATH",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "SYSTEMROOT",
        "WINDIR",
    }
    child_env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    child_env.update(
        HOME=str(profile),
        JARVIS_CONFIG=str(config),
        JARVIS_DATA_DIR=str(data),
        PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
        PYTHONIOENCODING="utf-8",
    )
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        child_env[name] = str(profile / name.lower())
    return child_env


def _run(*args: str) -> None:
    subprocess.run(
        list(args),
        check=True,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _install_previous(platform: str, package: Path, target: Path) -> Path:
    if platform == "linux":
        shutil.copy2(package, target)
        target.chmod(0o755)
        return target
    mount = target.parent / "prior-dmg"
    mount.mkdir()
    _run("hdiutil", "attach", str(package), "-readonly", "-nobrowse", "-mountpoint", str(mount))
    try:
        apps = list(mount.glob("*.app"))
        if len(apps) != 1:
            raise RuntimeError("previous DMG has no unique app bundle")
        shutil.copytree(apps[0], target, symlinks=True)
    finally:
        _run("hdiutil", "detach", str(mount))
    return target / "Contents/MacOS/jarvis"


def _version(executable: Path) -> str:
    result = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return result.stdout.strip().removeprefix("jarvis ")


def _manifest(
    path: Path,
    *,
    platform: str,
    candidate: Path,
    target: Path,
    expected: str,
    previous: str,
    port: int,
    receipt: Path,
) -> None:
    with candidate.open("rb") as asset:
        digest = hashlib.file_digest(asset, "sha256").hexdigest()
    payload = {
        "schema": 1,
        "parent_pid": 2147483647,
        "platform": platform,
        "installer": str(candidate.resolve()),
        "installer_sha256": digest,
        "target": str(target.resolve()),
        "version": expected,
        "previous_version": previous,
        "health_port": port,
        "shutdown_receipt": str(receipt.resolve()),
    }
    if platform == "darwin":
        payload["executable_relative"] = "Contents/MacOS/PersonalJarvis"
    path.write_text(json.dumps(payload), encoding="utf-8")


def check(
    *,
    platform: str,
    previous_package: Path,
    candidate: Path,
    previous_tag: str,
    candidate_version: str,
    tag: str,
    commit: str,
    proof: Path,
    evidence_dir: Path,
) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-native-smoke-") as scratch:
        root = Path(scratch)
        os.environ["HOME"] = str(root)
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            directory = root / name.lower()
            directory.mkdir()
            os.environ[name] = str(directory)
        target_name = "PersonalJarvis.AppImage" if platform == "linux" else "Personal Jarvis.app"
        target = root / target_name
        old_cli = _install_previous(platform, previous_package, target)
        previous_version = previous_tag.removeprefix("v")
        if _version(old_cli) != previous_version:
            raise RuntimeError("previous native package has the wrong version")

        port = _free_port()
        config = root / "jarvis.toml"
        data = root / "data"
        data.mkdir()
        os.environ["JARVIS_CONFIG"] = str(config)
        os.environ["JARVIS_DATA_DIR"] = str(data)
        with _WRITE_LOCK:
            _atomic_write(
                config, f"[ui]\nadmin_api_port = {port}\n# Native release smoke setting\n"
            )
        database = data / "agent_chat.db"
        store = AgentChatStore(database)
        store.create_session(
            provider="openai",
            model="release-smoke",
            effort="",
            cwd=str(root),
            title="Persisted native release chat",
            session_id="release-smoke",
            surface="jarvis",
        )
        store.close()
        _FileCredStore().set(KEYRING_SERVICE_NAME, "release_smoke_key", "native-smoke-secret")
        config_bytes = config.read_bytes()
        child_env = _isolated_env(root, config, data)
        seen_previous_health = False
        observations: Counter[str] = Counter()

        def health(health_port: int, version: str, nonce: str) -> bool:
            nonlocal seen_previous_health
            try:
                response = _probe_pool.client().get(f"http://127.0.0.1:{health_port}/api/health")
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                observations[f"probe_error:{code or type(exc).__name__}"] += 1
                return False
            if not isinstance(payload, dict):
                observations["probe_error:invalid_payload"] += 1
                return False
            observations[f"response_version:{payload.get('version', '<missing>')}"] += 1
            if payload.get("update_nonce") == nonce:
                observations["matching_nonce"] += 1
            matching = payload.get("ok") is True and payload.get("version") == version
            if version == previous_version and matching:
                # Releases predating the nonce contract can only be verified by
                # their version on this test's isolated port and owned process.
                seen_previous_health = True
                return True
            return matching and payload.get("update_nonce") == nonce

        receipt = root / "shutdown-receipt"
        receipt.write_text("graceful shutdown\n")
        failed = root / "failed-update.json"
        _manifest(
            failed,
            platform=platform,
            candidate=candidate,
            target=target,
            expected="999.0.0",
            previous=previous_version,
            port=port,
            receipt=receipt,
        )
        runner = TrackingRunner(evidence_dir / "failed-upgrade", root, child_env)
        runner.evidence_dir.mkdir(parents=True, exist_ok=True)
        try:
            if run_native_update_supervisor(
                failed,
                runner=runner,
                alive=lambda _: False,
                health=health,
                health_seconds=20,
            ):
                raise RuntimeError("broken candidate unexpectedly passed health")
            if not seen_previous_health or _version(old_cli) != previous_version:
                raise RuntimeError("failed update did not restore the previous app")
        finally:
            runner.stop_spawned()
            runner.record("failed-upgrade")
            (evidence_dir / "health-observations.json").write_text(
                json.dumps(observations, indent=2), encoding="utf-8"
            )

        receipt.write_text("graceful shutdown\n")
        successful = root / "successful-update.json"
        _manifest(
            successful,
            platform=platform,
            candidate=candidate,
            target=target,
            expected=candidate_version,
            previous=previous_version,
            port=port,
            receipt=receipt,
        )
        runner = TrackingRunner(evidence_dir / "successful-upgrade", root, child_env)
        runner.evidence_dir.mkdir(parents=True, exist_ok=True)
        try:
            if not run_native_update_supervisor(
                successful,
                runner=runner,
                alive=lambda _: False,
                health=health,
                health_seconds=90,
            ):
                raise RuntimeError("candidate did not become healthy")
            new_cli = target if platform == "linux" else target / "Contents/MacOS/jarvis"
            if _version(new_cli) != candidate_version:
                raise RuntimeError("successful update did not install candidate")
        finally:
            runner.stop_spawned()
            runner.record("successful-upgrade")

        if config.read_bytes() != config_bytes:
            raise RuntimeError("native update changed persisted settings")
        store = AgentChatStore(database)
        try:
            session = store.get_session("release-smoke")
        finally:
            store.close()
        if session is None or session.title != "Persisted native release chat":
            raise RuntimeError("native update lost persisted agent chat")
        if _FileCredStore().get(KEYRING_SERVICE_NAME, "release_smoke_key") != "native-smoke-secret":
            raise RuntimeError("native update lost persisted credential fallback")

    with candidate.open("rb") as asset:
        digest = hashlib.file_digest(asset, "sha256").hexdigest()
    proof.write_text(
        json.dumps(
            {
                "target": "linux-x86_64"
                if platform == "linux"
                else ("macos-arm64" if "arm64" in candidate.name else "macos-x64"),
                "tag": tag,
                "commit": commit,
                "asset_sha256": digest,
                "installed": True,
                "upgraded": True,
                "rollback_tested": True,
                "previous_tag": previous_tag,
                "settings_preserved": True,
                "database_preserved": True,
                "credential_preserved": True,
                "notarized": platform == "darwin" and tag.startswith("v"),
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("darwin", "linux"), required=True)
    parser.add_argument("--previous-package", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--previous-tag", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(**vars(args))
    finally:
        _probe_pool.close()


if __name__ == "__main__":
    main()

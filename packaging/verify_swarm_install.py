"""Exercise a native installer and its frozen Swarm API in a disposable CI profile.

This is a packaging check, not a production test mode. It starts the shipped
``jarvis serve`` entry point and uses only normal authenticated HTTP routes.
The same installer is applied twice: this proves native replacement and durable
state preservation, not an upgrade from a previous public release. Provider
qualification is disabled by default. Its manual-only option consumes one
explicitly supplied temporary CI credential and a bounded arithmetic task.

Run only on a disposable GitHub Actions runner: Inno Setup registers the
installation in that runner's account even with an explicit temporary target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import runpy
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from jarvis.core.config_writer import _atomic_write
from jarvis.core.installer_update import apply_installer

_process_support = runpy.run_path(str(Path(__file__).with_name("_native_process.py")))
contained_process = _process_support["contained_process"]
ContainmentError = _process_support["ContainmentError"]
_CLEANUP_TIMEOUT_S = 10.0
_LIVE_TIMEOUT_S = 180
_LIVE_TOKEN_BUDGET = 60000
_EXPECTED_RESULT = {"count": 4, "sum": 40, "mean": 10}


class LiveVerificationError(RuntimeError):
    """A fixed, credential-free diagnostic suitable for the CI summary."""


def live_environment(base: dict[str, str], provider: str, model: str) -> dict[str, str]:
    """Map one opt-in credential through the production secret resolver's ENV slot."""
    from jarvis.brain.provider_registry import BrainProviderRegistry
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES

    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise LiveVerificationError("Live provider verification requires manual workflow dispatch")
    credential = os.environ.get("SWARM_INSTALL_TEST_KEY", "")
    if not credential.strip():
        raise LiveVerificationError("Live mode requires the SWARM_INSTALL_TEST_KEY Actions secret")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", provider):
        raise LiveVerificationError("Select a supported scoped API provider for live verification")
    slots = PROVIDER_SECRET_CANDIDATES.get(provider, ())
    try:
        provider_class = BrainProviderRegistry().get_class(provider)
    except KeyError:
        raise LiveVerificationError("The selected API provider is not installed") from None
    if (
        not slots
        or getattr(provider_class, "scoped_execution_only", False) is not True
        or getattr(provider_class, "supports_tools", False) is not True
    ):
        raise LiveVerificationError("The selected provider cannot provide scoped API tool calls")
    if len(model) > 200 or any(ord(character) < 32 for character in model):
        raise LiveVerificationError("The live model override must be a bounded model identifier")
    env = dict(base)
    # The base environment has already stripped all inherited credentials. Do
    # not forward the CI secret's own name, write it to config, or pass it to the
    # installer process. The application reads this one normal resolver slot.
    env[slots[0][1]] = credential
    env["JARVIS__BRAIN__PRIMARY"] = provider
    env["JARVIS__BRAIN__WORKER__PROVIDER"] = provider
    if model:
        env["JARVIS__BRAIN__WORKER__MODEL"] = model
    return env


def live_task_spec() -> dict[str, Any]:
    """Use the ordinary explicit-plan API, with no planner or external egress."""
    return {
        "name": "Native one-key arithmetic qualification",
        "goal": "Calculate count, sum and mean of [4, 8, 12, 16] and save statistics.json.",
        "request_key": "native-one-key-arithmetic",
        "limits": {
            "token_budget": str(_LIVE_TOKEN_BUDGET),
            "runtime_seconds": _LIVE_TIMEOUT_S,
            "concurrency": 2,
            "worker_limit": "1",
            "max_attempts": 1,
            "max_output_tokens": 1024,
            "max_tool_calls": 6,
        },
        "policy": {
            "internet": False,
            "allow_dependencies": False,
            "tools": ["run_javascript", "write_artifact", "read_artifact"],
            "max_network_bytes": "0",
            "max_artifact_bytes": "262144",
        },
        "tasks": [
            {
                "id": "arithmetic",
                "title": "Compute and save summary statistics",
                "description": (
                    "Use run_javascript to calculate count, sum and mean of [4, 8, 12, 16]. "
                    "Return that object from main(input), then write the same JSON object as "
                    "statistics.json using write_artifact with application/json. Finally reply "
                    "with only that exact JSON object, without Markdown."
                ),
                "acceptance": "The executed and saved object is exactly count=4, sum=40, mean=10.",
                "verification": "javascript",
                "required_tools": ["run_javascript", "write_artifact"],
                "verification_script": (
                    "function main(input) { const correct = x => x && "
                    "Object.keys(x).length === 3 && x.count === 4 && "
                    "x.sum === 40 && x.mean === 10; "
                    "const saved = input.artifacts.some(a => { if(a.name !== 'statistics.json') "
                    "return false; try { return correct(JSON.parse(a.content)); } "
                    "catch { return false; } }); "
                    "return {accepted: correct(input.result) && saved, "
                    "reason: 'Arithmetic and saved JSON check'}; }"
                ),
            }
        ],
    }


def verify_live_artifact(api: SwarmApi, team_id: str) -> dict[str, Any]:
    """Require accepted current-attempt evidence and verify the downloaded bytes."""
    from jarvis.swarm.receipts import is_runtime_receipt

    task = api.request(f"/api/swarm/teams/{team_id}/tasks/record/arithmetic")
    if task.get("state") != "succeeded" or task.get("verification") != "javascript":
        raise LiveVerificationError("The arithmetic task has no deterministic acceptance")
    artifacts = api.request(f"/api/swarm/teams/{team_id}/artifacts?limit=200")
    evidence = set(task.get("evidence", []))
    accepted = [
        item
        for item in artifacts
        if item.get("id") in evidence
        and item.get("task_id") == "arithmetic"
        and item.get("attempt_fence") == task.get("fence")
        and item.get("owner_id") == task.get("owner_id")
    ]
    receipts = {item["provenance"]["receipt_kind"] for item in accepted if is_runtime_receipt(item)}
    if not {"execution", "verification"} <= receipts:
        raise LiveVerificationError("Accepted execution and verification receipts are missing")
    candidates = [item for item in accepted if item.get("name") == "statistics.json"]
    if len(candidates) != 1:
        raise LiveVerificationError("The accepted arithmetic artifact is missing or ambiguous")
    artifact = candidates[0]
    raw = api.download(f"/api/swarm/teams/{team_id}/artifacts/{artifact['id']}", maximum=4096)
    if hashlib.sha256(raw).hexdigest() != artifact.get("sha256"):
        raise LiveVerificationError("The arithmetic artifact hash does not match its metadata")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise LiveVerificationError("The arithmetic artifact is not JSON") from None
    if value != _EXPECTED_RESULT or any(type(value[key]) not in {int, float} for key in value):
        raise LiveVerificationError("The saved arithmetic result is incorrect")
    return {"artifact_id": artifact["id"], "sha256": artifact["sha256"], "result": value}


def run_live_task(api: SwarmApi, provider: str) -> dict[str, Any]:
    capabilities = api.request("/api/swarm/capabilities")
    selected = next((row for row in capabilities.get("providers", []) if row["id"] == provider), {})
    if selected.get("available") is not True or selected.get("credential_present") is not True:
        raise LiveVerificationError(
            "The installed API cannot resolve the selected scoped credential"
        )
    team = api.request("/api/swarm/teams", live_task_spec())
    team_id = team["id"]
    api.request(
        f"/api/swarm/teams/{team_id}/start",
        {
            "expected_version": team["version"],
            "expected_storage_generation": team["storage_generation"],
        },
    )
    deadline = time.monotonic() + _LIVE_TIMEOUT_S + 15
    while time.monotonic() < deadline:
        current = api.request(f"/api/swarm/teams/{team_id}")
        if current["state"] == "succeeded":
            used, reserved = int(current["tokens_used"]), int(current["tokens_reserved"])
            if not 0 < used <= _LIVE_TOKEN_BUDGET or reserved:
                raise LiveVerificationError("The live task lacks settled bounded provider usage")
            return {
                "status": "pass",
                "team_id": team_id,
                "provider": provider,
                "credential_count": 1,
                "tokens_used": str(used),
                "tokens_reserved": "0",
                "provider_requests": "not counted; nonzero usage verified",
                **verify_live_artifact(api, team_id),
            }
        if current["state"] != "running":
            # Never copy provider responses or model output into CI diagnostics.
            raise LiveVerificationError("The live arithmetic task stopped before acceptance")
        time.sleep(0.5)
    api.request(f"/api/swarm/teams/{team_id}/cancel", {})
    raise LiveVerificationError("The bounded live arithmetic task timed out")


class WorkspaceCleanupError(RuntimeError):
    """The native smoke's files must remain available for cleanup diagnosis."""


def remove_workspace(root: Path) -> None:
    """Retry transient Windows file locks only after process containment drained."""
    if not root.is_absolute() or not root.name.startswith("jarvis-native-smoke-"):
        raise WorkspaceCleanupError("Refusing cleanup outside the native smoke workspace")
    deadline = time.monotonic() + _CLEANUP_TIMEOUT_S

    def remove_readonly(function, path, exc_info):
        error = exc_info[1]
        if os.name != "nt" or not isinstance(error, PermissionError):
            raise error
        target = Path(path)
        if function not in {os.unlink, os.rmdir} or not target.resolve().is_relative_to(root):
            raise error
        attributes = target.stat(follow_symlinks=False)
        if not attributes.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY:
            raise error
        # Installer payloads can retain the DOS read-only bit. Change no ACLs,
        # and do not mistake a mapped DLL's access denial for that attribute.
        target.chmod(attributes.st_mode | stat.S_IWRITE)
        function(path)

    while True:
        try:
            if root.resolve(strict=True) != root:
                raise WorkspaceCleanupError("The native smoke cleanup target changed")
            # onerror keeps the supported Python 3.11 installer host compatible.
            shutil.rmtree(root, onerror=remove_readonly)
            return
        except OSError as exc:
            remaining = deadline - time.monotonic()
            # A drained Job proves process ownership ended; Windows image section
            # teardown or a scanner can still briefly deny deletion of a DLL.
            if (
                os.name != "nt"
                or getattr(exc, "winerror", None) not in {5, 32, 33, 145}
                or remaining <= 0
            ):
                raise WorkspaceCleanupError(
                    f"Native file cleanup failed; workspace retained at {root}"
                ) from exc
            time.sleep(min(0.1, remaining))


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
        return json.loads(self.download(path, body=body))

    def download(self, path: str, *, body: dict | None = None, maximum: int = 1_048_576) -> bytes:
        request = Request(  # noqa: S310 - fixed loopback base; redirects are rejected
            self.base + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=10) as response:
            payload = response.read(maximum + 1)
        if len(payload) > maximum:
            raise RuntimeError("The native smoke response exceeded its bound")
        return payload


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
def running_app(
    executable: Path, root: Path, env: dict[str, str], log_path: Path, *, live: bool = False
):
    """Contain only this smoke's child tree, including the AppImage runtime."""
    # Provider SDK diagnostics can contain response bodies. Optional live proof
    # retains only allowlisted verification facts, never raw child output.
    output_context = (
        nullcontext(subprocess.DEVNULL) if live else log_path.open("w", encoding="utf-8")
    )
    with output_context as output:
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
        raise WorkspaceCleanupError(
            f"Native process cleanup failed; workspace retained at {root}"
        ) from exc
    finally:
        if removable:
            remove_workspace(root)


def run(installer: Path, report: Path, *, live_provider: str = "", live_model: str = "") -> dict:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError(
            "Native installer verification requires a disposable GitHub Actions runner"
        )
    installer = installer.resolve(strict=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with installer.open("rb") as payload:
        digest = hashlib.file_digest(payload, "sha256").hexdigest()
    result = {
        "status": "running",
        "platform": sys.platform,
        "machine": platform.machine(),
        "installer": installer.name,
        "sha256": digest,
        "profile": "disposable",
        "provider_requests": 0,
        "live_provider_verification": (
            {"status": "pending"} if live_provider else {"status": "not-run", "reason": "disabled"}
        ),
        "previous_public_release_upgrade": "not exercised",
        "cleanup": "not confirmed",
    }
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    try:
        # Validate the explicit opt-in before any installer or application starts.
        # This environment is populated into a sanitized base only inside the
        # disposable profile, and is never handed to the installer itself.
        if live_provider:
            live_environment({}, live_provider, live_model)
            result["provider_requests"] = "not counted"
            result["live_provider_verification"] = {"status": "running"}
        with smoke_workspace() as root:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            key = secrets.token_urlsafe(32)
            env = isolated_environment(root, port, key)
            app_env = live_environment(env, live_provider, live_model) if live_provider else env
            # The normal config writer owns even this disposable configuration.
            _atomic_write(root / "jarvis.toml", f"[ui]\nadmin_api_port = {port}\n")
            api = SwarmApi(port, key)
            original = None
            goal = "Preserve this unstarted team across installer replacement."
            for index, phase in enumerate(("fresh_install", "same_artifact_replacement")):
                executable = install(installer, root, env)
                log_path = report.with_name(f"native-smoke-{index + 1}.log")
                with running_app(
                    executable,
                    root,
                    app_env if index == 0 else env,
                    log_path,
                    live=bool(live_provider),
                ) as child:
                    wait_ready(api, child)
                    verify_capabilities(api.request("/api/swarm/capabilities"))
                    if original is None:
                        original = api.request(
                            "/api/swarm/teams",
                            {
                                "name": "Native installation persistence probe",
                                "goal": goal,
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
                    live_result = result["live_provider_verification"]
                    expected_ids = {original["id"]}
                    if live_result.get("team_id"):
                        expected_ids.add(live_result["team_id"])
                    if {team["id"] for team in teams} != expected_ids:
                        raise RuntimeError(
                            "Installer replacement duplicated or lost the isolated team"
                        )
                    if live_provider and index == 0:
                        result["live_provider_verification"] = run_live_task(api, live_provider)
                    elif live_provider:
                        retained = verify_live_artifact(api, live_result["team_id"])
                        if any(retained[field] != live_result[field] for field in retained):
                            raise LiveVerificationError(
                                "Installer replacement changed the accepted artifact"
                            )
                        live_result["same_artifact_replacement"] = "pass"
                result[phase] = {"native_wasm": "pass", "persistent_team_and_lead": "pass"}
    except BaseException as exc:
        result["status"] = "failed"
        result["failure_type"] = type(exc).__name__
        if live_provider:
            result["live_provider_verification"]["status"] = "failed"
        if isinstance(exc, LiveVerificationError):
            result["failure"] = str(exc)
        if isinstance(exc, WorkspaceCleanupError):
            result["cleanup"] = "failed"
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        raise
    result.update(status="pass", cleanup="pass")
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--live-provider", default="", help="Opt in to one supported scoped API provider"
    )
    parser.add_argument(
        "--live-model", default="", help="Optional model override for the selected provider"
    )
    parser.add_argument(
        "--live-from-environment",
        action="store_true",
        help="Read the manual workflow's opt-in inputs",
    )
    args = parser.parse_args()
    provider, model = args.live_provider, args.live_model
    if args.live_from_environment:
        if provider or model:
            parser.error("Use either explicit live options or workflow environment inputs")
        enabled = os.environ.get("SWARM_INSTALL_LIVE_ENABLED", "false")
        if enabled not in {"true", "false"}:
            parser.error("SWARM_INSTALL_LIVE_ENABLED must be true or false")
        if enabled == "true":
            provider = os.environ.get("SWARM_INSTALL_LIVE_PROVIDER", "")
            model = os.environ.get("SWARM_INSTALL_LIVE_MODEL", "")
            if not provider:
                parser.error("Live mode requires a selected scoped API provider")
    if model and not provider:
        parser.error("A live model override requires a selected scoped API provider")
    try:
        print(
            json.dumps(
                run(args.installer, args.report, live_provider=provider, live_model=model), indent=2
            )
        )
    except Exception as exc:  # noqa: BLE001 - no provider body or exception chain in CI output
        message = str(exc) if isinstance(exc, LiveVerificationError) else type(exc).__name__
        print(f"Native verification failed: {message}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

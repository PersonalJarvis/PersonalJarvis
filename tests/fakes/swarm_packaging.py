"""Platform-independent PyInstaller input recorder for the frozen contract."""

import json
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
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


class ScopedApiProvider:
    scoped_execution_only = True
    supports_tools = True


class OfflineProviderRegistry:
    def get_class(self, name):
        if name == "fixture-api":
            return ScopedApiProvider
        if name == "ambient-agent":
            return type("AmbientAgent", (), {"supports_tools": True})
        raise KeyError(name)


class NativeSmokeApi:
    """Offline native-install API that retains accepted artifact provenance."""

    def __init__(self):
        from jarvis.core.swarm_types import SwarmActor, SwarmController
        from jarvis.swarm.receipts import build_receipt, canonical, provenance

        self.calls = []
        self.teams = {}
        self.final_state = "succeeded"
        self.tokens_used = "900"
        self.tokens_reserved = "0"
        self.provider_ready = True
        self.task = {
            "id": "arithmetic",
            "state": "succeeded",
            "verification": "javascript",
            "owner_id": "a" * 32,
            "fence": 1,
            "evidence": ["1" * 32, "2" * 32, "3" * 32],
        }
        actor = SwarmActor("b" * 32, "a" * 32, "offline-actor", "arithmetic", 1)
        controller = SwarmController(actor.team_id, "offline-controller", 1, "offline-control")
        self.artifact_bytes = b'{"count":4,"sum":40,"mean":10}'
        self.artifacts = [
            {
                "id": "1" * 32,
                "name": "statistics.json",
                "task_id": "arithmetic",
                "owner_id": actor.agent_id,
                "team_id": actor.team_id,
                "attempt_fence": 1,
                "sha256": sha256(self.artifact_bytes).hexdigest(),
                "provenance": {"origin": "worker-authored"},
            }
        ]
        for index, kind in enumerate(("execution", "verification"), 2):
            payload = (
                {
                    "script": "function main() { return 10; }",
                    "inputs": {},
                    "execution": {"output": 10, "stdout": "", "stderr": "", "exit_code": 0},
                }
                if kind == "execution"
                else {
                    "kind": "javascript",
                    "accepted": True,
                    "verifier_id": "independent-verifier",
                    "contract_hash": "0" * 64,
                }
            )
            receipt = build_receipt(controller, actor, kind, payload, ["1" * 32])
            self.artifacts.append(
                {
                    "id": str(index) * 32,
                    "name": kind,
                    "task_id": "arithmetic",
                    "owner_id": actor.agent_id,
                    "team_id": actor.team_id,
                    "attempt_fence": 1,
                    "sha256": sha256(canonical(receipt).encode()).hexdigest(),
                    "provenance": provenance(receipt),
                }
            )

    def request(self, path, body=None):
        self.calls.append((path, deepcopy(body)))
        if path == "/api/swarm/capabilities":
            return {
                "local": True,
                "sandbox": {"available": True, "kind": "wasmtime-quickjs"},
                "providers": [
                    {
                        "id": "fixture-api",
                        "available": self.provider_ready,
                        "credential_present": self.provider_ready,
                    }
                ],
            }
        if path == "/api/swarm/teams":
            if body is None:
                return list(deepcopy(self.teams).values())
            team_id = "b" * 32 if body.get("tasks") else "c" * 32
            team = dict(
                body,
                id=team_id,
                lead_id="lead",
                version=1,
                storage_generation="generation",
                state="created",
            )
            self.teams[team_id] = team
            return deepcopy(team)
        team_id = path.split("/")[4]
        if path.endswith("/start"):
            self.teams[team_id].update(
                state=self.final_state,
                tokens_used=self.tokens_used,
                tokens_reserved=self.tokens_reserved,
            )
            return deepcopy(self.teams[team_id])
        if path.endswith("/cancel"):
            self.teams[team_id]["state"] = "canceled"
            return deepcopy(self.teams[team_id])
        if path.endswith("/tasks/record/arithmetic"):
            return deepcopy(self.task)
        if path.endswith("/artifacts?limit=200"):
            return deepcopy(self.artifacts)
        return deepcopy(self.teams[team_id])

    def download(self, path, *, maximum):
        assert maximum == 4096
        self.calls.append((path, None))
        return self.artifact_bytes


class NativeSmokeHarness:
    """Record process environments without starting an installer or provider."""

    def __init__(self):
        self.api = NativeSmokeApi()
        self.installer_environments = []
        self.app_environments = []
        self.live_output = []

    def install(self, installer, root, env):
        self.installer_environments.append(dict(env))
        return root / "application"

    @contextmanager
    def running_app(self, executable, root, env, log_path, *, live=False):
        self.app_environments.append(dict(env))
        self.live_output.append(live)
        yield self

    def poll(self):
        return None

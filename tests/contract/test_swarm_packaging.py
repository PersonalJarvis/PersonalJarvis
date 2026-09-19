"""All native builds carry clients that source installs may enable later."""

from __future__ import annotations

import ast
import os
import runpy
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import psutil
import pytest
import yaml
from packaging.requirements import Requirement

from tests.fakes.swarm_packaging import BinaryWheel, BundleHooks, ObservedJob, descendant_probe

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = runpy.run_path(str(ROOT / "packaging" / "swarm_bundle.py"))
NATIVE_SMOKE = runpy.run_path(str(ROOT / "packaging" / "verify_swarm_install.py"))


@pytest.mark.parametrize(
    ("platform", "wasmtime", "postgres"),
    [
        ("windows", "wasmtime.dll", "psycopg_binary.libs/libpq-version.dll"),
        ("macos", "libwasmtime.dylib", "psycopg_binary/.dylibs/libpq.dylib"),
        ("linux", "libwasmtime.so", "psycopg_binary.libs/libpq-version.so.5.18"),
    ],
)
def test_native_layout_clients_metadata_tls_and_s3_models(platform, wasmtime, postgres):
    hooks = BundleHooks(native=wasmtime)
    wheel = BinaryWheel(postgres, "psycopg_binary.libs/.load-order-psycopg-binary.txt")
    bundle = MANIFEST["collect_swarm_bundle"](hooks, distribution_reader=lambda _: wheel)
    assert (f"wheel/{postgres}", str(Path(postgres).parent).replace("\\", "/")) in bundle[
        "binaries"
    ]
    assert (f"wasmtime/{wasmtime}", "wasmtime") in bundle["binaries"]
    assert "psycopg_binary._psycopg" in bundle["hiddenimports"]
    assert {"psycopg", "psycopg_pool", "redis", "boto3", "botocore"} <= set(hooks.modules)
    assert {"psycopg-binary", "psycopg-pool", "certifi"} <= set(hooks.metadata)
    assert {"certifi", "boto3", "botocore"} <= set(hooks.data)
    assert any(".load-order-" in source for source, _ in bundle["datas"])


@pytest.mark.parametrize("missing", MANIFEST["DISTRIBUTIONS"])
def test_missing_build_driver_aborts_before_analysis(missing):
    with pytest.raises(SystemExit, match="swarm-distributed"):
        MANIFEST["collect_swarm_bundle"](
            BundleHooks(missing=missing), distribution_reader=lambda _: BinaryWheel()
        )


@pytest.mark.parametrize("outdated", MANIFEST["MINIMUM_VERSIONS"])
def test_outdated_build_driver_aborts_before_analysis(outdated):
    with pytest.raises(SystemExit, match="Frozen Swarm requires"):
        MANIFEST["collect_swarm_bundle"](
            BundleHooks(),
            distribution_reader=lambda name: BinaryWheel(
                version="0" if name == outdated else "9999"
            ),
        )


def test_manifest_minimums_match_runtime_readiness():
    tree = ast.parse((ROOT / "jarvis/swarm/distributed/install.py").read_text(encoding="utf-8"))
    imports = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_IMPORTS" for target in node.targets)
    )
    for name, minimum in imports.values():
        assert MANIFEST["MINIMUM_VERSIONS"][name] == minimum


def test_source_base_and_full_keep_distributed_infrastructure_optional():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    base = {Requirement(value).name for value in project["dependencies"]}
    optional = project["optional-dependencies"]
    clients = {"psycopg", "redis", "boto3"}
    assert not clients & base
    assert clients <= {Requirement(value).name for value in optional["swarm-distributed"]}
    assert "swarm-distributed" not in " ".join(optional["full"])


def test_missing_postgres_native_wheel_payload_aborts():
    with pytest.raises(SystemExit, match="native libraries"):
        MANIFEST["collect_swarm_bundle"](BundleHooks(), distribution_reader=lambda _: BinaryWheel())


@pytest.mark.parametrize("target", ["windows", "macos", "linux"])
def test_each_native_release_target_installs_extra_and_uses_shared_manifest(target):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/desktop-installers.yml").read_text(encoding="utf-8")
    )
    commands = "\n".join(step.get("run", "") for step in workflow["jobs"][target]["steps"])
    assert 'pip install -e ".[desktop,dev,swarm-distributed]"' in commands
    extension = "ps1" if target == "windows" else "sh"
    assert f"packaging/{target}/build.{extension}" in commands
    script = (ROOT / f"packaging/{target}/build.{extension}").read_text(encoding="utf-8")
    assert "jarvis.spec" in script and "PyInstaller" in script
    spec = (ROOT / "jarvis.spec").read_text(encoding="utf-8")
    assert '"swarm_bundle.py"' in spec and '"collect_swarm_bundle"' in spec
    tree = ast.parse(spec)
    analysis = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    values = {keyword.arg: ast.unparse(keyword.value) for keyword in analysis.keywords}
    assert values["binaries"] == "swarm_binaries"
    assert values["datas"] == "datas" and values["hiddenimports"] == "hiddenimports"


@pytest.mark.parametrize("target", ["windows", "macos", "linux"])
def test_native_installation_proof_gates_artifact_upload(target):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/desktop-installers.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"][target]["steps"]
    smoke = next(
        index
        for index, step in enumerate(steps)
        if "packaging/verify_swarm_install.py" in step.get("run", "")
    )
    installer_upload = next(
        index
        for index, step in enumerate(steps)
        if step.get("with", {}).get("name", "").startswith("installer-")
    )
    assert smoke < installer_upload
    assert not steps[smoke].get("continue-on-error", False)
    assert "if" not in steps[smoke]


@pytest.mark.parametrize(
    "capabilities",
    [
        {"local": True, "sandbox": {"available": False, "kind": "wasmtime-quickjs"}},
        {"local": False, "sandbox": {"available": True, "kind": "wasmtime-quickjs"}},
        {"local": True, "sandbox": {"available": True, "kind": "host-shell"}},
    ],
)
def test_native_smoke_rejects_unavailable_or_wrong_execution_runtime(capabilities):
    with pytest.raises(RuntimeError, match="sandbox|WASM"):
        NATIVE_SMOKE["verify_capabilities"](capabilities)


@pytest.mark.parametrize("field", ["id", "lead_id", "goal", "limits", "policy"])
def test_native_smoke_rejects_lost_team_identity_or_authority(field):
    before = {"id": "team", "lead_id": "lead", "goal": "retained", "limits": {}, "policy": {}}
    after = dict(before, **{field: "changed"})
    with pytest.raises(RuntimeError, match=f"field {field}"):
        NATIVE_SMOKE["verify_identity"](before, after)


def test_native_smoke_removes_inherited_provider_credentials_and_python_paths(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-provider-secret")
    monkeypatch.setenv("JARVIS__BRAIN__PRIMARY", "inherited-provider")
    monkeypatch.setenv("PYTHONPATH", "inherited-module-path")
    env = NATIVE_SMOKE["isolated_environment"](tmp_path, 47899, "synthetic-control-key")
    assert not {"OPENAI_API_KEY", "JARVIS__BRAIN__PRIMARY", "PYTHONPATH"} & env.keys()
    assert env["JARVIS_DATA_DIR"] == str(tmp_path / "data")
    assert env["JARVIS_VOICE"] == "0"
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path / "home")
    assert env["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"


def test_native_smoke_refuses_installer_side_effects_outside_disposable_ci(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="disposable GitHub Actions"):
        NATIVE_SMOKE["run"](tmp_path / "installer", tmp_path / "report.json")
    assert not list(tmp_path.iterdir())


def _assert_probe_stopped(pid_path):
    pid = int(pid_path.read_text(encoding="utf-8"))
    try:
        child = psutil.Process(pid)
        state = child.status()
    except psutil.NoSuchProcess:
        return
    try:
        assert state == psutil.STATUS_ZOMBIE, "The owned descendant survived teardown"
    finally:
        # A failed regression must not itself leave a long-lived helper behind.
        if state != psutil.STATUS_ZOMBIE and child.is_running():
            child.kill()
            child.wait(timeout=10)


@pytest.mark.skipif(os.name not in {"nt", "posix"}, reason="Native process backend unavailable")
def test_native_installer_timeout_reaps_its_already_spawned_descendant(tmp_path):
    pid_path = tmp_path / "child.pid"
    runner = NATIVE_SMOKE["NativeRunner"](tmp_path, dict(os.environ))
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run(descendant_probe(pid_path, parent_exits=False), timeout_s=5)
    _assert_probe_stopped(pid_path)


@pytest.mark.skipif(os.name not in {"nt", "posix"}, reason="Native process backend unavailable")
def test_native_installer_retains_descendant_ownership_after_launcher_exit(tmp_path):
    pid_path = tmp_path / "child.pid"
    runner = NATIVE_SMOKE["NativeRunner"](tmp_path, dict(os.environ))
    rc, _stdout, stderr = runner.run(descendant_probe(pid_path, parent_exits=True), timeout_s=10)
    assert rc == 0, stderr
    _assert_probe_stopped(pid_path)


@pytest.mark.skipif(os.name not in {"nt", "posix"}, reason="Native process backend unavailable")
def test_native_app_context_reaps_child_after_exited_launcher(monkeypatch, tmp_path):
    pid_path = tmp_path / "child.pid"
    running_app = NATIVE_SMOKE["running_app"]
    actual_containment = NATIVE_SMOKE["contained_process"]

    def launch_probe(argv, **kwargs):
        assert argv == [str(tmp_path / "application"), "serve"]
        return actual_containment(descendant_probe(pid_path, parent_exits=True), **kwargs)

    monkeypatch.setitem(running_app.__wrapped__.__globals__, "contained_process", launch_probe)
    with running_app(
        tmp_path / "application", tmp_path, dict(os.environ), tmp_path / "application.log"
    ) as launcher:
        assert launcher.wait(timeout=10) == 0
        child = psutil.Process(int(pid_path.read_text(encoding="utf-8")))
        assert child.is_running()
    _assert_probe_stopped(pid_path)


def test_native_cleanup_failure_retains_workspace_instead_of_removing_live_files():
    workspace = None
    try:
        with pytest.raises(RuntimeError, match="workspace retained"):
            with NATIVE_SMOKE["smoke_workspace"]() as workspace:
                (workspace / "installer-output").write_text("keep", encoding="utf-8")
                raise NATIVE_SMOKE["ContainmentError"]("Synthetic incomplete shutdown")
        assert workspace is not None
        assert (workspace / "installer-output").read_text(encoding="utf-8") == "keep"
    finally:
        if workspace is not None:
            shutil.rmtree(workspace)


def test_native_workspace_is_removed_after_normal_cleanup():
    with NATIVE_SMOKE["smoke_workspace"]() as workspace:
        (workspace / "installer-output").write_text("temporary", encoding="utf-8")
    assert not workspace.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended launch contract")
@pytest.mark.parametrize("refuse", [False, True])
def test_native_windows_target_cannot_execute_before_job_assignment(monkeypatch, tmp_path, refuse):
    contained = NATIVE_SMOKE["contained_process"]
    native_globals = contained.__wrapped__.__globals__
    marker = tmp_path / "executed"
    observed = ObservedJob(native_globals["_windows_job"](), marker, refuse=refuse)
    monkeypatch.setitem(native_globals, "_windows_job", lambda: observed)
    command = [
        sys.executable,
        "-c",
        "import sys; from pathlib import Path; "
        "Path(sys.argv[1]).write_text('yes', encoding='utf-8')",
        str(marker),
    ]
    if refuse:
        with pytest.raises(OSError, match="Synthetic job assignment failure"):
            with contained(command, env=dict(os.environ)):
                pytest.fail("The rejected launch was exposed to its caller")
        assert observed.child is not None and observed.child.poll() is not None
        assert not marker.exists()
    else:
        with contained(command, env=dict(os.environ)) as child:
            assert child.wait(timeout=10) == 0
        assert marker.read_text(encoding="utf-8") == "yes"
    assert observed.saw_suspended_target

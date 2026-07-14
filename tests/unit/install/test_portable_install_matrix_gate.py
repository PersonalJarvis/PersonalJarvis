"""Regression tests for the target-resolved portable-install CI gate."""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_GATE = _ROOT / "scripts" / "ci" / "check_portable_install_matrix.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("portable_install_matrix_gate", _GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    module = _load_gate()
    yield module
    sys.modules.pop(module.__name__, None)


def _target(gate, key: str):
    return next(target for target in gate.TARGETS if target.key == key)


def _wheel_package(
    *,
    name: str = "example",
    version: str = "1.0",
    filename: str = "example-1.0-py3-none-any.whl",
    marker: str | None = None,
) -> dict:
    package = {
        "name": name,
        "version": version,
        "wheels": [
            {
                "url": f"https://files.pythonhosted.org/packages/aa/bb/{filename}",
                "hashes": {"sha256": "a" * 64},
            }
        ],
    }
    if marker is not None:
        package["marker"] = marker
    return package


def _sdist_package(
    *,
    name: str,
    version: str,
    url: str,
    sha256: str,
    marker: str | None = None,
) -> dict:
    package = {
        "name": name,
        "version": version,
        "sdist": {"url": url, "hashes": {"sha256": sha256}},
    }
    if marker is not None:
        package["marker"] = marker
    return package


def _lock(*packages: dict) -> dict:
    return {"lock-version": "1.0", "packages": list(packages)}


def test_matrix_covers_all_advertised_targets_and_python_minors(gate) -> None:
    assert gate.PYTHON_VERSIONS == ("3.11", "3.12", "3.13", "3.14")
    assert {target.key for target in gate.TARGETS} == {
        "linux-x86_64",
        "linux-arm64",
        "macos-x86_64",
        "macos-arm64",
        "windows-x86_64",
        "windows-arm64",
    }
    assert len(gate.TARGETS) * len(gate.PYTHON_VERSIONS) == 24


def test_inactive_package_is_ignored_but_marker_mutation_fails_closed(gate) -> None:
    target = _target(gate, "linux-x86_64")
    forbidden = _sdist_package(
        name="native-source-only",
        version="1.0",
        url="https://files.pythonhosted.org/packages/aa/bb/native-source-only-1.0.tar.gz",
        sha256="b" * 64,
        marker="sys_platform == 'win32'",
    )
    data = _lock(_wheel_package(), forbidden)
    assert gate.validate_pylock(data, target, "3.11", profile="base") == frozenset()

    forbidden["marker"] = "sys_platform == 'linux'"
    with pytest.raises(gate.LockValidationError, match="source build is not an audited exception"):
        gate.validate_pylock(data, target, "3.11", profile="base")


def test_exact_docopt_sdist_is_allowed_but_hash_mutation_is_rejected(gate) -> None:
    exception = next(item for item in gate.SDIST_EXCEPTIONS if item.name == "docopt")
    package = _sdist_package(
        name=exception.name,
        version=exception.version,
        url=exception.url,
        sha256=exception.sha256,
    )
    data = _lock(package)
    allowed = gate.validate_pylock(
        data,
        _target(gate, "macos-arm64"),
        "3.14",
        profile="full",
    )
    assert allowed == frozenset({"docopt"})

    package["sdist"]["hashes"]["sha256"] = "0" * 64
    with pytest.raises(gate.LockValidationError, match="not an audited exception"):
        gate.validate_pylock(
            data,
            _target(gate, "macos-arm64"),
            "3.14",
            profile="full",
        )


def test_pyyaml_sdist_exception_is_narrow_to_windows_arm64_cp311(gate) -> None:
    exception = next(item for item in gate.SDIST_EXCEPTIONS if item.name == "pyyaml")
    data = _lock(
        _sdist_package(
            name=exception.name,
            version=exception.version,
            url=exception.url,
            sha256=exception.sha256,
        )
    )
    allowed = gate.validate_pylock(
        data,
        _target(gate, "windows-arm64"),
        "3.11",
        profile="base",
    )
    assert allowed == frozenset({"pyyaml"})

    for target_key, python in (("windows-arm64", "3.12"), ("windows-x86_64", "3.11")):
        with pytest.raises(gate.LockValidationError, match="not an audited exception"):
            gate.validate_pylock(
                data,
                _target(gate, target_key),
                python,
                profile="base",
            )


def test_desktop_sdist_exception_cannot_drift_into_base(gate) -> None:
    exception = next(item for item in gate.SDIST_EXCEPTIONS if item.name == "mouseinfo")
    data = _lock(
        _sdist_package(
            name=exception.name,
            version=exception.version,
            url=exception.url,
            sha256=exception.sha256,
        )
    )
    target = _target(gate, "linux-x86_64")
    assert gate.validate_pylock(data, target, "3.11", profile="full") == frozenset(
        {"mouseinfo"}
    )
    with pytest.raises(gate.LockValidationError, match="not an audited exception"):
        gate.validate_pylock(data, target, "3.11", profile="base")


def test_free_threaded_and_musllinux_wheels_do_not_false_green(gate) -> None:
    free_threaded = _lock(
        _wheel_package(filename="example-1.0-cp314t-cp314t-win_arm64.whl")
    )
    with pytest.raises(gate.LockValidationError, match="no compatible"):
        gate.validate_pylock(
            free_threaded,
            _target(gate, "windows-arm64"),
            "3.14",
            profile="base",
        )

    musllinux = _lock(
        _wheel_package(filename="example-1.0-cp311-cp311-musllinux_1_2_aarch64.whl")
    )
    with pytest.raises(gate.LockValidationError, match="no compatible"):
        gate.validate_pylock(
            musllinux,
            _target(gate, "linux-arm64"),
            "3.11",
            profile="base",
        )


def test_missing_artifact_hash_and_private_index_fail_closed(gate) -> None:
    target = _target(gate, "windows-x86_64")
    missing_hash = _wheel_package(filename="example-1.0-cp311-cp311-win_amd64.whl")
    missing_hash["wheels"][0]["hashes"] = {}
    with pytest.raises(gate.LockValidationError, match="SHA-256"):
        gate.validate_pylock(_lock(missing_hash), target, "3.11", profile="base")

    private_index = _wheel_package(filename="example-1.0-cp311-cp311-win_amd64.whl")
    private_index["wheels"][0]["url"] = (
        "https://packages.example.test/example-1.0-cp311-cp311-win_amd64.whl"
    )
    with pytest.raises(gate.LockValidationError, match="public-PyPI"):
        gate.validate_pylock(_lock(private_index), target, "3.11", profile="base")


def test_cell_projects_full_pylock_before_target_dry_run(
    gate, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, timeout: int = gate.COMMAND_TIMEOUT_SECONDS):
        del cwd, timeout
        commands.append(command)
        if command[:3] == ["uv", "pip", "compile"]:
            output = Path(command[command.index("--output-file") + 1])
            digest = "a" * 64
            output.write_text(
                "lock-version = \"1.0\"\n\n"
                "[[packages]]\n"
                "name = \"example\"\n"
                "version = \"1.0\"\n"
                "wheels = [{ url = "
                "\"https://files.pythonhosted.org/packages/aa/bb/"
                "example-1.0-py3-none-any.whl\", "
                f"hashes = {{ sha256 = \"{digest}\" }} }}]\n",
                encoding="utf-8",
            )
        return gate.CommandResult(0)

    monkeypatch.setattr(gate, "_run_command", fake_run)
    result = gate._check_cell(_target(gate, "windows-arm64"), "3.11", tmp_path)
    assert result.ok
    assert [command[:3] for command in commands] == [
        ["uv", "pip", "compile"],
        ["uv", "pip", "compile"],
        ["uv", "pip", "install"],
        ["uv", "pip", "install"],
    ]
    assert "--require-hashes" in commands[2]
    assert "--require-hashes" in commands[3]
    full_requirements = Path(commands[3][commands[3].index("--requirements") + 1])
    assert full_requirements.name == "requirements.full.txt"
    full_text = full_requirements.read_text(encoding="utf-8")
    assert "example==1.0 --hash=sha256:" + "a" * 64 in full_text
    for command in commands:
        assert "--no-config" in command
        assert command[command.index("--default-index") + 1] == gate.PUBLIC_PYPI
        assert command[command.index("--python-platform") + 1] == "aarch64-pc-windows-msvc"


def test_target_requirement_projection_selects_active_compatible_hashes(
    gate, tmp_path: Path
) -> None:
    target = _target(gate, "windows-arm64")
    active = _wheel_package(
        name="native-example",
        filename="native_example-1.0-cp313-cp313-win_arm64.whl",
        marker="sys_platform == 'win32'",
    )
    active["wheels"].extend(
        [
            {
                "url": (
                    "https://files.pythonhosted.org/packages/aa/bb/"
                    "native_example-1.0-cp313-cp313-win_amd64.whl"
                ),
                "hashes": {"sha256": "b" * 64},
            },
            {
                "url": (
                    "https://files.pythonhosted.org/packages/aa/bb/"
                    "native_example-1.0-py3-none-any.whl"
                ),
                "hashes": {"sha256": "c" * 64},
            },
        ]
    )
    inactive = _wheel_package(
        name="linux-only",
        marker="sys_platform == 'linux'",
    )
    output = tmp_path / "requirements.full.txt"

    allowed = gate._materialize_target_requirements(
        _lock(active, inactive),
        output,
        target,
        "3.13",
        profile="full",
    )

    assert allowed == frozenset()
    text = output.read_text(encoding="utf-8")
    assert "native-example==1.0" in text
    assert "--hash=sha256:" + "a" * 64 in text
    assert "--hash=sha256:" + "c" * 64 in text
    assert "--hash=sha256:" + "b" * 64 not in text
    assert "linux-only" not in text


def test_uv_version_is_exact_and_missing_uv_fails(gate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gate,
        "_run_command",
        lambda *args, **kwargs: gate.CommandResult(0, stdout="uv 0.11.20\n"),
    )
    assert "0.11.19 is required" in gate._verify_uv_version()

    monkeypatch.setattr(
        gate,
        "_run_command",
        lambda *args, **kwargs: gate.CommandResult(127, stderr="Executable not found: uv"),
    )
    assert "Executable not found" in gate._verify_uv_version()


def test_requirements_recompile_is_no_upgrade_and_byte_compared(
    gate, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requirements_in = tmp_path / "requirements.in"
    requirements_txt = tmp_path / "requirements.txt"
    requirements_in.write_text("example>=1\n", encoding="utf-8")
    requirements_txt.write_bytes(b"example==1.0\n")
    monkeypatch.setattr(gate, "REQUIREMENTS_IN", requirements_in)
    monkeypatch.setattr(gate, "REQUIREMENTS_TXT", requirements_txt)
    commands: list[list[str]] = []

    def unchanged(command: list[str], *, cwd: Path, timeout: int = gate.COMMAND_TIMEOUT_SECONDS):
        del cwd, timeout
        commands.append(command)
        return gate.CommandResult(0)

    monkeypatch.setattr(gate, "_run_command", unchanged)
    assert gate._verify_reproducible_requirements() is None
    assert "--universal" in commands[0]
    assert "--generate-hashes" in commands[0]
    assert "--upgrade" not in commands[0]

    def mutate(command: list[str], *, cwd: Path, timeout: int = gate.COMMAND_TIMEOUT_SECONDS):
        del command, timeout
        (cwd / "requirements.txt").write_bytes(b"example==2.0\n")
        return gate.CommandResult(0)

    monkeypatch.setattr(gate, "_run_command", mutate)
    assert "not a byte-identical" in gate._verify_reproducible_requirements()


def test_mock_pylock_fixture_is_valid_toml() -> None:
    fixture = b"""lock-version = \"1.0\"\n[[packages]]\nname = \"example\"\nversion = \"1.0\"\n"""
    assert tomllib.loads(fixture.decode())["packages"][0]["name"] == "example"

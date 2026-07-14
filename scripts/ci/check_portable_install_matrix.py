#!/usr/bin/env python3
"""Prove the advertised installer matrix with target-resolved PyPI artifacts.

This gate models the two installer phases users actually receive:

1. the universal, hash-pinned ``requirements.txt`` base install;
2. that base plus the advertised ``personal-jarvis[full]`` profile.

For every supported CPython/OS/architecture cell, uv produces a target-specific
PEP 751 lock. Only packages whose markers are active in that cell are checked.
Every active package must expose a compatible wheel selected by uv, except for
the tiny, exact, hash-bound pure-source exceptions audited below. The resulting
base and full plans are then installed with ``--dry-run`` and source builds
disabled outside those exact exceptions.

The gate deliberately resolves against public PyPI with user configuration and
credential helpers disabled. It also proves that ``uv.lock`` is current and
that a no-upgrade universal recompile reproduces the shipped hash lock byte for
byte. A universal lock alone is not portability evidence: it can contain an
sdist or a wheel for a different target and still resolve successfully.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from packaging.markers import InvalidMarker, Marker
    from packaging.utils import canonicalize_name, parse_wheel_filename
except ImportError as exc:  # pragma: no cover - exercised by the CI entry point
    _PACKAGING_IMPORT_ERROR: ImportError | None = exc
else:
    _PACKAGING_IMPORT_ERROR = None


REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_IN = REPO_ROOT / "requirements.in"
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"

EXPECTED_UV_VERSION = "0.11.19"
PUBLIC_PYPI = "https://pypi.org/simple"
PYTHON_VERSIONS = ("3.11", "3.12", "3.13", "3.14")
DEFAULT_JOBS = 4
COMMAND_TIMEOUT_SECONDS = 420
_PUBLIC_ARTIFACT_HOST = "files.pythonhosted.org"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Target:
    """One advertised target understood by uv and Python environment markers."""

    key: str
    uv_platform: str
    sys_platform: str
    platform_machine: str
    platform_system: str
    wheel_arch: str


TARGETS = (
    Target(
        "linux-x86_64",
        "x86_64-manylinux_2_28",
        "linux",
        "x86_64",
        "Linux",
        "x86_64",
    ),
    Target(
        "linux-arm64",
        "aarch64-manylinux_2_28",
        "linux",
        "aarch64",
        "Linux",
        "aarch64",
    ),
    Target(
        "macos-x86_64",
        "x86_64-apple-darwin",
        "darwin",
        "x86_64",
        "Darwin",
        "x86_64",
    ),
    Target(
        "macos-arm64",
        "aarch64-apple-darwin",
        "darwin",
        "arm64",
        "Darwin",
        "arm64",
    ),
    Target(
        "windows-x86_64",
        "x86_64-pc-windows-msvc",
        "win32",
        "AMD64",
        "Windows",
        "amd64",
    ),
    Target(
        "windows-arm64",
        "aarch64-pc-windows-msvc",
        "win32",
        "ARM64",
        "Windows",
        "arm64",
    ),
)


@dataclass(frozen=True)
class SdistException:
    """One reviewed pure-Python sdist, narrowed by identity and target."""

    name: str
    version: str
    sha256: str
    url: str
    targets: frozenset[str] | None = None
    pythons: frozenset[str] | None = None
    profiles: frozenset[str] = frozenset({"base", "full"})

    def applies_to(
        self,
        name: str,
        version: str,
        target: Target,
        python: str,
        profile: str,
    ) -> bool:
        if canonicalize_name(name) != canonicalize_name(self.name):
            return False
        if version != self.version:
            return False
        if profile not in self.profiles:
            return False
        if self.targets is not None and target.key not in self.targets:
            return False
        return self.pythons is None or python in self.pythons


# docopt 0.6.2 is a single, pure-Python module. It has no wheel on PyPI and is
# pulled transitively by num2words. The immutable 2014 sdist is compiler-free.
#
# PyYAML 6.0.3 has no CPython 3.11 Windows ARM64 wheel. Its build explicitly
# falls back to the pure-Python implementation when libyaml cannot be compiled,
# so that one legacy cell is allowed to use the exact hash already carried by
# requirements.txt. Newer Python versions and every other target must use a
# wheel; broadening this exception requires a fresh audit.
SDIST_EXCEPTIONS = (
    SdistException(
        name="docopt",
        version="0.6.2",
        sha256="49b3a825280bd66b3aa83585ef59c4a8c82f2c8a522dbe754a8bc8d08c85c491",
        url=(
            "https://files.pythonhosted.org/packages/a2/55/"
            "8f8cab2afd404cf578136ef2cc5dfb50baa1761b68c9da1fb1e4eed343c9/"
            "docopt-0.6.2.tar.gz"
        ),
    ),
    SdistException(
        name="pyyaml",
        version="6.0.3",
        sha256="d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f",
        url=(
            "https://files.pythonhosted.org/packages/05/8e/"
            "961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/"
            "pyyaml-6.0.3.tar.gz"
        ),
        targets=frozenset({"windows-arm64"}),
        pythons=frozenset({"3.11"}),
    ),
    # The advertised desktop profile includes several small, legacy desktop
    # helpers that publish pure-Python sdists only. Each entry is full-only and
    # immutable by version, URL, and SHA-256; moving one into base or changing
    # any artifact fails until it is reviewed again.
    SdistException(
        name="global-hotkeys",
        version="0.1.7",
        sha256="6546d7a2ea2da6e646871b48dea47d231d66ec2982641f30bf83a2f7a2a2b249",
        url=(
            "https://files.pythonhosted.org/packages/d3/de/"
            "8c07714cd55c72e57a44059aaba43bd1b347e989b18a19012ad4764978a8/"
            "global_hotkeys-0.1.7.tar.gz"
        ),
        targets=frozenset({"windows-x86_64", "windows-arm64"}),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="mouseinfo",
        version="0.1.3",
        sha256="2c62fb8885062b8e520a3cce0a297c657adcc08c60952eb05bc8256ef6f7f6e7",
        url=(
            "https://files.pythonhosted.org/packages/28/fa/"
            "b2ba8229b9381e8f6381c1dcae6f4159a7f72349e414ed19cfbbd1817173/"
            "MouseInfo-0.1.3.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="proxy-tools",
        version="0.1.0",
        sha256="ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010",
        url=(
            "https://files.pythonhosted.org/packages/f2/cf/"
            "77d3e19b7fabd03895caca7857ef51e4c409e0ca6b37ee6e9f7daa50b642/"
            "proxy_tools-0.1.0.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="pyautogui",
        version="0.9.54",
        sha256="dd1d29e8fd118941cb193f74df57e5c6ff8e9253b99c7b04f39cfc69f3ae04b2",
        url=(
            "https://files.pythonhosted.org/packages/65/ff/"
            "cdae0a8c2118a0de74b6cf4cbcdcaf8fd25857e6c3f205ce4b1794b27814/"
            "PyAutoGUI-0.9.54.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="pygetwindow",
        version="0.0.9",
        sha256="17894355e7d2b305cd832d717708384017c1698a90ce24f6f7fbf0242dd0a688",
        url=(
            "https://files.pythonhosted.org/packages/e1/70/"
            "c7a4f46dbf06048c6d57d9489b8e0f9c4c3d36b7479f03c5ca97eaa2541d/"
            "PyGetWindow-0.0.9.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="pyrect",
        version="0.2.0",
        sha256="f65155f6df9b929b67caffbd57c0947c5ae5449d3b580d178074bffb47a09b78",
        url=(
            "https://files.pythonhosted.org/packages/cb/04/"
            "2ba023d5f771b645f7be0c281cdacdcd939fe13d1deb331fc5ed1a6b3a98/"
            "PyRect-0.2.0.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="pyscreeze",
        version="1.0.1",
        sha256="cf1662710f1b46aa5ff229ee23f367da9e20af4a78e6e365bee973cad0ead4be",
        url=(
            "https://files.pythonhosted.org/packages/ee/f0/"
            "cb456ac4f1a73723d5b866933b7986f02bacea27516629c00f8e7da94c2d/"
            "pyscreeze-1.0.1.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="python3-xlib",
        version="0.15",
        sha256="dc4245f3ae4aa5949c1d112ee4723901ade37a96721ba9645f2bfa56e5b383f8",
        url=(
            "https://files.pythonhosted.org/packages/ef/c6/"
            "2c5999de3bb1533521f1101e8fe56fd9c266732f4d48011c7c69b29d12ae/"
            "python3-xlib-0.15.tar.gz"
        ),
        targets=frozenset({"linux-x86_64", "linux-arm64"}),
        profiles=frozenset({"full"}),
    ),
    SdistException(
        name="pytweening",
        version="1.2.0",
        sha256="243318b7736698066c5f362ec5c2b6434ecf4297c3c8e7caa8abfe6af4cac71b",
        url=(
            "https://files.pythonhosted.org/packages/79/0c/"
            "c16bc93ac2755bac0066a8ecbd2a2931a1735a6fffd99a2b9681c7e83e90/"
            "pytweening-1.2.0.tar.gz"
        ),
        profiles=frozenset({"full"}),
    ),
    # Vosk's base dependency srt is also a single pure-Python module. Windows
    # ARM64 marker-excludes Vosk, so srt must not appear in that target cell.
    SdistException(
        name="srt",
        version="3.5.3",
        sha256="4884315043a4f0740fd1f878ed6caa376ac06d70e135f306a6dc44632eed0cc0",
        url=(
            "https://files.pythonhosted.org/packages/66/b7/"
            "4a1bc231e0681ebf339337b0cd05b91dc6a0d701fa852bb812e244b7a030/"
            "srt-3.5.3.tar.gz"
        ),
        targets=frozenset(
            {
                "linux-x86_64",
                "linux-arm64",
                "macos-x86_64",
                "macos-arm64",
                "windows-x86_64",
            }
        ),
    ),
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CellResult:
    target: Target
    python: str
    elapsed_seconds: float
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def label(self) -> str:
        return f"{self.target.key}/cp{self.python.replace('.', '')}"


class LockValidationError(ValueError):
    """Raised when one target-specific lock is not compiler-free."""


def _clean_environment() -> dict[str, str]:
    """Return an environment that cannot inherit private indexes or uv config."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PIP_", "UV_"))
    }
    environment.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PYTHONUTF8": "1",
            "UV_COLOR": "never",
            "UV_DEFAULT_INDEX": PUBLIC_PYPI,
            "UV_KEYRING_PROVIDER": "disabled",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
        }
    )
    return environment


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run one bounded uv command without a shell or inherited package config."""

    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=_clean_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return CommandResult(127, stderr=f"Executable not found: {command[0]}")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            124,
            stdout=stdout,
            stderr=f"Command timed out after {timeout}s.\n{stderr}",
        )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _public_uv_flags() -> list[str]:
    return [
        "--default-index",
        PUBLIC_PYPI,
        "--keyring-provider",
        "disabled",
        "--no-config",
        "--no-progress",
        "--color",
        "never",
    ]


def _marker_environment(target: Target, python: str, *, profile: str) -> dict[str, str]:
    return {
        "implementation_name": "cpython",
        "implementation_version": f"{python}.0",
        "os_name": "nt" if target.sys_platform == "win32" else "posix",
        "platform_machine": target.platform_machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": target.platform_system,
        "platform_version": "",
        "python_full_version": f"{python}.0",
        "python_version": python,
        "sys_platform": target.sys_platform,
        "extra": "full" if profile == "full" else "",
    }


def _package_is_active(package: dict[str, Any], target: Target, python: str, profile: str) -> bool:
    marker_text = package.get("marker")
    if marker_text is None:
        return True
    if not isinstance(marker_text, str) or not marker_text.strip():
        raise LockValidationError("package has an empty or non-string marker")
    try:
        marker = Marker(marker_text)
    except InvalidMarker as exc:
        raise LockValidationError(f"invalid package marker {marker_text!r}: {exc}") from exc
    return marker.evaluate(_marker_environment(target, python, profile=profile))


def _artifact_sha256(artifact: dict[str, Any]) -> str | None:
    hashes = artifact.get("hashes")
    if not isinstance(hashes, dict):
        return None
    digest = hashes.get("sha256")
    return digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None


def _is_public_pypi_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == _PUBLIC_ARTIFACT_HOST


def _interpreter_tag_matches(interpreter: str, abi: str, python: str) -> bool:
    major, minor = (int(part) for part in python.split("."))
    exact_cpython = f"cp{major}{minor}"
    if interpreter == exact_cpython:
        return abi in {exact_cpython, "abi3", "none"}
    if abi == "abi3" and re.fullmatch(r"cp\d{2,3}", interpreter):
        wheel_digits = interpreter[2:]
        wheel_major = int(wheel_digits[0])
        wheel_minor = int(wheel_digits[1:])
        return wheel_major == major and wheel_minor <= minor
    return abi == "none" and interpreter in {"py3", f"py{major}", f"py{major}{minor}"}


def _manylinux_platform_matches(platform: str, architecture: str) -> bool:
    if not platform.endswith(f"_{architecture}"):
        return False
    legacy_floors = {"manylinux1": 5, "manylinux2010": 12, "manylinux2014": 17}
    for prefix, glibc_minor in legacy_floors.items():
        if platform.startswith(f"{prefix}_"):
            return glibc_minor <= 28
    match = re.match(r"manylinux_2_(\d+)_", platform)
    return match is not None and int(match.group(1)) <= 28


def _platform_tag_matches(platform: str, target: Target) -> bool:
    platform = platform.lower()
    if platform == "any":
        return True
    if target.sys_platform == "win32":
        return platform == f"win_{target.wheel_arch}"
    if target.sys_platform == "darwin":
        return platform.startswith("macosx_") and (
            platform.endswith(f"_{target.wheel_arch}") or platform.endswith("_universal2")
        )
    # The advertised Linux floor is glibc/manylinux_2_28. musllinux and raw
    # linux wheels are not interchangeable with that target.
    return _manylinux_platform_matches(platform, target.wheel_arch)


def _wheel_is_compatible(artifact: dict[str, Any], target: Target, python: str) -> bool:
    url = artifact.get("url")
    if not _is_public_pypi_url(url) or _artifact_sha256(artifact) is None:
        return False
    filename = Path(urlparse(str(url)).path).name
    try:
        _name, _version, _build, tags = parse_wheel_filename(filename)
    except ValueError:
        return False
    return any(
        _interpreter_tag_matches(tag.interpreter, tag.abi, python)
        and _platform_tag_matches(tag.platform, target)
        for tag in tags
    )


def _matching_sdist_exception(
    package: dict[str, Any], target: Target, python: str, profile: str
) -> SdistException | None:
    name = package.get("name")
    version = package.get("version")
    sdist = package.get("sdist")
    if not isinstance(name, str) or not isinstance(version, str) or not isinstance(sdist, dict):
        return None
    url = sdist.get("url")
    digest = _artifact_sha256(sdist)
    if not _is_public_pypi_url(url) or digest is None:
        return None
    for exception in SDIST_EXCEPTIONS:
        if (
            exception.applies_to(name, version, target, python, profile)
            and url == exception.url
            and digest == exception.sha256
        ):
            return exception
    return None


def validate_pylock(
    lock_data: dict[str, Any],
    target: Target,
    python: str,
    *,
    profile: str,
) -> frozenset[str]:
    """Validate active artifacts and return exact sdists enabled for dry-run."""

    if lock_data.get("lock-version") != "1.0":
        raise LockValidationError("expected PEP 751 lock-version 1.0")
    packages = lock_data.get("packages")
    if not isinstance(packages, list) or not packages:
        raise LockValidationError("pylock has no packages")

    active_names: set[str] = set()
    allowed_sdists: set[str] = set()
    problems: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            problems.append("package entry is not a table")
            continue
        try:
            is_active = _package_is_active(package, target, python, profile)
        except LockValidationError as exc:
            problems.append(str(exc))
            continue
        if not is_active:
            continue

        raw_name = package.get("name")
        version = package.get("version")
        if not isinstance(raw_name, str) or not isinstance(version, str):
            problems.append("active package is missing a string name/version")
            continue
        name = canonicalize_name(raw_name)
        if name in active_names:
            problems.append(f"{name}: multiple versions are active in one target cell")
            continue
        active_names.add(name)

        wheels = package.get("wheels", [])
        if isinstance(wheels, list) and any(
            isinstance(wheel, dict) and _wheel_is_compatible(wheel, target, python)
            for wheel in wheels
        ):
            continue

        exception = _matching_sdist_exception(package, target, python, profile)
        if exception is not None:
            allowed_sdists.add(canonicalize_name(exception.name))
            continue

        artifact_hint = "no wheel"
        if wheels:
            artifact_hint = "no compatible public-PyPI wheel with a SHA-256 hash"
        problems.append(
            f"{name}=={version}: {artifact_hint}; source build is not an audited exception"
        )

    if not active_names:
        problems.append("no package markers are active for this target")
    if problems:
        raise LockValidationError("; ".join(problems))
    return frozenset(allowed_sdists)


def _materialize_target_requirements(
    lock_data: dict[str, Any],
    output: Path,
    target: Target,
    python: str,
    *,
    profile: str,
) -> frozenset[str]:
    """Project one validated pylock cell into exact hashed requirements.

    ``uv pip install`` currently checks a PEP 751 lock's top-level
    ``requires-python`` against the locally discovered interpreter, even when
    ``--python-version`` and ``--python-platform`` describe a different target.
    That makes a CPython 3.13/3.14 dry-run fail on a CPython 3.12 CI host after
    target resolution and wheel validation have already succeeded.

    An ordinary requirements file does honor the explicit target flags. Keep
    the pylock as the artifact proof, then project only that cell's active,
    exact pins and compatible artifact hashes for the install dry-run. The
    resulting plan cannot float to another version or artifact, and uv still
    validates package metadata and the complete dependency graph.
    """

    allowed_sdists = validate_pylock(
        lock_data,
        target,
        python,
        profile=profile,
    )
    requirements: list[tuple[str, str, tuple[str, ...]]] = []
    packages = lock_data.get("packages")
    if not isinstance(packages, list):  # validate_pylock already rejects this
        raise LockValidationError("pylock has no packages")

    for package in packages:
        if not isinstance(package, dict) or not _package_is_active(
            package,
            target,
            python,
            profile,
        ):
            continue
        raw_name = package.get("name")
        version = package.get("version")
        if not isinstance(raw_name, str) or not isinstance(version, str):
            raise LockValidationError("active package is missing a string name/version")
        name = canonicalize_name(raw_name)

        hashes = {
            digest
            for wheel in package.get("wheels", [])
            if isinstance(wheel, dict) and _wheel_is_compatible(wheel, target, python)
            if (digest := _artifact_sha256(wheel)) is not None
        }
        if not hashes:
            exception = _matching_sdist_exception(package, target, python, profile)
            sdist = package.get("sdist")
            if exception is not None and isinstance(sdist, dict):
                digest = _artifact_sha256(sdist)
                if digest is not None:
                    hashes.add(digest)
        if not hashes:  # defensive: validation above should make this unreachable
            raise LockValidationError(f"{name}=={version}: no validated artifact hash")
        requirements.append((name, version, tuple(sorted(hashes))))

    lines = [
        "# Target-specific install plan projected from a validated PEP 751 lock.",
    ]
    for name, version, hashes in sorted(requirements):
        hash_flags = " ".join(f"--hash=sha256:{digest}" for digest in hashes)
        lines.append(f"{name}=={version} {hash_flags}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return allowed_sdists


def _load_pylock(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LockValidationError(f"cannot read {path.name}: {exc}") from exc


def _compile_command(
    output: Path,
    target: Target,
    python: str,
    *,
    profile: str,
) -> list[str]:
    sources = [str(REQUIREMENTS_TXT)]
    extra_flags: list[str] = []
    if profile == "full":
        sources.append(str(PYPROJECT))
        extra_flags = ["--extra", "full", "--no-emit-package", "personal-jarvis"]
    return [
        "uv",
        "pip",
        "compile",
        *sources,
        *extra_flags,
        "--format",
        "pylock.toml",
        "--output-file",
        str(output),
        "--python-version",
        python,
        "--python-platform",
        target.uv_platform,
        "--no-sources",
        *_public_uv_flags(),
    ]


def _install_command(
    requirement: Path,
    install_target: Path,
    target: Target,
    python: str,
    *,
    require_hashes: bool,
    allowed_sdists: frozenset[str],
) -> list[str]:
    command = [
        "uv",
        "pip",
        "install",
        "--dry-run",
        "--target",
        str(install_target),
        "--python-version",
        python,
        "--python-platform",
        target.uv_platform,
        "--only-binary",
        ":all:",
    ]
    if require_hashes:
        command.append("--require-hashes")
    for name in sorted(allowed_sdists):
        command.extend(("--no-binary", name))
    command.extend(("--requirements", str(requirement), *_public_uv_flags()))
    return command


def _command_failure(phase: str, result: CommandResult) -> str:
    output = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
    lines = output.splitlines()
    tail = "\n".join(lines[-24:]) if lines else "uv returned no diagnostic output"
    return f"{phase} failed with exit {result.returncode}:\n{tail}"


def _check_cell(target: Target, python: str, workspace: Path) -> CellResult:
    started = time.monotonic()
    label = f"{target.key}-cp{python.replace('.', '')}"
    cell_dir = workspace / label
    cell_dir.mkdir(parents=True, exist_ok=False)
    errors: list[str] = []

    locks: dict[str, Path] = {}
    install_requirements: dict[str, Path] = {}
    allowed: dict[str, frozenset[str]] = {}
    for profile in ("base", "full"):
        lock_path = cell_dir / f"pylock.{profile}.toml"
        locks[profile] = lock_path
        result = _run_command(
            _compile_command(lock_path, target, python, profile=profile),
            cwd=REPO_ROOT,
        )
        if result.returncode:
            errors.append(_command_failure(f"{profile} target resolution", result))
            break
        try:
            lock_data = _load_pylock(lock_path)
            if profile == "full":
                requirement_path = cell_dir / "requirements.full.txt"
                install_requirements[profile] = requirement_path
                allowed[profile] = _materialize_target_requirements(
                    lock_data,
                    requirement_path,
                    target,
                    python,
                    profile=profile,
                )
            else:
                allowed[profile] = validate_pylock(
                    lock_data,
                    target,
                    python,
                    profile=profile,
                )
        except LockValidationError as exc:
            errors.append(f"{profile} artifact validation failed: {exc}")
            break

    if not errors:
        base_result = _run_command(
            _install_command(
                REQUIREMENTS_TXT,
                cell_dir / "base-install",
                target,
                python,
                require_hashes=True,
                allowed_sdists=allowed["base"],
            ),
            cwd=REPO_ROOT,
        )
        if base_result.returncode:
            errors.append(_command_failure("hash-pinned base dry-run", base_result))

    if not errors:
        full_result = _run_command(
            _install_command(
                install_requirements["full"],
                cell_dir / "full-install",
                target,
                python,
                require_hashes=True,
                allowed_sdists=allowed["full"],
            ),
            cwd=REPO_ROOT,
        )
        if full_result.returncode:
            errors.append(_command_failure("full-profile dry-run", full_result))

    return CellResult(target, python, time.monotonic() - started, tuple(errors))


def _verify_uv_version() -> str | None:
    result = _run_command(["uv", "--version"], cwd=REPO_ROOT, timeout=30)
    if result.returncode:
        return _command_failure("uv availability check", result)
    match = re.fullmatch(r"uv\s+(\d+\.\d+\.\d+)(?:\s+.*)?", result.stdout.strip())
    if match is None:
        return f"uv availability check returned an unrecognized version: {result.stdout.strip()!r}"
    actual = match.group(1)
    if actual != EXPECTED_UV_VERSION:
        return f"uv {EXPECTED_UV_VERSION} is required; found uv {actual}"
    return None


def _verify_uv_lock() -> str | None:
    result = _run_command(
        ["uv", "lock", "--check", *_public_uv_flags()],
        cwd=REPO_ROOT,
    )
    return _command_failure("uv lock --check", result) if result.returncode else None


def _requirements_diff(expected: bytes, actual: bytes) -> str:
    expected_lines = expected.decode("utf-8", errors="replace").splitlines()
    actual_lines = actual.decode("utf-8", errors="replace").splitlines()
    diff = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile="requirements.txt",
            tofile="no-upgrade universal recompile",
            lineterm="",
        )
    )
    return "\n".join(diff[:80]) or "files differ at the byte level"


def _verify_reproducible_requirements() -> str | None:
    with tempfile.TemporaryDirectory(prefix="jarvis-requirements-recompile-") as raw_dir:
        workspace = Path(raw_dir)
        generated = workspace / "requirements.txt"
        shutil.copyfile(REQUIREMENTS_IN, workspace / "requirements.in")
        # Seeding the output is load-bearing: without --upgrade, uv reuses these
        # exact pins and proves a no-upgrade maintenance recompile is stable.
        shutil.copyfile(REQUIREMENTS_TXT, generated)
        result = _run_command(
            [
                "uv",
                "pip",
                "compile",
                "--universal",
                "--generate-hashes",
                "--python-version",
                "3.11",
                "--output-file=requirements.txt",
                "requirements.in",
            ],
            cwd=workspace,
        )
        if result.returncode:
            return _command_failure("no-upgrade universal requirements recompile", result)
        expected = REQUIREMENTS_TXT.read_bytes()
        actual = generated.read_bytes()
        if actual != expected:
            return (
                "requirements.txt is not a byte-identical no-upgrade universal/hash recompile.\n"
                + _requirements_diff(expected, actual)
            )
    return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Maximum concurrent target cells (default: {DEFAULT_JOBS}, maximum: 8).",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 8:
        parser.error("--jobs must be between 1 and 8")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if _PACKAGING_IMPORT_ERROR is not None:
        print(
            "FAIL: packaging is required for marker and wheel validation; "
            f"import failed: {_PACKAGING_IMPORT_ERROR}"
        )
        return 1
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in (REQUIREMENTS_IN, REQUIREMENTS_TXT, PYPROJECT, UV_LOCK)
        if not path.is_file()
    ]
    if missing:
        print("FAIL: required dependency artifacts are missing: " + ", ".join(missing))
        return 1

    print("Checking pinned tooling, project lock freshness, and reproducible hash lock...")
    for check in (_verify_uv_version, _verify_uv_lock, _verify_reproducible_requirements):
        problem = check()
        if problem is not None:
            print(f"FAIL: {problem}")
            return 1

    cells = [(target, python) for target in TARGETS for python in PYTHON_VERSIONS]
    print(
        f"Resolving and dry-running {len(cells)} advertised target cells "
        f"with at most {args.jobs} concurrent uv workers..."
    )
    with tempfile.TemporaryDirectory(prefix="jarvis-portable-install-") as raw_workspace:
        workspace = Path(raw_workspace)
        results: list[CellResult] = []
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(_check_cell, target, python, workspace): (target, python)
                for target, python in cells
            }
            for future in as_completed(futures):
                target, python = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - fail-closed safety net
                    result = CellResult(target, python, 0.0, (f"unexpected gate error: {exc}",))
                results.append(result)
                state = "OK" if result.ok else "FAIL"
                print(f"[{state}] {result.label} ({result.elapsed_seconds:.1f}s)")

    order = {(target.key, python): index for index, (target, python) in enumerate(cells)}
    failures = sorted(
        (result for result in results if not result.ok),
        key=lambda result: order[(result.target.key, result.python)],
    )
    if failures:
        print("\nFAIL: advertised installer matrix is not compiler-free and reproducible.")
        for result in failures:
            print(f"\n--- {result.label} ---")
            print("\n".join(result.errors))
        return 1

    print(
        "OK: base hashes and the full profile resolve from public PyPI with compatible "
        "artifacts on all 24 advertised CPython/OS/architecture cells."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

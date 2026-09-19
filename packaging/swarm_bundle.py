"""Shared frozen Swarm dependency manifest; never imported by the application.

Source installations keep distributed clients optional. A frozen application
cannot install Python extras, so every native build must carry these clients,
their metadata, TLS roots, S3 models and the binary PostgreSQL client. Merely
collecting ``psycopg_binary`` misses wheel libraries in its sibling ``.libs``
directory on Windows and Linux.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import PurePosixPath
from typing import Any

MODULES = (
    "wasmtime",
    "psycopg",
    "psycopg_pool",
    "redis",
    "boto3",
    "botocore",
    "s3transfer",
    "certifi",
)
DISTRIBUTIONS = (
    "wasmtime",
    "psycopg",
    "psycopg-binary",
    "psycopg-pool",
    "redis",
    "boto3",
    "botocore",
    "s3transfer",
    "certifi",
)
MINIMUM_VERSIONS = {
    "wasmtime": "48.0.0",
    "psycopg": "3.3.5",
    "psycopg-binary": "3.3.5",
    "psycopg-pool": "3.3.1",
    "redis": "7.4.1",
    "boto3": "1.43.91",
    "certifi": "2026.7.22",
}


def collect_swarm_bundle(hooks: Any, *, distribution_reader: Any = distribution) -> dict:
    """Return PyInstaller inputs or fail before producing an incomplete bundle."""
    from packaging.version import Version

    datas: list[tuple[str, str]] = []
    binaries: list[tuple[str, str]] = []
    hiddenimports: list[str] = []
    try:
        for name in DISTRIBUTIONS:
            installed = distribution_reader(name)
            minimum = MINIMUM_VERSIONS.get(name)
            if minimum and Version(installed.version) < Version(minimum):
                raise SystemExit(
                    f"Frozen Swarm requires {name}>={minimum}; install the build "
                    "environment's swarm-distributed extra and retry."
                )
            datas += hooks.copy_metadata(name)
        binary_wheel = distribution_reader("psycopg-binary")
    except PackageNotFoundError as exc:
        raise SystemExit(
            "Frozen Swarm dependencies are missing. Install the build environment "
            "with pip install -e '.[desktop,dev,swarm-distributed]' and retry."
        ) from exc

    for name in MODULES:
        hiddenimports += hooks.collect_submodules(name, on_error="raise")
    # Importing psycopg_binary directly for discovery is unsupported by psycopg;
    # its extension modules are reached only after psycopg has initialized.
    hiddenimports += ["psycopg_binary", "psycopg_binary.pq", "psycopg_binary._psycopg"]
    for name in ("certifi", "boto3", "botocore"):
        datas += hooks.collect_data_files(name)
    binaries += hooks.collect_dynamic_libs("wasmtime")

    postgres_binaries = []
    for entry in binary_wheel.files or ():
        path = PurePosixPath(str(entry).replace("\\", "/"))
        if (
            ".." in path.parts
            or not path.parts
            or path.parts[0] not in {"psycopg_binary", "psycopg_binary.libs"}
        ):
            continue
        # Preserve wheel layout for both auditwheel and delvewheel repairs.
        # Python extensions are collected by Analysis through hiddenimports.
        if path.suffix.lower() in {".dll", ".dylib", ".so"} or ".so." in path.name:
            postgres_binaries.append((str(binary_wheel.locate_file(entry)), str(path.parent)))
        elif path.name.startswith(".load-order-"):
            datas.append((str(binary_wheel.locate_file(entry)), str(path.parent)))
    if not binaries or not postgres_binaries:
        raise SystemExit("Frozen Swarm native libraries were not found in the build environment")
    binaries += postgres_binaries
    return {"datas": datas, "binaries": binaries, "hiddenimports": hiddenimports}

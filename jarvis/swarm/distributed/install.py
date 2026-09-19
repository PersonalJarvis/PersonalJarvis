"""Trusted, explicit in-app setup of optional distributed client dependencies."""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import tempfile
import threading
from importlib.metadata import PackageNotFoundError, version

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.swarm.store import SwarmStoreError

PACKAGES = (
    "psycopg[binary,pool]==3.3.5",
    "psycopg-pool==3.3.1",
    "redis==7.4.1",
    "boto3==1.43.91",
    "certifi==2026.7.22",
)
_IMPORTS = {
    "psycopg": ("psycopg", "3.3.5"),
    "psycopg_binary": ("psycopg-binary", "3.3.5"),
    "psycopg_pool": ("psycopg-pool", "3.3.1"),
    "redis": ("redis", "7.4.1"),
    "boto3": ("boto3", "1.43.91"),
    "certifi": ("certifi", "2026.7.22"),
}
_INSTALL_LOCK = threading.Lock()


def driver_readiness() -> dict:
    """Read metadata only; no connections and no optional native initialization."""
    from packaging.version import Version

    missing = []
    for module, (distribution, minimum) in _IMPORTS.items():
        try:
            if importlib.util.find_spec(module) is None or Version(version(distribution)) < Version(
                minimum
            ):
                missing.append(distribution)
        except (ImportError, PackageNotFoundError, ValueError):
            missing.append(distribution)
    return {
        "available": not missing,
        "missing": missing,
        "extra": "swarm-distributed",
        "can_install": not bool(getattr(sys, "frozen", False)),
    }


def ensure_dependencies() -> dict:
    """Install only after explicit authenticated distributed setup, never at boot."""
    with _INSTALL_LOCK:
        readiness = driver_readiness()
        if readiness["available"]:
            return readiness
        if not readiness["can_install"]:
            raise SwarmStoreError(
                "This application build is missing distributed drivers; install its updated build"
            )
        try:
            # Fixed package versions and no shell: endpoint settings, worker text
            # and credentials cannot become process arguments.
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        "--only-binary=:all:",
                        *PACKAGES,
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                    check=False,
                )
            if completed.returncode:
                raise SwarmStoreError(
                    "Distributed driver installation failed; retry setup when package "
                    "downloads are available"
                )
        except (OSError, subprocess.TimeoutExpired):
            raise SwarmStoreError(
                "Distributed driver installation failed or timed out; retry setup"
            ) from None
        importlib.invalidate_caches()
        readiness = driver_readiness()
        if not readiness["available"]:
            raise SwarmStoreError("Distributed driver installation is incomplete; retry setup")
        return readiness

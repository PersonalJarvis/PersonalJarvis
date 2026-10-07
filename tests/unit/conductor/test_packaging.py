"""Conductor ships in the distribution, data files included.

``conductor`` was missing from ``packages.find``: it imported only when the
app's working directory was the checkout (run.bat), so the console script,
autostart and every wheel install served no ``/api/conductor/*`` at all and
``jarvis conductor ...`` answered 404.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

from setuptools import find_packages

ROOT = Path(__file__).resolve().parents[3]


def _setuptools_cfg() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["tool"]["setuptools"]


def test_conductor_packages_are_distributed() -> None:
    find = _setuptools_cfg()["packages"]["find"]
    packages = set(
        find_packages(
            where=str(ROOT / find.get("where", ["."])[0]),
            include=find["include"],
            exclude=find.get("exclude", []),
        )
    )
    assert {"conductor", "conductor.api", "conductor.core", "conductor.jobs"} <= packages


def test_conductor_runtime_data_files_are_declared() -> None:
    patterns = _setuptools_cfg()["package-data"]["conductor"]
    data_files = [
        path.relative_to(ROOT / "conductor").as_posix()
        for path in (ROOT / "conductor").rglob("*")
        if path.is_file() and path.suffix in {".sql", ".yaml"}
    ]
    assert data_files
    undeclared = [
        name for name in data_files if not any(fnmatch.fnmatch(name, p) for p in patterns)
    ]
    assert undeclared == []

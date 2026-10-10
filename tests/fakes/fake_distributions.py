"""Small installed-package layouts for packaging tests; nothing is installed."""

from __future__ import annotations

import csv
import io
from importlib.metadata import PathDistribution
from pathlib import Path


def installed_distribution(
    site_packages: Path,
    name: str,
    version: str,
    files: dict[str, bytes],
    *,
    declared_licenses: tuple[str, ...] = (),
    extra_records: tuple[str, ...] = (),
    file_list: bool = True,
) -> PathDistribution:
    """Create real dist-info metadata around caller-owned fake package files."""
    info = site_packages / f"{name}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    headers = [f"Name: {name}", f"Version: {version}", "License-Expression: MIT"]
    headers.extend(f"License-File: {value}" for value in declared_licenses)
    (info / "METADATA").write_text("\n".join(headers) + "\n", encoding="utf-8")
    for relative, contents in files.items():
        path = site_packages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    if file_list:
        record = io.StringIO(newline="")
        writer = csv.writer(record, lineterminator="\n")
        writer.writerows((relative, "", "") for relative in [*files, *extra_records])
        (info / "RECORD").write_text(record.getvalue(), encoding="utf-8")
    return PathDistribution(info)

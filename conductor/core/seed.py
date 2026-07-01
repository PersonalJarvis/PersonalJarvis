"""Load seed jobs — YAML files from ``conductor/seed/``.

Unlike Jarvis workflows (hardcoded Python factories), we maintain the
Conductor seeds as **YAML files**. That's the same job format users
type in themselves — we're our own test case for it.

Idempotency: each seed job's UUID lives in the YAML and is respected
across repeated calls.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .schema import Job
from .store import ConductorStore

log = logging.getLogger(__name__)

SEED_YAML_DIR = Path(__file__).resolve().parent.parent / "seed"


async def load_job_from_yaml(yaml_path: Path) -> Job:
    """Parses a YAML file into a validated ``Job`` model."""
    data: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return Job.model_validate(data)


async def ensure_seed_jobs(
    store: ConductorStore,
    seed_dir: Path | None = None,
    *,
    force: bool = False,
) -> int:
    """Reads all ``*.yaml`` files from ``seed_dir`` and upserts jobs.

    Args:
        store: The persistence store.
        seed_dir: Override for the seed folder.
        force: If True, existing jobs are overwritten (useful after a
            seed YAML update). Default False: create missing jobs,
            leave existing ones alone.

    Returns the number of jobs actually written.
    """
    root = seed_dir or SEED_YAML_DIR
    if not root.exists():
        return 0
    added = 0
    for yaml_file in sorted(root.glob("*.yaml")):
        try:
            job = await load_job_from_yaml(yaml_file)
        except Exception as exc:  # noqa: BLE001
            log.warning("Seed YAML %s ignored: %s", yaml_file.name, exc)
            continue
        existing = await store.get_job(str(job.id))
        if existing is not None and not force:
            continue
        await store.upsert_job(job)
        added += 1
    if added:
        log.info("Conductor seed: %d jobs %s from %s",
                 added, "force-updated" if force else "created", root)
    return added

"""Seed-Jobs laden — YAML-Dateien aus ``conductor/seed/``.

Anders als bei Jarvis-Workflows (hardcoded Python-Factories) pflegen wir
die Conductor-Seeds als **YAML-Dateien**. Das ist der Job-Format, das
auch User eintippen — wir sind damit unser eigener Testfall.

Idempotenz: Die UUID jedes Seed-Jobs steht im YAML und wird bei
wiederholtem Aufruf respektiert.
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
    """Parsed eine YAML-Datei in ein validiertes ``Job``-Modell."""
    data: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return Job.model_validate(data)


async def ensure_seed_jobs(
    store: ConductorStore,
    seed_dir: Path | None = None,
    *,
    force: bool = False,
) -> int:
    """Liest alle ``*.yaml`` aus ``seed_dir`` und upsert-ed Jobs.

    Args:
        store: Persistenz-Store.
        seed_dir: Override fuer den Seed-Ordner.
        force: Wenn True, bestehende Jobs werden ueberschrieben (nuetzlich
            nach einem Seed-YAML-Update). Default False: fehlende Jobs
            anlegen, bestehende in Ruhe lassen.

    Returnt die Anzahl tatsaechlich geschriebener Jobs.
    """
    root = seed_dir or SEED_YAML_DIR
    if not root.exists():
        return 0
    added = 0
    for yaml_file in sorted(root.glob("*.yaml")):
        try:
            job = await load_job_from_yaml(yaml_file)
        except Exception as exc:  # noqa: BLE001
            log.warning("Seed-YAML %s ignoriert: %s", yaml_file.name, exc)
            continue
        existing = await store.get_job(str(job.id))
        if existing is not None and not force:
            continue
        await store.upsert_job(job)
        added += 1
    if added:
        log.info("Conductor-Seed: %d Jobs %s aus %s",
                 added, "force-upgedatet" if force else "angelegt", root)
    return added

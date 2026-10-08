"""The runtime versions Jarvis installs, updates to and accepts.

``runtime-versions.json`` (next to this module) names, per runtime:

* ``minimum`` — older installs are not ready and get updated;
* ``tested`` — the release the agent-runtimes canary verified on all three
  OSes. Setup and the daily update go to exactly this release, never to
  upstream latest; a newer install still runs but is reported as untested;
* ``commit`` (Hermes) — the source commit of ``tested`` for its installer;
* ``config_version`` (Hermes) — the config schema ``tested`` writes, so Hermes
  migrates Jarvis' config forward instead of calling it unmigratable.

A last-good record per runtime (``<runtimes_root>/<runtime>-last-good.json``)
remembers the release that last finished a setup ready, for rollback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

from jarvis.agent_runtimes import base
from jarvis.agent_runtimes.base import format_version, parse_version, write_json_if_changed

log = logging.getLogger(__name__)

_PINS_FILE: Final[Path] = Path(__file__).with_name("runtime-versions.json")

#: Used only when the pins file is missing from a broken package: readiness
#: still gates on these, and setup falls back to the projects' latest release.
_FALLBACK_MINIMUM: Final[dict[str, str]] = {"hermes": "0.20.6", "openclaw": "2026.9.8"}


@dataclass(frozen=True, slots=True)
class Pin:
    runtime: str
    minimum: tuple[int, int, int]
    #: ``None`` when the pins file could not be read.
    tested: tuple[int, int, int] | None
    commit: str = ""
    config_version: int | None = None

    @property
    def tested_text(self) -> str:
        return format_version(self.tested) if self.tested else ""


def _version(raw: Any, field: str, runtime: str) -> tuple[int, int, int]:
    parsed = parse_version(str(raw or ""))
    if parsed is None:
        raise ValueError(f"runtime-versions.json: {runtime}.{field} is not a version")
    return parsed


@cache
def pin(runtime: str) -> Pin:
    """The pinned versions of ``runtime``."""
    try:
        data = json.loads(_PINS_FILE.read_text(encoding="utf-8"))
        row = data[runtime]
        config_version = row.get("config_version")
        return Pin(
            runtime,
            minimum=_version(row.get("minimum"), "minimum", runtime),
            tested=_version(row.get("tested"), "tested", runtime),
            commit=str(row.get("commit") or ""),
            config_version=int(config_version) if config_version is not None else None,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("agent runtimes: pinned versions unavailable (%s); using minimums only", exc)
        minimum = parse_version(_FALLBACK_MINIMUM.get(runtime, "0.0.0")) or (0, 0, 0)
        return Pin(runtime, minimum=minimum, tested=None)


def _last_good_path(runtime: str) -> Path:
    return base.runtimes_root() / f"{runtime}-last-good.json"


def last_good(runtime: str) -> dict[str, str] | None:
    """The newest release that finished a setup ready (``version``, ``revision``).

    ``revision`` is what the driver's installer reinstalls it from (a Hermes
    commit, an OpenClaw release).
    """
    try:
        data = json.loads(_last_good_path(runtime).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no record yet: nothing to roll back to
        return None
    return data if isinstance(data, dict) and data.get("version") else None


def remember_good(runtime: str, version: str, revision: str = "") -> None:
    """Record ``version`` as the runtime's last good release."""
    if not version:
        return
    path = _last_good_path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_if_changed(path, {"version": version, "revision": revision})

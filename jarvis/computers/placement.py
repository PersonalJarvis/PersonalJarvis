"""Where "Automatic" sends a new IDE workspace: weighted over this PC and computers.

Portions adapted from pingdotgg/t3code @ 12069ee (apps/web
LoadBalancingSettings.tsx: one share per switched-on machine, offered once at
least two machines can take work), MIT License, Copyright (c) 2026 T3 Tools
Inc. Full text: third_party/t3code/LICENSE.

Opt-in: off by default. When on, the new-workspace dialog offers "Automatic"
and :func:`pick` chooses among this PC and every switched-on computer that is
online, in proportion to each one's share (never / less / normal / more). A
computer's share lives on its record (``placement_weight``); this PC's share
and the switch live in ``<user data>/computers/placement.json``.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from jarvis.computers.models import Computer
from jarvis.core.paths import user_data_dir

log = logging.getLogger(__name__)

#: Share level -> relative weight. "More" is twice "normal", "less" half.
WEIGHTS: dict[int, int] = {0: 0, 1: 1, 2: 2, 3: 4}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class PlacementSettings:
    enabled: bool = False
    #: This PC's share, on the same 0-3 scale as a computer's.
    local_weight: int = 2


def _path() -> Path:
    return user_data_dir() / "computers" / "placement.json"


def load() -> PlacementSettings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:  # never set: the feature is off
        return PlacementSettings()
    except (OSError, ValueError) as exc:
        log.warning("computers: unreadable placement settings: %s", exc)
        return PlacementSettings()
    if not isinstance(data, dict):
        return PlacementSettings()
    weight = data.get("local_weight", 2)
    return PlacementSettings(
        enabled=bool(data.get("enabled", False)),
        local_weight=weight if isinstance(weight, int) and 0 <= weight <= 3 else 2,
    )


def save(settings: PlacementSettings) -> PlacementSettings:
    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".placement.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(settings), handle)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return settings


def candidates(
    settings: PlacementSettings, computers: list[Computer]
) -> list[tuple[str | None, int]]:
    """``(computer id or None for this PC, weight)`` for every machine that may take work."""
    pool: list[tuple[str | None, int]] = []
    if WEIGHTS[settings.local_weight]:
        pool.append((None, WEIGHTS[settings.local_weight]))
    for computer in computers:
        weight = WEIGHTS.get(computer.placement_weight, 0)
        if computer.enabled and weight and computer.health.status == "online":
            pool.append((computer.id, weight))
    return pool


def pick(
    settings: PlacementSettings, computers: list[Computer], *, rng: random.Random | None = None
) -> str | None:
    """The machine a new workspace goes to; ``None`` is this PC.

    With nothing else to choose (every computer off, offline or at "never"),
    the answer is this PC, whatever its own share says.
    """
    pool = candidates(settings, computers)
    if not pool:
        return None
    chooser = rng or random.SystemRandom()
    ids = [machine for machine, _weight in pool]
    weights = [weight for _machine, weight in pool]
    return chooser.choices(ids, weights=weights, k=1)[0]

"""The person's saved companion designs: colour skins kept for reuse.

A design is a :class:`~jarvis.society.companion.CompanionSkin` (colours, how
they lie on the body, an effect) the person saved from the look dialog or
imported from a design code, a CSS gradient or a picture. Any agent can then
wear it with one click. One small JSON file in the user data folder; an entry
that no longer validates (hand-edited, or written by a newer build with a field
this one does not know) is skipped, never fatal.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError

from jarvis.society.companion import CompanionSkin

#: Plenty for a personal collection, small enough to list in one answer.
MAX_DESIGNS = 60

_lock = threading.Lock()


def _path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "society" / "designs.json"


def _read() -> list[dict[str, Any]]:
    path = _path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:  # nothing saved yet
        return []
    except (OSError, ValueError) as exc:
        logger.warning("designs: unreadable {}: {}", path, exc)
        return []
    rows = raw.get("designs") if isinstance(raw, dict) else None
    designs: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        try:
            skin = CompanionSkin.model_validate(row.get("skin")).model_dump()
        except ValidationError as exc:
            logger.warning(
                "designs: skipping invalid design {}: {}", row.get("id"), exc.errors()[:1]
            )
            continue
        created = row.get("created")
        designs.append({
            "id": row["id"],
            "skin": skin,
            "created": created if isinstance(created, (int, float)) else 0,
        })
    return designs[:MAX_DESIGNS]


def _write(designs: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"designs": designs}, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def list_designs() -> list[dict[str, Any]]:
    """Saved designs, newest first."""
    with _lock:
        return _read()


def save_design(skin: CompanionSkin) -> dict[str, Any]:
    """Store ``skin`` as a new design at the front; the oldest drop off past the cap.

    Saving the same colours, layout and effect again moves the existing entry
    to the front under the new name instead of adding a duplicate.
    """
    entry = skin.model_dump()
    with _lock:
        designs = _read()
        same = next((d for d in designs if _look(d["skin"]) == _look(entry)), None)
        design = {
            "id": same["id"] if same else f"d{secrets.token_hex(6)}",
            "skin": entry,
            "created": time.time(),
        }
        designs = [design, *(d for d in designs if d is not same)][:MAX_DESIGNS]
        _write(designs)
    return design


def delete_design(design_id: str) -> bool:
    """Remove one saved design; False when it was not there."""
    with _lock:
        designs = _read()
        kept = [d for d in designs if d["id"] != design_id]
        if len(kept) == len(designs):
            return False
        _write(kept)
    return True


def _look(skin: dict[str, Any]) -> tuple[Any, ...]:
    colors = tuple(c.lower() for c in skin["colors"])
    return (colors, skin["pattern"], skin["angle"], skin["effect"])

"""Where an installed skill came from.

A ``SKILL.md`` downloaded from the marketplace is byte-identical to one the
owner wrote: same frontmatter, same body, nothing in it says "installed". The
Skills view still has to be able to show it — so the install path drops a
small receipt next to the file, and the listing reads it back.

The receipt is a sidecar in the skill's own folder rather than a central index
because deleting the folder must delete the fact: a shared index would keep
claiming an origin for a skill that no longer exists, and two installs racing
would fight over one file. ``.marketplace.json`` starts with a dot and sits
beside ``SKILL.md``, not inside ``references/`` or ``scripts/``, so the loader's
resource scan never picks it up as bundle content.

Never raises: an unreadable or hand-mangled receipt costs the badge, never the
skill.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Sidecar file name, beside SKILL.md inside the skill folder.
RECEIPT_NAME = ".marketplace.json"


@dataclass(frozen=True)
class SkillOrigin:
    """The published entry a skill was installed from."""

    source: str = "marketplace"
    source_id: str = ""
    publisher: str | None = None
    version: str | None = None
    source_url: str | None = None
    installed_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def receipt_path(skill_root: Path) -> Path:
    return Path(skill_root) / RECEIPT_NAME


def write_origin(skill_root: Path, origin: SkillOrigin) -> None:
    """Record where this skill came from. Failure is logged, never raised.

    A skill that installed fine but could not write its receipt is still a
    working skill; losing the install over a badge would be the wrong trade.
    """
    try:
        receipt_path(skill_root).write_text(
            json.dumps(origin.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("skill origin receipt not written for %s: %s", skill_root, exc)


def read_origin(skill_root: Path) -> SkillOrigin | None:
    """The recorded origin, or None for a skill nobody installed."""
    path = receipt_path(skill_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.debug("skill origin receipt unreadable at %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None

    def _opt(key: str) -> str | None:
        text = str(raw.get(key) or "").strip()
        return text[:200] or None

    source_id = str(raw.get("source_id") or "").strip()[:120]
    if not source_id:
        return None
    return SkillOrigin(
        source=str(raw.get("source") or "marketplace").strip()[:40] or "marketplace",
        source_id=source_id,
        publisher=_opt("publisher"),
        version=_opt("version"),
        source_url=_opt("source_url"),
        installed_at=_opt("installed_at"),
    )

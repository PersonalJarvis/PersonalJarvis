"""Soul — Jarvis' own persona (SOUL.md).

Separate from UserProfile because this is about the **agent**, not the user.
Contents: tone rules, humour calibration, boundaries.

The Curator may only append to the `## Kalibrierung` section here — tone rules
are editorial (set manually by the user or by the bootstrap ritual).
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import append_to_section, parse_frontmatter, write_frontmatter

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 1000


@dataclass
class Soul:
    path: Path
    _meta: dict[str, Any] = field(default_factory=dict)
    _body: str = ""

    @classmethod
    def load(cls, path: str | Path) -> Soul:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"SOUL.md fehlt: {p}")
        text = p.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        return cls(path=p, _meta=meta, _body=body)

    def save(self) -> None:
        self._meta["last_updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        text = write_frontmatter(self._meta, self._body)
        dir_ = self.path.parent
        fd, tmp_path = tempfile.mkstemp(prefix=".SOUL.md.", suffix=".tmp", dir=str(dir_))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def append_calibration(self, note: str) -> None:
        """Appends a calibration note — e.g. 'User responds positively to dry humour'."""
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        line = f"- [{date}] {note.strip()}"
        self._body = append_to_section(self._body, "calibration", line)

    def render_for_prompt(self, *, max_chars: int = MAX_PROMPT_CHARS) -> str:
        """Compact version for the system prompt.

        We include only the tone rules + boundaries (calibration is internal
        metadata). Budget-safe.
        """
        # Extract the first section up to and including "## Grenzen"
        lines = self._body.splitlines()
        out: list[str] = ["## Jarvis Persona"]
        in_tone = False
        in_grenzen = False
        for line in lines:
            if line.startswith("## Tone-Regeln") or line.startswith("## Tone"):
                in_tone = True
                out.append("### Tone")
                continue
            if line.startswith("## Grenzen"):
                in_tone = False
                in_grenzen = True
                out.append("### Grenzen")
                continue
            if line.startswith("## Kalibrierung") or line.startswith("## Wer ich bin"):
                in_tone = False
                in_grenzen = False
                continue
            if line.startswith("## "):
                # Ignore other sections
                in_tone = False
                in_grenzen = False
                continue
            if in_tone or in_grenzen:
                stripped = line.strip()
                if stripped.startswith("-") or stripped.startswith("*"):
                    out.append(stripped)

        text = "\n".join(out)
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text

"""UserProfile — read/write/render handle for `USER.md`.

Central access point for everything Jarvis knows about the **user themselves**.
Other people go into `PersonStore` (people.py) — never here.

Schema: YAML frontmatter with 5 clusters (identity/communication/work_style/
values/relationship), followed by free Markdown sections with Curator markers.

Design decisions:

- **Atomic writes:** we write to a temp file and `rename()` it — so the
  process can be killed mid-write without corrupting USER.md.
- **Last-updated timestamp:** automatically updated on every persist.
  The field signals to the Curator "just re-read, don't edit again
  immediately" — anti-feedback-loop.
- **Render budget:** `render_for_prompt` caps at ~2000 characters. The prompt
  cache prefers stable content — we truncate only the dynamic observations
  when the budget is exceeded.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import append_to_section, parse_frontmatter, replace_section, write_frontmatter

log = logging.getLogger(__name__)


# Canonical clusters — from the research (see templates.py)
CLUSTERS = ("identity", "communication", "work_style", "values", "relationship")

# Section markers in the Markdown body
SECTIONS = ("context", "projects", "observations")

# Maximum size of the prompt block — cache-friendly
MAX_PROMPT_CHARS = 2000


@dataclass
class UserProfile:
    """Handle for USER.md. Thread-naive (single process)."""

    path: Path
    _meta: dict[str, Any] = field(default_factory=dict)
    _body: str = ""

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> UserProfile:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"USER.md fehlt: {p} — Workspace.ensure() vorher aufrufen."
            )
        text = p.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        return cls(path=p, _meta=meta, _body=body)

    def reload(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        self._meta, self._body = parse_frontmatter(text)

    def save(self) -> None:
        """Atomic write: temp → rename. No partially written USER.md possible."""
        self._meta["last_updated"] = _now_iso()
        text = write_frontmatter(self._meta, self._body)

        dir_ = self.path.parent
        fd, tmp_path = tempfile.mkstemp(
            prefix=".USER.md.", suffix=".tmp", dir=str(dir_)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            # os.replace is atomic on Windows within the same volume
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Strukturierte Felder (Frontmatter)
    # ------------------------------------------------------------------

    def get(self, cluster: str, field_name: str) -> Any:
        """Retrieves a field from a cluster. Returns None if not set."""
        if cluster not in CLUSTERS:
            raise ValueError(f"Unbekannter Cluster: {cluster}")
        return self._meta.get(cluster, {}).get(field_name)

    def set(self, cluster: str, field_name: str, value: Any) -> bool:
        """Sets a field. Returns True if anything changed."""
        if cluster not in CLUSTERS:
            raise ValueError(f"Unbekannter Cluster: {cluster}")
        sec = self._meta.setdefault(cluster, {})
        old = sec.get(field_name)
        if old == value:
            return False
        sec[field_name] = value
        return True

    def append_list(self, cluster: str, field_name: str, value: Any) -> bool:
        """Appends a value to a list. Deduplicates. Returns True if changed."""
        if cluster not in CLUSTERS:
            raise ValueError(f"Unbekannter Cluster: {cluster}")
        sec = self._meta.setdefault(cluster, {})
        current = sec.get(field_name)
        if not isinstance(current, list):
            current = []
        if value in current:
            return False
        current.append(value)
        sec[field_name] = current
        return True

    def clear(self, cluster: str, field_name: str) -> bool:
        """Removes a field entirely (the user 'forgets' it via the Profile UI).

        Drops the key so the field reads back as unset (``get`` → None) and the
        Knowledge matrix shows it as "not known yet" again. Returns True only if
        a real (non-empty) value was actually removed — clearing an already-empty
        field is a no-op (mirrors ``set``'s changed-or-not contract).
        """
        if cluster not in CLUSTERS:
            raise ValueError(f"Unbekannter Cluster: {cluster}")
        sec = self._meta.get(cluster)
        if not isinstance(sec, dict):
            return False
        old = sec.get(field_name)
        had_value = old is not None and old != "" and old != []
        sec.pop(field_name, None)
        return had_value

    def remove_list_item(self, cluster: str, field_name: str, value: Any) -> bool:
        """Removes a single item from a list field (the chip 'x').

        Returns True if the value was present and got removed. An emptied list is
        left as ``[]`` — which the UI renders as "not known yet" just like an
        unset field.
        """
        if cluster not in CLUSTERS:
            raise ValueError(f"Unbekannter Cluster: {cluster}")
        sec = self._meta.get(cluster)
        if not isinstance(sec, dict):
            return False
        current = sec.get(field_name)
        if not isinstance(current, list) or value not in current:
            return False
        sec[field_name] = [item for item in current if item != value]
        return True

    @property
    def meta(self) -> dict[str, Any]:
        return dict(self._meta)

    @property
    def name(self) -> str | None:
        return self.get("identity", "name")

    @property
    def preferred_address(self) -> str | None:
        return self.get("identity", "preferred_address") or self.name

    # ------------------------------------------------------------------
    # Markdown sections
    # ------------------------------------------------------------------

    def append_observation(self, field_label: str, value: str, evidence: str) -> None:
        """Appends a new observation to `## Observations over time`.

        Format: `- [YYYY-MM-DD] <field>: <value> — "<evidence>"`.
        """
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        quote = _truncate(evidence, 120).replace('"', "'")
        line = f'- [{date}] {field_label}: {value} — "{quote}"'
        self._body = append_to_section(self._body, "observations", line)

    def set_section(self, marker: str, content: str) -> None:
        """Replaces the context/projects/calibration section entirely."""
        self._body = replace_section(self._body, marker, content)

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def render_for_prompt(self, *, max_chars: int = MAX_PROMPT_CHARS) -> str:
        """Renders a compact Markdown block for the system prompt.

        Priority order:
        1. Name + form of address (most important info — must never be missing)
        2. Communication and relationship preferences (drives Jarvis' tone)
        3. Values + pet peeves
        4. Work style
        5. Last 5 observations (if budget remains)

        On budget overflow we cut from the bottom (observations first).
        """
        parts: list[str] = ["## About the User"]

        # 1. Identity
        ident = self._meta.get("identity", {}) or {}
        name = ident.get("name")
        if name:
            addr = ident.get("preferred_address") or name
            parts.append(f"- **Name:** {name} — address them as \"{addr}\"")
        if ident.get("pronouns"):
            parts.append(f"- **Pronouns:** {ident['pronouns']}")
        langs = ident.get("languages") or []
        if langs:
            primary = ident.get("primary_language", "de")
            parts.append(f"- **Languages:** {', '.join(langs)} (Primary: {primary})")
        if ident.get("timezone"):
            parts.append(f"- **Timezone:** {ident['timezone']}")

        # 2. Communication style — directly drives tone
        comm = self._meta.get("communication", {}) or {}
        comm_lines = []
        if comm.get("directness") is not None:
            comm_lines.append(f"Directness {comm['directness']}/5")
        if comm.get("formality") is not None:
            comm_lines.append(f"Formality {comm['formality']}/5")
        if comm.get("verbosity"):
            comm_lines.append(f"Verbosity={comm['verbosity']}")
        if comm.get("humor_types"):
            comm_lines.append(f"Humor={'+'.join(comm['humor_types'])}")
        if comm.get("emoji_ok") is False:
            comm_lines.append("NO emojis")
        if comm_lines:
            parts.append(f"- **Communication:** {', '.join(comm_lines)}")

        # 3. Values + pet peeves — short list
        vals = self._meta.get("values", {}) or {}
        if vals.get("top_values"):
            parts.append(f"- **Values:** {', '.join(vals['top_values'])}")
        if vals.get("pet_peeves"):
            parts.append(f"- **Pet Peeves:** {', '.join(vals['pet_peeves'])}")

        # 4. Work style
        ws = self._meta.get("work_style", {}) or {}
        ws_lines = []
        if ws.get("focus_mode"):
            ws_lines.append(f"Focus={ws['focus_mode']}")
        if ws.get("planning_horizon"):
            ws_lines.append(f"Horizon={ws['planning_horizon']}")
        if ws_lines:
            parts.append(f"- **Working style:** {', '.join(ws_lines)}")

        # 5. Relationship dynamics
        rel = self._meta.get("relationship", {}) or {}
        if rel.get("feedback_pref"):
            parts.append(f"- **Feedback style:** {rel['feedback_pref']}")

        base = "\n".join(parts)

        # 6. Observations, if budget remains
        remaining = max_chars - len(base)
        if remaining > 150:
            obs = _extract_last_observations(self._body, n=5)
            if obs:
                obs_block = "\n\n**Recent observations:**\n" + "\n".join(obs)
                if len(base) + len(obs_block) <= max_chars:
                    base += obs_block
                else:
                    # Observations could be truncated
                    base += obs_block[: remaining - 5] + "…"

        if len(base) > max_chars:
            base = base[: max_chars - 1] + "…"
        return base


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _extract_last_observations(body: str, n: int = 5) -> list[str]:
    """Extracts the last N observation lines from the body."""
    start = "<!-- curator:observations:start -->"
    end = "<!-- curator:observations:end -->"
    i = body.find(start)
    j = body.find(end)
    if i == -1 or j == -1 or j < i:
        return []
    block = body[i + len(start) : j]
    lines = [l.strip() for l in block.splitlines() if l.strip().startswith("- [")]
    return lines[-n:]

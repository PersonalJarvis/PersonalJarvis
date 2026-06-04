"""PersonStore — collection of `people/<slug>.md` files.

**The firewall against subject confusion:** every other person (partner,
colleagues, family) has their own Markdown file. The Curator decides
**before** writing whether a fact belongs to the user or to a person —
and it ends up in exactly one file.

Example scenario from a user request:
    User says: "My girlfriend Laura works at X."
    → Extractor recognises: subject=person:Laura, field=profession=X,
      relationship_to_user=partner
    → Validator checks: "Laura" is not User.name
    → Merger: `people/laura.md` is created (if not yet present) or
      updated. USER.md remains unchanged.

The user can open `people/laura.md` at any time and see what Jarvis knows
about Laura. The separation is visible, not hidden.
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
from .templates import render_person_md
from .workspace import Workspace, person_slug

log = logging.getLogger(__name__)


@dataclass
class Person:
    """Represents a single person (people/<slug>.md)."""

    path: Path
    _meta: dict[str, Any] = field(default_factory=dict)
    _body: str = ""

    @classmethod
    def load(cls, path: Path) -> Person:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        return cls(path=path, _meta=meta, _body=body)

    @property
    def name(self) -> str:
        return (self._meta.get("identity", {}) or {}).get("name", self.path.stem)

    @property
    def relationship(self) -> str:
        return self._meta.get("relationship", "unbekannt")

    @property
    def aliases(self) -> list[str]:
        return (self._meta.get("identity", {}) or {}).get("aliases", []) or []

    def add_alias(self, alias: str) -> bool:
        aliases = self.aliases
        if alias in aliases or alias == self.name:
            return False
        ident = self._meta.setdefault("identity", {})
        ident["aliases"] = aliases + [alias]
        return True

    def append_observation(self, field_label: str, value: str, evidence: str) -> None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        quote = evidence.strip().replace('"', "'")
        if len(quote) > 120:
            quote = quote[:119] + "…"
        line = f'- [{date}] {field_label}: {value} — "{quote}"'
        self._body = append_to_section(self._body, "observations", line)

    def save(self) -> None:
        self._meta["last_updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        text = write_frontmatter(self._meta, self._body)
        dir_ = self.path.parent
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(dir_)
        )
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


@dataclass
class PersonStore:
    """Manages `data/workspace/people/`.

    Lookup is slug-based (`person_slug("Laura Müller") == "laura_mueller"`)
    so that umlauts and special characters do not cause filename issues.
    """

    workspace: Workspace

    def get_or_create(self, name: str, relationship: str = "unbekannt") -> Person:
        """Loads an existing person file or creates a new one."""
        path = self.workspace.person_path(name)
        if path.exists():
            return Person.load(path)

        # New person: render template and write file
        text = render_person_md(name=name, relationship=relationship)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        log.info("Neue Person angelegt: %s → %s", name, path.name)
        return Person.load(path)

    def find_by_alias(self, query: str) -> Person | None:
        """Searches for a person by name or alias.

        Important on second contact: the user says "Laura" once, then "Lola" as
        a nickname — we match on both.
        """
        slug = person_slug(query)
        for path in self.workspace.list_people():
            if path.stem == slug:
                return Person.load(path)
        # Alias scan
        q_lower = query.strip().lower()
        for path in self.workspace.list_people():
            p = Person.load(path)
            if p.name.lower() == q_lower:
                return p
            if any(a.lower() == q_lower for a in p.aliases):
                return p
        return None

    def list_all(self) -> list[Person]:
        return [Person.load(p) for p in self.workspace.list_people()]

    def render_for_prompt(self, *, max_chars: int = 800) -> str:
        """Compact prompt block with names and relationships of known people.

        We do NOT inject the full person files into every prompt (too large);
        instead we include only a list so Jarvis knows that 'Laura' exists and
        that she is a partner. Details are read on demand via a tool call
        (see the `remember` tool).
        """
        people = self.list_all()
        if not people:
            return ""
        parts = ["## Personen im Umfeld"]
        for p in people:
            rel = p.relationship
            aliases = p.aliases
            tag = f" (aka {', '.join(aliases)})" if aliases else ""
            parts.append(f"- **{p.name}**{tag} — {rel}")
        out = "\n".join(parts)
        if len(out) > max_chars:
            out = out[: max_chars - 1] + "…"
        return out

"""Core memory: always-in-context persona + user facts.

Automatically injected into the system prompt on every brain call. Deliberately
kept small (~1500 tokens max) so that the prompt cache stays efficient.
Persisted as JSON in `data/core_memory.json`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CORE_MEMORY_FILENAME = "core_memory.json"


def default_core_memory() -> dict[str, Any]:
    """Minimal defaults for the first run. Overridden by the user via wizard/runtime."""
    return {
        "persona": {
            "name": "Jarvis",
            "role": "Voice-gesteuerter Meta-Orchestrator",
            "style": "hilfsbereit, präzise, auf Deutsch",  # i18n-allow
        },
        "user_facts": {},
        "preferences": {
            "confirmation_fatigue": "low",
            "language_default": "de",
            "voice": "Algieba",
        },
        "current_projects": {},
    }


@dataclass
class CoreMemory:
    """Read/write handle for the core-memory JSON.

    Not thread-safe — we assume a single process.
    """

    path: Path
    _data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> CoreMemory:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            data = default_core_memory()
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Corrupt file — backup, then use defaults
                backup = p.with_suffix(".corrupted.json")
                p.rename(backup)
                data = default_core_memory()
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(path=p, _data=data)

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reload(self) -> None:
        """Re-reads the JSON from disk. Required before every brain call;
        otherwise the LLM will not see facts added after initialisation
        (user additions via UI, remember-tool from a parallel OpenClaw worker,
        external editor)."""
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Corrupt — stay on last-known-good instead of overwriting.
            pass

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get_section(self, name: str) -> dict[str, Any]:
        return dict(self._data.get(name, {}))

    def set_value(self, section: str, key: str, value: Any) -> None:
        sec = self._data.setdefault(section, {})
        sec[key] = value
        self.save()

    def add_fact(self, fact: str, category: str = "general") -> None:
        """Appends a free-form fact to `user_facts[category]`."""
        facts = self._data.setdefault("user_facts", {})
        bucket = facts.setdefault(category, [])
        if fact not in bucket:
            bucket.append(fact)
        self.save()

    def remove_fact(self, fact: str, category: str = "general") -> bool:
        bucket = self._data.get("user_facts", {}).get(category, [])
        if fact in bucket:
            bucket.remove(fact)
            self.save()
            return True
        return False

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    # ------------------------------------------------------------------
    # System-prompt injection
    # ------------------------------------------------------------------

    def render_system_prompt_block(self) -> str:
        """Renders the core memory as a Markdown block for the system prompt.

        Kept compact — currently ~300-500 tokens. Frequently prompt-cached,
        so changes should be made sparingly.
        """
        persona = self._data.get("persona", {})
        facts = self._data.get("user_facts", {})
        prefs = self._data.get("preferences", {})
        projects = self._data.get("current_projects", {})

        lines: list[str] = ["## Core-Memory (persistent)"]

        if persona:
            lines.append("")
            lines.append("### Persona")
            for k, v in persona.items():
                lines.append(f"- **{k}**: {v}")

        if facts:
            lines.append("")
            lines.append("### User-Facts")
            for cat, items in facts.items():
                if isinstance(items, list):
                    for item in items:
                        lines.append(f"- [{cat}] {item}")
                else:
                    lines.append(f"- [{cat}] {items}")

        if prefs:
            lines.append("")
            lines.append("### Preferences")
            for k, v in prefs.items():
                lines.append(f"- {k}: {v}")

        if projects:
            lines.append("")
            lines.append("### Active Projects")
            for name, desc in projects.items():
                lines.append(f"- **{name}**: {desc}")

        return "\n".join(lines)

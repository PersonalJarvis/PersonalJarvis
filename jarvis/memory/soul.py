"""Soul — the assistant's own character file (SOUL.md).

Separate from UserProfile because this is about the **assistant**, not the
user: its name, who it is, its tone, its limits, and what it has learned
about its own character. It is the one place the assistant's identity is
written down, and every prompt surface — typed chat, the realtime voice
engines and GPT-Live — renders it through :mod:`jarvis.brain.identity`.

Two parts of the file are maintained automatically:

* the ``- **Name:**`` line under ``## Who I am`` mirrors the name the wake
  word gives the assistant (``jarvis.brain.assistant_name``; the wake word
  stays the single control for the name), so a person reading the file sees
  the truth;
* the ``## Calibration`` section between the ``curator:calibration`` markers
  holds what the assistant learned about its own character. The learning
  loop (``jarvis.memory.learning``) adds, replaces and removes entries there
  — each one an id-tagged notebook entry in the same format as the Society
  notebooks, so a correction never loses its history.

Everything else (role, vibe, tone rules, limits) is editorial: the user
edits it by hand and nothing here rewrites it.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter, write_frontmatter

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 8_000
#: Prompt budget for the learned character notes (whole entries only).
LEARNED_PROMPT_CHARS = 1_200

_LEARNED_START = "<!-- curator:calibration:start -->"
_LEARNED_END = "<!-- curator:calibration:end -->"
_NAME_LINE = re.compile(r"^- \*\*Name:\*\*.*$", re.MULTILINE)
_WHO_HEADING = re.compile(
    r"^## (?:Who I am|Wer ich bin)[^\n]*$", re.MULTILINE  # i18n-allow: legacy heading
)
_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_S = 2.0

# Section headings in either language the file has used, mapped to a key.
_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("who", ("## Who I am", "## Wer ich bin")),  # i18n-allow: legacy heading
    ("tone", ("## Tone",)),
    ("limits", ("## Limits", "## Grenzen")),  # i18n-allow: legacy heading
    ("learned", ("## Calibration", "## Kalibrierung")),  # i18n-allow: legacy heading
)


def _section_key(line: str) -> str | None:
    for key, prefixes in _SECTIONS:
        if any(line.startswith(prefix) for prefix in prefixes):
            return key
    return None


@dataclass
class Soul:
    path: Path
    _meta: dict[str, Any] = field(default_factory=dict)
    _body: str = ""

    @classmethod
    def load(cls, path: str | Path) -> Soul:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"SOUL.md missing: {p}")
        return cls.parse(p, p.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, path: str | Path, text: str) -> Soul:
        meta, body = parse_frontmatter(text)
        return cls(path=Path(path), _meta=meta, _body=body.replace("\r\n", "\n"))

    def save(self) -> None:
        self._meta["last_updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        text = write_frontmatter(self._meta, self._body)
        dir_ = self.path.parent
        fd, tmp_path = tempfile.mkstemp(prefix=".SOUL.md.", suffix=".tmp", dir=str(dir_))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── body ─────────────────────────────────────────────────────────────

    @property
    def body(self) -> str:
        """The Markdown under the frontmatter, as the person edits it."""
        return self._body

    def set_body(self, text: str) -> bool:
        """Replace the hand-written Markdown; True when it changed.

        The frontmatter is kept. The managed parts (name line, learned
        section) are whatever the new text says; the next name sync and the
        next learned note restore them if the person removed them.
        """
        body = (text or "").replace("\r\n", "\n").strip()
        body = body + "\n" if body else ""
        if body == self._body:
            return False
        self._body = body
        return True

    # ── name ─────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """The name the file states, ``""`` when it states none."""
        match = _NAME_LINE.search(self._body)
        if not match:
            return ""
        return match.group(0).split(":**", 1)[1].strip()

    def set_name(self, name: str) -> bool:
        """Write ``name`` into the managed name line; True when the body changed."""
        name = " ".join((name or "").split())
        if not name or name == self.name:
            return False
        line = f"- **Name:** {name}"
        if _NAME_LINE.search(self._body):
            self._body = _NAME_LINE.sub(lambda _m: line, self._body, count=1)
            return True
        heading = _WHO_HEADING.search(self._body)
        if heading is None:
            # A hand-written file without the section: add it after the title.
            self._body = self._body.rstrip("\n") + f"\n\n## Who I am\n\n{line}\n"
            return True
        head, rest = self._body[: heading.end()], self._body[heading.end() :].lstrip("\n")
        # Directly above the section's bullets, or alone before the next heading.
        gap = "\n" if rest.startswith(("-", "*")) else "\n\n"
        self._body = f"{head}\n\n{line}{gap}{rest}"
        return True

    # ── learned character notes ─────────────────────────────────────────

    def _learned_span(self) -> tuple[int, int] | None:
        start = self._body.find(_LEARNED_START)
        end = self._body.find(_LEARNED_END)
        if start == -1 or end == -1 or end < start:
            return None
        return start + len(_LEARNED_START), end

    def learned(self) -> list[Any]:
        """The learned entries (``jarvis.society.notebook.Entry``), oldest first."""
        from jarvis.society.notebook import Entry, identity, parse

        span = self._learned_span()
        if span is None:
            return []
        raw = self._body[span[0] : span[1]].strip()
        if not raw:
            return []
        if "<!-- memory-entry:" in raw:
            return parse(raw)
        # Legacy calibration lines ("- [2026-08-03] note") become one entry each.
        rows = []
        for index, line in enumerate(raw.splitlines()):
            text = line.strip().lstrip("-* ").strip()
            if text:
                rows.append(Entry(identity(text), text, 5, index, "legacy"))
        return rows

    def set_learned(self, entries: list[Any]) -> None:
        from jarvis.society.notebook import render

        content = render(entries).strip() if entries else ""
        block = f"{_LEARNED_START}\n{content}\n{_LEARNED_END}" if content else (
            f"{_LEARNED_START}\n{_LEARNED_END}"
        )
        span = self._learned_span()
        if span is None:
            self._body = (
                self._body.rstrip("\n") + "\n\n## Calibration (learns over time)\n\n" + block + "\n"
            )
            return
        start = span[0] - len(_LEARNED_START)
        end = span[1] + len(_LEARNED_END)
        self._body = self._body[:start] + block + self._body[end:]

    def append_calibration(self, note: str) -> None:
        """Add one learned note, e.g. 'The user likes dry humour'."""
        from jarvis.society.notebook import change

        self.set_learned(change(self.learned(), note.strip(), operation="add", origin="user"))

    # ── prompt ───────────────────────────────────────────────────────────

    def sections(self) -> dict[str, list[str]]:
        """Bullet lines per known section (``who``, ``tone``, ``limits``)."""
        out: dict[str, list[str]] = {"who": [], "tone": [], "limits": []}
        current: str | None = None
        for line in self._body.splitlines():
            if line.startswith("## "):
                current = _section_key(line)
                continue
            if current not in out:
                continue
            stripped = line.strip()
            if stripped.startswith(("-", "*")) and not _NAME_LINE.match(stripped):
                out[current].append(stripped)
        return out

    def render_for_prompt(self, *, max_chars: int = MAX_PROMPT_CHARS, compact: bool = False) -> str:
        """The character block for a system prompt; ``""`` when the file says nothing.

        The name line is left out on purpose: the identity directive states
        the live name, so a stale line in a hand-edited file can never
        contradict it. ``compact`` (small self-hosted brains) keeps who the
        assistant is and what it learned, and drops the tone rules and limits
        the compact persona already carries.
        """
        from jarvis.society.notebook import select_entries

        sections = self.sections()
        out: list[str] = []
        if sections["who"]:
            out += ["### Who you are", *sections["who"]]
        if not compact:
            if sections["tone"]:
                out += ["### Your tone", *sections["tone"]]
            if sections["limits"]:
                out += ["### Your limits", *sections["limits"]]
        learned = self.learned()
        if learned:
            budget = LEARNED_PROMPT_CHARS // (2 if compact else 1)
            picked, _omitted = select_entries(learned, max_chars=budget)
            picked.sort(key=lambda e: e.revision)
            if picked:
                out.append("### What you have learned about yourself")
                out += ["- " + " ".join(e.text.split()) for e in picked]
        if not out:
            return ""
        text = "\n".join(
            [
                "## Your character (SOUL.md)",
                "Your own character file. It describes YOU, the assistant, never the user.",
                *out,
            ]
        )
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text


def edit_soul(path: str | Path, mutate: Callable[[Soul], bool]) -> bool:
    """Load SOUL.md under its lock, apply ``mutate``, save when it returns True.

    The lock keeps the learning loop and a boot-time name sync from
    overwriting each other; a hand edit between two writes is never lost
    because every write re-reads the file first. Returns whether the file
    changed. A missing file is not created here — ``Workspace.ensure`` owns
    that.
    """
    from filelock import FileLock

    p = Path(path)
    with FileLock(str(p.with_name(p.name + _LOCK_SUFFIX)), timeout=_LOCK_TIMEOUT_S):
        soul = Soul.load(p)
        if not mutate(soul):
            return False
        soul.save()
        return True


def sync_name(path: str | Path, name: str) -> bool:
    """Mirror the live assistant name into SOUL.md; True when the file changed.

    Never raises: a missing or locked file only means the reader of the file
    sees an older name for a while — the prompts state the live name anyway.
    """
    if not name:
        return False
    try:
        return edit_soul(path, lambda soul: soul.set_name(name))
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001 — cosmetic sync; the prompt directive carries the name
        log.warning("SOUL.md: could not record the assistant name", exc_info=True)
        return False

"""Editable memory entries inside the existing Markdown memory page.

The page remains the source of truth. Stable ids survive corrections; old
date-heading pages are read without a separate migration or a second notebook.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

_MARKER = re.compile(r"^<!-- memory-entry: (.+) -->$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    text: str
    importance: int = 5
    revision: int = 0
    origin: str = "agent"


def identity(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def parse(body: str) -> list[Entry]:
    marks = list(_MARKER.finditer(body))
    if not marks:
        chunks = re.split(r"(?m)(?=^## \d{4}-\d{2}-\d{2}\s*$)", body)
        return [
            Entry(identity(s), s.strip(), revision=i) for i, s in enumerate(chunks) if s.strip()
        ]
    entries: list[Entry] = []
    prefix = body[: marks[0].start()].strip()
    if prefix:
        entries.extend(parse(prefix))
    for i, mark in enumerate(marks):
        # Invalid metadata must fail the write, never discard existing content.
        meta = json.loads(mark.group(1))
        text = body[mark.end() : marks[i + 1].start() if i + 1 < len(marks) else len(body)].strip()
        entries.append(
            Entry(
                str(meta["id"]),
                text,
                int(meta.get("importance", 5)),
                int(meta.get("revision", i)),
                str(meta.get("origin", "agent")),
            )
        )
    return entries


def render(entries: list[Entry]) -> str:
    parts = []
    for entry in entries:
        meta = asdict(entry)
        meta.pop("text")
        parts.append(
            "<!-- memory-entry: " + json.dumps(meta, ensure_ascii=True) + " -->\n" + entry.text
        )
    return "\n\n".join(parts) + "\n"


def change(
    entries: list[Entry],
    text: str,
    *,
    operation: str = "add",
    entry_id: str = "",
    old_text: str = "",
    importance: int = 5,
    origin: str = "agent",
) -> list[Entry]:
    if operation not in {"add", "replace", "remove"}:
        raise ValueError("operation must be add, replace or remove")
    if operation != "remove" and not text.strip():
        raise ValueError("text is required")
    if "<!-- memory-entry:" in text:
        raise ValueError("memory metadata cannot be supplied as content")
    revision = max((e.revision for e in entries), default=0) + 1
    if operation == "add":
        if any(e.text.strip() == text.strip() for e in entries):
            return entries
        return [*entries, Entry(identity(text), text.strip(), importance, revision, origin)]
    found = [
        e
        for e in entries
        if (e.id == entry_id if entry_id else bool(old_text) and old_text in e.text)
    ]
    if len(found) != 1:
        raise ValueError("identify exactly one existing memory by entry_id or unique old_text")
    target = found[0]
    return [
        Entry(e.id, text.strip(), importance, revision, origin) if e.id == target.id else e
        for e in entries
        if operation != "remove" or e.id != target.id
    ]


def briefing(entries: list[Entry], *, max_chars: int) -> str:
    """Select whole entries by importance, then recency; never silently clip a fact."""
    lines: list[str] = []
    omitted: list[str] = []
    used = 0
    for entry in sorted(entries, key=lambda e: (-e.importance, -e.revision, e.id)):
        line = f"[{entry.id}] {entry.text}"
        if used + len(line) + 2 <= max_chars:
            lines.append(line)
            used += len(line) + 2
        else:
            omitted.append(entry.id)
    if omitted:
        lines.append(
            f"{len(omitted)} more entries remain on disk; "
            "use society_memory_recall to retrieve them."
        )
    return "\n\n".join(lines)

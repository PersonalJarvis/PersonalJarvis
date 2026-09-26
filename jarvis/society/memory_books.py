"""Two private notebooks per agent, with recoverable migration of legacy memory.md.

USER.md holds facts and preferences about the person. MEMORY.md holds project
knowledge, environment facts and learned working methods. The original page is
backed up before migration, and a journal makes interrupted two-file writes
recoverable without dropping stable entry identities.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from .notebook import Entry, change, parse, render

FILES = {"memory": "MEMORY.md", "user": "USER.md"}
PROMPT_BUDGETS = {"memory": 8_000, "user": 4_000}
_PROFILE = re.compile(
    r"^(?:the user(?:'s| is | prefers | wants | manages | develops | works | lives )|"
    r"user(?:'s| prefers | wants )|i (?:am |prefer |want )|my (?:name|email|timezone|pronouns)|"
    r"working rule: (?:reply|respond|write|use (?:plain text|bullet|short))|"
    r"reports? (?:must|should|use)|(?:please )?(?:reply|respond|write) always in |"
    r"ich (?:bin|bevorzuge|möchte)|mein(?:e)? (?:name|email|zeitzone)|"  # i18n-allow
    r"prefiero |mi (?:nombre|correo|zona horaria))",
    re.IGNORECASE,
)
_FRONTMATTER = re.compile(r"\A\ufeff?---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)


def classify(text: str) -> str:
    """Conservative fallback for legacy entries and clients without an explicit target."""
    return "user" if _PROFILE.search(text.strip()) else "memory"


def body(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def folder_for(vault: Path, agent_id: str) -> Path:
    if not agent_id or any(c in agent_id for c in "/\\:") or agent_id in {".", ".."}:
        raise ValueError("Invalid agent memory namespace")
    folder = vault.resolve() / "society" / agent_id
    if folder.resolve() != folder:
        raise ValueError("Linked agent memory namespaces are not supported")
    return folder


def _document(agent: Any, target: str, entries: list[Entry], original: str = "") -> str:
    from .memory import SocietyMemory

    match = _FRONTMATTER.match(original)
    prefix = (
        match[0]
        if match
        else SocietyMemory._frontmatter(
            f"{agent.name} — {'user profile' if target == 'user' else 'memory'}",
            agent.agent_id,
            "agent",
            "",
        )
    )
    return prefix.rstrip() + "\n\n" + render(entries)


def _recover(folder: Path, journal: Path) -> None:
    from .memory import atomic_write

    plan = json.loads(journal.read_text(encoding="utf-8"))
    if plan.get("version") != 2 or set(plan.get("files", {})) != set(FILES.values()):
        raise ValueError("Invalid agent memory migration journal")
    for name, item in plan["files"].items():
        if _hash(_read(folder / name)) not in {item["before_hash"], _hash(item["after"])}:
            raise ValueError("Agent memory changed during migration; originals were preserved")
    for item in plan["legacy"]:
        name = item["name"]
        if name.lower() not in {"memory.md", "user.md"}:
            raise ValueError("Invalid legacy notebook name")
        legacy = folder / name
        canonical = folder / ("USER.md" if name.lower() == "user.md" else "MEMORY.md")
        if legacy.is_file() and (not canonical.exists() or not legacy.samefile(canonical)):
            if _hash(_read(legacy)) != item["hash"]:
                raise ValueError("Legacy memory changed during migration; originals were preserved")
    for name, item in plan["files"].items():
        if _read(folder / name) != item["after"]:
            atomic_write(folder / name, item["after"])
    for item in plan["legacy"]:
        legacy = folder / item["name"]
        canonical = folder / ("USER.md" if item["name"].lower() == "user.md" else "MEMORY.md")
        if legacy.is_file() and not legacy.samefile(canonical):
            if _hash(_read(legacy)) != item["hash"]:
                raise ValueError("Legacy memory changed during migration; originals were preserved")
            os.replace(legacy, folder / f".legacy-{item['name']}-{item['hash']}.bak")
    atomic_write(folder / ".memory-layout.json", '{"version": 2}\n')
    journal.unlink()


def _ensure_locked(folder: Path, agent: Any) -> None:
    from .memory import atomic_write

    journal = folder / ".memory-layout.pending.json"
    marker = folder / ".memory-layout.json"
    for path in folder.iterdir():
        if path.name.lower() in {"memory.md", "user.md"} or path in {journal, marker}:
            if path.resolve() != path:
                raise ValueError("Linked notebook files are not supported")
    if marker.is_file():
        if json.loads(marker.read_text(encoding="utf-8")).get("version") != 2:
            raise ValueError("Unknown agent memory layout")
        journal.unlink(missing_ok=True)  # The marker is the committed migration receipt.
        return
    if journal.is_file():
        _recover(folder, journal)
        return
    by_name = {path.name: path for path in folder.iterdir() if path.is_file()}
    books = {
        target: _read(by_name[name]) if name in by_name else "" for target, name in FILES.items()
    }
    entries = {target: parse(body(text)) for target, text in books.items()}
    legacy = []
    for name, path in by_name.items():
        if name in FILES.values() or name.lower() not in {"memory.md", "user.md"}:
            continue
        original = _read(path)
        for entry in parse(body(original)):
            target = "user" if name.lower() == "user.md" else classify(entry.text)
            current = next((e for e in entries[target] if e.id == entry.id), None)
            if current and current.text != entry.text:
                raise ValueError("Conflicting legacy entry ids; originals were preserved")
            if current is None:
                entries[target].append(entry)
        backup = folder / f".legacy-{name}-{_hash(original)}.bak"
        if not backup.exists():
            atomic_write(backup, original)
        legacy.append({"name": name, "hash": _hash(original)})
    plan = {
        "version": 2,
        "legacy": legacy,
        "files": {
            name: {
                "before_hash": _hash(_read(folder / name)),
                "after": _document(agent, target, entries[target], books[target]),
            }
            for target, name in FILES.items()
        },
    }
    atomic_write(journal, json.dumps(plan, ensure_ascii=False))
    _recover(folder, journal)


def ensure_books(vault: Path, agent: Any) -> dict[str, Path]:
    folder = folder_for(vault, agent.agent_id)
    folder.mkdir(parents=True, exist_ok=True)
    with FileLock(str(folder / ".memory-books.lock"), timeout=2):
        _ensure_locked(folder, agent)
    return {target: folder / name for target, name in FILES.items()}


def read_books(vault: Path, agent: Any) -> dict[str, list[Entry]]:
    paths = ensure_books(vault, agent)
    with FileLock(str(paths["memory"].parent / ".memory-books.lock"), timeout=2):
        return {target: parse(body(_read(path))) for target, path in paths.items()}


@dataclass(frozen=True)
class BookChange:
    path: Path
    target: str
    before: str
    after: str
    changed: bool


def edit_book(
    vault: Path, agent: Any, text: str, *, target: str | None = None, **options: Any
) -> BookChange:
    from .memory import atomic_write

    if target is not None and target not in FILES:
        raise ValueError("Memory target must be memory or user")
    paths = ensure_books(vault, agent)
    with FileLock(str(paths["memory"].parent / ".memory-books.lock"), timeout=2):
        documents = {key: _read(path) for key, path in paths.items()}
        entries = {key: parse(body(value)) for key, value in documents.items()}
        if target is None and options.get("operation", "add") != "add":
            entry_id, old = options.get("entry_id"), options.get("old_text")
            matches = [
                key
                for key, rows in entries.items()
                if any(e.id == entry_id if entry_id else bool(old) and old in e.text for e in rows)
            ]
            if len(matches) != 1:
                raise ValueError("Identify an existing entry in exactly one memory target")
            target = matches[0]
        target = target or classify(text)
        updated = change(entries[target], text, **options)
        before = documents[target]
        changed = updated != entries[target]
        after = _document(agent, target, updated, before) if changed else before
        if changed:
            atomic_write(paths[target], after)
        return BookChange(paths[target], target, before, after, changed)

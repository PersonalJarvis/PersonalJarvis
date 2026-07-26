"""Write the UltraWiki projection out as a real Obsidian vault.

The knowledge lives in a database, which is right for retrieval and useless
for reading with your own eyes. This module projects it onto disk as plain
Markdown with ``[[wikilinks]]``, so any Markdown tool — Obsidian above all —
can browse the same knowledge, follow the links, and draw its own graph.

**One-way by design.** The three generated directories are rewritten on every
export; ``My notes/`` is created once and never touched again, and is what the
existing ``obsidian-vault`` connector reads back in. That gives two-way flow
without a merge problem: generated content is disposable, authored content is
never written by us.

**Deletion is fail-closed.** A stale note is removed only when its front
matter says we wrote it. A file that a person put into ``Topics/`` by hand
survives every export, because "it is in our folder" is not proof that it is
ours.

Filenames are the whole cross-platform surface. Measured on a real corpus, 14
of 947 topic labels contain a character that is illegal or path-changing in a
filename and 12 more differ only in case — which is the same file on Windows
and macOS. :func:`safe_note_name` and :func:`assign_note_names` are therefore
pure, deterministic, and tested independently of the OS the tests run on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

__all__ = [
    "GENERATED_DIRS",
    "GENERATED_MARKER",
    "MANIFEST_NAME",
    "MOMENTS_DIR",
    "OVERVIEW_DIR",
    "TOPICS_DIR",
    "USER_DIR",
    "ExportResult",
    "assign_note_names",
    "export_vault",
    "resolve_vault_root",
    "safe_note_name",
]

TOPICS_DIR = "Topics"
MOMENTS_DIR = "Moments"
OVERVIEW_DIR = "Overview"

#: Rewritten on every export. Everything else in the vault is the user's.
GENERATED_DIRS: tuple[str, ...] = (TOPICS_DIR, MOMENTS_DIR, OVERVIEW_DIR)

#: Created once, never written to again — and read back by the
#: ``obsidian-vault`` connector, which is how notes flow the other way.
USER_DIR = "My notes"

#: The front-matter line that authorises deletion. No marker, no delete.
GENERATED_MARKER = "generated_by: ultrawiki"

#: What the last export wrote: {vault-relative POSIX path: content hash}.
#:
#: Without it, deciding "has this note changed" means reading all of them
#: back, which measured 2.5 ms per file on a machine with a live virus
#: scanner — 24 s for a re-export of a corpus where nothing changed at all,
#: four times longer than writing the whole vault from scratch.
#:
#: It is an optimisation, never the authority: a missing or unreadable
#: manifest falls back to the front-matter marker, so the export still prunes
#: correctly on the first run after an update or after someone deletes it.
MANIFEST_NAME = ".ultrawiki-manifest.json"
_MANIFEST_VERSION = 1

#: Illegal on Windows, and `/` would silently create directories on POSIX.
_UNSAFE_CHARS = '<>:"/\\|?*'

#: Reserved DOS device names: a file called `CON.md` cannot be created on
#: Windows at all, and the failure is obscure.
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Leaves room for the collision suffix well inside every filesystem limit.
_MAX_STEM = 120

#: Bucket for moments whose timestamp could not be read.
_UNDATED = "undated"


class _Nameable(Protocol):
    key: str
    label: str


@dataclass(frozen=True, slots=True)
class ExportResult:
    """What one export wrote, for the honest report the UI shows."""

    root: Path
    topics: int
    moments: int
    written: int
    unchanged: int
    removed: int


def resolve_vault_root(
    data_dir: str | Path | None = None, configured: str = ""
) -> Path:
    """One absolute vault path, independent of the process working directory.

    Mirrors ``resolve_ultrawiki_db_path``: a relative path (including the
    ``./data`` default) is anchored at the repo root, never at whatever
    directory the app happened to start in.
    """
    from jarvis.core.paths import repo_root  # lazy: keep module import cheap

    if configured.strip():
        raw = Path(configured.strip())
        return (raw if raw.is_absolute() else repo_root() / raw).resolve(strict=False)
    base = Path(data_dir) if data_dir is not None else Path("data")
    directory = base if base.is_absolute() else repo_root() / base
    return (directory / "ultrawiki-vault").resolve(strict=False)


def safe_note_name(label: str) -> str:
    """A filename stem that is legal and identical on Windows, macOS and Linux.

    Not merely defensive: on a real corpus a slash in a topic name would have
    created directories on Linux and raised on Windows, and a trailing dot
    would be dropped by Windows so the file on disk no longer matches the name
    we recorded — which quietly breaks the orphan comparison and makes the
    export delete-and-rewrite forever.
    """
    text = unicodedata.normalize("NFC", label)
    cleaned = "".join(
        "-" if (char in _UNSAFE_CHARS or ord(char) < 32) else char for char in text
    )
    cleaned = " ".join(cleaned.split())
    # A name must not END on a separator, a dot or a space: the first is ugly,
    # the second and third are silently dropped by Windows.
    cleaned = cleaned.rstrip(" .-_")
    if not cleaned:
        cleaned = "untitled"
    if len(cleaned) > _MAX_STEM:
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[:_MAX_STEM].rstrip(' .-_')}-{digest}"
    if cleaned.split(".")[0].lower() in _RESERVED_NAMES:
        cleaned = f"{cleaned}-note"
    return cleaned


def assign_note_names(
    items: Iterable[_Nameable], taken: set[str] | None = None
) -> dict[str, str]:
    """Collision-free filename stems, keyed by the item's key.

    Names are unique across the WHOLE vault, not per directory, because an
    Obsidian ``[[wikilink]]`` resolves by name: two notes sharing one name in
    different folders make every link to that name ambiguous.

    Collisions resolve in key order so a re-export produces the same names —
    otherwise every export would churn the vault and Obsidian would lose its
    per-note state.
    """
    used = {name.lower() for name in (taken or set())}
    names: dict[str, str] = {}
    for item in sorted(items, key=lambda entry: entry.key):
        stem = safe_note_name(item.label)
        candidate = stem
        suffix = 2
        while candidate.lower() in used:
            candidate = f"{stem}-{suffix}"
            suffix += 1
        used.add(candidate.lower())
        names[item.key] = candidate
    return names


@dataclass(frozen=True, slots=True)
class _MomentName:
    """Adapter so moments go through the same naming rules as topics."""

    key: str
    label: str


def _front_matter(fields: dict[str, Any]) -> str:
    lines = ["---", GENERATED_MARKER]
    for name, value in fields.items():
        lines.append(f"{name}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _link(name: str) -> str:
    return f"[[{name}]]"


def _load_manifest(vault: Path) -> dict[str, str]:
    """What the previous export wrote, or an empty map if that is unknowable."""
    path = vault / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _MANIFEST_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save_manifest(vault: Path, files: dict[str, str]) -> None:
    payload = json.dumps(
        {"version": _MANIFEST_VERSION, "files": files},
        ensure_ascii=False,
        indent=0,
        sort_keys=True,
    )
    _write_atomic(vault / MANIFEST_NAME, payload.encode("utf-8"))


def _write_if_changed(path: Path, text: str, known_hash: str | None) -> tuple[bool, str]:
    """Write *text* atomically unless it is already on disk unchanged.

    Skipping unchanged files keeps a re-export from touching every note's
    mtime — a vault where every file changed on every export re-syncs,
    re-indexes and backs up in full for no reason. The comparison goes through
    the manifest hash plus one cheap existence check, never a full read.
    """
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    if known_hash == digest and path.exists():
        return False, digest
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, payload)
    return True, digest


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _is_ours(path: Path) -> bool:
    """True only for a file we generated. Anything unreadable counts as NOT
    ours — the export must never delete a file it could not verify."""
    try:
        with path.open("r", encoding="utf-8", errors="strict") as stream:
            head = stream.read(400)
    except (OSError, UnicodeDecodeError):
        return False
    return GENERATED_MARKER in head


def _topic_note(entity: Any, names: dict[str, str], moment_names: dict[str, str],
                moments: tuple[Any, ...]) -> str:
    lines = [
        _front_matter(
            {
                "topic_key": entity.key,
                "mentions": entity.mentions,
                "first_seen": entity.first_seen,
                "last_seen": entity.last_seen,
            }
        ),
        "",
        f"# {entity.label}",
        "",
        f"Mentioned {entity.mentions} time(s), "
        f"{entity.first_seen[:10] or 'unknown'} to {entity.last_seen[:10] or 'unknown'}.",
        "",
    ]

    if entity.neighbors:
        lines.append("## Appears with")
        lines.append("")
        for key, shared in entity.neighbors[:30]:
            target = names.get(key)
            if target:
                lines.append(f"- {_link(target)} — {shared} shared moment(s)")
        lines.append("")

    if moments:
        lines.append("## Moments")
        lines.append("")
        for moment in moments[:200]:
            name = moment_names.get(str(moment.document_id))
            date = moment.timestamp_utc[:10]
            if name:
                lines.append(f"- {date} — {_link(name)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _moment_note(moment: Any, names: dict[str, str]) -> str:
    lines = [
        _front_matter(
            {
                "document_id": moment.document_id,
                "date": moment.timestamp_utc,
                "source": moment.source_label,
            }
        ),
        "",
        f"# {moment.title}",
        "",
    ]
    if moment.summary:
        lines += [moment.summary, ""]
    if moment.resolution:
        lines += ["## Outcome", "", moment.resolution, ""]

    topics = [names[key] for key in moment.entity_keys if key in names]
    if topics:
        lines += ["## Topics", "", " · ".join(_link(name) for name in topics), ""]

    lines += [
        "---",
        "",
        f"{moment.timestamp_utc[:10]} · {moment.source_label}",
    ]
    if moment.permalink:
        lines.append(f"[Open the original]({moment.permalink})")
    return "\n".join(lines).rstrip() + "\n"


def _overview_topics(projection: Any, names: dict[str, str]) -> str:
    lines = [
        _front_matter({"overview": "topics", "count": len(projection.entities)}),
        "",
        "# All topics",
        "",
        "Everything the knowledge base has a name for, most mentioned first.",
        "",
    ]
    for entity in projection.entities:
        name = names.get(entity.key)
        if name:
            lines.append(f"- {_link(name)} — {entity.mentions}")
    return "\n".join(lines).rstrip() + "\n"


def _overview_timeline(projection: Any, moment_names: dict[str, str]) -> str:
    buckets: dict[str, list[Any]] = {}
    for moment in projection.moments:
        buckets.setdefault(moment.month or _UNDATED, []).append(moment)

    lines = [
        _front_matter({"overview": "timeline", "months": len(buckets)}),
        "",
        "# Timeline",
        "",
        "Every moment, newest month first.",
        "",
    ]
    for month in sorted(buckets, reverse=True):
        lines += [f"## {month}", ""]
        for moment in buckets[month]:
            name = moment_names.get(str(moment.document_id))
            if name:
                lines.append(f"- {moment.timestamp_utc[:10]} — {_link(name)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _overview_sources(projection: Any) -> str:
    counts: dict[str, int] = {}
    for moment in projection.moments:
        label = moment.source_label or moment.source_id or "unknown"
        counts[label] = counts.get(label, 0) + 1

    lines = [
        _front_matter({"overview": "sources", "count": len(counts)}),
        "",
        "# Sources",
        "",
        "Where these moments came from.",
        "",
    ]
    for label in sorted(counts, key=lambda name: (-counts[name], name)):
        lines.append(f"- {label} — {counts[label]} moment(s)")
    return "\n".join(lines).rstrip() + "\n"


def _readme(result_dirs: tuple[str, ...]) -> str:
    generated = ", ".join(f"`{name}/`" for name in result_dirs)
    return (
        _front_matter({"overview": "readme"})
        + "\n\n"
        + "# This vault is generated\n\n"
        + f"Jarvis rewrites {generated} on every export. Edits inside those\n"
        + "folders are replaced the next time it runs.\n\n"
        + f"`{USER_DIR}/` is yours. Nothing in this vault ever writes there, and\n"
        + "Jarvis can read those notes back in as a source.\n\n"
        + "Start at [[All topics]] or [[Timeline]].\n"
    )


def export_vault(projection: Any, root: str | Path) -> ExportResult:
    """Write the whole projection to *root* and prune our own stale notes."""
    vault = Path(root)
    vault.mkdir(parents=True, exist_ok=True)
    (vault / USER_DIR).mkdir(exist_ok=True)

    topic_names = assign_note_names(projection.entities)
    moment_items = [
        _MomentName(key=str(moment.document_id), label=moment.title)
        for moment in projection.moments
    ]
    # Moments share the namespace with topics so no wikilink is ambiguous.
    moment_names = assign_note_names(
        moment_items, taken=set(topic_names.values())
    )

    previous = _load_manifest(vault)
    current: dict[str, str] = {}
    written = 0
    unchanged = 0

    def emit(path: Path, text: str) -> None:
        nonlocal written, unchanged
        relative = path.relative_to(vault).as_posix()
        did_write, digest = _write_if_changed(path, text, previous.get(relative))
        current[relative] = digest
        if did_write:
            written += 1
        else:
            unchanged += 1

    for entity in projection.entities:
        emit(
            vault / TOPICS_DIR / f"{topic_names[entity.key]}.md",
            _topic_note(
                entity,
                topic_names,
                moment_names,
                projection.moments_by_entity.get(entity.key, ()),
            ),
        )

    for moment in projection.moments:
        name = moment_names[str(moment.document_id)]
        emit(
            vault / MOMENTS_DIR / (moment.month or _UNDATED) / f"{name}.md",
            _moment_note(moment, topic_names),
        )

    emit(vault / OVERVIEW_DIR / "All topics.md", _overview_topics(projection, topic_names))
    emit(vault / OVERVIEW_DIR / "Timeline.md", _overview_timeline(projection, moment_names))
    emit(vault / OVERVIEW_DIR / "Sources.md", _overview_sources(projection))
    emit(vault / "README.md", _readme(GENERATED_DIRS))

    removed = _prune(vault, {vault / rel for rel in current}, previous)
    _save_manifest(vault, current)

    return ExportResult(
        root=vault,
        topics=len(projection.entities),
        moments=len(projection.moments),
        written=written,
        unchanged=unchanged,
        removed=removed,
    )


def _prune(vault: Path, kept: set[Path], previous: dict[str, str]) -> int:
    """Delete our own notes that this export did not write, and nothing else.

    Two independent proofs of ownership, and either one suffices: the note is
    listed in the previous manifest, or its front matter carries the marker.
    The marker path is what keeps pruning correct when the manifest is missing
    (first run after an update, or a user who deleted it); the manifest path
    is what avoids reading every surviving note back on a normal run.
    """
    removed = 0
    for directory in GENERATED_DIRS:
        base = vault / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if path in kept:
                continue
            relative = path.relative_to(vault).as_posix()
            if relative not in previous and not _is_ours(path):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:  # a locked file must not abort the export
                log.warning("vault export: could not remove %s (%s)", path, exc)
        # Empty month folders left behind read as "nothing happened then",
        # which is a claim the data does not make.
        for folder in sorted(base.rglob("*"), reverse=True):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    return removed

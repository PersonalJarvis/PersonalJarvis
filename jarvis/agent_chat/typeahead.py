"""What the composer offers when a person types ``/``, ``@`` or ``$``.

Every coding CLI has a typeahead: ``/`` lists the slash commands and skills,
``@`` the files of the folder (and, in Claude Code, the subagents), and Codex
spells an explicit skill ``$name``. The chat composer wears the same three
gestures — but the seat behind a chat decides what each one means, and the
list must be what THAT seat will honour when the sentence arrives. Nothing
here is a catalogue typed by hand: every row is read from the disk the
runner reads.

    runner       "/"                          "@"              "$"
    claude-cli   skills, commands, plugins    files, agents    —
    codex-cli    —                            files            skills
    agy-cli      —                            files            —
    grok-cli     —                            files            —
    api          —                            files            —
    brain        Jarvis' own skills           —                —

A Claude Code seat reads its user-level definitions from the account's
config dir (``CLAUDE_CONFIG_DIR``, the same seat the turn is spawned on —
never the maintainer's ``~/.claude``); a Codex seat from ``CODEX_HOME``.
Project-level definitions come from the chat's folder, and both the
``.claude`` tree and its tool-neutral twin ``.agents`` are read because a
checkout may carry only one of them.

The Jarvis surface's ``/`` is its own skill registry; the brain runner turns
a leading ``/slug`` into a noted trigger (``runner_brain._note_skill_trigger``),
so the pick here is honoured the same way a spoken trigger phrase is.

Cross-platform: ``os.scandir`` + ``pathlib`` only, paths reported POSIX-style
relative to the folder, nothing OS-specific. The file walk is bounded (depth,
entry count, wall time) and cached for a few seconds per folder, so a
keystroke never re-reads a large checkout.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

log = logging.getLogger(__name__)

SLASH: Final[str] = "/"
MENTION: Final[str] = "@"
SKILL_REF: Final[str] = "$"

#: runner id -> the trigger characters that seat honours (see module doc).
TRIGGERS_BY_RUNNER: Final[dict[str, tuple[str, ...]]] = {
    "claude-cli": (SLASH, MENTION),
    # Claude Code under Z.ai's endpoint: the same commands and agents.
    "glm-cli": (SLASH, MENTION),
    "codex-cli": (MENTION, SKILL_REF),
    "agy-cli": (MENTION,),
    "grok-cli": (MENTION,),
    "opencode-cli": (MENTION,),
    "kimi-cli": (MENTION,),
    # One task in, one answer out — nothing to complete against.
    "dsh-cli": (),
    "api": (MENTION,),
    "brain": (SLASH,),
}

#: The runners that read Claude Code's ``.claude/agents`` — Claude Code, and
#: the launch profile that IS Claude Code (GLM Coding Plan).
_CLAUDE_SHAPED: Final[frozenset[str]] = frozenset({"claude-cli", "glm-cli"})

#: Group ids the composer turns into headings (i18n keys ``agent_chat.typeahead_group_*``).
GROUP_PROJECT: Final[str] = "project"
GROUP_ACCOUNT: Final[str] = "account"
GROUP_PLUGINS: Final[str] = "plugins"
GROUP_JARVIS: Final[str] = "jarvis"
GROUP_AGENTS: Final[str] = "agents"
GROUP_FILES: Final[str] = "files"

#: Hidden directories that still hold things worth offering under ``@``.
_VISIBLE_DOT_DIRS: Final[frozenset[str]] = frozenset({".claude", ".agents", ".codex", ".github"})
_MAX_WALK_ENTRIES: Final[int] = 25000
_MAX_WALK_DEPTH: Final[int] = 12
_MAX_WALK_SECONDS: Final[float] = 2.5
_WALK_TTL_S: Final[float] = 20.0
_GIT_TIMEOUT_S: Final[float] = 5.0
#: Inside a visible dot dir, the sub-trees that are caches or other checkouts,
#: never something to mention (``.claude/worktrees`` holds whole repos).
_SKIP_UNDER_DOT_DIRS: Final[frozenset[str]] = frozenset(
    {"worktrees", "cache", "projects", "plugins", "shell-snapshots", "sessions"}
)
_MAX_CACHED_FOLDERS: Final[int] = 8
_MAX_DEFINITIONS: Final[int] = 400
_DESCRIPTION_CHARS: Final[int] = 160
_FRONTMATTER_BYTES: Final[int] = 8192


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One row of the list.

    ``value`` is what lands in the text box after the trigger character —
    ``commit``, ``github:issue``, ``src/app.py`` — never the character itself.
    """

    value: str
    label: str
    hint: str = ""
    #: skill | command | agent | file | folder
    kind: str = ""
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def triggers_for(runner: str) -> tuple[str, ...]:
    """The trigger characters a seat honours; unknown runners get none."""
    return TRIGGERS_BY_RUNNER.get(runner, ())


def suggest(
    *,
    runner: str,
    cwd: str | Path | None,
    trigger: str,
    query: str = "",
    limit: int = 40,
) -> dict[str, Any]:
    """The list for one trigger on one seat, filtered by ``query``.

    Returns ``{"trigger", "items", "truncated"}``; a trigger the seat does not
    honour yields an empty list rather than an error, so a stale picker on a
    changed seat degrades to "nothing here".
    """
    if trigger not in triggers_for(runner):
        return {"trigger": trigger, "items": [], "truncated": False}
    folder = Path(cwd).expanduser() if cwd else None
    q = (query or "").strip()
    rows: list[Suggestion]
    if trigger == SLASH:
        rows = jarvis_skills() if runner == "brain" else claude_slash(folder)
        rows = _filter_definitions(rows, q)
    elif trigger == SKILL_REF:
        rows = _filter_definitions(codex_skills(folder), q)
    else:
        rows = claude_agents(folder) if runner in _CLAUDE_SHAPED else []
        rows = _filter_definitions(rows, q) if q else rows
        rows = rows + file_suggestions(folder, q, limit=limit)
    truncated = len(rows) > limit
    return {
        "trigger": trigger,
        "items": [r.to_dict() for r in rows[:limit]],
        "truncated": truncated,
    }


# ------------------------------------------------------------- definitions


def _frontmatter(path: Path) -> dict[str, str]:
    """``name`` and ``description`` from a Markdown file's YAML frontmatter.

    A deliberately small reader — the two scalar keys, quoted or bare, plus
    the folded/literal block forms (``>`` / ``|``) collapsed to one line — so
    no YAML dependency sits on this path and a malformed file costs nothing
    but its description.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_FRONTMATTER_BYTES).decode("utf-8", errors="replace")
    except OSError:  # unreadable file: the row keeps its name and loses its description
        return {}
    if not head.startswith("---"):
        return {}
    end = head.find("\n---", 3)
    block = head[3:end] if end != -1 else head[3:]
    out: dict[str, str] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^(name|description)\s*:\s*(.*)$", lines[i])
        i += 1
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if raw in (">", "|", ">-", "|-"):
            parts: list[str] = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                if lines[i].strip():
                    parts.append(lines[i].strip())
                i += 1
            raw = " ".join(parts)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        out[key] = raw.strip()
    return out


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= _DESCRIPTION_CHARS:
        return text
    return text[: _DESCRIPTION_CHARS - 1].rstrip() + "…"


def _hidden(name: str) -> bool:
    return name.startswith(".") or name.startswith("_")


def _skill_dirs(base: Path, *, depth: int = 2) -> list[tuple[str, Path]]:
    """``(name, SKILL.md)`` for every skill folder under ``base``, up to ``depth`` levels.

    A skill is a folder holding a ``SKILL.md``; both Claude Code and Codex
    also accept them one level deeper (a category folder), which is what the
    second level covers.
    """
    found: list[tuple[str, Path]] = []
    if not base.is_dir():
        return found
    queue: deque[tuple[Path, int]] = deque([(base, 0)])
    while queue and len(found) < _MAX_DEFINITIONS:
        current, level = queue.popleft()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:  # a folder that cannot be listed contributes no skills
            continue
        for entry in entries:
            if _hidden(entry.name):
                continue
            try:
                # follow_symlinks on purpose: account dirs symlink their skills.
                if not entry.is_dir(follow_symlinks=True):
                    continue
            except OSError:  # an entry that vanished mid-scan is not a skill
                continue
            skill_md = Path(entry.path) / "SKILL.md"
            if skill_md.is_file():
                found.append((entry.name, skill_md))
            elif level + 1 < depth:
                queue.append((Path(entry.path), level + 1))
    return found


def _command_files(base: Path) -> list[tuple[str, Path]]:
    """``(name, file)`` for every ``*.md`` under ``base`` — nested ones keep their stem."""
    found: list[tuple[str, Path]] = []
    if not base.is_dir():
        return found
    queue: deque[tuple[Path, int]] = deque([(base, 0)])
    while queue and len(found) < _MAX_DEFINITIONS:
        current, level = queue.popleft()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:  # a folder that cannot be listed contributes no commands
            continue
        for entry in entries:
            if _hidden(entry.name):
                continue
            try:
                if entry.is_dir(follow_symlinks=True):
                    if level < 2:
                        queue.append((Path(entry.path), level + 1))
                    continue
                if not entry.is_file(follow_symlinks=True):
                    continue
            except OSError:  # an entry that vanished mid-scan is not a command
                continue
            if entry.name.lower().endswith(".md") and entry.name.upper() != "README.MD":
                found.append((entry.name[:-3], Path(entry.path)))
    return found


def _definition_rows(
    pairs: list[tuple[str, Path]],
    *,
    kind: str,
    group: str,
    prefix: str = "",
    seen: set[str],
) -> list[Suggestion]:
    rows: list[Suggestion] = []
    for name, path in pairs:
        value = f"{prefix}{name}"
        if value in seen:
            continue
        seen.add(value)
        meta = _frontmatter(path)
        rows.append(
            Suggestion(
                value=value,
                label=value,
                hint=_short(meta.get("description", "")),
                kind=kind,
                group=group,
            )
        )
    return rows


def _project_roots(folder: Path | None) -> list[Path]:
    """The ``.claude`` tree and its ``.agents`` twin inside the chat's folder."""
    if folder is None:
        return []
    return [folder / ".claude", folder / ".agents"]


def claude_config_dir() -> Path:
    """The Claude Code seat's config dir — the account the chat spawns on."""
    try:
        from jarvis.agent_chat.runner_cli import _account_env

        raw = _account_env("claude").get("CLAUDE_CONFIG_DIR", "").strip()
    except Exception:  # noqa: BLE001 — no account layer: the CLI's own default
        log.debug("typeahead: claude account env unavailable", exc_info=True)
        raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def codex_home() -> Path:
    """The Codex seat's home — ``CODEX_HOME`` of the account, else ``~/.codex``."""
    try:
        from jarvis.agent_chat.runner_cli import _account_env

        raw = _account_env("codex").get("CODEX_HOME", "").strip()
    except Exception:  # noqa: BLE001 — no account layer: the CLI's own default
        log.debug("typeahead: codex account env unavailable", exc_info=True)
        raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _installed_plugins(config_dir: Path) -> list[tuple[str, Path]]:
    """``(plugin, install_path)`` for every plugin Claude Code has installed AND enabled.

    ``plugins/installed_plugins.json`` (v2) maps ``name@marketplace`` to its
    install records; the user-scoped record wins, else the first whose path
    still exists. ``settings.json``'s ``enabledPlugins`` may switch one off.
    """
    manifest = config_dir / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no manifest, or a broken one: no plugins — not an error
        return []
    enabled: dict[str, Any] = {}
    try:
        settings = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))
        if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
            enabled = settings["enabledPlugins"]
    except (OSError, ValueError):  # no settings file: every installed plugin counts as enabled
        pass
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return []
    out: list[tuple[str, Path]] = []
    for key, records in sorted(plugins.items()):
        if enabled.get(key) is False:
            continue
        name = str(key).split("@", 1)[0]
        if not name:
            continue
        candidates = records if isinstance(records, list) else [records]
        chosen: Path | None = None
        for rec in sorted(
            (r for r in candidates if isinstance(r, dict)),
            key=lambda r: 0 if r.get("scope") == "user" else 1,
        ):
            path = Path(str(rec.get("installPath") or "")).expanduser()
            if rec.get("installPath") and path.is_dir():
                chosen = path
                break
        if chosen is not None:
            out.append((name, chosen))
    return out


def claude_slash(folder: Path | None) -> list[Suggestion]:
    """Claude Code's ``/`` list for this folder and this seat.

    Project skills and commands first (they shadow the account's), then the
    account's own, then every enabled plugin's as ``plugin:name`` — the
    spelling Claude Code itself lists them under.
    """
    seen: set[str] = set()
    rows: list[Suggestion] = []
    for root in _project_roots(folder):
        rows += _definition_rows(
            _skill_dirs(root / "skills"), kind="skill", group=GROUP_PROJECT, seen=seen
        )
        rows += _definition_rows(
            _command_files(root / "commands"), kind="command", group=GROUP_PROJECT, seen=seen
        )
    account = claude_config_dir()
    rows += _definition_rows(
        _skill_dirs(account / "skills"), kind="skill", group=GROUP_ACCOUNT, seen=seen
    )
    rows += _definition_rows(
        _command_files(account / "commands"), kind="command", group=GROUP_ACCOUNT, seen=seen
    )
    for plugin, install in _installed_plugins(account):
        rows += _definition_rows(
            _skill_dirs(install / "skills"),
            kind="skill",
            group=GROUP_PLUGINS,
            prefix=f"{plugin}:",
            seen=seen,
        )
        rows += _definition_rows(
            _command_files(install / "commands"),
            kind="command",
            group=GROUP_PLUGINS,
            prefix=f"{plugin}:",
            seen=seen,
        )
    return rows


def claude_agents(folder: Path | None) -> list[Suggestion]:
    """Claude Code's subagents — project, account, plugins — for ``@name``."""
    seen: set[str] = set()
    rows: list[Suggestion] = []
    for root in _project_roots(folder):
        rows += _definition_rows(
            [(n, p) for n, p in _command_files(root / "agents") if n != "INDEX"],
            kind="agent",
            group=GROUP_AGENTS,
            seen=seen,
        )
    account = claude_config_dir()
    rows += _definition_rows(
        [(n, p) for n, p in _command_files(account / "agents") if n != "INDEX"],
        kind="agent",
        group=GROUP_AGENTS,
        seen=seen,
    )
    for plugin, install in _installed_plugins(account):
        rows += _definition_rows(
            _command_files(install / "agents"),
            kind="agent",
            group=GROUP_AGENTS,
            prefix=f"{plugin}:",
            seen=seen,
        )
    return rows


def codex_skills(folder: Path | None) -> list[Suggestion]:
    """Codex's ``$`` list.

    The folder's ``.agents/skills`` (and its ``.claude`` twin) first, then the
    account's ``CODEX_HOME/skills``.
    """
    seen: set[str] = set()
    rows: list[Suggestion] = []
    if folder is not None:
        for root in (folder / ".agents", folder / ".claude"):
            rows += _definition_rows(
                _skill_dirs(root / "skills"), kind="skill", group=GROUP_PROJECT, seen=seen
            )
    rows += _definition_rows(
        _skill_dirs(codex_home() / "skills"), kind="skill", group=GROUP_ACCOUNT, seen=seen
    )
    return rows


def jarvis_skills() -> list[Suggestion]:
    """The Jarvis surface's ``/`` list: every active skill of the registry, by slug."""
    try:
        from jarvis.skills.skill_context import try_get_skill_context

        ctx = try_get_skill_context()
    except Exception:  # noqa: BLE001 — the skill layer is optional at boot
        log.debug("typeahead: skill context unavailable", exc_info=True)
        return []
    if ctx is None:
        return []
    rows: list[Suggestion] = []
    seen: set[str] = set()
    active: list[Any] = list(ctx.registry.list_active())
    for skill in sorted(active, key=lambda s: str(s.name).lower()):
        try:
            slug = str(skill.path.parent.name) or str(skill.name)
        except Exception:  # noqa: BLE001 — a pathless skill keeps its name
            slug = str(skill.name)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        fm = skill.frontmatter
        description = str(getattr(fm, "description", "") or "") if fm is not None else ""
        rows.append(
            Suggestion(
                value=slug,
                label=str(skill.name) or slug,
                hint=_short(description),
                kind="skill",
                group=GROUP_JARVIS,
            )
        )
    return rows


def _filter_definitions(rows: list[Suggestion], query: str) -> list[Suggestion]:
    """Prefix matches first, then substring — on the value, the label, or the hint."""
    if not query:
        return rows
    q = query.lower()
    ranked: list[tuple[int, int, Suggestion]] = []
    for idx, row in enumerate(rows):
        value = row.value.lower()
        label = row.label.lower()
        if value.startswith(q) or label.startswith(q):
            score = 0
        elif q in value or q in label:
            score = 1
        elif q in row.hint.lower():
            score = 2
        else:
            continue
        ranked.append((score, idx, row))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in ranked]


# ------------------------------------------------------------------- files


@dataclass(frozen=True, slots=True)
class _FileEntry:
    path: str  # POSIX, relative to the folder; folders end in "/"
    name: str
    is_dir: bool
    depth: int


#: folder -> (walked_at, entries, truncated)
_walk_cache: dict[str, tuple[float, list[_FileEntry], bool]] = {}


def _skip_dir(name: str) -> bool:
    from jarvis.agentic_ide.folders import SKIP_DIRS

    if name in SKIP_DIRS:
        return True
    return name.startswith(".") and name not in _VISIBLE_DOT_DIRS


def _git_entries(folder: Path) -> tuple[list[_FileEntry], bool] | None:
    """What git would show: tracked files plus untracked ones ``.gitignore`` lets through.

    The list a coding CLI's own ``@`` shows is exactly this — no build
    output, no virtualenv, no ``node_modules`` — and git answers it in a
    fraction of the time a walk needs (9 500 paths in 0.1 s on the
    maintainer's checkout). ``None`` when the folder is no checkout or git
    cannot answer; the walk then stands in.
    """
    try:
        if not (folder / ".git").exists():
            return None
    except OSError:  # an unreadable folder is no checkout; the walk decides
        return None
    git = shutil.which("git")
    if not git:
        return None
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        proc = subprocess.run(
            [
                git,
                "-C",
                str(folder),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],  # noqa: E501
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("typeahead: git ls-files failed in %s", folder, exc_info=True)
        return None
    if proc.returncode != 0:
        return None
    paths = [p for p in proc.stdout.decode("utf-8", errors="replace").split("\0") if p]
    truncated = len(paths) > _MAX_WALK_ENTRIES
    files: list[_FileEntry] = []
    folders: dict[str, _FileEntry] = {}
    for rel in paths[:_MAX_WALK_ENTRIES]:
        rel = rel.replace("\\", "/")
        if rel.endswith("/"):
            # An untracked nested checkout shows as its directory alone.
            rel = rel[:-1]
            entry = _FileEntry(rel + "/", rel.rsplit("/", 1)[-1], True, rel.count("/"))
            folders.setdefault(entry.path, entry)
            continue
        parts = rel.split("/")
        files.append(_FileEntry(rel, parts[-1], False, len(parts) - 1))
        for i in range(1, len(parts)):
            key = "/".join(parts[:i]) + "/"
            if key not in folders:
                folders[key] = _FileEntry(key, parts[i - 1], True, i - 1)
    entries = list(folders.values()) + files
    entries.sort(key=lambda e: (e.depth, not e.is_dir, e.path.lower()))
    return entries, truncated


def _walk(folder: Path) -> tuple[list[_FileEntry], bool]:
    """Breadth-first, bounded listing of ``folder``: near things first (the no-git fallback)."""
    entries: list[_FileEntry] = []
    truncated = False
    started = time.monotonic()
    queue: deque[tuple[Path, str, int]] = deque([(folder, "", 0)])
    while queue:
        current, rel, depth = queue.popleft()
        if time.monotonic() - started > _MAX_WALK_SECONDS:
            truncated = True
            break
        try:
            with os.scandir(current) as it:
                listed = sorted(it, key=lambda e: (not _safe_is_dir(e), e.name.lower()))
        except OSError:  # a folder that cannot be listed is skipped; the rest still lists
            continue
        for entry in listed:
            if len(entries) >= _MAX_WALK_ENTRIES:
                truncated = True
                queue.clear()
                break
            is_dir = _safe_is_dir(entry)
            if is_dir and _skip_dir(entry.name):
                continue
            if is_dir and rel.startswith(".") and entry.name in _SKIP_UNDER_DOT_DIRS:
                continue
            if not is_dir and entry.name.startswith("."):
                continue
            child_rel = f"{rel}{entry.name}"
            if is_dir:
                entries.append(_FileEntry(child_rel + "/", entry.name, True, depth))
                if depth + 1 < _MAX_WALK_DEPTH:
                    queue.append((Path(entry.path), child_rel + "/", depth + 1))
            else:
                entries.append(_FileEntry(child_rel, entry.name, False, depth))
    return entries, truncated


def _safe_is_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:  # an entry that vanished mid-scan is treated as a file and drops out
        return False


def _entries_for(folder: Path) -> tuple[list[_FileEntry], bool]:
    key = str(folder)
    now = time.monotonic()
    cached = _walk_cache.get(key)
    if cached and now - cached[0] < _WALK_TTL_S:
        return cached[1], cached[2]
    listed = _git_entries(folder)
    entries, truncated = listed if listed is not None else _walk(folder)
    if len(_walk_cache) >= _MAX_CACHED_FOLDERS:
        oldest = min(_walk_cache, key=lambda k: _walk_cache[k][0])
        _walk_cache.pop(oldest, None)
    _walk_cache[key] = (now, entries, truncated)
    return entries, truncated


def forget_folder(folder: str | Path | None = None) -> None:
    """Drop the cached walk (all folders when none is named) — tests and file changes."""
    if folder is None:
        _walk_cache.clear()
    else:
        _walk_cache.pop(str(Path(folder).expanduser()), None)


def _subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def file_suggestions(folder: Path | None, query: str, *, limit: int = 40) -> list[Suggestion]:
    """Files and folders under ``folder`` matching ``query``, nearest and best first.

    Empty query: the top of the tree in walk order. Otherwise ranked — a name
    that starts with the query, a name that contains it, a path that
    contains it, a path where its letters appear in order — and within a
    rank the shallower path wins, so ``src/app.py`` beats ``vendor/x/app.py``.
    The letters-in-order match is on the name alone and from three letters
    up: on a whole path almost any word matches, which is noise, not help.
    """
    if folder is None:
        return []
    try:
        if not folder.is_dir():
            return []
    except OSError:  # an unreadable folder has no files to offer
        return []
    entries, _ = _entries_for(folder)
    q = query.strip().lower().replace("\\", "/")
    picked: list[_FileEntry]
    if not q:
        picked = entries[:limit]
    else:
        ranked: list[tuple[int, int, str, _FileEntry]] = []
        for e in entries:
            name = e.name.lower()
            path = e.path.lower()
            if name.startswith(q):
                score = 0
            elif q in name:
                score = 1
            elif q in path:
                score = 2
            elif len(q) >= 3 and _subsequence(q, name):
                score = 3
            else:
                continue
            ranked.append((score, e.depth, path, e))
        ranked.sort(key=lambda t: (t[0], t[1], t[2]))
        picked = [e for _, _, _, e in ranked[:limit]]
    return [
        Suggestion(
            value=e.path,
            label=e.name + ("/" if e.is_dir else ""),
            hint=str(PurePosixPath(e.path).parent) if e.depth > 0 else "",
            kind="folder" if e.is_dir else "file",
            group=GROUP_FILES,
        )
        for e in picked
    ]


__all__ = [
    "GROUP_ACCOUNT",
    "GROUP_AGENTS",
    "GROUP_FILES",
    "GROUP_JARVIS",
    "GROUP_PLUGINS",
    "GROUP_PROJECT",
    "MENTION",
    "SKILL_REF",
    "SLASH",
    "TRIGGERS_BY_RUNNER",
    "Suggestion",
    "claude_agents",
    "claude_config_dir",
    "claude_slash",
    "codex_home",
    "codex_skills",
    "file_suggestions",
    "forget_folder",
    "jarvis_skills",
    "suggest",
    "triggers_for",
]

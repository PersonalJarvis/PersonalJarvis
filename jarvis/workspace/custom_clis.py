"""Terminal CLIs the USER adds — the same registry, filled from a file.

:mod:`jarvis.workspace.agents` is deliberately an open registry: a new
interactive CLI is a spec rather than a code change spread across detection,
launching and the UI. Until now the only way to put something in it was to ship
Python, so an install could run exactly the CLIs this project happened to know
about. That is the wrong boundary for a tool whose whole job is to open other
people's terminals: a coding CLI is released roughly monthly, and waiting for
a release of THIS app to be able to open one is not a workflow.

So an entry here is the same :class:`~jarvis.workspace.agents.WorkspaceAgent`
the built-ins are, built from four things the user actually knows — a name, the
command that starts it, a line of description, and (optionally) a logo. Once
stored it is offered by every surface that reads the registry: the setup
wizard's terminal allocation, the "open a terminal — what?" picker, the pane
split menu, the CLI and the voice catalog.

**Not the shared CLI catalog.** ``jarvis/clis/catalog`` also has a custom.json,
and it is a different thing on purpose: entries there become brain-callable
tools. An entry here is only ever something a terminal RUNS, chosen by a person
looking at a picker. Keeping the two stores apart is what stops a command typed
into a terminal picker from quietly becoming a tool an LLM may call (AP-5/14).

**What a custom entry deliberately does NOT get**, because guessing would be
worse than the honest gap:

* *A version number.* Detection for built-ins runs ``<binary> --version`` — one
  subprocess per entry on the shared event loop, which is where the wake
  microphone is delivered. A stranger's command has no promise to answer that
  flag; plenty of TUIs would simply OPEN, and the pane's boot would then hold
  the loop the wake word arrives on. A custom entry is therefore detected by
  looking its first word up on PATH, and reports no version at all.
* *An install command.* We do not know how it is installed, and offering a
  command that cannot work is worse than saying nothing.
* *Trust pre-seeding, several subscriptions, conversation resume.* Each of
  those is a file format belonging to one specific vendor. Every layer already
  degrades honestly when an entry declares none of them.

Cross-platform: the command is stored verbatim and started through this host's
own shell (``jarvis.terminal.shells``), so a Windows entry may be a ``.cmd`` and
a Linux one a shell function without either needing to know about the other.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from jarvis.core.paths import workspace_clis_dir, workspace_clis_path

log = logging.getLogger(__name__)

#: Bumped whenever the stored set changes, so the agent registry can notice
#: without re-reading the file on every lookup.
_revision = 0

#: Longest a name may be. Long enough for "Antigravity Coding Plan", short
#: enough that the picker row it lands in stays one line.
MAX_NAME_LEN = 60
#: A command line, not a script. Anything longer is a shell script that wants to
#: live in a file and be called by name.
MAX_COMMAND_LEN = 500
MAX_DESCRIPTION_LEN = 200

#: Upload ceiling for a logo. A brand mark is a few KB of SVG or a small PNG;
#: a megabyte is already a photograph that will be drawn at 20 pixels.
MAX_LOGO_BYTES = 1024 * 1024

#: Image kinds a logo may be, mapped to what the browser is told they are.
LOGO_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

#: First bytes that prove a file is what its extension claims. Checked because
#: the store hands these bytes back out over HTTP: an "svg" that is really a
#: zip is not a logo, and the honest moment to say so is the upload.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}

#: Shell characters that mean the command cannot be exec'd as a single process.
#: Their presence is not an error — it decides HOW the entry is started, see
#: :func:`needs_shell`.
_SHELL_META = set("|&;<>()$`\n\r")

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: ``NAME=value`` — an environment assignment in front of the actual command.
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_assignment(token: str) -> bool:
    """Is this token an environment assignment rather than a program name?

    ``KIMI_NO_UPDATE=1 kimi`` is a shape people copy straight out of a README,
    and reading its first word as the binary looks for a program called
    ``KIMI_NO_UPDATE=1``, never finds it, and reports a working command as
    "not installed".
    """
    return bool(_ASSIGNMENT_RE.match(token))


@dataclass(frozen=True, slots=True)
class CustomCli:
    """One CLI the user added, exactly as it is stored.

    ``id`` is assigned once, at creation, and never changes again — renaming the
    entry must not orphan the open panes, saved workspaces and resume offers
    that recorded which agent they were running.
    """

    id: str
    display_name: str
    command: str
    description: str = ""
    #: Logo file name inside :func:`logo_dir`, or empty for none.
    logo: str = ""
    #: How a dropped file is written into this CLI's prompt. Same meaning as
    #: :attr:`~jarvis.workspace.agents.WorkspaceAgent.file_reference`; ``@path``
    #: is what most modern coding CLIs read, but a wrong guess silently pastes
    #: a path a CLI treats as prose, so it stays the user's choice.
    file_reference: Literal["at", "quoted"] = "quoted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "command": self.command,
            "description": self.description,
            "logo": self.logo,
            "file_reference": self.file_reference,
        }

    @property
    def binary(self) -> str:
        """The word that has to be on PATH for this command to start.

        Usually the first one — but not when the line opens with environment
        assignments (``KIMI_NO_UPDATE=1 kimi``), which is a shape people copy
        straight out of a README. Taking the first word literally there looks
        for a binary called ``KIMI_NO_UPDATE=1``, never finds it, and reports a
        perfectly good command as "not installed". Empty when nothing in the
        line looks like a command name, which the caller reads as "this cannot
        be checked ahead of time".
        """
        for part in split_command(self.command):
            if _is_assignment(part):
                continue
            return part
        return ""


class CustomCliError(ValueError):
    """A stored entry was asked for something it cannot be given.

    Carries a message meant to be READ by the person who typed the form, so
    every raise site says what to change rather than what went wrong.
    """


# --------------------------------------------------------------------------
# Command handling
# --------------------------------------------------------------------------


def split_command(command: str) -> tuple[str, ...]:
    """Split a command line into words, the same way on every OS.

    Not :func:`shlex.split`. Its POSIX mode treats a backslash as an escape
    character, which turns ``C:\\tools\\agy.exe`` into ``C:toolsagy.exe`` — a
    binary that does not exist, reported as "not installed" on the one platform
    where that path shape is normal. Its non-POSIX mode keeps the quotes glued
    to the token instead, which fails the same lookup for a different reason.

    So: quotes group, whitespace separates, and a backslash is just a character.
    """
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    try:
        return tuple(part for part in lexer if part)
    except ValueError:
        # An unbalanced quote. The command is still storable and still runnable
        # through a shell, which is where the user will see the real complaint.
        return tuple(part for part in command.split() if part)


def needs_shell(command: str) -> bool:
    """Does this command need a shell around it, or can it be exec'd directly?

    A pipeline, a variable expansion or a chain of two commands is not an argv —
    it is shell source. Launching it as a process would look for a binary whose
    name contains a pipe character. An environment assignment in front counts
    too: only a shell knows that ``FOO=1 tool`` means "set FOO, then run tool"
    rather than "run a program called FOO=1".

    Answered on the text rather than stored, because it is a property of the
    command and would only be one more field to keep in step with it.
    """
    if any(char in _SHELL_META for char in command):
        return True
    words = split_command(command)
    return bool(words) and _is_assignment(words[0])


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


def logo_dir() -> Path:
    return workspace_clis_dir() / "logos"


def revision() -> int:
    """Changes since this process started — a cache key for the registry."""
    return _revision


def _bump() -> None:
    global _revision
    _revision += 1


def _read_raw() -> list[dict[str, Any]]:
    path = workspace_clis_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt store must not brick boot
        log.warning("workspace-clis: %s is unreadable (%s); ignoring it", path, exc)
        return []
    entries = data if isinstance(data, list) else data.get("entries", [])
    return [e for e in entries if isinstance(e, dict)]


def _parse(raw: Mapping[str, Any]) -> CustomCli | None:
    """One stored record, or ``None`` when it is not usable.

    Skipping beats raising: one hand-edited entry must not take the other five
    down with it, and the user's route to fixing it is the same UI that lists
    the ones that survived.
    """
    entry_id = str(raw.get("id") or "").strip()
    name = str(raw.get("display_name") or "").strip()
    command = str(raw.get("command") or "").strip()
    if not entry_id or not name or not command:
        log.warning("workspace-clis: skipping incomplete entry %r", raw)
        return None
    reference = str(raw.get("file_reference") or "quoted")
    return CustomCli(
        id=entry_id,
        display_name=name[:MAX_NAME_LEN],
        command=command[:MAX_COMMAND_LEN],
        description=str(raw.get("description") or "")[:MAX_DESCRIPTION_LEN],
        logo=str(raw.get("logo") or "").strip(),
        file_reference="at" if reference == "at" else "quoted",
    )


def list_custom_clis() -> list[CustomCli]:
    """Every stored entry, in the order the user put them in."""
    out: list[CustomCli] = []
    seen: set[str] = set()
    for raw in _read_raw():
        entry = _parse(raw)
        if entry is None or entry.id in seen:
            continue
        seen.add(entry.id)
        out.append(entry)
    return out


def get_custom_cli(entry_id: str) -> CustomCli | None:
    return next((e for e in list_custom_clis() if e.id == entry_id), None)


def _write(entries: Iterable[CustomCli]) -> None:
    """Persist the whole list, atomically.

    Written to a sibling and moved into place: a half-written store is a store
    that reads as empty on the next boot, and the user's own CLIs quietly
    disappearing is the one failure this file must not have.
    """
    path = workspace_clis_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "entries": [entry.to_dict() for entry in entries],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    _bump()


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _clean_name(value: str) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise CustomCliError("Give the CLI a name.")
    if len(name) > MAX_NAME_LEN:
        raise CustomCliError(f"Keep the name under {MAX_NAME_LEN} characters.")
    return name


def _clean_command(value: str) -> str:
    command = str(value or "").strip()
    if not command:
        raise CustomCliError("Give the command that starts this CLI.")
    if "\n" in command or "\r" in command:
        raise CustomCliError("The command has to be a single line.")
    if len(command) > MAX_COMMAND_LEN:
        raise CustomCliError(f"Keep the command under {MAX_COMMAND_LEN} characters.")
    return command


def _clean_description(value: str) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_DESCRIPTION_LEN]


def _slug(name: str) -> str:
    """A stable, filesystem- and URL-safe id derived from the name.

    Accents are folded rather than dropped, so "Café CLI" becomes ``cafe-cli``
    rather than ``cli`` — an id built only from the ASCII that survived is one
    the user cannot recognise as theirs.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_RE.sub("-", ascii_only).strip("-")
    return slug or "cli"


def _taken_ids() -> set[str]:
    """Every name a new entry may not claim — built-in registry keys included.

    A second thing answering to "codex" is the kind of collision that surfaces
    as a pane running the wrong tool, so the check covers the built-ins even
    though they live in a different module.
    """
    from jarvis.workspace import agents as workspace_agents

    taken = {entry.id for entry in list_custom_clis()}
    taken |= set(workspace_agents.builtin_names())
    return taken


def _free_id(name: str) -> str:
    base = _slug(name)
    taken = _taken_ids()
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise CustomCliError("Too many CLIs with that name — pick a different one.")


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------


def create_custom_cli(
    display_name: str,
    command: str,
    *,
    description: str = "",
    file_reference: str = "quoted",
) -> CustomCli:
    """Store a new entry and return it, id assigned."""
    name = _clean_name(display_name)
    entry = CustomCli(
        id=_free_id(name),
        display_name=name,
        command=_clean_command(command),
        description=_clean_description(description),
        file_reference="at" if file_reference == "at" else "quoted",
    )
    _write([*list_custom_clis(), entry])
    return entry


def update_custom_cli(
    entry_id: str,
    *,
    display_name: str | None = None,
    command: str | None = None,
    description: str | None = None,
    file_reference: str | None = None,
) -> CustomCli:
    """Change a stored entry in place. The id is never touched — see the class."""
    entries = list_custom_clis()
    index = next((i for i, e in enumerate(entries) if e.id == entry_id), -1)
    if index < 0:
        raise CustomCliError("That CLI is not in the list any more.")
    entry = entries[index]
    if display_name is not None:
        entry = replace(entry, display_name=_clean_name(display_name))
    if command is not None:
        entry = replace(entry, command=_clean_command(command))
    if description is not None:
        entry = replace(entry, description=_clean_description(description))
    if file_reference is not None:
        entry = replace(
            entry, file_reference="at" if file_reference == "at" else "quoted"
        )
    entries[index] = entry
    _write(entries)
    return entry


def delete_custom_cli(entry_id: str) -> CustomCli:
    """Forget an entry and its logo. Open panes running it are left alone.

    A pane is a live process with a conversation in it; killing it because its
    menu entry was deleted would throw away work the user never asked to lose.
    It keeps running under its own name and simply cannot be started again.
    """
    entries = list_custom_clis()
    entry = next((e for e in entries if e.id == entry_id), None)
    if entry is None:
        raise CustomCliError("That CLI is not in the list any more.")
    _write([e for e in entries if e.id != entry_id])
    _remove_logo_files(entry_id)
    return entry


# --------------------------------------------------------------------------
# Logos
# --------------------------------------------------------------------------


def _remove_logo_files(entry_id: str) -> None:
    for suffix in LOGO_TYPES:
        candidate = logo_dir() / f"{entry_id}{suffix}"
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:  # noqa: PERF203 - one failure must not stop the rest
            log.warning("workspace-clis: could not remove %s: %s", candidate, exc)


def _looks_like(suffix: str, data: bytes) -> bool:
    """Do these bytes match what the extension claims?

    The store hands this file back out over HTTP, so "the user said it was a
    PNG" is not enough to serve it as one. SVG is text and has no magic number,
    so it is checked for the one tag that has to be in it.
    """
    if suffix == ".svg":
        head = data[:4096].lstrip()
        try:
            text = head.decode("utf-8", errors="ignore").lower()
        except Exception:  # noqa: BLE001 - undecodable is simply not an SVG
            return False
        return "<svg" in text
    return any(data.startswith(magic) for magic in _MAGIC.get(suffix, ()))


def set_logo(entry_id: str, data: bytes, filename: str) -> CustomCli:
    """Store an image as this entry's mark and return the updated entry."""
    entries = list_custom_clis()
    index = next((i for i, e in enumerate(entries) if e.id == entry_id), -1)
    if index < 0:
        raise CustomCliError("That CLI is not in the list any more.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in LOGO_TYPES:
        readable = ", ".join(sorted(s.lstrip(".").upper() for s in LOGO_TYPES))
        raise CustomCliError(f"A logo has to be one of: {readable}.")
    if not data:
        raise CustomCliError("That file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise CustomCliError(
            f"Keep the logo under {MAX_LOGO_BYTES // 1024} KB."
        )
    if not _looks_like(suffix, data):
        raise CustomCliError(f"That file is not really a {suffix.lstrip('.').upper()}.")

    # One logo per entry: the old file goes first, so a switch from PNG to SVG
    # does not leave a stale sibling that a later lookup could pick up.
    _remove_logo_files(entry_id)
    target = logo_dir() / f"{entry_id}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    entries[index] = replace(entries[index], logo=target.name)
    _write(entries)
    return entries[index]


def clear_logo(entry_id: str) -> CustomCli:
    """Drop the logo and fall back to the monogram the UI draws without one."""
    entries = list_custom_clis()
    index = next((i for i, e in enumerate(entries) if e.id == entry_id), -1)
    if index < 0:
        raise CustomCliError("That CLI is not in the list any more.")
    _remove_logo_files(entry_id)
    entries[index] = replace(entries[index], logo="")
    _write(entries)
    return entries[index]


def logo_file(entry_id: str) -> tuple[Path, str] | None:
    """``(path, media type)`` of this entry's logo, or ``None`` if it has none.

    The file name is rebuilt from the id rather than trusted from the store, so
    a hand-edited ``"logo": "../../.env"`` reads as "no logo" instead of as a
    path traversal out of the logo directory.
    """
    entry = get_custom_cli(entry_id)
    if entry is None or not entry.logo:
        return None
    suffix = Path(entry.logo).suffix.lower()
    media = LOGO_TYPES.get(suffix)
    if media is None:
        return None
    path = logo_dir() / f"{entry_id}{suffix}"
    return (path, media) if path.is_file() else None


def logo_url(entry: CustomCli) -> str:
    """Where the UI fetches this entry's mark, or empty when it has none."""
    return f"/api/workspace-clis/{entry.id}/logo" if entry.logo else ""


__all__ = [
    "LOGO_TYPES",
    "MAX_COMMAND_LEN",
    "MAX_DESCRIPTION_LEN",
    "MAX_LOGO_BYTES",
    "MAX_NAME_LEN",
    "CustomCli",
    "CustomCliError",
    "clear_logo",
    "create_custom_cli",
    "delete_custom_cli",
    "get_custom_cli",
    "list_custom_clis",
    "logo_dir",
    "logo_file",
    "logo_url",
    "needs_shell",
    "revision",
    "set_logo",
    "split_command",
    "update_custom_cli",
]

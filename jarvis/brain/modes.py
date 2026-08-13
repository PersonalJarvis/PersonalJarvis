"""Assistant modes: a shelf of named characters, one of them active.

A *mode* is how the assistant behaves — the difference between a butler who
answers in one line and a friend who asks how your day went. Until now there
was exactly one character (``JARVIS_PERSONA.md``) plus a single-slot override
(``data/custom_system_prompt.md``). This module turns that one slot into a
library.

Why a mode is a LAYER, never a replacement
------------------------------------------
The packaged persona carries the rules that keep the assistant honest — never
claim an action that did not happen, never read a path aloud, never invent a
result. A mode that replaced the whole prompt would drop those rules the moment
the user picked "Friend", and the failure would look like a personality quirk
rather than the safety regression it is (the BUG-136 class: a spoken claim that
ran ahead of reality).

So a mode contributes only a *character block*, appended to the base persona by
``persona_loader.load_effective_persona_prompt``. The base always wins on
honesty; the mode decides tone, length, and how much the assistant volunteers.
The ``assistant`` built-in contributes an EMPTY block on purpose: the default
mode reproduces today's behaviour byte for byte, so shipping this feature
changes nothing until the user chooses otherwise.

Where a mode lives
------------------
Built-ins are packaged Markdown next to this file (``jarvis/brain/modes/``) —
readable, forkable, and never written to at runtime. User modes are sidecar
files under ``DATA_DIR/modes/``, exactly the arrangement
``persona_loader.custom_prompt_path`` already uses, so "reset" is a delete and
the packaged set is always recoverable.

Which mode is active
--------------------
Two layers, and the split is the whole point:

* ``[persona] active_mode`` in ``jarvis.toml`` — the user's deliberate choice.
  Sticky, survives a restart, written only through ``config_writer``.
* An in-memory *section override* — what the Agentic IDE section sets while it
  is on screen. Deliberately NOT persisted: a mode that a screen switched on
  must not outlive the process. That is precisely how coding mode became
  permanently stuck (``AgenticIdeView`` switched it on and nothing ever switched
  it off), and a non-persisted override makes that class of bug structurally
  impossible — the worst case is one session, not forever.

Five-layer enum discipline (``docs/anti-drift-three-layer.md``): ``VERBOSITIES``
and ``PROACTIVITIES`` are the single source of truth here; the Pydantic Literals
in ``jarvis/ui/web/modes_routes.py`` assert against these tuples at import time,
and the TypeScript union mirrors them.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 0 — the single source of truth for every enum-like value below.
# ---------------------------------------------------------------------------

VERBOSITY_BRIEF = "brief"
VERBOSITY_NORMAL = "normal"
VERBOSITY_RICH = "rich"
VERBOSITIES: tuple[str, ...] = (VERBOSITY_BRIEF, VERBOSITY_NORMAL, VERBOSITY_RICH)

PROACTIVITY_REACTIVE = "reactive"
PROACTIVITY_NORMAL = "normal"
PROACTIVITY_FORWARD = "forward"
PROACTIVITIES: tuple[str, ...] = (
    PROACTIVITY_REACTIVE,
    PROACTIVITY_NORMAL,
    PROACTIVITY_FORWARD,
)

# Built-in slugs. Symbolic so a typo is an AttributeError at import, not a
# silently missing mode weeks later (defence D1 of the anti-drift doc).
MODE_ASSISTANT = "assistant"
MODE_FRIEND = "friend"
MODE_COACH = "coach"
MODE_FOCUS = "focus"
MODE_CODING = "coding"
BUILTIN_SLUGS: tuple[str, ...] = (
    MODE_ASSISTANT,
    MODE_FRIEND,
    MODE_COACH,
    MODE_FOCUS,
    MODE_CODING,
)

#: The mode in force when the user has never chosen one. Reproduces the
#: pre-modes behaviour exactly (empty character block).
DEFAULT_MODE = MODE_ASSISTANT

#: Slug grammar. Restrictive on purpose: the slug becomes a FILE NAME written
#: by an HTTP route and by a tool the realtime model can call, so anything that
#: could escape ``modes_dir()`` — separators, dots, absolute paths, NTFS
#: alternate data streams — must not survive validation.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

#: Reserved on Windows regardless of extension; a mode called "con" would create
#: a file nobody can delete afterwards.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_MODE_FILE_SUFFIX = ".md"
_PACKAGED_DIR_NAME = "modes"

# Guards the in-memory section override. Voice turns, HTTP handlers and the UI
# poll read it from different threads.
_override_lock = threading.Lock()
_section_override: str | None = None


class ModeError(ValueError):
    """A mode could not be resolved, validated, or written."""


@dataclass(frozen=True)
class Mode:
    """One selectable character.

    ``character`` is the block appended to the base persona — NOT a complete
    system prompt. Empty means "add nothing", which is what ``assistant`` does.
    """

    slug: str
    name: str
    emoji: str
    description: str
    character: str
    built_in: bool = False
    #: TTS voice id this mode prefers. Empty keeps the configured voice — a
    #: mode that named a voice the active provider does not have would
    #: otherwise mute the assistant rather than merely sounding wrong.
    voice: str = ""
    verbosity: str = VERBOSITY_NORMAL
    proactivity: str = PROACTIVITY_NORMAL

    def to_payload(self) -> dict[str, object]:
        """JSON-ready shape shared by the REST routes and the CLI."""
        return {
            "slug": self.slug,
            "name": self.name,
            "emoji": self.emoji,
            "description": self.description,
            "character": self.character,
            "built_in": self.built_in,
            "voice": self.voice,
            "verbosity": self.verbosity,
            "proactivity": self.proactivity,
        }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def packaged_modes_dir() -> Path:
    """Directory holding the shipped built-in mode files (read-only)."""
    return Path(__file__).resolve().parent / _PACKAGED_DIR_NAME


def modes_dir() -> Path:
    """Directory holding the user's own modes.

    Resolved at call time from ``jarvis.core.config.DATA_DIR`` — the same
    convention ``persona_loader.custom_prompt_path`` follows — so tests can
    redirect it and a headless install can relocate the whole data directory.
    """
    from jarvis.core import config as core_config

    return core_config.DATA_DIR / _PACKAGED_DIR_NAME


def _mode_path(slug: str) -> Path:
    return modes_dir() / f"{slug}{_MODE_FILE_SUFFIX}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def normalize_slug(raw: str) -> str:
    """Turn a human name into a safe slug, or raise ``ModeError``.

    Accepts what a person types ("My Friend Mode") and returns ``my-friend-mode``.
    Rejects anything that would not round-trip as a plain file name.
    """
    text = (raw or "").strip().lower()
    # Path-shaped input is refused rather than repaired. Stripping the illegal
    # characters below would already make traversal impossible (the grammar has
    # no dots and no separators, so the result cannot leave ``modes_dir()``),
    # but it would turn "/etc/passwd" into a mode cheerfully named "etcpasswd".
    # This slug can arrive from a tool the realtime model calls; a caller that
    # hands over a path has made a mistake and should hear about it.
    if any(sep in text for sep in ("/", "\\", ":")) or ".." in text:
        raise ModeError(f"{raw!r} looks like a path, not a mode name.")
    text = text.replace("_", "-").replace(" ", "-")
    # Drop everything outside the grammar rather than escaping it: a slug is a
    # label, and a silently escaped one is unreadable in the UI and the CLI.
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text or not _SLUG_RE.match(text):
        raise ModeError(
            f"{raw!r} does not produce a usable mode id. Use letters, digits and "
            "hyphens (1-48 characters)."
        )
    if text in _WINDOWS_RESERVED:
        raise ModeError(f"{text!r} is a reserved device name on Windows. Pick another name.")
    return text


def _validate_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    candidate = (value or "").strip().lower()
    if not candidate:
        return allowed[allowed.index("normal")] if "normal" in allowed else allowed[0]
    if candidate not in allowed:
        raise ModeError(f"{field} must be one of {', '.join(allowed)} — got {value!r}.")
    return candidate


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _parse_mode_file(path: Path, *, built_in: bool) -> Mode | None:
    """Read one ``<slug>.md`` into a :class:`Mode`, or ``None`` when unusable.

    A corrupt mode file degrades to "this mode does not exist" instead of
    raising: the file sits in a user-writable directory, and one bad hand-edit
    must not take down the mode list (or, through the persona layer, every turn).
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        log.warning("Mode %s unreadable (%s) — skipped.", path.name, exc)
        return None

    from jarvis.memory.frontmatter import parse_frontmatter

    meta, body = parse_frontmatter(text)
    if not isinstance(meta, dict):
        meta = {}
    slug = path.stem.lower()
    try:
        slug = normalize_slug(slug)
    except ModeError:
        log.warning("Mode file %s has an unusable name — skipped.", path.name)
        return None

    def _str(key: str, default: str = "") -> str:
        value = meta.get(key, default)
        return value.strip() if isinstance(value, str) else default

    try:
        verbosity = _validate_choice(_str("verbosity"), VERBOSITIES, "verbosity")
        proactivity = _validate_choice(_str("proactivity"), PROACTIVITIES, "proactivity")
    except ModeError as exc:
        # A hand-edited typo costs the knob, not the mode.
        log.warning("Mode %s: %s — falling back to the normal setting.", slug, exc)
        verbosity, proactivity = VERBOSITY_NORMAL, PROACTIVITY_NORMAL

    return Mode(
        slug=slug,
        name=_str("name") or slug.replace("-", " ").title(),
        emoji=_str("emoji"),
        description=_str("description"),
        character=body.strip(),
        built_in=built_in,
        voice=_str("voice"),
        verbosity=verbosity,
        proactivity=proactivity,
    )


def _read_dir(directory: Path, *, built_in: bool) -> dict[str, Mode]:
    found: dict[str, Mode] = {}
    try:
        entries = sorted(directory.glob(f"*{_MODE_FILE_SUFFIX}"))
    except OSError:
        return found
    for path in entries:
        mode = _parse_mode_file(path, built_in=built_in)
        if mode is not None:
            found[mode.slug] = mode
    return found


def list_modes() -> tuple[Mode, ...]:
    """Every available mode: built-ins first, then the user's own, A-Z.

    A user file whose name collides with a built-in slug WINS — that is how
    "copy a built-in and tweak it" works — but it keeps the ``built_in`` flag
    so the UI can still offer "restore the original" rather than pretending the
    packaged version is gone.
    """
    builtins = _read_dir(packaged_modes_dir(), built_in=True)
    overrides = _read_dir(modes_dir(), built_in=False)

    merged: dict[str, Mode] = dict(builtins)
    for slug, mode in overrides.items():
        merged[slug] = replace(mode, built_in=slug in builtins)

    ordered = [merged[s] for s in BUILTIN_SLUGS if s in merged]
    ordered.extend(
        sorted(
            (m for s, m in merged.items() if s not in BUILTIN_SLUGS),
            key=lambda m: m.name.lower(),
        )
    )
    return tuple(ordered)


def get_mode(slug: str) -> Mode | None:
    """One mode by slug, or ``None`` when there is no such mode."""
    try:
        wanted = normalize_slug(slug)
    except ModeError:
        return None
    for mode in list_modes():
        if mode.slug == wanted:
            return mode
    return None


def has_user_copy(slug: str) -> bool:
    """True when the user has their own file for this slug (built-in or not)."""
    try:
        return _mode_path(normalize_slug(slug)).is_file()
    except (ModeError, OSError):
        return False


# ---------------------------------------------------------------------------
# Which mode is active
# ---------------------------------------------------------------------------


def _configured_slug() -> str:
    """The user's sticky choice from ``[persona] active_mode``."""
    try:
        from jarvis.core.config import get_config

        raw = getattr(getattr(get_config(), "persona", None), "active_mode", "")
    except Exception:  # noqa: BLE001 - config unavailable mid-reload
        return DEFAULT_MODE
    slug = (raw or "").strip().lower()
    return slug or DEFAULT_MODE


def active_slug() -> str:
    """The slug in force for this turn — section override first, then config.

    Falls back to :data:`DEFAULT_MODE` when the stored slug names a mode that no
    longer exists (deleted by hand, or a config carried to another machine).
    Silence here would be wrong in the other direction: the user would keep
    seeing a mode name the assistant is not actually running.
    """
    with _override_lock:
        override = _section_override
    candidate = override or _configured_slug()
    if get_mode(candidate) is not None:
        return candidate
    if override is not None:
        log.warning("Section override %r names no known mode — ignoring it.", override)
    elif candidate != DEFAULT_MODE:
        log.warning(
            "Active mode %r no longer exists — falling back to %s.", candidate, DEFAULT_MODE
        )
    return DEFAULT_MODE


def active_mode() -> Mode:
    """The mode in force for this turn. Never ``None``."""
    mode = get_mode(active_slug())
    if mode is not None:
        return mode
    # Neither the chosen mode nor the packaged default could be read — a broken
    # install. An empty character block is the honest answer: base persona only.
    return Mode(
        slug=DEFAULT_MODE,
        name="Assistant",
        emoji="",
        description="",
        character="",
        built_in=True,
    )


def set_active(slug: str) -> Mode:
    """Persist the user's mode choice. Applies on the next turn, no restart.

    Raises ``ModeError`` when the slug names no known mode — writing a pointer
    to a mode that does not exist would show a name in the UI that the assistant
    is not running.
    """
    mode = get_mode(slug)
    if mode is None:
        raise ModeError(f"No mode called {slug!r}.")

    from jarvis.core import config_writer

    config_writer.set_active_mode(mode.slug)
    log.info("Active mode set to %s.", mode.slug)
    return mode


def set_section_override(slug: str | None) -> str | None:
    """Set (or clear with ``None``) the screen-scoped mode.

    In-memory only, by design — see the module docstring. Returns the slug now
    in force, so the caller can report what actually happened rather than what
    it asked for.
    """
    global _section_override
    if slug is None:
        with _override_lock:
            _section_override = None
        return active_slug()
    if get_mode(slug) is None:
        raise ModeError(f"No mode called {slug!r}.")
    with _override_lock:
        _section_override = normalize_slug(slug)
    return active_slug()


def section_override() -> str | None:
    """The screen-scoped mode currently in force, if any."""
    with _override_lock:
        return _section_override


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write UTF-8 without a BOM via tempfile + ``os.replace`` (AP-7)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_mode(
    *,
    slug: str,
    name: str,
    character: str,
    emoji: str = "",
    description: str = "",
    voice: str = "",
    verbosity: str = VERBOSITY_NORMAL,
    proactivity: str = PROACTIVITY_NORMAL,
) -> Mode:
    """Create or replace a user mode. Returns the mode as it was stored.

    Editing a built-in is allowed and writes a user copy alongside the packaged
    file; the packaged one is never touched, so ``restore_builtin`` is a delete.
    """
    safe_slug = normalize_slug(slug)
    display = (name or "").strip() or safe_slug.replace("-", " ").title()
    body = (character or "").strip()
    if not body:
        raise ModeError("A mode needs a character description — an empty one changes nothing.")

    mode = Mode(
        slug=safe_slug,
        name=display,
        emoji=(emoji or "").strip(),
        description=(description or "").strip(),
        character=body,
        built_in=safe_slug in BUILTIN_SLUGS,
        voice=(voice or "").strip(),
        verbosity=_validate_choice(verbosity, VERBOSITIES, "verbosity"),
        proactivity=_validate_choice(proactivity, PROACTIVITIES, "proactivity"),
    )

    from jarvis.memory.frontmatter import write_frontmatter

    meta: dict[str, object] = {
        "name": mode.name,
        "emoji": mode.emoji,
        "description": mode.description,
        "voice": mode.voice,
        "verbosity": mode.verbosity,
        "proactivity": mode.proactivity,
    }
    _atomic_write(_mode_path(safe_slug), write_frontmatter(meta, mode.character))
    log.info("Mode %s saved.", safe_slug)
    return mode


def delete_mode(slug: str) -> bool:
    """Remove a user mode (or a user copy of a built-in). Idempotent.

    Deleting the mode that is currently active resets the pointer to
    :data:`DEFAULT_MODE` rather than leaving a dangling name on screen.
    """
    safe_slug = normalize_slug(slug)
    if safe_slug in BUILTIN_SLUGS and not has_user_copy(safe_slug):
        raise ModeError(f"{safe_slug!r} is a built-in mode and cannot be deleted.")

    try:
        _mode_path(safe_slug).unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    except OSError as exc:
        raise ModeError(f"Could not delete {safe_slug!r}: {exc}") from exc

    if removed and _configured_slug() == safe_slug and get_mode(safe_slug) is None:
        try:
            from jarvis.core import config_writer

            config_writer.set_active_mode(DEFAULT_MODE)
        except Exception as exc:  # noqa: BLE001 - the delete already happened
            log.warning("Deleted the active mode but could not reset the pointer: %s", exc)
    with _override_lock:
        stale_override = _section_override == safe_slug
    if stale_override:
        set_section_override(None)
    return removed


def restore_builtin(slug: str) -> bool:
    """Drop the user's copy of a built-in so the packaged version applies again."""
    safe_slug = normalize_slug(slug)
    if safe_slug not in BUILTIN_SLUGS:
        raise ModeError(f"{safe_slug!r} is not a built-in mode.")
    try:
        _mode_path(safe_slug).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ModeError(f"Could not restore {safe_slug!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Migration from the single-slot custom prompt
# ---------------------------------------------------------------------------

MIGRATED_SLUG = "my-mode"


def migrate_legacy_custom_prompt() -> str | None:
    """Adopt an existing ``custom_system_prompt.md`` as a normal mode.

    Runs at most once: the legacy file is left in place (so a downgrade still
    works and nothing the user wrote is destroyed) and the copy is only made
    when no mode with that slug exists yet. Returns the slug it created, or
    ``None`` when there was nothing to migrate.

    The migrated mode is NOT activated. The legacy file is still honoured by
    ``persona_loader`` for exactly that reason — the user's words keep applying
    either way, and the migration only makes them switchable.
    """
    from jarvis.brain import persona_loader

    legacy = persona_loader.read_custom_prompt()
    if not legacy:
        return None
    if _mode_path(MIGRATED_SLUG).is_file():
        return None
    try:
        save_mode(
            slug=MIGRATED_SLUG,
            name="My Mode",
            emoji="✍️",
            description="The custom system prompt you wrote before modes existed.",
            character=legacy,
        )
    except (ModeError, OSError) as exc:
        log.warning("Could not migrate the custom system prompt into a mode: %s", exc)
        return None
    log.info("Migrated the custom system prompt into the %r mode.", MIGRATED_SLUG)
    return MIGRATED_SLUG


# ---------------------------------------------------------------------------
# Compiling a mode into prompt text
# ---------------------------------------------------------------------------

_VERBOSITY_LINES = {
    VERBOSITY_BRIEF: (
        "Answer in as few words as carry the meaning — usually one or two "
        "sentences. Do not summarise what you just did unless asked."
    ),
    VERBOSITY_NORMAL: "",
    VERBOSITY_RICH: (
        "Give the reasoning as well as the answer: what you considered, what you "
        "ruled out, and why. Detail is welcome as long as every sentence earns "
        "its place."
    ),
}

_PROACTIVITY_LINES = {
    PROACTIVITY_REACTIVE: (
        "Answer what was asked and stop. Do not suggest next steps, do not offer "
        "related work, and do not raise topics the user did not open."
    ),
    PROACTIVITY_NORMAL: "",
    PROACTIVITY_FORWARD: (
        "Think a step ahead: name the thing the user has not thought of yet, "
        "flag what is about to become a problem, and offer the obvious next move."
    ),
}

_BLOCK_HEADER = "ACTIVE MODE"


def mode_prompt_block(mode: Mode) -> str:
    """The text a mode contributes to the system prompt. ``""`` adds nothing.

    Deliberately closes with a precedence line. Without it, a mode that says
    "be casual and just go for it" reads as permission to skip the honesty rules
    in the base persona, and the model has no way to know which instruction is
    the standing one.
    """
    parts = [line for line in (mode.character.strip(),) if line]
    for line in (
        _VERBOSITY_LINES.get(mode.verbosity, ""),
        _PROACTIVITY_LINES.get(mode.proactivity, ""),
    ):
        if line:
            parts.append(line)
    if not parts:
        return ""

    label = f"{mode.emoji} {mode.name}".strip() if mode.emoji else mode.name
    body = "\n\n".join(parts)
    return (
        f"{_BLOCK_HEADER}: {label}\n"
        "This is how you behave right now. It changes your tone, your length "
        "and what you volunteer — it never overrides the rules above about "
        "honesty, about only claiming actions you actually took, or about what "
        "is safe to say out loud. Where this section and those rules disagree, "
        f"those rules win.\n\n{body}"
    )


def active_prompt_block() -> str:
    """The prompt contribution of whichever mode is active. Never raises."""
    try:
        return mode_prompt_block(active_mode())
    except Exception as exc:  # noqa: BLE001 - a mode must never break a turn
        log.warning("Mode block unavailable (%s) — running the base persona.", exc)
        return ""


def active_voice() -> str:
    """TTS voice the active mode prefers, or ``""`` to keep the configured one."""
    try:
        return active_mode().voice
    except Exception:  # noqa: BLE001 - never let a mode mute the assistant
        return ""


__all__ = [
    "BUILTIN_SLUGS",
    "DEFAULT_MODE",
    "MODE_ASSISTANT",
    "MODE_CODING",
    "MODE_COACH",
    "MODE_FOCUS",
    "MODE_FRIEND",
    "PROACTIVITIES",
    "VERBOSITIES",
    "Mode",
    "ModeError",
    "active_mode",
    "active_prompt_block",
    "active_slug",
    "active_voice",
    "delete_mode",
    "get_mode",
    "has_user_copy",
    "list_modes",
    "migrate_legacy_custom_prompt",
    "mode_prompt_block",
    "modes_dir",
    "normalize_slug",
    "packaged_modes_dir",
    "restore_builtin",
    "save_mode",
    "section_override",
    "set_active",
    "set_section_override",
]

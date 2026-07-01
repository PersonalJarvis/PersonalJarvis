"""User-editable agent-instructions file — an ``AGENTS.md`` / ``CLAUDE.md`` equivalent.

This is the user's own standing-instructions file: free-text Markdown where the
user writes personal preferences for *how the assistant should treat them* — tone,
language, formatting, default choices, standing facts. It is deliberately distinct
from the packaged persona (``JARVIS_PERSONA.md`` / ``data/custom_system_prompt.md``,
handled by ``persona_loader``), which defines *who the assistant is and what it may
do*. Preferences refine behaviour; they never override safety rules, confirmation
gates, or capabilities.

The file is named after the assistant: assistant "Alex" -> ``Alex.md``. The name
is resolved through :func:`jarvis.brain.assistant_name.resolve_assistant_name`
(the wake phrase with its prefix stripped, else the neutral ``Assistant`` fallback —
there is no longer a separate ``[persona].name`` setting) and sanitised to a
filesystem-safe basename. The
file lives in its own directory under ``DATA_DIR`` so that a rename can be tracked
unambiguously: when the assistant is renamed, the next read migrates the single
existing file to the new name (no content is lost on a rebrand).

Mirrors the ``persona_loader`` custom-prompt seam:

* atomic write (tempfile + ``os.replace``), UTF-8 **without** a BOM, ``newline="\n"``
  — the AP-7 / BUG-018 defenses;
* read **fresh** every call (no cache) so an edit takes effect on the next turn
  with no restart — ``_build_system_prompt`` reassembles each turn anyway;
* every path helper reads ``core_config.DATA_DIR`` at call time (never bound at
  import) so the path is CWD-immune (the onboarding-reappears bug class) and tests
  can redirect ``DATA_DIR``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name

log = logging.getLogger(__name__)

_INSTRUCTIONS_DIRNAME = "agent_instructions"
_DEFAULT_BASENAME = DEFAULT_ASSISTANT_NAME  # "Assistant" — when a name sanitises to ""
_MAX_BASENAME_LEN = 60

# ä/ö/ü/ß transliteration so a German name yields an ASCII-safe, portable filename  # i18n-allow: English comment merely mentioning umlaut characters, not German prose
# (e.g. "Jürgen" -> "Juergen.md"). Case-preserving for the common single-cap case.  # i18n-allow: same, illustrative umlaut example name
_UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",  # i18n-allow: umlaut-transliteration data table, not prose
}
# Characters that are unsafe in a filename on Windows and/or POSIX.
_UNSAFE = set('<>:"/\\|?*')
# Windows reserved device names (case-insensitive) — appended with "_" if matched.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def instructions_dir() -> Path:
    """Directory holding the agent-instructions file (created lazily on write)."""
    from jarvis.core import config as core_config

    return core_config.DATA_DIR / _INSTRUCTIONS_DIRNAME


def _safe_basename(name: str) -> str:
    """Sanitise a resolved assistant name into a filesystem-safe basename (no ext).

    Transliterates umlauts, drops control + reserved characters, trims leading/
    trailing dots and whitespace (Windows strips these), caps length, and guards
    Windows reserved device names. Falls back to ``Assistant`` when nothing usable
    remains.
    """
    raw = (name or "").strip()
    out: list[str] = []
    for ch in raw:
        if ch in _UMLAUTS:
            out.append(_UMLAUTS[ch])
        elif ch in _UNSAFE or ord(ch) < 32:
            continue
        else:
            out.append(ch)
    cleaned = "".join(out).strip().strip(".").strip()
    cleaned = cleaned[:_MAX_BASENAME_LEN].strip()
    if not cleaned:
        return _DEFAULT_BASENAME
    if cleaned.lower() in _RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned


def instructions_filename(config: Any) -> str:
    """The display/on-disk filename for ``config``'s assistant, e.g. ``Alex.md``."""
    return f"{_safe_basename(resolve_assistant_name(config))}.md"


def instructions_path(config: Any) -> Path:
    """Absolute path to the agent-instructions file for ``config``'s assistant."""
    return instructions_dir() / instructions_filename(config)


def _migrate_legacy_if_needed(target: Path) -> None:
    """Rename a single stray ``*.md`` to ``target`` so the file follows a rename.

    Only acts when ``target`` does not exist and **exactly one** other ``*.md``
    file is present in the directory (an unambiguous rename). Zero files -> nothing
    to do; multiple files -> ambiguous, leave them untouched. Best-effort: any OS
    error is swallowed (a failed migration must never break the brain build).
    """
    try:
        if target.exists():
            return
        directory = target.parent
        if not directory.is_dir():
            return
        candidates = [p for p in directory.glob("*.md") if p.is_file()]
        if len(candidates) == 1 and candidates[0] != target:
            candidates[0].replace(target)
            log.info("agent-instructions migrated %s -> %s", candidates[0].name, target.name)
    except OSError as exc:  # noqa: BLE001 — migration is best-effort
        log.debug("agent-instructions migration skipped: %s", exc)


def read_agent_instructions(config: Any) -> str | None:
    """Return the stored instructions, or ``None`` when there is no usable file.

    A missing, unreadable, or whitespace-only file all count as "no instructions".
    Triggers a one-shot rename-migration first so the file follows the assistant's
    current name. Read fresh (no cache) — an edit applies on the next turn.
    """
    path = instructions_path(config)
    _migrate_legacy_if_needed(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:  # noqa: BLE001
        log.warning("agent-instructions not readable (%s) — ignoring.", exc)
        return None
    text = text.strip()
    return text or None


def has_agent_instructions(config: Any) -> bool:
    """True when a non-empty agent-instructions file is in effect."""
    return read_agent_instructions(config) is not None


def save_agent_instructions(config: Any, text: str) -> None:
    """Persist the instructions atomically (tempfile + ``os.replace``).

    UTF-8 without a BOM (AP-7), ``newline="\n"``, body stripped before writing.
    """
    import os
    import tempfile

    body = (text or "").strip()
    path = instructions_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".agent_instructions.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def reset_agent_instructions(config: Any) -> bool:
    """Delete the agent-instructions file.

    Returns True when a file was removed, False when there was nothing to remove
    (idempotent — a double reset is not an error).
    """
    path = instructions_path(config)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def render_for_prompt(config: Any) -> str:
    """The system-prompt block for the current user-instructions state.

    The block is framed so the model treats the content as *preferences* — it
    refines tone/language/defaults but never overrides safety rules, confirmation
    gates, or capabilities, and never authorises new tools or actions.
    """
    content = read_agent_instructions(config)
    filename = instructions_filename(config)
    if not content:
        return (
            f"USER PREFERENCES & STANDING INSTRUCTIONS (from {filename}):\n"
            "No active user preferences are currently set in Jarvis.md. "
            "Ignore any earlier Jarvis.md instructions from previous turns. "
            "Do not continue, infer, or imitate older style, address, tone, "
            "language, or wording rules that are absent from this current block.\n\n"
            "END USER PREFERENCES & STANDING INSTRUCTIONS"
        )
    return (
        f"USER PREFERENCES & STANDING INSTRUCTIONS (from {filename}):\n"
        "The following are personal preferences written by the user for how you "
        "should work with them. Only the instructions inside this current block are "
        "active; ignore any earlier Jarvis.md instructions from previous turns that "
        "are absent or conflict. Treat the current block as binding for tone, "
        "language, formatting, default choices, forms of address, and standing facts "
        "about the user. They override default style/address/tone/language guidance "
        "where they conflict. "
        "They never override your safety rules, confirmation gates, or capabilities, "
        "and never authorise new tools or actions.\n\n"
        f"{content}\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS"
    )


def render_for_flash(config: Any) -> str:
    """A CONCISE preferences block for the latency-critical flash spoken tiers.

    Used by the pre-thinking ack preamble and the spawn-announcement composer.
    Kept short (one framing sentence + the content) to add minimal tokens to a
    sub-second call. Frames the content as authoritative for *style* — it
    OVERRIDES the tier's default address/opener/tone guidance where they
    conflict (so e.g. a user-requested form of address is used even if the
    default persona discourages honorifics) — while explicitly NOT overriding
    the tier's core job, brevity limit, safety rules, or the ban on naming
    internal components. Returns ``""`` when no file is set.
    """
    content = read_agent_instructions(config)
    if not content:
        return ""
    return (
        "USER STANDING PREFERENCES (the user set these — honor them in your tone, "
        "form of address, and wording; they OVERRIDE the default style/address/opener "
        "guidance above where they conflict, including any 'forbidden address' list — "
        "use the address the user asks for). They do NOT change your core job, your "
        "brevity limit, the safety rules, or the ban on naming internal components:\n"
        f"{content}"
    )


def seed_template(config: Any) -> str:
    """A commented starter template so a first-time editor is not a blank box.

    English source (per the repo Output Language Policy); the user's own content
    is whatever language they write. The assistant's name is woven in so the file
    reads as "this is how I work with <name>".
    """
    name = resolve_assistant_name(config)
    return (
        f"# How {name} should work with me\n"
        "\n"
        "# These are your personal preferences. They shape how the assistant talks\n"
        "# to you and the defaults it picks. Edit freely — changes apply on the\n"
        "# next message, no restart. (They never override safety or confirmations.)\n"
        "\n"
        "## Communication style\n"
        "- \n"
        "\n"
        "## Language & locale\n"
        "- \n"
        "\n"
        "## Do\n"
        "- \n"
        "\n"
        "## Don't\n"
        "- \n"
        "\n"
        "## About me / standing facts\n"
        "- \n"
    )

"""Shared context extraction for subscription-CLI brain prompts.

Codex and Antigravity flatten a Jarvis turn into one plain prompt for an
official CLI. They intentionally do not forward the full router system prompt:
that prompt is large, tool-heavy, and misleading for read-only conversational
CLI calls. User standing instructions are different: they are the Jarvis.md
equivalent and must still reach the answering model.
"""
from __future__ import annotations

_PREF_START = "USER PREFERENCES & STANDING INSTRUCTIONS"
_PREF_END = "END USER PREFERENCES & STANDING INSTRUCTIONS"
_LEGACY_MAX_CHARS = 6000
_NO_ACTIVE_PREFS_MARKER = "No active user preferences are currently set"

# The BrainManager appends the authoritative reply-language directive LAST to
# the system prompt (manager._reply_language_directive). Both forms — the hard
# "REPLY LANGUAGE — MANDATORY: ..." pin and the soft "REPLY LANGUAGE: mirror the
# user ..." auto line — start with this marker.
_REPLY_LANG_MARKER = "REPLY LANGUAGE"


def extract_standing_instructions_block(system_prompt: str | None) -> str:
    """Return only the user standing-instructions block from a system prompt."""
    if not system_prompt:
        return ""
    start = system_prompt.find(_PREF_START)
    if start == -1:
        return ""
    end = system_prompt.find(_PREF_END, start)
    if end != -1:
        end += len(_PREF_END)
        return system_prompt[start:end].strip()
    # Legacy prompts did not have an end marker. Keep the fallback bounded so a
    # stale process never drags the whole router/tool prompt into a CLI turn.
    return system_prompt[start : start + _LEGACY_MAX_CHARS].strip()


def extract_reply_language_directive(system_prompt: str | None) -> str:
    """Return the trailing reply-language directive from a system prompt.

    The single authoritative output-language decision for a turn is rendered by
    ``BrainManager._reply_language_directive`` and appended LAST to the system
    prompt (so it overrides the otherwise German prompt above it). The CLI
    brains rebuild their own flattened prompt and would otherwise DROP this
    directive entirely — the subscription model (Gemini via agy, GPT via codex)
    then never learns which language to answer in and anchors to the German
    persona (live bug 2026-06-21: an English voice request answered in German,
    spoken with an English TTS voice). Re-extract it here so the directive still
    reaches the CLI model. ``rfind`` so an earlier incidental mention never wins
    over the real, last-appended directive.
    """
    if not system_prompt:
        return ""
    idx = system_prompt.rfind(_REPLY_LANG_MARKER)
    if idx == -1:
        return ""
    return system_prompt[idx:].strip()


def render_structured_prompt(req: object) -> str:
    """Render a background/structured request verbatim for a CLI agent.

    Voice turns are flattened into a light conversational prompt on purpose,
    but background curation (the wiki extractor and Stage-2 judge) carries its
    own binding contract in ``req.system`` ("Return ONLY a JSON array ...").
    Replacing that contract with the conversational wrapper ("answer in one to
    three short sentences, plain text only") makes structured output
    impossible BY INSTRUCTION — the subscription CLI then looks permanently
    broken to the wiki provider chain (live 2026-07-18: every antigravity
    extraction died with "no JSON array found in response"). Structured mode
    forwards the system contract and the user payload verbatim instead.
    """
    parts: list[str] = []
    system = str(getattr(req, "system", "") or "").strip()
    if system:
        parts.append(system)
    for message in getattr(req, "messages", ()) or ():
        content = getattr(message, "content", None)
        if (
            getattr(message, "role", "") in ("system", "user")
            and isinstance(content, str)
            and content.strip()
        ):
            parts.append(content.strip())
    return "\n\n".join(parts)


def render_cli_standing_instructions(system_prompt: str | None) -> str:
    """Render a compact system-like block for flattened CLI prompts."""
    block = extract_standing_instructions_block(system_prompt)
    if not block:
        return ""
    if _NO_ACTIVE_PREFS_MARKER in block:
        return (
            "CURRENT JARVIS.MD STATE:\n"
            "No active user preferences are currently set; do not continue or "
            "imitate any earlier Jarvis.md instruction, assistant response style, "
            "form of address, opener, language rule, or wording rule that is not "
            "present in the current Jarvis.md block below.\n\n"
            f"{block}"
        )
    return (
        "USER STANDING INSTRUCTIONS FROM JARVIS.MD:\n"
        "Apply these as binding output-style preferences for this answer. "
        "If they conflict with the light CLI style above, the user's instructions "
        "win. They do not authorise tools, file access, commands, or safety bypasses.\n\n"
        f"{block}"
    )

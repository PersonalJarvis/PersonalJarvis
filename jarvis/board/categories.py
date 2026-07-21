"""Single source of truth for Board usage categories.

The six categories answer the question *"what did you use Jarvis FOR"* — the
honest Jarvis analogue of a dictation overlay's "top apps" panel. A dictation
overlay lives *inside* other apps, so it can report which application you
dictated into.
Jarvis is the agent itself, not an overlay, so the meaningful axis is the *kind
of task*, derived from the tools that actually ran
(``voice_turns.tool_calls_json`` and ``ActionExecuted`` events).

Five-layer-enum discipline (``docs/anti-drift-three-layer.md``): these keys
cross Python -> Pydantic -> TypeScript -> UI label. The keys are defined HERE
and mirrored in ``frontend/src/lib/boardCategories.ts``; a parity test
(``tests/board/test_categories.py`` + the frontend mirror) guards drift. Never
reorder or rename a key without updating both sides — that is exactly the
BUG-008 class this project has hit four times.
"""
from __future__ import annotations

# Ordered, stable wire keys. The order is the canonical display order for
# empty/tie categories. NEVER reorder or rename without a parity-test update.
BOARD_CATEGORY_KEYS: tuple[str, ...] = (
    "agents",
    "browser",
    "mail",
    "community",
    "knowledge",
    "system",
)

# Catch-all bucket for tools that match no rule. "system" is intentionally the
# fallback because an unrecognised tool is almost always a config/admin/utility
# call rather than user-facing work.
_FALLBACK = "system"

# Substring rules, evaluated in this order — FIRST hit wins. Order matters:
# "knowledge" is placed before "community" so that the "recall" substring in
# ``awareness-recall`` / ``wiki-recall`` is not mis-caught by the "call" needle
# of the community bucket.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "agents",
        (
            "spawn_openclaw", "spawn_sub_jarvis", "spawn_worker", "spawn_skill_author",
            "spawn-cli-worker", "multi_spawn", "dispatch_to_harness",
            "dispatch_with_review", "run-skill", "run_shell", "cli_", "cli-worker",
            "harness", "mission", "worker",
        ),
    ),
    (
        "mail",
        ("gmail", "email", "e-mail", "smtp", "inbox", "agentmail", "send_email", "mailbox"),
    ),
    (
        "knowledge",
        (
            "wiki", "awareness", "remember", "memory", "recall", "notebook", "note",
            "whoami", "profile", "curator", "soul",
        ),
    ),
    (
        "community",
        (
            "contact", "call", "discord", "telegram", "channel", "social", "sms",
            "twilio", "phone", "friend", "federation",
        ),
    ),
    (
        "browser",
        (
            "browser", "web", "navigate", "url", "firecrawl", "search",
            "computer_use", "click", "open_app", "type_text", "hotkey",
            "screenshot", "switch_window", "scroll", "inspect-pointer",
            "ui_state", "ui-", "read_visible", "wait_for_ui", "element", "pointer",
            "vision", "drag",
        ),
    ),
    (
        "system",
        (
            "config", "setting", "admin", "cli-tools", "reveal-key", "list_mutable",
            "get_config", "set_config", "terminal", "describe-app", "elevate",
            "shell",
        ),
    ),
)


def categorize_tool(tool_name: str) -> str:
    """Map a tool name to one of ``BOARD_CATEGORY_KEYS``.

    Matching is case-insensitive substring matching against ``_RULES`` in
    order; the first rule that matches wins. Unknown, blank, or ``None`` names
    fall back to ``"system"`` so the count is never lost.
    """
    name = (tool_name or "").strip().lower()
    if not name:
        return _FALLBACK
    for key, needles in _RULES:
        for needle in needles:
            if needle in name:
                return key
    return _FALLBACK


__all__ = ["BOARD_CATEGORY_KEYS", "categorize_tool"]

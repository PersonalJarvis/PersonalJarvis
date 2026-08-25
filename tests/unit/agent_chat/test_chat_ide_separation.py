"""Two chats, two worlds — and they must stay that way.

Jarvis has two typed surfaces that both run a coding CLI, and they mean
different things:

* the **front-page chat** (``jarvis.agent_chat``) is Jarvis with a keyboard.
  It gets Jarvis' own tools over MCP, so a sentence there can open an app,
  read the calendar or switch the brain provider.
* the **Agentic-IDE panes** (``jarvis.agentic_ide``) are coding agents in a
  workspace. Each pane is a real PTY on the person's own subscription seat,
  with the CLI's own tools and nothing else — the terminal experience, in a
  window.

Merging them would be easy and wrong in both directions: a coding pane that
can silently re-point the brain provider is a surprise, and a Jarvis chat that
has to be a workspace first is the very thing the maintainer rejected on
2026-08-24. These tests pin the seam that keeps them apart.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_IDE = _ROOT / "jarvis" / "agentic_ide"

#: The chat-only modules. Nothing under ``jarvis/agentic_ide`` may reach these:
#: they are what hands a session Jarvis' own tools.
_CHAT_ONLY = ("jarvis.agent_chat.jarvis_harness", "jarvis.agent_chat.runner_cli")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.skipif(not _IDE.is_dir(), reason="agentic_ide package not present")
def test_the_ide_never_reaches_into_the_chat_runner():
    """A pane must not acquire Jarvis' tools by importing the chat's plumbing."""
    offenders: list[str] = []
    for module in sorted(_IDE.rglob("*.py")):
        imported = _imported_modules(module)
        for banned in _CHAT_ONLY:
            if any(name == banned or name.startswith(banned + ".") for name in imported):
                offenders.append(f"{module.relative_to(_ROOT)} imports {banned}")
    assert not offenders, "the IDE reached into the chat runner:\n" + "\n".join(offenders)


def test_only_the_chat_package_hands_out_jarvis_tools():
    """The MCP config has exactly one home: nothing outside jarvis/agent_chat builds it."""
    callers: list[str] = []
    for module in sorted((_ROOT / "jarvis").rglob("*.py")):
        if module.parent.name == "agent_chat":
            continue
        text = module.read_text(encoding="utf-8", errors="replace")
        if "mcp_config_json" in text or "codex_config_args" in text:
            callers.append(str(module.relative_to(_ROOT)))
    assert callers == [], f"unexpected callers of the Jarvis MCP config: {callers}"


def test_the_chat_child_environment_is_a_copy_never_the_process_environment():
    """The control key must reach the chat's child and nothing else on the box."""
    import os

    from jarvis.agent_chat import jarvis_harness

    before = os.environ.get(jarvis_harness.KEY_ENV_VAR)
    child: dict[str, str] = {}
    jarvis_harness.apply_env(child)
    assert os.environ.get(jarvis_harness.KEY_ENV_VAR) == before


def test_the_ide_panes_and_the_chat_do_not_share_a_spawn_function():
    """One shared argv builder is how two surfaces silently become one."""
    from jarvis.agent_chat import runner_cli

    ide_session = _IDE / "session.py"
    if not ide_session.is_file():
        pytest.skip("agentic_ide.session not present")
    text = ide_session.read_text(encoding="utf-8", errors="replace")
    for name in ("plan_claude", "plan_codex", "plan_agy", "plan_grok"):
        if hasattr(runner_cli, name):
            assert name not in text, f"the IDE calls the chat's {name}"

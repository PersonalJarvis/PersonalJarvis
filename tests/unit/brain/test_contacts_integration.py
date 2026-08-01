"""Chunk B — BrainManager contact integration (name-index + e-mail-by-name rule).

Two pieces of glue live in the system prompt:

1. **Name-index injection** — a ``ContactStore`` (Contract 1) is wired into the
   BrainManager and its ``render_for_prompt()`` (names + relationship only, NOT
   the details) is appended to the system prompt. Cheap string render, off the
   voice latency budget. Mirrors the existing ``_people`` block.

2. **E-mail-by-name rule** — when ``contact-lookup`` AND ``gmail`` are both
   wired, a directive tells the brain to resolve a named person via
   ``contact-lookup`` first, then send via ``gmail`` (no new e-mail tool).

The store is stubbed (Contract 1) so this is testable before Chunk A merges.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult


class _RecordingExecutor:
    async def execute(self, *_: Any, **__: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _FakeContacts:
    """Contract-1 stub: only render_for_prompt is exercised here."""

    def __init__(self, block: str) -> None:
        self._block = block

    def render_for_prompt(self, *, max_chars: int = 800) -> str:
        return self._block


class _FakeContactLookupTool:
    name = "contact-lookup"
    schema: dict[str, Any] = {}


class _FakeGmailTool:
    name = "gmail"
    schema: dict[str, Any] = {}


class _FakeSpawnTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


def _manager(*, contacts: Any = None, tools: dict[str, Any] | None = None) -> BrainManager:
    return BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools=tools if tools is not None else {"spawn_worker": _FakeSpawnTool()},
        tool_executor=_RecordingExecutor(),  # type: ignore[arg-type]
        contacts=contacts,
    )


# --------------------------------------------------------------------------- #
# 1) Name-index injection
# --------------------------------------------------------------------------- #
def test_contacts_name_index_appears_in_system_prompt() -> None:
    contacts = _FakeContacts("## Contacts\n- Christoph (friend)\n- Laura (colleague)")
    prompt = _manager(contacts=contacts)._build_system_prompt()
    assert "## Contacts" in prompt
    assert "Christoph" in prompt
    assert "Laura" in prompt


def test_no_contacts_block_when_store_absent() -> None:
    """Chunk A not merged -> contacts is None -> no block, no crash."""
    prompt = _manager(contacts=None)._build_system_prompt()
    assert "## Contacts" not in prompt


def test_empty_contacts_render_adds_no_block() -> None:
    """An empty contact book renders to '' and must not inject a stray heading."""
    prompt = _manager(contacts=_FakeContacts(""))._build_system_prompt()
    assert "## Contacts" not in prompt


def test_contacts_render_error_does_not_crash_prompt_build() -> None:
    class _Boom:
        def render_for_prompt(self, *, max_chars: int = 800) -> str:
            raise RuntimeError("boom")

    # Must not raise — the prompt build swallows a render error defensively.
    prompt = _manager(contacts=_Boom())._build_system_prompt()
    assert isinstance(prompt, str) and prompt


# --------------------------------------------------------------------------- #
# 2) E-mail-by-name rule
# --------------------------------------------------------------------------- #
def test_email_by_name_directive_present_when_tools_wired() -> None:
    tools = {"contact-lookup": _FakeContactLookupTool(), "gmail": _FakeGmailTool()}
    prompt = _manager(tools=tools)._build_system_prompt()
    assert "contact-lookup" in prompt
    assert "gmail" in prompt
    # The directive block opens with the "CONTACTS:" label; this is the
    # unambiguous marker that the directive is present (the German "KONTAKT"
    # label was dropped when the persona was reworked to English-first).
    assert "CONTACTS" in prompt


def test_email_by_name_directive_absent_without_gmail() -> None:
    """No gmail tool -> no directive (never instruct a tool that is not wired —
    the hard 'do not invent tools' rule)."""
    tools = {"contact-lookup": _FakeContactLookupTool(), "spawn_worker": _FakeSpawnTool()}
    prompt = _manager(tools=tools)._build_system_prompt()
    assert "contact-lookup first" not in prompt.lower()


def test_email_by_name_directive_absent_without_contact_lookup() -> None:
    tools = {"gmail": _FakeGmailTool(), "spawn_worker": _FakeSpawnTool()}
    prompt = _manager(tools=tools)._build_system_prompt()
    assert "contact-lookup first" not in prompt.lower()

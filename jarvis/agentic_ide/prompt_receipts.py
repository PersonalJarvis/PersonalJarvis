"""What the person said and dropped, kept beside the brief a pane received.

A pane's chat stage reads the coding CLI's own transcript, and that transcript
holds what was TYPED into the CLI: the brief Jarvis wrote — ``## Task``, a
``## Dropped files`` section quoting a vision description or "could not be
described" — never the sentence the person spoke and never the picture. Drawn
as the person's turn, that brief reads as noise they did not write (reported
2026-08-27: "why is there a markdown Task and then Python, bla bla, where the
images were thrown in?").

The front page's chat solved the same problem at the event: a ``user_message``
carries ``text`` (the composed prompt), ``typed`` (the person's own sentence)
and ``attachments`` (receipts), and the reducer shows ``typed`` when it is
there (``jarvis/agent_chat/service.py``, ``reduce.ts``). A pane cannot write
its CLI's transcript, so the receipt is recorded in the pane's own prompt
history at send time (:mod:`prompt_history`) and folded back into the
transcript's events when the chat stage reads them (:func:`annotate`). Same
event shape, same renderer — the two chats cannot drift.

Images get a ``url`` on top: the drop lives inside the workspace, so the
workspace file route can serve it, and the stage draws the picture itself
rather than a chip with its name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import quote

from jarvis.agentic_ide import drops
from jarvis.agentic_ide.prompt_history import PromptHistoryEntry

#: The mark the transcript reader leaves where it cut a long block out of the
#: middle (``agent_transcript._clip``). A brief longer than that ceiling is
#: matched on what is left on either side of it.
CLIP_MARK = "[…]"

_WS = re.compile(r"\s+")


def receipts_for(attachments: Sequence[Any]) -> tuple[dict[str, str], ...]:
    """The receipt rows for the drops that went in with one prompt.

    ``attachments`` are ``DropAnalysis``-shaped objects (name, reference, kind,
    described_by, note). ``path`` is the workspace-relative location behind
    the agent-facing reference — what a viewer needs, where the agent needed
    the reference. The description itself is not kept: it is in the brief,
    and the point of the receipt is to show the person their own picture
    instead of a paragraph about it.
    """
    out: list[dict[str, str]] = []
    for item in attachments or ():
        name = str(getattr(item, "name", "") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "kind": str(getattr(item, "kind", "") or "other"),
                "described_by": str(getattr(item, "described_by", "") or "none"),
                "note": str(getattr(item, "note", "") or ""),
                "path": drops.dereference(str(getattr(item, "reference", "") or "")),
            }
        )
    return tuple(out)


def file_url(workspace_id: str, path: str) -> str:
    """The same-origin URL the workspace file route serves ``path`` at.

    The twin of ``workspaceFileUrl`` in ``src/lib/agenticIdeApi.ts``: one
    route, two writers of its address, and a test on each side pins the shape.
    """
    return (
        f"/api/agentic-ide/workspaces/{quote(workspace_id, safe='')}/file"
        f"?path={quote(path, safe='')}"
    )


def _key(text: str) -> str:
    """Whitespace-insensitive identity of a prompt.

    What the CLI wrote down and what Jarvis typed differ in whitespace only —
    a trailing space closes a completion popup, a paste the pane refused was
    re-sent as one line — so the comparison ignores all of it.
    """
    return _WS.sub(" ", str(text or "")).strip()


def _matches(entry_key: str, shown: str) -> bool:
    key = _key(shown)
    if not key or not entry_key:
        return False
    if key == entry_key:
        return True
    if CLIP_MARK in key:
        head, _mark, tail = key.partition(CLIP_MARK)
        head, tail = head.strip(), tail.strip()
        return bool(head) and entry_key.startswith(head) and entry_key.endswith(tail)
    return False


def _wire_row(receipt: dict[str, Any], workspace_id: str) -> dict[str, str]:
    row = {
        "name": str(receipt.get("name") or ""),
        "kind": str(receipt.get("kind") or ""),
        "described_by": str(receipt.get("described_by") or ""),
        "note": str(receipt.get("note") or ""),
    }
    path = str(receipt.get("path") or "")
    if path and row["kind"] == "image" and workspace_id:
        row["url"] = file_url(workspace_id, path)
    return row


def annotate(
    events: Iterable[dict[str, Any]],
    entries: Sequence[PromptHistoryEntry],
    *,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """``events`` with each ``user_message`` carrying its receipt, where one exists.

    A prompt's receipt is found by its text: the entry whose prompt the
    transcript's message is (whitespace aside, and allowing for the reader's
    middle cut). Entries are claimed oldest-first so two identical briefs map
    to their own receipts; a message that matches only already-claimed entries
    still takes one, because a repeated prompt is the same prompt. A message
    with no receipt — a prompt typed straight into the CLI, or one sent before
    receipts existed — is returned untouched and draws as it always did.

    Never mutates what it was given; the transcript reader's list is its own.
    """
    pool = [(_key(entry.text), entry) for entry in entries if entry.typed or entry.attachments]
    if not pool:
        return list(events)
    claimed: set[int] = set()
    out: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event, dict) else None
        if event.get("kind") != "user_message" or not isinstance(payload, dict):
            out.append(event)
            continue
        shown = str(payload.get("text") or "")
        hits = [i for i, (key, _entry) in enumerate(pool) if _matches(key, shown)]
        hit = next((i for i in hits if i not in claimed), hits[0] if hits else None)
        if hit is None:
            out.append(event)
            continue
        claimed.add(hit)
        entry = pool[hit][1]
        enriched = dict(payload)
        if entry.typed:
            enriched["typed"] = entry.typed
        if entry.attachments:
            enriched["attachments"] = [_wire_row(r, workspace_id) for r in entry.attachments]
        out.append({**event, "payload": enriched})
    return out


__all__ = ["CLIP_MARK", "annotate", "file_url", "receipts_for"]

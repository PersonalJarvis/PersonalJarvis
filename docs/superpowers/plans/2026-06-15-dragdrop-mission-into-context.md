# Drag-and-drop a mission into the conversation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user drag a sub-agent mission/output card onto an in-app "Jarvis presence dock"; on drop, Jarvis pulls the mission into the live conversation context and speaks a contextual recap.

**Architecture:** A dropped card fires a new WS command `mission.inject`. The server composes a clean, bounded, human-readable directive from the card's own data and publishes `MessageSent(role="user", source_layer="ui.web.ws.mission_inject")`. That reuses the existing brain-turn pipeline — the reply is spoken on the voice build and shown in chat, and the mission text lands in `BrainManager._history` (the context window). The drop target is a global React dock mounted in `App.tsx` that mirrors the active overlay style (bar vs. `MascotGigi` ghost), so it works in any browser on any OS (cloud-first), unlike the separate Tk overlay windows.

**Tech Stack:** Python/FastAPI + Pydantic (backend WS), React/TypeScript + zod + Vitest + Tailwind (frontend), pytest (backend tests), claude-in-chrome (live test).

Spec: `docs/superpowers/specs/2026-06-15-dragdrop-mission-into-context-design.md`

---

### Task 1: Backend — `mission.inject` directive composer (pure function)

**Files:**
- Create: `jarvis/ui/web/mission_inject.py`
- Test: `tests/unit/ui/web/test_mission_inject.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the mission-inject directive composer."""
from __future__ import annotations

from jarvis.ui.web.mission_inject import compose_mission_inject_text, MISSION_INJECT_CAP


def test_compose_uses_utterance_status_and_summary() -> None:
    text = compose_mission_inject_text(
        {
            "slug": "20260615__recherchiere__abc123",
            "utterance": "recherchiere AI-News",
            "status": "success",
            "summary": "Found three reports on model releases.",
        }
    )
    assert text is not None
    assert "recherchiere AI-News" in text
    assert "success" in text
    assert "Found three reports" in text
    # Emoji-prefixed so it reads as a deliberate "pulled in" turn.
    assert text.startswith("📎")


def test_compose_includes_error_when_present() -> None:
    text = compose_mission_inject_text(
        {"utterance": "build the thing", "status": "error", "error": "boom: exit 2"}
    )
    assert text is not None
    assert "boom: exit 2" in text


def test_compose_returns_none_for_empty_payload() -> None:
    assert compose_mission_inject_text({}) is None
    assert compose_mission_inject_text({"utterance": "  ", "slug": ""}) is None


def test_compose_caps_length() -> None:
    text = compose_mission_inject_text(
        {"utterance": "x", "status": "success", "summary": "y" * 10_000}
    )
    assert text is not None
    assert len(text) <= MISSION_INJECT_CAP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ui/web/test_mission_inject.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.ui.web.mission_inject'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Compose the brain-turn directive for a mission dragged into the conversation.

A dropped Outputs card carries its own display text (utterance / status /
summary / error). We turn that into one clean, bounded, human-readable user
turn. Publishing it as ``MessageSent(role="user")`` reuses the whole existing
brain pipeline — the reply is spoken on the voice build and shown in chat, and
the text lands in the brain's history (the "context window") so follow-ups work.
"""
from __future__ import annotations

from typing import Any

# Hard cap so a huge worker summary can't blow the token budget or the
# ``_WS_SEND_TIMEOUT_S`` circuit-breaker on the event broadcast.
MISSION_INJECT_CAP = 4000


def compose_mission_inject_text(payload: dict[str, Any]) -> str | None:
    """Build the user-turn directive, or ``None`` if there is nothing to inject."""
    utterance = str(payload.get("utterance") or "").strip()
    slug = str(payload.get("slug") or "").strip()
    if not utterance and not slug:
        return None

    title = utterance or slug
    status = str(payload.get("status") or "unknown").strip() or "unknown"
    summary = str(payload.get("summary") or "").strip()
    error = str(payload.get("error") or "").strip()

    parts = [f'📎 Let\'s talk about the sub-agent task "{title}" (status: {status}).']
    if summary:
        parts.append(f"\nHere is what it produced:\n{summary}")
    if error:
        parts.append(f"\nIt reported this error:\n{error}")
    parts.append(
        "\nGive me a short recap and let's discuss it — you can pull more "
        "detail from its outputs if I ask."
    )

    text = "\n".join(parts).strip()
    if len(text) > MISSION_INJECT_CAP:
        text = text[:MISSION_INJECT_CAP].rstrip() + " …"
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ui/web/test_mission_inject.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/mission_inject.py tests/unit/ui/web/test_mission_inject.py
git commit -m "feat(ui/web): mission-inject directive composer (drag-drop into context)"
```

---

### Task 2: Backend — accept `mission.inject` WS command and publish `MessageSent`

**Files:**
- Modify: `jarvis/ui/web/schema.py:72-83` (add action to `WSCommand.action` Literal)
- Modify: `jarvis/ui/web/server.py:1091-1094` (new branch in `_handle_command` + handler method)
- Test: `tests/unit/ui/web/test_ws_mission_inject.py`

- [ ] **Step 1: Write the failing test**

```python
"""A `mission.inject` WS command publishes a MessageSent that the brain answers."""
from __future__ import annotations

import asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import MessageSent
from jarvis.ui.web.schema import WSCommand
from jarvis.ui.web.server import WebServer


async def test_mission_inject_publishes_message_sent() -> None:
    bus = EventBus()
    seen: list[MessageSent] = []
    bus.subscribe(MessageSent, lambda e: seen.append(e))  # type: ignore[arg-type]

    srv = WebServer(JarvisConfig(), bus=bus)
    cmd = WSCommand(
        type="command",
        action="mission.inject",
        payload={
            "slug": "20260615__recherchiere__abc",
            "utterance": "recherchiere AI-News",
            "status": "success",
            "summary": "Three reports found.",
            "thread_id": "thread-7",
        },
    )
    await srv._handle_command("sess-1", cmd, asyncio.Lock())
    await asyncio.sleep(0)  # let fire-and-forget dispatch settle

    assert len(seen) == 1
    msg = seen[0]
    assert msg.role == "user"
    assert msg.thread_id == "thread-7"
    assert msg.source_layer == "ui.web.ws.mission_inject"
    assert "recherchiere AI-News" in msg.text


async def test_mission_inject_empty_payload_publishes_nothing() -> None:
    bus = EventBus()
    seen: list[MessageSent] = []
    bus.subscribe(MessageSent, lambda e: seen.append(e))  # type: ignore[arg-type]

    srv = WebServer(JarvisConfig(), bus=bus)
    cmd = WSCommand(type="command", action="mission.inject", payload={})
    await srv._handle_command("sess-1", cmd, asyncio.Lock())
    await asyncio.sleep(0)

    assert seen == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ui/web/test_ws_mission_inject.py -v`
Expected: FAIL — `WSCommand` rejects `action="mission.inject"` (ValidationError) / `_handle_command` has no branch.

- [ ] **Step 3a: Add the action to the Pydantic Literal**

In `jarvis/ui/web/schema.py`, extend `WSCommand.action`:

```python
    action: Literal[
        "ping",
        "test_event",
        "terminal.spawn",
        "terminal.input",
        "terminal.resize",
        "terminal.close",
        # Chat mic-dictation: payload {"mode": "start" | "stop"}. Transcribe-only
        # into the chat input — never reaches the brain.
        "stt_dictate",
        # Drag-drop a mission/output card onto the Jarvis dock: payload
        # {slug, utterance, status, summary?, error?, mission_id?, thread_id?}.
        # Pulls the sub-agent task into the live conversation context.
        "mission.inject",
    ]
```

- [ ] **Step 3b: Add the handler branch + method in `server.py`**

In `_handle_command`, after the `stt_dictate` branch (server.py:1091-1092), add:

```python
        elif cmd.action == "mission.inject":
            await self._handle_mission_inject(session_id, cmd.payload)
```

Then add the handler method (next to `_handle_dictation`):

```python
    async def _handle_mission_inject(
        self, session_id: str, payload: dict[str, Any]
    ) -> None:
        """Drag-drop a mission card → inject it into the live conversation.

        Composes a bounded, human-readable user turn from the card's own data
        and publishes it as a normal ``MessageSent``. The existing brain
        dispatcher then answers it (spoken on voice, shown in chat) and the
        text lands in ``BrainManager._history`` so follow-ups stay in context.
        A distinct ``source_layer`` marks the turn for traceability; the brain
        dispatcher does NOT skip it (only ``"chat"``/``"brain:mock"`` are
        skipped), so it triggers a turn exactly like a typed message.
        """
        from jarvis.ui.web.mission_inject import compose_mission_inject_text

        text = compose_mission_inject_text(payload)
        if not text:
            logger.debug("mission.inject: empty/unparseable payload — ignored")
            return
        thread_id = str(payload.get("thread_id") or session_id)
        await self.bus.publish(
            MessageSent(
                thread_id=thread_id,
                role="user",
                text=text,
                source_layer="ui.web.ws.mission_inject",
            )
        )
```

(`MessageSent` is already imported in `server.py:35`; `Any` is already imported.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ui/web/test_ws_mission_inject.py tests/unit/ui/web/test_mission_inject.py -v`
Expected: PASS (6 tests total)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/schema.py jarvis/ui/web/server.py tests/unit/ui/web/test_ws_mission_inject.py
git commit -m "feat(ui/web): accept mission.inject WS command -> MessageSent brain turn"
```

---

### Task 3: Frontend — add `mission.inject` to the zod WSCommand enum

**Files:**
- Modify: `jarvis/ui/web/frontend/src/schema/ws.ts:39-50`
- Test: `jarvis/ui/web/frontend/src/schema/ws.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";
import { WSCommand } from "./ws";

describe("WSCommand mission.inject", () => {
  it("validates a mission.inject command", () => {
    const parsed = WSCommand.parse({
      type: "command",
      action: "mission.inject",
      payload: { slug: "s", utterance: "u", status: "success" },
    });
    expect(parsed.action).toBe("mission.inject");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/schema/ws.test.ts`
Expected: FAIL — zod rejects `"mission.inject"` (not in enum).

- [ ] **Step 3: Add the action to the zod enum**

In `ws.ts`, add to the `WSCommand` action enum (after `"stt_dictate"`):

```ts
    // Chat mic-dictation: payload {mode:"start"|"stop"} — transcribe-only.
    "stt_dictate",
    // Drag-drop a mission card onto the Jarvis dock — pulls the sub-agent
    // task into the live conversation context.
    "mission.inject",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/schema/ws.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/schema/ws.ts jarvis/ui/web/frontend/src/schema/ws.test.ts
git commit -m "feat(ui/web): zod WSCommand accepts mission.inject"
```

---

### Task 4: Frontend — make `SessionRow` draggable

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/OutputsView.tsx:152-251` (the `SessionRow` `<button>`)
- Test: `jarvis/ui/web/frontend/src/views/OutputsView.dragdrop.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { buildMissionDragPayload } from "./OutputsView";

describe("buildMissionDragPayload", () => {
  it("serialises the fields the dock needs", () => {
    const json = buildMissionDragPayload({
      slug: "20260615__x__abc",
      utterance: "recherchiere AI-News",
      status: "success",
      summary: "Three reports.",
      mission_id: "019ecb",
    });
    const parsed = JSON.parse(json);
    expect(parsed).toMatchObject({
      slug: "20260615__x__abc",
      utterance: "recherchiere AI-News",
      status: "success",
      summary: "Three reports.",
      mission_id: "019ecb",
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/views/OutputsView.dragdrop.test.tsx`
Expected: FAIL — `buildMissionDragPayload` is not exported.

- [ ] **Step 3: Export a payload builder and wire dragstart**

In `OutputsView.tsx`, add an exported helper near the top (after `URL_REGEX`):

```tsx
/** MIME type carrying a mission reference between a card and the Jarvis dock. */
export const MISSION_DND_MIME = "application/x-jarvis-mission";

/** Serialise the fields the dock/server need from a dragged Outputs card. */
export function buildMissionDragPayload(meta: OutputSummary): string {
  return JSON.stringify({
    slug: meta.slug,
    utterance: meta.utterance ?? "",
    status: meta.status ?? "unknown",
    summary: meta.summary ?? "",
    error: meta.error ?? "",
    mission_id: meta.mission_id ?? null,
  });
}
```

Then make the `SessionRow` button draggable — add these props to the `<button>` (line 153-162):

```tsx
    <button
      type="button"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(MISSION_DND_MIME, buildMissionDragPayload(meta));
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={onSelect}
      className={cn(
        "w-full cursor-grab rounded-lg border p-3 text-left transition-colors hover:border-primary/40 active:cursor-grabbing",
        isSelected
          ? "border-primary/40 bg-primary/10"
          : "border-border bg-card/40",
      )}
    >
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/views/OutputsView.dragdrop.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/OutputsView.tsx jarvis/ui/web/frontend/src/views/OutputsView.dragdrop.test.tsx
git commit -m "feat(ui/web): Outputs cards are draggable (carry mission ref)"
```

---

### Task 5: Frontend — the `JarvisDock` drop target

**Files:**
- Create: `jarvis/ui/web/frontend/src/components/JarvisDock.tsx`
- Test: `jarvis/ui/web/frontend/src/components/JarvisDock.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const send = vi.fn();
vi.mock("@/hooks/useWebSocket", () => ({ getWSClient: () => ({ send }) }));
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({ config: { style: "mascot", options: [] }, loading: false }),
}));
vi.mock("@/store/events", () => ({
  useEventStore: Object.assign(() => undefined, {
    getState: () => ({ ensureActiveThread: async () => "thread-9" }),
  }),
}));

import { JarvisDock, MISSION_DND_MIME } from "./JarvisDock";

function dropPayload(el: Element, json: string) {
  const dataTransfer = {
    getData: (mime: string) => (mime === MISSION_DND_MIME ? json : ""),
    types: [MISSION_DND_MIME],
  };
  fireEvent.drop(el, { dataTransfer });
}

describe("JarvisDock", () => {
  beforeEach(() => send.mockClear());

  it("sends a mission.inject command on a valid drop", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    dropPayload(
      zone,
      JSON.stringify({ slug: "s", utterance: "u", status: "success", summary: "y" }),
    );
    // flush the ensureActiveThread() microtask
    await Promise.resolve();
    await Promise.resolve();
    expect(send).toHaveBeenCalledTimes(1);
    const arg = send.mock.calls[0][0];
    expect(arg.type).toBe("command");
    expect(arg.action).toBe("mission.inject");
    expect(arg.payload.utterance).toBe("u");
    expect(arg.payload.thread_id).toBe("thread-9");
  });

  it("ignores a drop with no mission payload", () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    dropPayload(zone, "");
    expect(send).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/components/JarvisDock.test.tsx`
Expected: FAIL — `JarvisDock` module does not exist.

- [ ] **Step 3: Implement `JarvisDock`**

```tsx
import { useState } from "react";
import { getWSClient } from "@/hooks/useWebSocket";
import { useOverlayStyle } from "@/hooks/useOverlayStyle";
import { useEventStore } from "@/store/events";
import { MascotGigi } from "@/components/MascotGigi";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** MIME type carrying a mission reference from an Outputs card. Must match
 *  `MISSION_DND_MIME` in OutputsView.tsx. */
export const MISSION_DND_MIME = "application/x-jarvis-mission";

/**
 * A small, always-present "Jarvis presence" dock in the bottom-right corner.
 * Drop a mission/output card on it to pull that sub-agent task into the live
 * conversation (Jarvis speaks about it + it enters the context window).
 *
 * It mirrors the chosen on-screen display style: a slim bar for `whisper_bar`,
 * the ghost mascot otherwise. This in-app surface is the cloud-first drop
 * target — it works in any browser, unlike the separate Tk overlay windows.
 */
export function JarvisDock() {
  const t = useT();
  const { config } = useOverlayStyle();
  const [armed, setArmed] = useState(false); // a card is hovering
  const [flash, setFlash] = useState(false); // brief post-drop confirmation
  const isBar = config?.style === "whisper_bar";

  function hasMission(dt: DataTransfer | null): boolean {
    if (!dt) return false;
    return Array.from(dt.types ?? []).includes(MISSION_DND_MIME);
  }

  async function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setArmed(false);
    const raw = e.dataTransfer.getData(MISSION_DND_MIME);
    if (!raw) return;
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (!payload || (!payload.utterance && !payload.slug)) return;
    let threadId: string | undefined;
    try {
      threadId = await useEventStore.getState().ensureActiveThread();
    } catch {
      threadId = undefined;
    }
    getWSClient()?.send({
      type: "command",
      action: "mission.inject",
      payload: { ...payload, thread_id: threadId },
    });
    setFlash(true);
    setTimeout(() => setFlash(false), 1200);
  }

  return (
    <div
      data-testid="jarvis-dock"
      role="button"
      aria-label={t("jarvis_dock.aria")}
      title={t("jarvis_dock.hint")}
      onDragEnter={(e) => {
        if (hasMission(e.dataTransfer)) setArmed(true);
      }}
      onDragOver={(e) => {
        if (hasMission(e.dataTransfer)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDragLeave={() => setArmed(false)}
      onDrop={onDrop}
      className={cn(
        "fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-full border px-3 py-2 shadow-lg backdrop-blur transition-all",
        armed
          ? "scale-110 border-primary bg-primary/20 ring-2 ring-primary"
          : "border-border bg-card/70",
        flash && "ring-2 ring-emerald-400",
      )}
    >
      {isBar ? (
        <span className="flex h-6 items-end gap-0.5" aria-hidden>
          <span className="h-3 w-1 rounded-sm bg-primary/80" />
          <span className="h-5 w-1 rounded-sm bg-primary" />
          <span className="h-2 w-1 rounded-sm bg-primary/60" />
          <span className="h-4 w-1 rounded-sm bg-primary/80" />
        </span>
      ) : (
        <MascotGigi size={28} reactToVoice enableComments={false} />
      )}
      {armed && (
        <span className="text-xs font-medium text-primary">
          {t("jarvis_dock.drop_here")}
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/components/JarvisDock.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/JarvisDock.tsx jarvis/ui/web/frontend/src/components/JarvisDock.test.tsx
git commit -m "feat(ui/web): JarvisDock drop target for drag-drop mission injection"
```

---

### Task 6: Frontend — mount the dock globally + i18n strings

**Files:**
- Modify: `jarvis/ui/web/frontend/src/App.tsx:31-36`
- Modify: locale files (EN source) — `jarvis/ui/web/frontend/src/i18n/` (mirror existing key structure for `en`, `de`, `es`)

- [ ] **Step 1: Add the i18n keys (EN source first)**

Find the locale files (e.g. `src/i18n/en.ts` / `locales/en.json` — match the existing layout). Add:

```
"jarvis_dock.aria": "Jarvis — drop a mission here to talk about it",
"jarvis_dock.hint": "Drag a sub-agent output here to pull it into the conversation",
"jarvis_dock.drop_here": "Drop to discuss",
```

Mirror with translated values for `de` and `es` (German: "Jarvis — zieh eine Mission hierher, um darüber zu sprechen" / "Zum Besprechen ablegen"; Spanish equivalents). Keep the `i18n-allow` rule in mind — these are i18n keys with an EN source, which is allowed.

- [ ] **Step 2: Mount the dock in `App.tsx`**

Add the import and render it next to `ToastLayer`:

```tsx
import { JarvisDock } from "@/components/JarvisDock";
```

```tsx
      <ToastLayer />
      <JarvisDock />
```

- [ ] **Step 3: Build the frontend + run the full vitest suite**

Run:
```bash
cd jarvis/ui/web/frontend && npm run build && npx vitest run src/components/JarvisDock.test.tsx src/views/OutputsView.dragdrop.test.tsx src/schema/ws.test.ts
```
Expected: build OK, all new tests PASS, no type errors.

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/frontend/src/App.tsx jarvis/ui/web/frontend/src/i18n
git commit -m "feat(ui/web): mount JarvisDock globally + i18n strings"
```

---

### Task 7: Restart the live app and load the new build

**Files:** none (operational)

- [ ] **Step 1:** Confirm the editable install + dist are current (`npm run build` done in Task 6).
- [ ] **Step 2:** Restart the running app so the new backend handler + dist load:

```bash
curl -s -X POST http://127.0.0.1:<port>/api/settings/restart-app
```

(Use the live port. NOT `Stop-Process` — that returns Access Denied under the tray `pythonw.exe`. Restart is `POST /api/settings/restart-app`.)

- [ ] **Step 3:** Verify a clean boot (no errors in the launch log; `/api/health` responds).

---

### Task 8: Practical live test in Chrome (mandatory — maintainer asked for it)

**Files:** none (verification)

Use the `chrome-checkup-loop` / claude-in-chrome tools to drive the **running** app:

- [ ] **Step 1:** Open the app URL; switch to the **Outputs** view; confirm at least one mission card is present (seed one if the list is empty by dispatching a tiny mission, or use an existing card).
- [ ] **Step 2:** Confirm the `JarvisDock` is visible in the bottom-right corner.
- [ ] **Step 3:** Perform a real HTML5 drag from an Outputs card onto the dock (Playwright `browser_drag` or claude-in-chrome drag). Confirm the dock highlights on drag-over.
- [ ] **Step 4:** Verify, via console/network and the chat surface:
  - the WS frame `{type:"command", action:"mission.inject", ...}` was sent (network/WS log),
  - a `MessageSent` (the 📎 turn) and then a brain reply appear,
  - no console errors, no failed network requests.
- [ ] **Step 5:** Send a follow-up question ("what did that task find?") and confirm the reply shows the mission is in context (the brain references the dropped content) — proving "back into the context window".
- [ ] **Step 6:** Capture a screenshot into `screenshots/` for the record. Re-run the whole pass until one pass is fully clean.

---

## Self-review notes

- **Spec coverage:** drop surface (Tasks 5-6, in-app dock mirroring overlay style); draggable cards (Task 4); WS command (Tasks 2-3, both Pydantic + zod = five-layer guard); context injection via `MessageSent` reuse (Tasks 1-2); voice/text speak-about-it (reuse, verified in Task 8); error/edge handling (empty payload guarded both ends, char cap in Task 1); practical test (Task 8). Out-of-scope native Tk overlay correctly excluded.
- **No placeholders:** every code step shows full code; commands have expected output.
- **Type consistency:** `MISSION_DND_MIME` identical in OutputsView.tsx and JarvisDock.tsx; `compose_mission_inject_text`/`MISSION_INJECT_CAP` names match across Tasks 1-2; `mission.inject` action string identical in Pydantic (Task 2) and zod (Task 3); payload field names (`slug/utterance/status/summary/error/mission_id/thread_id`) consistent across builder (Task 4), dock (Task 5), and composer (Task 1).

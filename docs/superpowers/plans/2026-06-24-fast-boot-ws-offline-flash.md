# Fast-Boot "OFFLINE flash" Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a backend self-restart, the desktop window must show an honest "Starting…" state instead of a scary "OFFLINE" + dead chat input, and must reconnect within ~1s of the backend becoming ready.

**Architecture:** The fast-boot `FastBootstrap` server (branch `feat/fast-boot-bootstrap`) answers `/api/health` 200 in ~150ms so the window appears immediately, but it currently **holds** every WebSocket handshake open until the real app is ready (up to a 120s hold). A browser times out a pending WS handshake after tens of seconds → its reconnect backoff escalates to the 10s cap → the window shows "OFFLINE" for the whole ~20–50s warmup. The fix is a coordinated three-part change: (1) the bootstrap **accept-then-closes** a warming WebSocket with code **1013** ("try again later") instead of holding it, so the client gets a fast, *readable* close code; (2) the frontend reads that code, reconnects fast (no backoff escalation), and treats it as a distinct **warming** state; (3) the UI renders warming as "Starting…" (not "OFFLINE").

**Tech Stack:** Python (ASGI / uvicorn), `pytest` + `websockets` for the server probe; React + Zustand + Vitest for the frontend.

---

## Root cause (verified, with evidence)

Confirmed by live forensics + isolated probes (not assumptions):

1. **The OFFLINE label is NOT hardcoded.** `Sidebar.tsx:143` derives it from the store `connected` flag, which `useWebSocket.ts:47-48` sets from the WS `open`/`close` callbacks (default `false`, `events.ts:259`).
2. **The text chat sends over the WS**, so disabling the input on `!connected` is internally consistent — but `connected` is the wrong signal during a restart warmup (`ChatInput.tsx:81-86`, `172-173`).
3. **The bootstrap HOLDS warming WS handshakes.** In `fast_bootstrap.py._asgi`, a websocket scope during warming falls through to `await asyncio.wait_for(self._ready.wait(), timeout=120s)` (`fast_bootstrap.py:70-71`) — it does **not** fast-reject. Probe result: a client `websockets.connect(open_timeout=4)` against a warming bootstrap raises `TimeoutError: timed out during opening handshake`. The 1013 close in `_warming` (`fast_bootstrap.py:124-126`) only fires after the full 120s hold timeout, and being **pre-accept** it reaches the browser as a 403/1006, not 1013.
4. **Close-code reachability (decisive probe):**
   - **pre-accept** `websocket.close(1013)` → client sees `HTTP 403` (browser close code **1006** — unreadable as 1013).
   - **post-accept** `websocket.accept` then `websocket.close(1013)` → client connects, then `ws.recv()` raises `ConnectionClosedError` with **`code == 1013`** (readable).
   ⇒ To give the frontend a usable signal, the bootstrap MUST accept **then** close with 1013.
5. **The frontend ignores the close code and escalates backoff.** `ws.ts:92-96` close handler takes no `code`; `ws.ts:121-129` escalates 500ms→1s→2s→4s→8s→10s with no eager-reconnect trigger.

The self-restart is a full process + window relaunch (`desktop_app.py request_restart` → `relauncher`), so the window the user sees after a restart is always a fresh client whose first-ever connect happens during the warmup — exactly when the hold bites.

## Design decisions

- **Window still appears early.** We do NOT delay the window or change `/api/health` timing — fast-boot is the intended feature. We only fix what the window *shows* and how it reconnects during warmup. No change to `desktop_app.py` boot path (keeps the risky boot sequence untouched).
- **Three connection states, not two.** Add a `wsWarming` boolean alongside `connected`. Display precedence: `connected` → normal; else `wsWarming` → "Starting…"; else → "OFFLINE".
- **`connected` becomes welcome-gated.** Mark `connected=true` only when the real app's `welcome` frame arrives (not on raw socket `open`). The bootstrap's accept-then-close never sends a welcome, so it can never flip `connected` true → no flicker.
- **`wsWarming` defaults `true`** so the very first paint after a restart reads "Starting…", not "OFFLINE". The first non-1013 close (e.g. a genuinely dead backend → 1006) flips it to `false` → honest OFFLINE.
- **Input stays disabled while not connected** (you genuinely cannot send during boot), but the placeholder reads "Starting…" — removing the "broken" feeling. A send-queue is explicitly out of scope (YAGNI).

---

## Task 1: Bootstrap fast-rejects a warming WebSocket with a readable 1013

**Files:**
- Create: `tests/unit/ui/web/test_fast_bootstrap_ws.py`
- Modify: `jarvis/ui/web/fast_bootstrap.py:46-81` (the `_asgi` warming branch)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ui/web/test_fast_bootstrap_ws.py
"""A warming FastBootstrap must NOT hold a WS handshake open; it must
accept-then-close with code 1013 so the client gets a fast, readable
"try again later" and reconnects (instead of the browser timing out the
pending handshake → escalating backoff → a long spurious 'OFFLINE')."""
from __future__ import annotations

import asyncio
import logging

import pytest
import websockets

from jarvis.ui.web.fast_bootstrap import FastBootstrap


@pytest.mark.asyncio
async def test_warming_ws_is_fast_closed_with_1013() -> None:
    logging.disable(logging.CRITICAL)
    bs = FastBootstrap()
    await bs.serve("127.0.0.1", 47995)  # NOT set_app -> warming
    try:
        # Must connect quickly (handshake accepted) — the old hold made this
        # time out. open_timeout well under the old 120s hold proves no-hold.
        async with websockets.connect(
            "ws://127.0.0.1:47995/ws", open_timeout=3
        ) as ws:
            with pytest.raises(websockets.ConnectionClosed) as exc:
                await asyncio.wait_for(ws.recv(), timeout=3)
            assert exc.value.code == 1013
    finally:
        await bs.stop()
        logging.disable(logging.NOTSET)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/ui/web/test_fast_bootstrap_ws.py -v`
Expected: FAIL — `TimeoutError: timed out during opening handshake` (the bootstrap currently holds the handshake instead of accepting+closing).

- [ ] **Step 3: Add the warming-websocket branch (stop holding)**

In `jarvis/ui/web/fast_bootstrap.py`, inside `_asgi`, immediately AFTER the health-200 block (after line 68 `return`) and BEFORE the `try: await asyncio.wait_for(self._ready.wait(), ...)` block, insert:

```python
        # A websocket during warming must NOT hold the handshake open: a
        # browser times out a pending WS handshake (tens of seconds) and its
        # client then escalates its reconnect backoff, so the desktop window
        # shows a long spurious "OFFLINE" after every restart. Accept-then-close
        # with 1013 ("try again later") instead — the client receives a readable
        # close code and reconnects fast once the real app is registered.
        if kind == "websocket":
            await receive()  # consume the websocket.connect event
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1013})
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/ui/web/test_fast_bootstrap_ws.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/fast_bootstrap.py tests/unit/ui/web/test_fast_bootstrap_ws.py
git commit -m "fix(fast-boot): accept-then-close warming WS with 1013 instead of holding"
```

---

## Task 2: Add the honest `booting` i18n label

**Files:**
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json` (the `voice_state` object)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/de.json` (the `voice_state` object)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/es.json` (the `voice_state` object)

- [ ] **Step 1: Add the key to all three locales**

In each file, inside `"voice_state": { ... }`, add a `"booting"` entry next to `"offline"`:

`en.json`:
```json
    "offline": "Offline",
    "booting": "Starting…",
```

`de.json`:
```json
    "offline": "Offline",
    "booting": "Startet…",
```

`es.json` (place next to its existing `"offline"`):
```json
    "offline": "Sin conexión",
    "booting": "Iniciando…",
```

- [ ] **Step 2: Verify the JSON parses**

Run: `node -e "for(const l of ['en','de','es']){const j=require('./jarvis/ui/web/frontend/src/i18n/locales/'+l+'.json'); if(!j.voice_state.booting) throw new Error(l+' missing voice_state.booting'); console.log(l, j.voice_state.booting)}"`
Expected: prints the three labels, no throw.

- [ ] **Step 3: Commit**

```bash
git add jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "i18n: add voice_state.booting label (de/en/es)"
```

---

## Task 3: `WSClient` surfaces the close code and fast-retries on 1013

**Files:**
- Modify: `jarvis/ui/web/frontend/src/lib/ws.ts:10-17` (`WSClientOptions.onClose` signature), `:29-46` (field), `:92-101` (close listener), `:121-129` (`scheduleReconnect`)
- Test: `jarvis/ui/web/frontend/src/__tests__/ws.test.ts` (add cases)

- [ ] **Step 1: Write the failing tests**

Append to `jarvis/ui/web/frontend/src/__tests__/ws.test.ts`, inside the existing `describe("WSClient", ...)` block (the `MockWebSocket` already lives in this file):

```ts
  it("passes the close code to onClose", async () => {
    const onClose = vi.fn();
    const client = new WSClient({ onClose });
    client.connect();
    await Promise.resolve();
    MockWebSocket.last!.fire("close", { code: 1013 });
    expect(onClose).toHaveBeenCalledWith(1013);
    client.close();
  });

  it("retries fast (no backoff escalation) after a 1013 warming close", async () => {
    vi.useFakeTimers();
    try {
      const client = new WSClient();
      client.connect();
      const first = MockWebSocket.last;
      // Warming close → must reconnect at MIN_BACKOFF (500ms), not escalate.
      first!.fire("close", { code: 1013 });
      vi.advanceTimersByTime(500);
      expect(MockWebSocket.last).not.toBe(first); // a new socket was opened
      client.close();
    } finally {
      vi.useRealTimers();
    }
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/__tests__/ws.test.ts`
Expected: FAIL — `onClose` called with no argument; the 1013 test may pass incidentally but the `onClose(code)` test fails (current handler calls `this.onClose?.()`).

- [ ] **Step 3: Implement the close-code plumbing**

In `jarvis/ui/web/frontend/src/lib/ws.ts`:

(a) Change the option type (around line 16):
```ts
  onClose?: (code?: number) => void;
```

(b) Add a field next to the other private fields (around line 39):
```ts
  private lastCloseCode: number | undefined;
```

(c) Replace the close listener (lines 92-96) with:
```ts
    this.ws.addEventListener("close", (ev) => {
      this.stopPing();
      this.lastCloseCode = (ev as CloseEvent).code;
      this.onClose?.(this.lastCloseCode);
      if (!this.stopped) this.scheduleReconnect();
    });
```

(d) Replace `scheduleReconnect` (lines 121-129) with:
```ts
  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    // The fast-boot bootstrap closes a warming WS with code 1013 ("try again
    // later"): the backend is still booting, this is NOT a failure. Retry at a
    // fixed short interval instead of escalating the backoff, so the window
    // reconnects within ~1s of the real app becoming ready.
    const warming = this.lastCloseCode === 1013;
    const delay = warming ? MIN_BACKOFF : this.backoff;
    if (!warming) this.backoff = Math.min(MAX_BACKOFF, this.backoff * 2);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/__tests__/ws.test.ts`
Expected: PASS (all cases, including the pre-existing two).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/lib/ws.ts jarvis/ui/web/frontend/src/__tests__/ws.test.ts
git commit -m "feat(ws): surface close code + fast-retry on 1013 warming close"
```

---

## Task 4: Store gains `wsWarming` (default true) + `setWarming`

**Files:**
- Modify: `jarvis/ui/web/frontend/src/store/events.ts:164-165` (interface), `:219-220` (action type), `:258-259` (initial state), `:290-291` (action impl)
- Test: `jarvis/ui/web/frontend/src/store/events.test.ts` (add a case)

- [ ] **Step 1: Write the failing test**

Append to `jarvis/ui/web/frontend/src/store/events.test.ts` (use whatever import the file already has for `useEventStore`):

```ts
  it("tracks wsWarming and defaults it to true", () => {
    // Fresh store: warming until proven connected/offline so the first paint
    // after a restart reads "Starting…", never a scary "OFFLINE".
    expect(useEventStore.getState().wsWarming).toBe(true);
    useEventStore.getState().setWarming(false);
    expect(useEventStore.getState().wsWarming).toBe(false);
    useEventStore.getState().setWarming(true);
    expect(useEventStore.getState().wsWarming).toBe(true);
  });
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/store/events.test.ts`
Expected: FAIL — `wsWarming` / `setWarming` undefined.

- [ ] **Step 3: Add the state + action**

In `jarvis/ui/web/frontend/src/store/events.ts`:

(a) In the `EventStore` interface, next to `connected: boolean;` (line 165):
```ts
  // True while the WS keeps getting closed with code 1013 by the fast-boot
  // bootstrap (backend still warming up). Distinct from `connected`: drives the
  // honest "Starting…" indicator instead of "OFFLINE". Defaults true so the
  // first paint after a restart never flashes "OFFLINE".
  wsWarming: boolean;
```

(b) In the interface action list, next to `setConnected` (line 220):
```ts
  setWarming: (warming: boolean) => void;
```

(c) In the initial state object, next to `connected: false,` (line 259):
```ts
  wsWarming: true,
```

(d) In the action implementations, next to `setConnected` (line 291):
```ts
  setWarming: (warming) => set({ wsWarming: warming }),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/store/events.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/store/events.ts jarvis/ui/web/frontend/src/store/events.test.ts
git commit -m "feat(store): add wsWarming state (default true) for the boot indicator"
```

---

## Task 5: `useWebSocket` gates `connected` on the welcome frame and sets `wsWarming` on 1013

**Files:**
- Modify: `jarvis/ui/web/frontend/src/hooks/useWebSocket.ts:31-48` (grab `setWarming`, change `onOpen`/`onClose`), `:49-52` (welcome branch sets connected)
- Test: Create `jarvis/ui/web/frontend/src/hooks/useWebSocket.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// jarvis/ui/web/frontend/src/hooks/useWebSocket.test.tsx
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWebSocket } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";

/** Same minimal WebSocket mock shape as src/__tests__/ws.test.ts. */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = MockWebSocket.OPEN;
  static last: MockWebSocket | null = null;
  private listeners: Record<string, Array<(ev: any) => void>> = {};
  constructor(public url: string) {
    MockWebSocket.last = this;
    queueMicrotask(() => this.fire("open", {}));
  }
  addEventListener(type: string, fn: (ev: any) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", { code: 1000 });
  });
  fire(type: string, ev: any) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }
  deliver(data: unknown) {
    this.fire("message", { data: typeof data === "string" ? data : JSON.stringify(data) });
  }
}

function Harness() {
  useWebSocket();
  return null;
}

describe("useWebSocket connection state", () => {
  const OriginalWS = globalThis.WebSocket;
  beforeEach(() => {
    (globalThis as any).WebSocket = MockWebSocket;
    (window as any).__JARVIS_TOKEN = undefined;
    useEventStore.setState({ connected: false, wsWarming: true });
  });
  afterEach(() => {
    cleanup();
    (globalThis as any).WebSocket = OriginalWS;
    MockWebSocket.last = null;
  });

  it("marks connected only when the welcome frame arrives", async () => {
    render(<Harness />);
    await act(async () => { await Promise.resolve(); }); // run the queued "open"
    // Raw socket open alone must NOT mark connected (the bootstrap also opens).
    expect(useEventStore.getState().connected).toBe(false);
    await act(async () => {
      MockWebSocket.last!.deliver({
        type: "welcome",
        session_id: "s",
        version: "0.1.0",
        token: "t",
      });
    });
    expect(useEventStore.getState().connected).toBe(true);
    expect(useEventStore.getState().wsWarming).toBe(false);
  });

  it("sets wsWarming on a 1013 close and clears it on a non-1013 close", async () => {
    render(<Harness />);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { MockWebSocket.last!.fire("close", { code: 1013 }); });
    expect(useEventStore.getState().wsWarming).toBe(true);
    expect(useEventStore.getState().connected).toBe(false);
    await act(async () => { MockWebSocket.last!.fire("close", { code: 1006 }); });
    expect(useEventStore.getState().wsWarming).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/hooks/useWebSocket.test.tsx`
Expected: FAIL — first test fails because `onOpen` currently sets `connected=true` on raw open (welcome-gating not implemented); the warming test fails because `wsWarming` is never set.

- [ ] **Step 3: Implement welcome-gating + warming**

In `jarvis/ui/web/frontend/src/hooks/useWebSocket.ts`:

(a) Add the store selector next to the others (after line 31 `const setConnected = ...`):
```ts
  const setWarming = useEventStore((s) => s.setWarming);
```

(b) Replace the `onOpen`/`onClose` lines (47-48) with:
```ts
      // `connected` is welcome-gated (see the welcome branch below), so a raw
      // socket open must NOT mark connected — the fast-boot bootstrap also
      // opens then closes with 1013 without ever sending a welcome frame.
      onOpen: () => {},
      onClose: (code) => {
        // 1013 = bootstrap "try again later" → backend still warming, not down.
        setWarming(code === 1013);
        setConnected(false);
      },
```

(c) In `onMessage`, replace the welcome short-circuit (lines 50-51):
```ts
        const welcome = WSWelcome.safeParse(raw);
        if (welcome.success) {
          // The real app sends `welcome` immediately after accepting the socket;
          // this — not the raw open — is the authoritative "connected" signal.
          setConnected(true);
          setWarming(false);
          return;
        }
```

(d) Add `setWarming` to the effect dependency array (the list ending at line 265, after `setConnected,`):
```ts
    setConnected,
    setWarming,
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/hooks/useWebSocket.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useWebSocket.ts jarvis/ui/web/frontend/src/hooks/useWebSocket.test.tsx
git commit -m "feat(ws): welcome-gate connected; set wsWarming on 1013 close"
```

---

## Task 6: Sidebar shows "Starting…" while warming, "OFFLINE" only when truly offline

**Files:**
- Modify: `jarvis/ui/web/frontend/src/components/layout/Sidebar.tsx:130-147` (read `wsWarming`, branch the label + spinner)
- Test: `jarvis/ui/web/frontend/src/components/layout/Sidebar.test.tsx` (add cases)

- [ ] **Step 1: Write the failing tests**

Append inside the existing `describe("Sidebar voice header", ...)` block in `Sidebar.test.tsx`:

```tsx
  test("shows the booting label (not OFFLINE) while warming", () => {
    useEventStore.setState({ connected: false, wsWarming: true });
    render(<Sidebar />);
    expect(screen.getByText("Starting…")).toBeInTheDocument();
    expect(screen.queryByText("Offline")).toBeNull();
  });

  test("shows OFFLINE only when disconnected and not warming", () => {
    useEventStore.setState({ connected: false, wsWarming: false });
    render(<Sidebar />);
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/components/layout/Sidebar.test.tsx`
Expected: FAIL — while disconnected the label is always "Offline" (no warming branch yet).

- [ ] **Step 3: Implement the warming branch**

In `jarvis/ui/web/frontend/src/components/layout/Sidebar.tsx`:

(a) Add the selector next to `const connected = ...` (line 130):
```ts
  const wsWarming = useEventStore((s) => s.wsWarming);
```

(b) Replace the `voiceWarming` + `voiceLabel` block (lines 141-147) with:
```ts
  const voiceWarming = connected && !voiceReady;
  // A disconnected-but-warming socket (fast-boot backend still starting) reads
  // "Starting…", not the alarming "OFFLINE".
  const bootWarming = !connected && wsWarming;
  const showSpinner = voiceWarming || bootWarming;
  const vs = VOICE_STATE_STYLE[voiceState] ?? VOICE_STATE_STYLE.idle;
  const voiceLabel = !connected
    ? bootWarming
      ? t("voice_state.booting")
      : t("voice_state.offline")
    : voiceWarming
      ? t("voice_state.starting")
      : t(`voice_state.${voiceState}`);
```

(c) Replace the spinner-vs-dot condition (line 170 `{voiceWarming ? (`) with `{showSpinner ? (`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/components/layout/Sidebar.test.tsx`
Expected: PASS (existing cases stay green — they set `connected: true`).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/layout/Sidebar.tsx jarvis/ui/web/frontend/src/components/layout/Sidebar.test.tsx
git commit -m "feat(sidebar): show Starting… while warming, OFFLINE only when truly offline"
```

---

## Task 7: ChatInput shows an honest "Starting…" placeholder while warming

**Files:**
- Modify: `jarvis/ui/web/frontend/src/components/ChatInput.tsx:17` (read `wsWarming`), `:172` (placeholder)
- Test: Create `jarvis/ui/web/frontend/src/components/ChatInput.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// jarvis/ui/web/frontend/src/components/ChatInput.test.tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { ChatInput } from "@/components/ChatInput";
import { useEventStore } from "@/store/events";

describe("ChatInput offline/warming placeholder", () => {
  beforeEach(() => {
    useEventStore.setState({ connected: false, wsWarming: true, chatThinking: false, dictating: false });
  });
  afterEach(() => cleanup());

  test("shows the booting placeholder while warming", () => {
    render(<ChatInput />);
    const box = screen.getByPlaceholderText("Starting…");
    expect(box).toBeDisabled();
  });

  test("shows the offline placeholder when truly offline", () => {
    useEventStore.setState({ connected: false, wsWarming: false });
    render(<ChatInput />);
    expect(screen.getByPlaceholderText("Offline")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/components/ChatInput.test.tsx`
Expected: FAIL — placeholder is always "Offline" when disconnected.

- [ ] **Step 3: Implement the warming placeholder**

In `jarvis/ui/web/frontend/src/components/ChatInput.tsx`:

(a) Add the selector next to `const connected = ...` (line 17):
```ts
  const wsWarming = useEventStore((s) => s.wsWarming);
```

(b) Replace the `placeholder` prop (line 172) with:
```tsx
          placeholder={
            connected
              ? t("chats_view.input_placeholder")
              : wsWarming
                ? t("voice_state.booting")
                : t("voice_state.offline")
          }
```

(Leave `disabled={!connected}` unchanged — sending genuinely requires the live socket; the honest placeholder is the fix.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/components/ChatInput.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/ChatInput.tsx jarvis/ui/web/frontend/src/components/ChatInput.test.tsx
git commit -m "feat(chat-input): honest Starting… placeholder while the backend warms up"
```

---

## Task 8: Full verification + production build

**Files:** none (verification only)

- [ ] **Step 1: Run the frontend test suite**

Run: `cd jarvis/ui/web/frontend && npx vitest run`
Expected: PASS (no regressions; the new cases green).

- [ ] **Step 2: Type-check + production build**

Run: `cd jarvis/ui/web/frontend && npm run build`
Expected: `tsc` clean, Vite build succeeds → `jarvis/ui/web/dist` refreshed (so the running editable backend serves the new bundle).

- [ ] **Step 3: Run the bootstrap server test**

Run: `python -m pytest tests/unit/ui/web/test_fast_bootstrap_ws.py -v`
Expected: PASS.

- [ ] **Step 4: Live verification (manual, on the maintainer's desktop)**

Restart the running app via `POST /api/settings/restart-app`, then watch the new window during the ~20–50s warmup. Expected: the sidebar reads **"Startet…"** with a spinner and the chat placeholder reads **"Startet…"** (NOT "Offline"); within ~1s of the backend finishing boot the sidebar flips to the normal voice state and the chat input becomes active. There must be no multi-second "OFFLINE" + dead input.

- [ ] **Step 5: Final commit (if any build artifacts changed)**

```bash
git add jarvis/ui/web/dist
git commit -m "build(frontend): rebuild bundle with the boot-warming indicator"
```

---

## Self-review

- **Spec coverage:** Server no-hold + readable 1013 (Task 1) ✓; fast-retry on 1013 (Task 3) ✓; tri-state with honest label (Tasks 4–7) ✓; no `desktop_app.py`/boot-path change (by design) ✓.
- **Type/name consistency:** `wsWarming` (state) + `setWarming` (action) used identically in Tasks 4, 5, 6, 7. `onClose?: (code?: number) => void` defined in Task 3 and consumed in Task 5. `voice_state.booting` added in Task 2 and consumed in Tasks 6, 7.
- **Risk:** All frontend display logic keys off two booleans already in the store pattern (`connected`, `voiceReady`); the only behavioral change to existing flows is welcome-gating `connected`, which adds at most one network-frame of latency before the chat unlocks on a normal connect — covered by the Task 5 welcome test. The server change is additive (a new early branch) and cannot affect the post-`set_app` delegated path.
- **Out of scope (intentional, YAGNI):** a send-queue that buffers typed messages during boot; `focus`/`online`/`visibilitychange` eager-reconnect triggers (the 1013 fast-retry already reconnects within ~1s).

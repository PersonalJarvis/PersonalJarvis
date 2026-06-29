# Agent D — Live-Reload & Obsidian Integration

You are Agent D on Phase B3 of Personal Jarvis. **Read `00-OVERVIEW.md` first**, then this. You build two things:

1. **Live-reload**: a filesystem watchdog on `wiki/obsidian-vault/` that pushes change events to the frontend over WebSocket, so the Wiki tab updates instantly when the WikiCurator writes a page or the user edits in Obsidian.
2. **Obsidian integration**: a "Open in Obsidian" button per page, plus a global vault button, that calls the `obsidian://` URL scheme to hand editing off to the real Obsidian app. Graceful fallback if Obsidian is not installed.

You touch the backend (watchdog + WebSocket endpoint) and the frontend (React hook + button component). You do **not** touch the tree, page renderer, graph, or search.

---

## 1. What you own

| File | Status | Purpose |
|---|---|---|
| `jarvis/memory/wiki/watcher.py` | **NEW** | `watchdog`-based file watcher with debounce + EventBus publisher |
| `jarvis/ui/web/wiki_ws.py` | **NEW** | FastAPI WebSocket endpoint `/api/wiki/live`; subscribes to bus, forwards JSON messages |
| `jarvis/ui/web/server.py` | **MODIFY** | Register the watcher on startup; mount the WS route |
| `jarvis/core/events.py` | **MODIFY (additive)** | Add `WikiPageChanged` event dataclass |
| `jarvis/ui/web/frontend/src/hooks/useWikiLive.ts` | **NEW** | React hook: connects to WS, invalidates React Query keys on each event |
| `jarvis/ui/web/frontend/src/components/wiki/ObsidianButton.tsx` | **NEW** | Per-page "Open in Obsidian" button + protocol-handler detection |
| `jarvis/ui/web/frontend/src/lib/obsidian.ts` | **NEW** | Pure helper: build `obsidian://open?vault=…&file=…` URL, encode params correctly |
| `tests/unit/memory/wiki/test_watcher.py` | **NEW** | Watcher emits one event per file change after debounce |
| `tests/unit/ui/web/test_wiki_ws.py` | **NEW** | WS forwards bus events, drops disconnected clients cleanly |
| `tests/integration/ui/wiki/test_live_reload.py` | **NEW** | End-to-end: write a file in tmp vault → WS client receives event |

---

## 2. What you reuse

| Use | Where | What it does |
|---|---|---|
| `watchdog` library | already in `requirements.txt` (used by Skill registry hot-reload) | Cross-platform filesystem events |
| `EventBus` | `jarvis.core.bus` | Lateral comms; watcher publishes, WS endpoint subscribes |
| WebSocket pattern | `jarvis/ui/web/server.py` existing WS endpoints (search for `WebSocket(`) | Reply-envelope, accept/disconnect handling |
| Skill watcher pattern | `jarvis/skills/registry.py:94, 134` | Reference for debounce + thread-safety in a watchdog handler |
| `process_utils.NO_WINDOW_CREATIONFLAGS` | `jarvis/core/process_utils.py` | Not needed here (no subprocess), but worth knowing exists |

---

## 3. Backend behaviour

### 3.1 Watchdog

```python
# jarvis/memory/wiki/watcher.py

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from jarvis.core.bus import EventBus
from jarvis.core.events import WikiPageChanged
from pathlib import Path
import threading, time, asyncio

class WikiWatcher:
    """Watch a vault root, debounce, publish WikiPageChanged events.

    Debounce window is 500 ms — the WikiCurator typically writes 10-15 pages
    in <300 ms during one ingest; we want one burst of UI updates, not 15.
    """
    def __init__(self, vault_root: Path, bus: EventBus, debounce_ms: int = 500): ...
    def start(self) -> None: ...
    async def shutdown(self) -> None: ...
```

Implementation notes:

- Use `Observer` (not `PollingObserver` — we are on local NTFS, native events work).
- Only watch `entities/`, `concepts/`, `projects/`, `sessions/`. Ignore `_archive/`, `attachments/`, `99-templates/`. Filter on `.md` extension.
- Debounce per-file: if the same path fires twice in <500 ms, drop the second. Use a per-path `threading.Timer` keyed in a `dict[Path, Timer]`.
- Map `FileCreatedEvent`/`FileModifiedEvent`/`FileMovedEvent`/`FileDeletedEvent` to `kind`: `"created" | "modified" | "deleted"`. For `FileMovedEvent`, emit two events (deleted at src, created at dest).
- Cross-thread publish: watchdog runs in its own thread; use `asyncio.run_coroutine_threadsafe(bus.publish(event), loop)` with the loop captured at startup.

### 3.2 New event

```python
# jarvis/core/events.py — add:

@dataclass(frozen=True)
class WikiPageChanged:
    """Emitted when a markdown file in the wiki vault changes on disk."""
    trace_id: UUID
    timestamp_ns: int
    slug: str
    path: str           # vault-relative POSIX path, e.g. "entities/sam.md"
    kind: str           # "created" | "modified" | "deleted"
```

### 3.3 WebSocket endpoint

```python
# jarvis/ui/web/wiki_ws.py

@router.websocket("/api/wiki/live")
async def wiki_live(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    def _on_event(ev: WikiPageChanged) -> None:
        if not queue.full():
            queue.put_nowait(ev)

    unsub = bus.subscribe(WikiPageChanged, _on_event)
    try:
        while True:
            ev = await queue.get()
            await ws.send_json({
                "type": "page_changed",
                "slug": ev.slug,
                "path": ev.path,
                "kind": ev.kind,
            })
    except WebSocketDisconnect:
        pass
    finally:
        unsub()
```

Drop policy: if a client is slow and the queue fills, *drop new events* (queue.full() check above) rather than backpressure the bus. That's acceptable because the client also re-fetches on reconnect.

### 3.4 Server wiring

In `jarvis/ui/web/server.py`'s startup section (look for where B5's `bootstrap_wiki_integration` is called — that is the existing reference pattern):

```python
# After bootstrap_wiki_integration:
from jarvis.memory.wiki.watcher import WikiWatcher
watcher = WikiWatcher(vault_root=vault_root, bus=app.state.bus)
watcher.start()
app.state.wiki_watcher = watcher

# At shutdown:
await app.state.wiki_watcher.shutdown()
```

Wrap watcher startup in a `try/except` that **logs and continues** if `watchdog` cannot create the observer (e.g. vault root missing). The desktop app must still boot when the vault is empty.

---

## 4. Frontend behaviour

### 4.1 `useWikiLive` hook

```typescript
// jarvis/ui/web/frontend/src/hooks/useWikiLive.ts

export function useWikiLive(): { connected: boolean; lastEventAt: number | null } {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);

  useEffect(() => {
    const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/wiki/live`;
    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "page_changed") {
          // Invalidate the four queries that depend on vault state.
          qc.invalidateQueries({ queryKey: ["wiki", "tree"] });
          qc.invalidateQueries({ queryKey: ["wiki", "page", msg.slug] });
          qc.invalidateQueries({ queryKey: ["wiki", "graph"] });
          qc.invalidateQueries({ queryKey: ["wiki", "backlinks", msg.slug] });
          setLastEventAt(Date.now());
        }
      };
      ws.onclose = () => {
        setConnected(false);
        // Reconnect with exponential backoff up to 30 s
        reconnectTimer = window.setTimeout(connect, Math.min(30_000, 1000 * Math.pow(2, reconnectAttempts++)));
      };
      ws.onerror = () => ws?.close();
    };

    let reconnectAttempts = 0;
    connect();

    return () => {
      ws?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [qc]);

  return { connected, lastEventAt };
}
```

The hook is mounted **once** at `WikiView` level (not deeper) so we have a single WebSocket connection per tab. If the user switches away from the Wiki tab, the hook unmounts and the WS closes — that is desired behaviour (no background polling when not visible).

### 4.2 `ObsidianButton`

```typescript
// jarvis/ui/web/frontend/src/components/wiki/ObsidianButton.tsx

interface ObsidianButtonProps {
  vaultRelPath: string;     // e.g. "entities/sam.md"
}

export function ObsidianButton({ vaultRelPath }: ObsidianButtonProps): JSX.Element {
  const handleClick = () => {
    const url = buildObsidianUrl(vaultRelPath);
    // Try to open. If Obsidian protocol handler is not registered,
    // the browser will silently fail — show a fallback toast after 800ms.
    window.location.href = url;
    setTimeout(() => {
      // We can't actually detect success/failure of obsidian:// in the browser.
      // The toast informs the user what was attempted.
      toast({
        title: "An Obsidian übergeben",
        description: `${vaultRelPath} — falls Obsidian nicht startet, ist es nicht installiert.`,
      });
    }, 800);
  };
  return <button onClick={handleClick} className="obsidian-btn">…</button>;
}
```

### 4.3 `obsidian.ts` helper

```typescript
// jarvis/ui/web/frontend/src/lib/obsidian.ts

export const VAULT_NAME = "obsidian-vault";  // matches wiki/obsidian-vault/ folder name

export function buildObsidianUrl(vaultRelPath: string): string {
  return `obsidian://open?vault=${encodeURIComponent(VAULT_NAME)}&file=${encodeURIComponent(vaultRelPath)}`;
}
```

Pure function, easy to test.

### 4.4 Global vault button (mockup shows this in the tab header)

In addition to the per-page button, render a small "Open vault in Obsidian" button in the Wiki tab's `ViewHeader` (mockup top-right). Same handler, but with `vaultRelPath = ""` and the URL becomes `obsidian://open?vault=obsidian-vault` (Obsidian opens to the vault root).

---

## 5. Tests

### 5.1 `test_watcher.py`

Min 5 cases (use `pytest-asyncio` + `tmp_path`):

1. Create a `.md` file in the temp vault → exactly one `WikiPageChanged{kind="created"}` event on the bus within 1 s.
2. Modify the file → one `kind="modified"` event.
3. Delete the file → one `kind="deleted"` event.
4. Modify the same file 5 times in 200 ms → exactly one event after debounce.
5. Create a non-markdown file (`.txt`) → no event.

### 5.2 `test_wiki_ws.py`

Min 3 cases (FastAPI `TestClient` WebSocket test):

1. Connect to `/api/wiki/live`, publish `WikiPageChanged` on the bus → client receives JSON.
2. Connect 3 clients, publish one event → all 3 receive it.
3. Disconnect mid-stream, publish event → no exception, bus subscriber removed.

### 5.3 `test_live_reload.py` (integration)

One end-to-end test:
1. Start the server with a tmp vault.
2. Open a WS connection.
3. Write a new file `entities/test.md` to the vault directly.
4. Within 2 s, the WS client receives the expected `page_changed` JSON.

### 5.4 Frontend tests

`obsidian.test.ts`:
1. `buildObsidianUrl("entities/sam.md")` → exact expected URL with proper URL encoding.
2. Empty path → URL ends with `vault=obsidian-vault` (no `&file=`).
3. Path with spaces → space → `%20`.

`useWikiLive` test (Vitest + WebSocket mock):
1. Mounts → opens WS.
2. Receives `page_changed` → calls `queryClient.invalidateQueries` four times with the right keys.
3. Unmounts → closes WS.

---

## 6. Hard negatives

- ❌ Don't use `PollingObserver`. Native NTFS events are fast and free.
- ❌ Don't write the file path directly into the WS message without normalising to POSIX (`Path.as_posix()`). Frontend treats paths as strings; Windows backslashes leak through JSON unescaped.
- ❌ Don't catch and swallow watcher exceptions silently. Log with `log.warning("wiki_watcher_event_failed", ...)` and re-raise in dev (`if cfg.app.debug: raise`).
- ❌ Don't add CSRF protection or auth on the WS. The desktop app is local-only.
- ❌ Don't try to detect whether Obsidian is installed via JS — it's not reliably possible from the browser sandbox. The 800 ms post-click toast is sufficient.
- ❌ Don't bundle the watchdog observer in a `try/except` that suppresses everything. Specific exceptions only (`FileNotFoundError` for missing vault, `PermissionError` for locked dirs).
- ❌ Don't reuse a stale `event loop` reference across reloads. Capture the loop in `start()` not in `__init__`.
- ❌ Don't fire WS reconnects without exponential backoff. A failed handshake in a loop can DOS the server.

---

## 7. Size estimate

`watcher.py` ~150 lines. `wiki_ws.py` ~80 lines. `useWikiLive.ts` ~80 lines. `ObsidianButton.tsx` ~60 lines. `obsidian.ts` ~20 lines. Tests ~400 lines. Event addition ~10 lines. Total ~800 lines of new code.

---

## 8. Closing report

Final line: `Goal erfüllt: ja — Grund: <one sentence>` (or `nein`).

# Overlay IPC Protocol

> Reference for the inter-process communication between Hauptjarvis (Python) and the
> Overlay process (Qt/WebView). Extracted from `OS-Level/OS-LEVEL_PLAN.md` §10
> (IPC Protocol), §10.4 (Backpressure), §10.5 (Reconnection), §10.6 (Channel-Routing)
> and AD-5 (Triple-Channel Design).

## 1. Overview — Three Channels (Plan §10.5, AD-5)

The overlay uses **three transport channels** in priority order. The implementation
falls back automatically; the protocol on the wire is identical across channels.

| Channel | Purpose | Data Rate |
|---|---|---|
| **WebSocket** (`127.0.0.1:7842`) | Primary control channel — state changes, clicks, action lifecycle, config reload, heartbeat, errors | Low (≤ 5 KB/s active) |
| **Shared Memory** (`jarvis-cursor-{8 hex chars}`, 32 B block) | High-frequency cursor stream (60 Hz) — bypasses the WS-event-loop entirely | ~480 B/s |
| **Named Pipe** (`\\.\pipe\jarvis-overlay`) | Fallback for the WS channel if loopback-binding is blocked (corp AV / locked-down environments) | Same envelope as WS |

The Hauptjarvis process is always the **server** for WS + Named-Pipe; the Overlay
is always the **client**. The cursor SHM block is **single-writer** (Hauptjarvis) /
**single-reader** (Overlay) with a Seqlock pattern (Plan §11.3 / §11.4).

---

## 2. Wire-Envelope Format (Plan §10.1)

Every message — regardless of channel — uses this envelope:

```json
{
  "v": 1,
  "type": "state | click | action_started | action_ended | cursor | heartbeat | config | ack | error",
  "id": "01HX9...ULID",
  "ts_ns": 1714478400123456789,
  "target": "edgeglow | mascot | *",
  "payload": { /* per-type, see §3 */ }
}
```

| Field | Type | Notes |
|---|---|---|
| `v` | int | Schema-Version. Major-bump = breaking. Currently `1`. |
| `type` | enum | Discriminant — selects the payload schema. |
| `id` | string (ULID) | Unique per message, used for dedup and ack-correlation. |
| `ts_ns` | int (ns since unix epoch) | Sender wallclock; lets observers compute end-to-end latency. |
| `target` | enum | Which overlay component should consume; `*` = broadcast to all. |
| `payload` | object | Type-specific (see §3). |

---

## 3. Payload Schemas (Plan §10.2)

### 3.1 `state`
```json
{
  "state": "idle | listening | thinking | typing | clicking | speaking | error | hidden",
  "intensity": 1.0,
  "since_ts_ns": 1714478400000000000,
  "reason": "wakeword | user | tool | timeout | error"
}
```

`intensity` is normalized `[0.0, 1.0]` and modulates the visual strength of the
state (`--intensity` CSS-variable in the renderer). Per-state defaults are listed
in `docs/overlay-state-machine.md` §1.

### 3.2 `click`
```json
{
  "x": 1024,
  "y": 768,
  "monitor": "\\\\.\\DISPLAY1",
  "button": "left | right | middle",
  "modifiers": ["ctrl", "shift"],
  "wallclock_ns": 1714478400500000000
}
```

Ripple-effect coordinates. `monitor` uses the Win32 `szDevice` form.

### 3.3 `action_started`
```json
{
  "kind": "click | type | move | navigate | hotkey | scroll",
  "action_id": "01HX9...ULID",
  "duration_hint_ms": 2000
}
```

Emitted by `OverlayBridge.action(...)` decorator/context-manager **before** the
actual PC-action runs. Triggers the `typing` or `clicking` state and the
yellow-glow.

### 3.4 `action_ended`
```json
{
  "action_id": "01HX9...ULID",
  "succeeded": true,
  "duration_actual_ms": 1953
}
```

Closes an action started with the matching `action_id`. Returns the state to
`idle` (or whatever the post-action state is).

### 3.5 `cursor` (fallback only)
```json
{
  "x": 512,
  "y": 384,
  "monitor": "\\\\.\\DISPLAY1"
}
```

**Sent only if SHM is unavailable.** Normal cursor streaming uses the
shared-memory block (32 B, 60 Hz, see Plan §11) for zero-IPC-overhead transfer.

### 3.6 `heartbeat` (1 Hz, bidirectional)
```json
{
  "uptime_s": 1234,
  "rss_mb": 78.5,
  "fps_actual": 59.8,
  "fps_target": 60,
  "drops": 0,
  "ws_connected": true,
  "shm_attached": true
}
```

### 3.7 `config` (Hauptjarvis → Overlay, on config reload)
```json
{
  "theme": { "yellow_primary": "#FFC700" },
  "mascot_enabled": true,
  "mascot_pos": { "monitor": "\\\\.\\DISPLAY1", "x": 200, "y": 80 },
  "fps_active": 30,
  "fps_burst": 60,
  "all_monitors": false,
  "hide_on_fullscreen": true,
  "hide_from_capture": true,
  "respect_reduced_motion": true,
  "shm_cursor_name": "jarvis-cursor-7f3e",
  "shm_cursor_hz": 60
}
```

Hauptjarvis filters `jarvis.toml` so only the `[overlay]` subset reaches the
Overlay process (Plan §18.4 — secrets never leak).

### 3.8 `ack` (Overlay → Hauptjarvis, optional)
```json
{
  "ack_id": "01HX9...ULID",
  "received_ts_ns": 1714478400123000000,
  "rendered_ts_ns": 1714478400140000000
}
```

Only used for state-changes that have a caller waiting (rare). `rendered_ts_ns`
is optional and present only when measurable from the renderer.

### 3.9 `error`
```json
{
  "code": "schema_invalid | render_failed | shm_unavailable",
  "message": "human-readable",
  "recoverable": true,
  "context": { }
}
```

---

## 4. Backpressure Policy (Plan §10.4)

- **Outbound queue (Python → Overlay):** bounded at **256 messages**.
  - When full: drop **oldest non-state messages first** (`cursor`, `ack`).
  - Drop `state` messages **last** — state must always reach the overlay.
  - Log a `WARNING` and increment a drop-counter.
  - **Never block the sender.** A blocked PC-action thread is worse than a
    missed cursor frame.
- **Inbound queue (Overlay → Python):** bounded at **64 messages**.
- **Coalescing** per AD-17 happens at send-time, **before** queueing. Two
  identical-type events within 16 ms collapse to one; click events are never
  coalesced (every click deserves a ripple).

---

## 5. Reconnection (Plan §10.5)

- **Heartbeat interval:** 1 s (each side sends).
- **Heartbeat timeout:** 3 s.
- **Reconnect-backoff:** `0.5 s, 1 s, 2 s, 4 s, 8 s, 30 s` (cap), reset on
  successful heartbeat.
- **Direction:** Initial connection is always Overlay → Hauptjarvis. If the WS
  drops, the Overlay attempts reconnect.
- **State-resync after reconnect:** Hauptjarvis sends the current `state`
  message **as the first message** after a reconnect — the Overlay re-renders
  from a known-good baseline.

---

## 6. Channel-Routing (Plan §10.6)

The Overlay process listens on **one** WS connection. Inside the Overlay
process, messages are dispatched to the appropriate window by the `target`
field:

- `target == "edgeglow"` → routed to the per-monitor edge-glow windows.
- `target == "mascot"` → routed to the mascot window.
- `target == "*"` → broadcast to all windows.

Single-connection routing keeps the WS handshake/heartbeat budget at one pair
regardless of how many windows the Overlay manages (one per monitor + mascot).

---

## 7. Validation (Plan §10.3)

- **Python side:** Pydantic v2 models in `OS-Level/src/overlay/schema.py`.
- **TypeScript side:** Zod schemas in `OS-Level/overlay-ui/src/schema.ts`.
- **CI-Gate:** `pytest tests/overlay/test_schema_symmetry.py` — exports
  JSON-Schema from Pydantic, parses Zod-equivalent, compares structurally. CI
  fails on drift.

**Source of truth for the wire format:** `OS-Level/src/overlay/schema.py`
(Pydantic models). Any change to the IPC must update the Pydantic model first,
then sync the Zod schema, and re-run the symmetry test.

---

## 8. Network Boundary (Plan §18.5)

- WS server binds **`127.0.0.1` only** — never `0.0.0.0`.
- Default port: `7842`. No authentication — loopback is the security boundary.
- Verifiable via `netstat -an | findstr 7842` — must show `127.0.0.1:7842`.

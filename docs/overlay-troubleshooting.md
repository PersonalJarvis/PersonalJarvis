# Overlay Troubleshooting

> Common issues with the OS-Level Overlay (edge-glow + mascot) and how to fix
> them. References to the master plan use §-numbers from
> `OS-Level/OS-LEVEL_PLAN.md`.

---

## 1. Overlay starts not at all

**Symptoms:** No glow, no mascot, no overlay process visible.

**Checks (in order):**

1. **`[overlay].enabled = true` in `jarvis.toml`?**
   - Open `jarvis.toml`, search for the `[overlay]` section.
   - If `enabled = false` or the section is missing, add/set `enabled = true`
     and restart Jarvis.

2. **`pythonw.exe` for the overlay-subprocess running?**
   - Open Task Manager → Details tab → look for a `pythonw.exe` whose
     command-line contains `overlay`. If absent, the supervisor never spawned
     it.
   - Check `logs/jarvis.log` for `OverlaySupervisor` entries — a spawn failure
     is logged with stack-trace.

3. **Tray icon visible?**
   - The Jarvis tray-icon should be present. If yes but no overlay → the
     overlay is opt-in via the toml flag (step 1).

4. **Log output:**
   - `logs/jarvis.log` filtered by `overlay` — Spawn-failures, schema-validation
     errors, port-bind issues are all there.

5. **Port 7842 already in use?**
   - `netstat -an | findstr 7842` — if another process holds it, change
     `[overlay].port` in `jarvis.toml`.

**Source files:** `OS-Level/src/overlay/supervisor.py`,
`OS-Level/src/overlay/process.py`.

---

## 2. Glow does not appear when Hauptjarvis is typing

**Symptoms:** Mascot reacts, but the yellow edge-glow stays dark when
keystrokes go through.

**Checks:**

1. **Phase-9.8-Hook present in the production path?**
   - The `OverlayBridge.action(...)` decorator/context-manager must wrap the
     keystroke call (`pyautogui.typewrite`, `pywinauto.SendKeys`, etc.).
   - Grep the production action site for `OverlayBridge` — if the bridge is
     only wired in tests, glow will silently no-op.

2. **`JARVIS_DEPTH == 0`?**
   - In Jarvis-Agents (`JARVIS_DEPTH > 0` env var, see Plan §8.7), the
     `OverlayBridge` is a **no-op stub by design**. Only the top-level
     Hauptjarvis triggers the glow.
   - Verify with `echo %JARVIS_DEPTH%` (cmd) or `$env:JARVIS_DEPTH`
     (PowerShell) inside the relevant subprocess.

3. **Renderer in `typing` state?**
   - Open the overlay window with the debug-flag (Plan §22.3 dev-mode):
     append `?debug=1` to the WebView URL. The root element should carry
     `data-state="typing"` while keystrokes fire.
   - If the attribute stays `idle`, the WS-event never reached the renderer —
     check `OverlayBridge` → WS server → renderer trace in
     `logs/jarvis.log`.

4. **State coalescing collapsing rapid changes?**
   - AD-17 (16 ms window) drops same-type duplicates. Two `typing` enters
     within 16 ms collapse to one. Should not cause "no glow at all" but can
     cause a missed *re-enter* visual.

**Source files:** `OS-Level/src/overlay/bridge.py`,
`OS-Level/overlay-ui/src/components/EdgeGlow.tsx`.

---

## 3. Cursor trail missing

**Symptoms:** Mascot and glow work, but the cursor doesn't leave a fading
trail.

**Checks:**

1. **`[overlay].cursor_trail_enabled = true`?**
   - Off by default in some profiles; enable in `jarvis.toml`.

2. **Shared-Memory block accessible?**
   - The cursor stream uses a `multiprocessing.shared_memory` block named
     `jarvis-cursor-{8 hex chars}` (Plan §11). Some Antivirus / EDR products
     block SHM-creation between unrelated processes.
   - Check `heartbeat` payload (`shm_attached`) — if `false`, SHM is blocked
     and the system has fallen back to the WS `cursor` channel (lower
     resolution).
   - Whitelist `pythonw.exe` and `OS-Level/src/overlay/process.py` in the AV
     product, or grant SHM-create rights via group policy.

3. **DPI scaling on the source monitor?**
   - Coordinates are physical pixels (Plan §11.2). On a 200%-scaled monitor,
     trail points should appear at 2× the logical position; if they appear
     halved, a DPI-aware bug is in play — file an issue with monitor layout.

**Source files:** `OS-Level/src/overlay/cursor_shm.py`,
`OS-Level/overlay-ui/src/components/CursorTrail.tsx`.

---

## 4. Mascot disappears after monitor change

**Symptoms:** After unplugging a monitor, plugging in a new one, or
re-arranging displays in Windows settings, the mascot vanishes.

**Explanation (Plan §13.4):** Mascot position is persisted as
`(szDevice, x_relative, y_relative)`. If the saved monitor's `szDevice` is no
longer present at startup, a **deterministic fallback** kicks in:

1. Pick the **primary monitor** via
   `MonitorFromWindow(hwnd_desktop, MONITOR_DEFAULTTOPRIMARY)`.
2. Place mascot at the **default position** for that monitor:
   `(rcWork.left + 200, rcWork.top + 80)`.
3. Log `mascot.position_recovered = "primary_fallback"` to telemetry.
4. **The user must drag the mascot to the new desired position** — the old
   relative offset is intentionally discarded because it was anchored to a
   now-missing virtual-desktop region.

To stop this happening on every re-dock: drag the mascot to the new spot once
after re-docking; the new `szDevice` + position is persisted on `mouseRelease`.

**Source files:** `OS-Level/src/overlay/mascot_position.py`.

---

## 5. Hauptjarvis crashes — does the overlay survive?

**No.** By design (Plan AD-9).

The overlay subprocess is assigned to a **Win32 Job Object** with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The Hauptjarvis process holds the
non-inheritable Job-Handle. If Hauptjarvis dies — even via a hard kill —
Windows closes the Job-Handle, which kills every member of the Job, including
the overlay process. **Cleanup is guaranteed within ≤ 1 s** (Plan §6.4).

If you do see an orphaned overlay process after a crash:

1. Check the process tree (`pslist -t` or Process Explorer).
2. Check the Job-Object assignment via Process Explorer → process properties →
   Job tab. If the process is **not** in the expected Job, the assignment
   failed at spawn-time and AD-9 was not honored — file a bug with the
   spawning sequence in `logs/jarvis.log`.

**Source files:** `OS-Level/src/overlay/job_object.py`,
`OS-Level/src/overlay/supervisor.py`.

---

## 6. Performance issues (CPU / GPU / Battery)

**Symptoms:** Fan ramps when overlay is active, battery drains faster than
expected, FPS counter in heartbeat reports < target.

**Explanation (Plan §17.3):** A Throttler reduces FPS automatically:

- **Battery vs AC:** On battery (`GetSystemPowerStatus`), all FPS targets are
  halved (60 → 30, 30 → 15) and cosmetic particle effects are skipped.
- **Idle Detection:** 30 s without events → mascot drops to 1 fps, edge-glow
  frozen. 5 min without events → WebView `IsVisible = false` (Chromium
  throttles hidden views).
- **Fullscreen Detection:** Fullscreen app → overlay enters `hidden` state.
- **Adaptive on budget breach:** If heartbeat reports CPU > 5% for 3
  consecutive seconds, FPS is dropped 50% and a warning is logged. After 30 s
  still over budget, the overlay forces `hidden` and surfaces a tray-warning.

To verify the throttler is active: read
`heartbeat.fps_actual` vs `heartbeat.fps_target` in the log. A `fps_target` of
30 on AC indicates the throttler kicked in and the source state is not
glow-active.

**Source files:** `OS-Level/src/overlay/throttler.py`.

---

## 7. OBS / Teams / Zoom captures the overlay

**Symptoms:** The overlay shows up in screen-shares or recordings, even though
it should be invisible to capture by default.

**Explanation (Plan §18.1):** The overlay calls
`SetWindowDisplayAffinity(hWnd, WDA_EXCLUDEFROMCAPTURE)` on every overlay HWND
on every monitor (mascot included). This is **default-on**.

**If the overlay still appears in capture:**

1. **Win11 quirk (Plan §12.4):** `WDA_EXCLUDEFROMCAPTURE` was historically
   buggy on certain Win11 builds combined with certain capture drivers (older
   OBS GameCapture, some Teams versions). Update to current OBS / Teams /
   Zoom; the issue was largely fixed mid-2023 but a few capture-paths still
   slip.
2. **`reapply` on display change:** The affinity flag must be re-applied on
   `WM_DPICHANGED`, on `screenAdded`, and after each `show()`. If the overlay
   was hidden and re-shown without re-applying, capture-bypass is lost. Check
   `logs/jarvis.log` for `display_affinity_reapplied`.

**Tutorial-recording opt-out:** If you **want** the overlay visible in a
recording (showing off the feature in a tutorial video), set:

```toml
[overlay]
hide_from_capture = false
```

This deactivates the capture-bypass — the overlay becomes intentionally
visible in screen-shares. **Restore to `true` after recording** for normal
privacy.

**Source files:** `OS-Level/src/overlay/window_affinity.py`.

---

## 8. Jarvis-Agent triggers the overlay

**Symptoms:** Spawned Jarvis-Agent or Worker triggers the glow, even though only
Hauptjarvis is supposed to.

**Explanation (Plan §8.7):** In Jarvis-Agents (`JARVIS_DEPTH > 0`),
`OverlayBridge` is a **no-op stub by design**. If a Jarvis-Agent's actions
trigger the glow, the env-var inheritance broke.

**Checks:**

1. **Confirm `JARVIS_DEPTH > 0` is set in the Jarvis-Agent subprocess:**
   - Inspect the spawn command (in the Worker code or the Jarvis-Agent
     supervisor).
   - The parent must inject `env={**os.environ, "JARVIS_DEPTH": str(depth+1)}`
     when spawning the child.
2. **Verify at runtime:** add a debug-print in the Jarvis-Agent's `OverlayBridge`
   instantiation; it should hit the no-op-stub branch, not the real-bridge
   branch.
3. **Test:** `pytest OS-Level/tests/test_subagent_noop.py` (sets
   `JARVIS_DEPTH=2`, instantiates `OverlayBridge`, asserts all `emit_*` are
   no-ops). If this test passes but the integration breaks, the bug is in
   spawn-env-injection, not in the bridge itself.

**Source files:** `OS-Level/src/overlay/bridge.py` (no-op-stub branch),
spawn sites in the Phase-5 Jarvis-Agent manager.

---

## 9. Multi-Monitor smoke test (per release, manual — Plan §23.5)

For each release on hardware with ≥ 2 monitors:

1. **Single-monitor:** smoke test all 8 states.
2. **Two monitors, same DPI:** verify overlay on primary only by default;
   opt-in to `all_monitors = true`, verify glow on both.
3. **Two monitors, different DPI (100% + 200%):** verify per-monitor
   DPI-correct rendering — the 200% monitor should not show a half-sized
   glow.
4. **Hotplug — unplug:** with the overlay running, unplug the secondary;
   verify no crash, mascot recovers (see §4 above).
5. **Hotplug — plug in:** with the overlay running, plug in the secondary;
   if `all_monitors = true`, verify glow appears on the new monitor within
   one frame.

---

## 10. Where to look in the source

| Concern | Module |
|---|---|
| State enum + transitions | `OS-Level/src/overlay/state.py` |
| IPC envelope + payload schemas | `OS-Level/src/overlay/schema.py` |
| WS server + Named-Pipe fallback | `OS-Level/src/overlay/ipc_server.py` |
| Cursor SHM block | `OS-Level/src/overlay/cursor_shm.py` |
| Process spawn + Job-Object | `OS-Level/src/overlay/supervisor.py`, `job_object.py` |
| OverlayBridge (Hauptjarvis API) | `OS-Level/src/overlay/bridge.py` |
| Renderer (Glow + Mascot) | `OS-Level/overlay-ui/src/` |
| Throttling + idle-detection | `OS-Level/src/overlay/throttler.py` |
| Mascot position persistence | `OS-Level/src/overlay/mascot_position.py` |
| Capture-bypass (`SetWindowDisplayAffinity`) | `OS-Level/src/overlay/window_affinity.py` |

For protocol-level questions, see `docs/overlay-ipc-protocol.md`. For the
state model, see `docs/overlay-state-machine.md`.

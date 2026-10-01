# Background agent service

Routines and chat channels keep running after the desktop app is closed.

## What the user sees

- Closing the app (the window's X, or **Quit** in the tray) with something to
  keep — an armed routine, or a running Telegram / Discord channel — starts a
  small windowless service. A tray icon ("agents keep running in the
  background") shows it is there, with **Open** and **Stop background
  agents**.
- Opening the app again takes over from the service in a couple of seconds.
  Nothing has to be stopped by hand.
- With nothing to keep, closing the app stops everything, as before.
- **Settings → App settings → Keep agents running after closing** turns the
  hand-off off. The same card shows what a quit would keep right now, and
  offers **At login, start only the background agents** (needs "Launch app at
  login"), which starts the service instead of the window after a reboot.

## How it works

The service is the existing headless backend
(`python -m jarvis.ui.web.launcher --background-service`, which implies
`--headless`) with no window, no microphone, no wake word and no overlay
(`JARVIS_VOICE=0`). It runs the same task scheduler, channel stack, agent
society runtime and Agentic IDE re-attach as a headless install.

Exactly one of desktop app and service runs at a time. Both go through the
single-instance lock (`data/jarvis.lock`):

1. **Hand-off on quit** (`DesktopApp._hand_off_to_background_service` →
   `jarvis.core.background_service.hand_off_on_quit`). On a real quit, if
   `[background] keep_agents_running` is on and `work_from_state` finds an
   armed routine, a run in flight or a started channel, the desktop spawns the
   service detached (`--after-pid <desktop pid>`). A restart, an update, a
   declined Terms gate or a failed backend skip it. Only the default instance
   hands off; a dev instance stops with its window.
2. **Service boot** (`launcher._prepare_background_service`). It waits for the
   desktop pid to exit, steps aside when a desktop launch is already waiting,
   and takes the lock without evicting anyone, BEFORE it binds the port.
   It writes `user_data_dir()/background/service{suffix}.json` and the usual
   instance sidecar.
3. **Hand-back on launch** (`launcher._take_over_from_background_service` →
   `take_over_from_service`). A desktop launch that finds the service drops
   `handover{suffix}.request`; the service polls that file every 0.5 s, shuts
   down and releases the lock; the desktop takes it and boots normally. A
   service that does not let go within 45 s is stopped. A file carries the
   request (not HTTP) because it works while the service is still booting
   and needs no credentials.
4. **Self-exit.** The service checks every 5 minutes that it still has work
   and exits after two empty checks. Its teardown is capped at 30 s, then the
   process ends hard — an invisible process holding the lock is the one thing
   it must never become.

Log: `<data dir>/jarvis_background.log`, next to `jarvis_desktop.log`.

## Limits

- Spoken routines (`speak` actions) fail in the service: there is no voice
  without the app. Agent routines and chat channels are unaffected.
- A turn that is running in the desktop at the moment of quitting is
  interrupted, as before; the service starts fresh. The Agentic IDE's coding
  agents are unaffected — they live in the PTY host.
- macOS draws no menu-bar icon for the service (status items need the main
  thread, which the service's event loop owns). Open the app to stop it, or
  turn the setting off.

## Verifying

- Unit: `tests/unit/core/test_background_service.py`.
- Live round trip on an isolated dev instance (`JARVIS_DATA_DIR` and
  `LOCALAPPDATA` pointed at a temp folder, `--port` set): start the service
  with `--after-pid` of a short-lived process, confirm it boots only after
  that process exits, fire an `after_delay` task through `POST /api/tasks`,
  then call `launcher._take_over_from_background_service()` and confirm the
  lock is returned, the service exits with code 0 and the marker is gone.
  Measured 2026-10-01 on Windows 11: hand-back 2.4 s, process gone at 2.4 s
  (in-process spawn) and 11.7 s (detached spawn, 5 s exit-hook flush).

# Desktop Black Screen Incident

Date: 2026-04-29
Status: Fixed
Area: Desktop app, pywebview/WebView2, React frontend

## Symptom

After clicking through multiple sections in the Jarvis desktop app, the window
turned into a black screen. Restarting the desktop app did not reliably recover
the UI.

## Root Cause

The backend stayed alive, but the frontend could render itself into an empty
state. Two risks combined:

1. A React runtime exception inside a section could unmount the whole app,
   leaving only the dark WebView background visible.
2. `TerminalView` initialized xterm while hidden at app startup. WebView2 can be
   fragile when complex canvas/terminal components initialize in a zero-size or
   hidden container.

There was also no explicit no-cache policy for `index.html`, so WebView2 could
keep stale HTML after frontend rebuilds.

## Fix

- `jarvis/ui/web/frontend/src/components/ViewErrorBoundary.tsx`
  - Added a reusable React error boundary.
  - A crashed section now shows a recovery panel instead of blacking out the
    whole app.

- `jarvis/ui/web/frontend/src/components/layout/MainView.tsx`
  - Wrapped sections in `ViewErrorBoundary`.
  - Changed `TerminalView` to lazy-then-persistent mounting: it is created only
    after the Terminal section is opened for the first time, then kept mounted
    to preserve PTY/xterm state.

- `jarvis/ui/web/frontend/src/main.tsx`
  - Wrapped the whole app in a root error boundary as a final safety net.

- `jarvis/ui/web/server.py`
  - `index.html` is now served with:
    `Cache-Control: no-store, max-age=0`
  - This prevents WebView2 from reusing stale app shell HTML after rebuilds.

## Regression Rules

Do not remove these protections:

1. Every top-level view must remain behind an error boundary.
2. Complex renderer components such as xterm, canvas, WebGL, or graph editors
   must not be initialized while hidden unless the component is proven safe in a
   zero-size container.
3. `index.html` must stay no-cache/no-store. Asset files may remain cacheable
   because Vite fingerprints them.
4. If a new section can crash independently, it must fail as a visible panel,
   not as a blank app window.

## Verification

Run:

```bash
cd jarvis/ui/web/frontend
npm run build
```

Run:

```bash
python -m compileall jarvis/ui/web/server.py
```

Optional server check:

```bash
python - <<'PY'
from fastapi.testclient import TestClient
from jarvis.core.config import load_config
from jarvis.ui.web.server import WebServer

client = TestClient(WebServer(load_config()).app)
r = client.get("/")
assert r.status_code == 200
assert r.headers["cache-control"] == "no-store, max-age=0"
print("desktop shell cache policy OK")
PY
```

## User Recovery

If the black screen appears again:

1. Restart Jarvis completely from the tray menu.
2. If the UI still stays black, open `data/jarvis_desktop.log`.
3. Search for `Jarvis view crashed` in the WebView console if debug mode is on.
4. Check the most recently opened section first, especially Terminal or any
   canvas-heavy view.

# Phase 1a — Manual Verification Checklist

This checklist verifies all acceptance criteria of Phase 1a (Web UI as
primary channel, desktop shell via pywebview, WebSocket event stream).

## Prerequisites

1. Install Python dependencies:
   ```bash
   pip install -e . --no-deps
   pip install -r requirements.txt
   ```
2. Frontend Node deps:
   ```bash
   cd jarvis/ui/web/frontend
   npm install
   ```
3. Build the frontend (production static build):
   ```bash
   python scripts/build_frontend.py
   ```

## Acceptance Tests (manual)

### 1. Backend Headless Mode

```bash
python -m jarvis.ui.web.launcher --headless
```

Expectation: log line `Jarvis-Backend läuft auf http://127.0.0.1:47821`

- `curl http://127.0.0.1:47821/api/health` → `{"ok":true,"version":"0.1.0"}`
- `curl http://127.0.0.1:47821/api/plugins` → all 7 plugin groups, incl. `jarvis.channel`
- `Ctrl+C` → clean shutdown, no tracebacks

### 2. Desktop Window with Static Build

Beforehand: `python scripts/build_frontend.py`

```bash
python -m jarvis.ui.web.launcher
```

Expectation:
- Native window (1280x800)
- React UI appears
- WebSocket indicator green (connected)
- 3-column layout (Threads | Chat | Admin)

### 3. Dev Mode with Vite HMR

- Terminal A: `cd jarvis/ui/web/frontend && npm run dev`
- Terminal B: `python -m jarvis.ui.web.launcher --dev`

Expectation: window shows the Vite dev UI, HMR works (edit in .tsx immediately visible).

### 4. Single-Instance Lock

Start `python -m jarvis.ui.web.launcher` twice in a row.
The second invocation must abort with exit code `3` and the message `Jarvis läuft bereits.` (Jarvis is already running.)

### 5. WebSocket Event Echo in the Browser

Window open → Admin panel tab "Debug" → click the "Emit Test-Event" button →
EventTimeline shows the new event in under 1 second.

### 6. Plugin Registry in the Admin Panel

Admin panel tab "Plugins" shows all 7 plugin groups including
`jarvis.channel` with the `web` entry.

### 7. Dark-Mode Toggle

Admin panel → Theme → Toggle. The UI switches immediately. After a reload the
selection is preserved (localStorage).

### 8. Automated Tests Green

```bash
pytest tests/contract/test_channel_adapter_contract.py -v
pytest tests/integration/test_phase1a_smoke.py -v
```

Both test modules must be green (or cleanly `SKIPPED` when optional
deps / plugins are not installed; no `FAILED` or `ERROR`).

## Known Open Points

- `python -m jarvis` (without the submodule path) still starts the old tray path
  from Phase 0. The integration into `jarvis/__main__.py` happens in a
  later merge turn, as soon as Phase 1b (Voice) is finished in parallel.
- The pywebview window start cannot be automated headlessly —
  points 2 and 3 of the checklist must be verified manually.

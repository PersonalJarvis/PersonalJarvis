# Jarvis Frontend (Phase 1a)

Vite + React 18 + TypeScript + TailwindCSS + shadcn/ui.

## Quickstart

```bash
cd jarvis/ui/web/frontend
npm install
npm run dev          # dev server on http://localhost:5173 (proxies /api and /ws to 127.0.0.1:47821)
npm run build        # emits ../dist/ (served by FastAPI as StaticFiles)
npm run test         # vitest
```

## Layout

3-column shell (`src/App.tsx`):

- **Sidebar** (left, `w-64`) — conversation list placeholder.
- **MainView** (center) — `VoiceIndicator` + `EventTimeline` + `ChatInput`.
- **AdminPanel** (right, `w-80`) — Providers / Plugins / Theme / Debug tabs.

## Notes / Limitations

- shadcn/ui primitives under `src/components/ui/` are hand-copied from the shadcn source, not CLI-generated (no Node toolchain used by this agent).
- `src/schema/ws.ts` is manual Zod. `scripts/export_ws_schema.py` can later auto-export JSON-Schema once `jarvis/ui/web/schema.py` is filled by Agent-2.
- WS auth: reads token from `window.__JARVIS_TOKEN` and passes it as `?token=...` query param since browsers can't set WS headers.

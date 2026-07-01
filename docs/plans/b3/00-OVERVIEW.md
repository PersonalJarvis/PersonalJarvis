# B3 — Desktop Wiki View: Overview

**Goal.** Make the existing on-disk Obsidian vault (`wiki/obsidian-vault/`) visible inside the Jarvis Desktop App. Replace the legacy "Notizen" sidebar tab (`MemoryView.tsx`, backed by the obsolete `data/core_memory.json` flat store) with a hybrid Wiki view: graph-first landing, file tree, rendered Markdown pages with clickable `[[wikilinks]]`, FTS5 search, plus a per-page "Open in Obsidian" button that hands editing off to the real Obsidian app via the `obsidian://` URL scheme. Read-only — writes still happen exclusively through the `WikiCurator` (B1) or the user's manual edits in Obsidian.

**This document is the single source of truth for B3.** All four parallel coding agents read this first, then their own `AGENT-X-*.md` briefing.

Mockup reference (visual contract for the final UI): `<USER_HOME>\Desktop\b3-wiki-view-mockup.html`.

---

## 1. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  Jarvis Desktop App  (pywebview + FastAPI + React)                  │
│                                                                     │
│  ┌──────────────────────────────┐                                   │
│  │ React: WikiView.tsx          │ ← Agent B                         │
│  │ ├─ TreeSidebar               │                                   │
│  │ ├─ PageRenderer (markdown)   │                                   │
│  │ ├─ WikiGraph (force-graph)   │ ← Agent C                         │
│  │ ├─ SearchBar                 │ ← Agent C                         │
│  │ └─ ObsidianButton (per page) │ ← Agent D                         │
│  └────────────┬─────────────────┘                                   │
│               │ HTTP + WebSocket                                    │
│  ┌────────────▼─────────────────┐                                   │
│  │ FastAPI: wiki_routes.py      │ ← Agent A                         │
│  │ GET  /api/wiki/tree          │                                   │
│  │ GET  /api/wiki/page/{slug}   │                                   │
│  │ GET  /api/wiki/graph         │                                   │
│  │ GET  /api/wiki/backlinks/{s} │                                   │
│  │ GET  /api/wiki/search?q=…    │                                   │
│  │ WS   /api/wiki/live          │ ← Agent D                         │
│  └────────────┬─────────────────┘                                   │
│               │ reuses                                              │
│  ┌────────────▼─────────────────┐                                   │
│  │ existing B1/B5 infrastructure │                                   │
│  │  PageRepository, VaultIndex   │                                   │
│  │  VaultSearch (FTS5)           │                                   │
│  │  WikiCurator                  │                                   │
│  └────────────┬─────────────────┘                                   │
│               │ reads / writes                                      │
│  ┌────────────▼─────────────────┐                                   │
│  │ wiki/obsidian-vault/ (disk)  │ ◀── also watched by Agent D for   │
│  │ entities/ concepts/ projects/│     live-reload events            │
│  └──────────────────────────────┘                                   │
└────────────────────────────────────────────────────────────────────┘
```

B3 is a **pure view layer**. It does not author wiki content. It does not write to disk. It does not call the brain. All intelligence lives in the existing B1/B5 components.

---

## 2. The four agents

| Code | Name | Owns | One-sentence mission |
|------|------|------|----------------------|
| **A** | `backend-api` | `jarvis/ui/web/wiki_routes.py` (new), `jarvis/ui/web/server.py` registration | Expose the on-disk vault as a JSON API: tree, page, graph, backlinks, search. Wraps existing `PageRepository` + `VaultIndex` + `VaultSearch`. No business logic. |
| **B** | `react-tab` | `jarvis/ui/web/frontend/src/views/WikiView.tsx` (new), `Sidebar.tsx` label change, `MemoryView.tsx` deletion | Build the React tab that replaces "Notizen". Tree + Page-render + frontmatter pills + crumb + backlinks panel. No graph, no search, no live-reload — those come from C and D. |
| **C** | `graph-and-search` | `jarvis/ui/web/frontend/src/components/wiki/WikiGraph.tsx` (new), `WikiSearch.tsx` (new) | Force-graph visualisation of `[[wikilinks]]` + Ctrl-K full-text search bar. Plugs into Agent B's tab as a default landing view. |
| **D** | `live-reload-and-obsidian` | `jarvis/memory/wiki/watcher.py` (new), `jarvis/ui/web/frontend/src/hooks/useWikiLive.ts` (new), per-page "Open in Obsidian" button wiring | Watchdog on `wiki/obsidian-vault/`, WebSocket push on file change, React Query invalidation hook. Plus `obsidian://` URL-scheme button with graceful fallback when Obsidian is not installed. |

Agents work in **parallel** from `impl/b3-base`. The Wave-2 merge step is owned by the review agent (orchestrator), not by the four agents.

---

## 3. Shared interface contracts (binding for all agents)

These shapes are **non-negotiable**. Any agent that deviates breaks the merge.

### 3.1 REST contract (Agent A produces, B/C/D consume)

```python
# GET /api/wiki/tree
{
  "ok": true,
  "vault_root": "wiki/obsidian-vault",
  "folders": [
    {
      "name": "entities",
      "kind": "entity",
      "count": 2,
      "files": [
        {"slug": "alex",  "title": "Alex",  "mtime": 1715600000.0, "size": 412},
        {"slug": "sam", "title": "Sam", "mtime": 1715590000.0, "size": 289}
      ]
    },
    {"name": "concepts", "kind": "concept", "count": 0, "files": []},
    {"name": "projects", "kind": "project", "count": 1, "files": [...]},
    {"name": "sessions", "kind": "session", "count": 0, "files": []}
  ],
  "stats": {"total_pages": 3, "total_links": 8, "last_curator_run": "2026-05-13T13:59:00"}
}

# GET /api/wiki/page/{slug}    e.g. /api/wiki/page/sam
{
  "ok": true,
  "slug": "sam",
  "kind": "entity",
  "title": "Sam",
  "path": "entities/sam.md",
  "frontmatter": {
    "type": "entity", "entity_kind": "person", "slug": "sam",
    "aliases": [], "created": "2026-05-13", "updated": "2026-05-13"
  },
  "body_md": "# Sam\n\n## Summary\nSam is a person born in 1990.\n\n## Facts\n- Born in 1990.\n…",
  "wikilinks": ["alex"],
  "stats": {"words": 17, "bytes": 289, "mtime": 1715590000.0}
}

# GET /api/wiki/graph
{
  "ok": true,
  "nodes": [
    {"id": "sam",           "kind": "entity",  "title": "Sam"},
    {"id": "alex",            "kind": "entity",  "title": "Alex"},
    {"id": "pixel-art-editor", "kind": "project", "title": "Pixel Art Editor"}
  ],
  "edges": [
    {"source": "alex", "target": "sam",           "context": "Father is [[sam]]"},
    {"source": "alex", "target": "pixel-art-editor", "context": "Working on [[pixel-art-editor]]"}
  ],
  "broken": []   // list of {source, target} where target page does not exist
}

# GET /api/wiki/backlinks/{slug}
{
  "ok": true,
  "slug": "sam",
  "backlinks": [
    {"slug": "alex", "title": "Alex", "snippet": "...father is [[sam]] — born 1990..."}
  ]
}

# GET /api/wiki/search?q=pizza&k=5
{
  "ok": true,
  "query": "pizza",
  "hits": [
    {"slug": "alex", "title": "Alex", "path": "entities/alex.md",
     "snippet": "...Favorite food is Pizza (source: voice-fact:...)...",
     "score": 0.92}
  ]
}

# Error envelope (any endpoint, any failure)
{"ok": false, "error": "<short message>"}
```

All endpoints return HTTP 200 even on logical errors; clients read `ok`. HTTP 404 is reserved for unknown routes. HTTP 500 only for unhandled exceptions.

### 3.2 WebSocket contract (Agent D produces, B consumes)

```
WS /api/wiki/live

Server → Client messages (JSON, one per file event, debounced 500 ms):
{"type": "page_changed", "slug": "sam", "path": "entities/sam.md", "kind": "modified"}
{"type": "page_changed", "slug": "new-thing", "path": "entities/new-thing.md", "kind": "created"}
{"type": "page_changed", "slug": "old", "path": "entities/old.md", "kind": "deleted"}

Client → Server messages: none. (Subscribe-only stream.)
```

The client invalidates React Query caches for `tree`, `page/{slug}`, `graph`, `backlinks/{slug}` whenever any message arrives. Coalescing is the client's job; the server just forwards events.

### 3.3 React component prop contracts

```typescript
// WikiView (Agent B) — top-level
export function WikiView(): JSX.Element;

// TreeSidebar (Agent B)
interface TreeSidebarProps {
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
}

// PageRenderer (Agent B)
interface PageRendererProps {
  slug: string;
  onWikilinkClick: (targetSlug: string) => void;
}

// WikiGraph (Agent C)
interface WikiGraphProps {
  onNodeClick: (slug: string) => void;
  highlightSlug?: string;
}

// WikiSearch (Agent C)
interface WikiSearchProps {
  onResultClick: (slug: string) => void;
}

// ObsidianButton (Agent D)
interface ObsidianButtonProps {
  vaultRelPath: string;   // e.g. "entities/sam.md"
}

// useWikiLive (Agent D)
export function useWikiLive(): { connected: boolean; lastEventAt: number | null };
```

---

## 4. Global anti-patterns (apply to all four agents)

| # | Don't | Why |
|---|-------|-----|
| **AP-1** | Don't write to the vault from the desktop view. | Read-only is a hard architectural decision. Writes happen via `WikiCurator` or the user's Obsidian app. Two writers = the 30s concurrent-edit lock dance returns. |
| **AP-2** | Don't reimplement parsing/rendering of YAML frontmatter or `[[wikilinks]]` in the frontend. | The backend already has `PageRepository` (B1) that parses everything. Frontend consumes the parsed JSON. |
| **AP-3** | Don't hardcode the vault path. | Read from config (`cfg.memory.wiki.vault_root` or the path the existing `VaultIndex` was constructed with). Tests use temp directories. |
| **AP-4** | Don't add network calls inside the voice critical path. | B3 is a UI tab. The user can have it open or not. Never block voice on a UI-tab subscription. |
| **AP-5** | Don't mock SQLite / the file system in integration tests. | Use real temp directories. BUG-008 came back twice from mocked tests masking drift. |
| **AP-6** | Don't introduce a new EventBus instance. | Reuse the bus the server already passes via `app.state.bus`. |
| **AP-7** | Don't write user-facing strings in German inside *code* (comments, docstrings, log messages, exception messages, error responses). | Project Output Language Policy. **Exception:** rendered Markdown content comes from the vault verbatim, which is in whatever language the user dictated — that is *content*, not code. The mockup also shows German labels in the *UI strings* (button captions, section headers) — those follow the existing `MemoryView.tsx` precedent and stay German. The distinction is `console.log("Loading tree")` (English) vs `<button>In Obsidian öffnen</button>` (German UI string, allowed). <!-- i18n-allow: historical AP-7 exception quoting a literal German UI-string example; superseded by CLAUDE.md's current English-only rule, kept verbatim as the record of the original decision --> |
| **AP-8** | Don't catch and silently swallow exceptions in the watcher or route handlers. | Log structured (`log.warning("wiki_route_failed", route=..., error=...)`), return `{"ok": false, "error": "..."}`. Silent failures are the most common source of "the UI is empty and we don't know why" bugs. |
| **AP-9** | Don't read the vault on the asyncio event loop. | All file IO goes through `aiofiles` or `asyncio.to_thread`. Wraps `VaultIndex` if needed. The vault is small, but the principle matters. |
| **AP-10** | Don't ship a 1 MB JS bundle for the graph. | `react-force-graph-2d` is ~120 KB minified. Use code-splitting (`React.lazy`) so the graph chunk loads only when the user is on the Wiki tab. |

---

## 5. Operational rules (apply to all four agents)

### 5.1 Worktree

Each agent works in **its own git worktree**, branched off `impl/b3-base`:

```
<USER_HOME>\Desktop\jarvis-b3-agent-A\
<USER_HOME>\Desktop\jarvis-b3-agent-B\
<USER_HOME>\Desktop\jarvis-b3-agent-C\
<USER_HOME>\Desktop\jarvis-b3-agent-D\
```

Worktree branch names: `impl/b3-agent-A`, `…B`, `…C`, `…D`.

The agent **must** run `pip install -e . --no-deps` in its worktree before any other action (editable-install pin trap — see BUG-006, BUG-014 episode 2).

For frontend agents (B, C, D-frontend): `cd jarvis/ui/web/frontend && npm install` before `npm run build` so the package-lock matches.

### 5.2 Commits

The agent commits **once** at the very end of its session:

```
feat(wiki-view/b3/<X>): <one-line summary>

<body — bullet list of what was added, max ~12 lines>
```

Example: `feat(wiki-view/b3/a): add wiki_routes FastAPI module`. No intermediate commits. No `git push` — the review agent merges.

### 5.3 Plausible-assumption fallback

If something is ambiguous: pick the most plausible interpretation, document it in the closing report under `## Assumptions made`. **Exception:** if the ambiguity touches a §3 shared interface, **stop and report** instead of guessing.

### 5.4 Pre-flight & post-flight test gate

```powershell
# Pre-flight
python -m pytest tests/unit/ -q > pre-flight.log

# Post-flight
python -m pytest tests/unit/ -q > post-flight.log
```

If `post-flight.log` shows new failures that are not in `pre-flight.log`, the agent does not commit. Pre-existing failures on `impl/b3-base` are documented in §6.

Frontend agents additionally run:

```powershell
cd jarvis/ui/web/frontend
npm run build           # must succeed
npx tsc --noEmit -p .   # zero TypeScript errors
```

### 5.5 Closing report

Free text but the **final line must be exactly one of**:

```
Goal fulfilled: yes — Reason: <one sentence>
Goal fulfilled: no — Reason: <one sentence>
```

---

## 6. Baseline branch setup (review agent does this before spawning)

```powershell
git checkout main
git checkout -b impl/b3-base
# baseline pre-flight test set captured below
python -m pytest tests/unit/ -q > pre-flight-baseline.log
```

Pre-existing test failures on `impl/b3-base` at the moment of branching will be enumerated here once the review agent has run the baseline. They are **not** an agent's problem.

---

## 7. Wave 2 — final integration (review agent owns this)

After all four agents commit and report, the review agent:

1. Reads each report, verifies the mandatory closing line.
2. Reviews each diff against its briefing.
3. Merges the four branches into `impl/b3-base` in order: **A → B → C → D**
   - A first because B/C/D consume its API.
   - B before C because C plugs into B's tab.
   - C before D because D's live-reload needs the graph to demo invalidation.
4. Resolves any merge conflicts in-place.
5. Runs the integration smoke test:
   ```powershell
   python -m pytest tests/integration/ui/wiki/ -q
   cd jarvis/ui/web/frontend && npm run build && npx tsc --noEmit -p .
   ```
6. Runs the live walk-through:
   - Launch desktop app: `Start-Process pythonw -ArgumentList "-m","jarvis.ui.web.launcher"`
   - Click sidebar tab where "Notizen" used to be — sees the new Wiki view.
   - Lands on the graph view, sees 3 nodes (sam, alex, pixel-art-editor).
   - Clicks the `sam` node → page loads, frontmatter pills visible, body rendered.
   - Clicks the `[[alex]]` wikilink in the body → navigates to alex.md.
   - Clicks "In Obsidian öffnen" — either Obsidian launches the file (if installed) or a toast says "Obsidian nicht verfügbar — Datei: entities/alex.md". <!-- i18n-allow -->
   - Triggers a live test: manually adds a file `entities/test-live.md` via the terminal. Within ~1 s the tree updates, the new file appears, the graph re-fetches.
   - Voice command: "Hey Jarvis, schreib in dein Wiki: meine Lieblingsfarbe ist Blau" — wait ~10 s — the Wiki tab auto-refreshes, alex.md shows the new fact.

If the walk-through passes, B3 is done.

---

## 8. Definition of Done for B3 as a whole

- All four agent branches merged into `impl/b3-base` without breaking the §6 pre-flight test set.
- New integration tests under `tests/integration/ui/wiki/` are green.
- The §7 walk-through succeeds end-to-end on a live Jarvis instance.
- `MemoryView.tsx` is deleted; the sidebar slot points to `WikiView.tsx`.
- `docs/plans/b3/00-OVERVIEW.md` (this file) gets a final section `## Outcome` appended.
- The dashboard `jarvis-status-dashboard.html` gets B3 flipped to "done" and B6 marked "next".

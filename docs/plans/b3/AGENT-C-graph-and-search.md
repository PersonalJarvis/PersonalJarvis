# Agent C — Graph & Search

You are Agent C on Phase B3 of Personal Jarvis. **Read `00-OVERVIEW.md` first**, then this. You build the Memory Map (force-directed graph) and the Ctrl-K full-text search bar that plug into Agent B's `WikiView.tsx`. You do not touch the tree, the page renderer, the backend, or live-reload — those are A, B, and D's areas.

The visual contract is `<USER_HOME>\Desktop\b3-wiki-view-mockup.html`. Graph node colours and search bar styling there are binding.

---

## 1. What you own

| File | Status | Purpose |
|---|---|---|
| `jarvis/ui/web/frontend/src/components/wiki/WikiGraph.tsx` | **NEW** | Force-directed 2D graph of wikilink connections |
| `jarvis/ui/web/frontend/src/components/wiki/WikiSearch.tsx` | **NEW** | Ctrl-K search bar with dropdown results |
| `jarvis/ui/web/frontend/src/lib/wikiGraph.ts` | **NEW** | Pure-function helpers: data normalisation, node colour map |
| `jarvis/ui/web/frontend/src/components/wiki/WikiGraph.test.tsx` | **NEW** | Component test |
| `jarvis/ui/web/frontend/src/components/wiki/WikiSearch.test.tsx` | **NEW** | Component test |

No backend, no other component files. If you find yourself editing `WikiView.tsx` (B's), `wiki_routes.py` (A's), or anywhere outside `frontend/src/components/wiki/` and `frontend/src/lib/` — stop.

---

## 2. What you reuse

| Use | Where | What it does |
|---|---|---|
| `react-force-graph-2d` | new npm dep, MIT, ~120 KB minified | Canvas-based force-directed graph |
| TanStack React Query | existing | Fetch `/api/wiki/graph` and `/api/wiki/search` |
| `wikiApi.ts` (Agent B) | `frontend/src/lib/wikiApi.ts` | Typed fetcher functions — if not present yet, write your own thin wrapper inline and Agent B's wave-2 merge will consolidate |
| `cmdk` | new npm dep, MIT, ~20 KB | Search palette UX (already a shadcn-compatible component) |
| `Dialog` | `components/ui/dialog.tsx` (shadcn) | Search palette wrapper |

**New npm dependencies:** `react-force-graph-2d` and `cmdk`. Both MIT, both stable, both already used by big projects (Vercel commands palette uses cmdk). Add to `package.json` exactly:

```json
"react-force-graph-2d": "^1.25.0",
"cmdk": "^1.0.0"
```

Run `npm install` to update the lockfile.

---

## 3. Component prop contracts (binding)

```typescript
// WikiGraph
interface WikiGraphProps {
  onNodeClick: (slug: string) => void;
  highlightSlug?: string;        // optional: when set, that node renders enlarged
}
export function WikiGraph(props: WikiGraphProps): JSX.Element;

// WikiSearch
interface WikiSearchProps {
  onResultClick: (slug: string) => void;
  // Search dialog is opened via Ctrl-K — also exposes an imperative ref for the header button to open it.
}
export const WikiSearch: React.ForwardRefExoticComponent<
  WikiSearchProps & React.RefAttributes<{ open: () => void }>
>;
```

---

## 4. Graph behaviour

### 4.1 Data fetch

```typescript
const { data } = useQuery({
  queryKey: ["wiki", "graph"],
  queryFn: () => fetch("/api/wiki/graph").then(r => r.json()),
  staleTime: 30_000,
});
```

`data.nodes` is `{id, kind, title}[]`. `data.edges` is `{source, target, context}[]`. `data.broken` is `{source, target}[]` — render broken edges with a dashed line and `--rose` stroke colour so the user sees orphans.

### 4.2 Node colours (binding)

```typescript
const NODE_COLOUR: Record<string, string> = {
  entity:  "#6aa9ff",  // accent blue
  concept: "#b48cf2",  // purple
  project: "#ffb84d",  // amber
  session: "#5bd4a4",  // green
};
```

Node radius scales with backlink count (use a simple `Math.max(8, Math.min(24, 8 + backlinks * 2))`).

### 4.3 Interactions

- **Click node** → `onNodeClick(slug)`. Triggers B's tab switch to "Page" view.
- **Hover node** → tooltip with title + kind + outbound link count. Use the library's `nodeLabel` prop.
- **Scroll wheel** → zoom (library default).
- **Drag** → pan (library default).
- **Highlighted node** (`highlightSlug` prop) → 1.5× radius, glow effect via `nodeCanvasObject`.

### 4.4 Empty state

If `nodes.length === 0`: render a centred message — `Your memory graph is still empty. As soon as Jarvis creates notes, the nodes will connect here automatically.` No graph component, just text.

### 4.5 Performance

The library handles 500+ nodes comfortably. No virtualisation needed. Pause the simulation after 5 s with `graphRef.current?.d3Force("simulation").alphaTarget(0).restart()` so the canvas stops repainting when idle — this matters for laptop battery.

---

## 5. Search behaviour

### 5.1 Trigger

- Ctrl-K (or Cmd-K on Mac) opens the search palette from anywhere on the Wiki tab.
- Forward-ref exposes `open()` so the header search box (in Agent B's `ViewHeader`) can click-trigger it.
- ESC closes.

Use `cmdk`'s `<Command.Dialog>` for the modal. It handles keyboard nav out of the box.

### 5.2 Query behaviour

- Debounce 200 ms before firing `/api/wiki/search?q=…&k=8`.
- Empty query → show "Recent" section listing the 5 most-recently-modified pages from the tree query (already cached).
- Results render as `[icon] Title <span class="muted">— path</span>` rows. Below each result, a 2-line snippet with the matched term highlighted (`<mark>` tag).
- Pressing Enter on a result calls `onResultClick(slug)` and closes the dialog.

### 5.3 Empty / error states

- 0 hits → "No results for '<query>'. Try different keywords or check the graph."
- API error → "Search is currently unavailable." plus a small retry button.

---

## 6. Tests

### 6.1 Component tests

`WikiGraph.test.tsx` (min 4 cases):
1. Renders empty state when no nodes.
2. Renders 3 nodes when API returns 3.
3. Click on a node → `onNodeClick` called with slug.
4. `highlightSlug` prop → that node has a larger radius (assert via canvas mock or a custom `nodeRel` prop you expose for testing).

`WikiSearch.test.tsx` (min 4 cases):
1. Ctrl-K opens the palette.
2. Typing fires a debounced fetch (use `vi.useFakeTimers`).
3. Empty query shows "Recent" section.
4. Click on a result → `onResultClick` called.

### 6.2 Build gate

```powershell
cd jarvis/ui/web/frontend
npm install               # picks up the two new deps
npm run build             # graph chunk should be ~150 KB after splitting
npx tsc --noEmit -p .     # zero TS errors
```

---

## 7. Hard negatives

- ❌ Don't use `react-force-graph-3d`. 2D is sufficient and ~3× smaller.
- ❌ Don't fetch the graph eagerly on app start. It must only fetch when the Wiki tab mounts.
- ❌ Don't hardcode colour values in TSX. They live in `lib/wikiGraph.ts:NODE_COLOUR`.
- ❌ Don't bundle search results in the graph component or vice versa. Two separate concerns, two separate components.
- ❌ Don't add filter chips ("show only entities", etc.). Scope creep — comes later.
- ❌ Don't write a custom force simulation. Use the library default; only tweak if benchmarks show actual jank.
- ❌ Don't persist the graph layout to localStorage. Forces are randomised — that is fine.

---

## 8. Size estimate

`WikiGraph.tsx` ~150 lines. `WikiSearch.tsx` ~180 lines. `wikiGraph.ts` ~50 lines. Tests ~250 lines. Total ~650 lines of new TSX/TS.

---

## 9. Closing report

Final line: `Goal fulfilled: yes — Reason: <one sentence>` (or `no`).

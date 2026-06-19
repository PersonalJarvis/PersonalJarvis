# Agent B — React Tab (`WikiView.tsx`)

You are Agent B on Phase B3 of Personal Jarvis. **Read `00-OVERVIEW.md` first**, then this. You replace the legacy `MemoryView.tsx` (`data/core_memory.json` flat memory) with a new `WikiView.tsx` backed by the on-disk Obsidian vault. You own the tree + page-render half of the tab. You do **not** own the graph (Agent C), the search bar (Agent C), the live-reload hook (Agent D), or the Obsidian button (Agent D) — your tab integrates their components but does not build them.

The visual contract is `C:\Users\Administrator\Desktop\b3-wiki-view-mockup.html`. Open it; match it.

---

## 1. What you own

| File | Status | Purpose |
|---|---|---|
| `jarvis/ui/web/frontend/src/views/WikiView.tsx` | **NEW** | Top-level tab component, three-column layout, tree on left, page in middle |
| `jarvis/ui/web/frontend/src/components/wiki/TreeSidebar.tsx` | **NEW** | Folder tree, collapsible, click selects |
| `jarvis/ui/web/frontend/src/components/wiki/PageRenderer.tsx` | **NEW** | Markdown render with `[[wikilink]]` resolution |
| `jarvis/ui/web/frontend/src/components/wiki/PageHeader.tsx` | **NEW** | Title + crumb + frontmatter pills + Obsidian-button slot (provided by D) |
| `jarvis/ui/web/frontend/src/components/wiki/BacklinksPanel.tsx` | **NEW** | Right-column backlinks card |
| `jarvis/ui/web/frontend/src/lib/wikiApi.ts` | **NEW** | Typed fetchers for Agent A's endpoints (one function per route) |
| `jarvis/ui/web/frontend/src/components/layout/Sidebar.tsx` | **MODIFY** | Change `labelKey: "nav.memory"` → `labelKey: "nav.wiki"`; icon stays `Notebook` for now |
| `jarvis/ui/web/frontend/src/components/layout/MainView.tsx` | **MODIFY** | Replace `<MemoryView />` mount with `<WikiView />` |
| `jarvis/ui/web/frontend/src/views/MemoryView.tsx` | **DELETE** | Legacy view, obsolete |

No backend changes. No CSS framework changes (existing Tailwind config is fine). No new npm dependencies unless documented under §3.

---

## 2. What you reuse

| Use | Where | What it does |
|---|---|---|
| TanStack React Query | already in `MemoryView.tsx` | Fetch + cache + `invalidateQueries` for Agent D's live-reload |
| `ViewHeader` | `views/ChatsView.tsx` exports it | Standard view title bar — use this for the Wiki tab's top bar |
| `Button`, `Badge`, `ScrollArea` | `components/ui/*` (shadcn) | Existing UI primitives |
| Lucide icons | `lucide-react` | `Notebook`, `Folder`, `FileText`, `Network`, `Search` |
| Tailwind classes | existing config | Style with the same dark theme tokens (`bg-background`, `text-muted-foreground`, etc.) |

You depend on **one new dependency**: `react-markdown` for body rendering. Add it to `package.json` (5 KB gzipped, MIT). Do **not** add `remark-wiki-link` — wikilink resolution is a custom pre-process step (see §4.2) so the link click handler can call back into React state.

---

## 3. Component prop contracts (binding)

```typescript
// WikiView (this file owns)
export function WikiView(): JSX.Element;

// TreeSidebar
interface TreeSidebarProps {
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
}

// PageRenderer
interface PageRendererProps {
  slug: string;
  onWikilinkClick: (targetSlug: string) => void;
}

// PageHeader
interface PageHeaderProps {
  slug: string;
  kind: "entity" | "concept" | "project" | "session";
  title: string;
  frontmatter: Record<string, string | string[]>;
  vaultRelPath: string;
}
// PageHeader renders the breadcrumb + title + pills, then mounts a slot:
//   <ObsidianButton vaultRelPath={vaultRelPath} />
// ObsidianButton is provided by Agent D and imported from `components/wiki/ObsidianButton.tsx`.
// If D's file does not exist yet, render a placeholder <button disabled>Open in Obsidian</button>.

// BacklinksPanel
interface BacklinksPanelProps {
  slug: string;
  onSelect: (targetSlug: string) => void;
}
```

---

## 4. Behaviour requirements

### 4.1 Layout

Three-column grid that fills the view area:
- Left: 260 px tree sidebar (resizable in a later phase, not B3).
- Middle: viewport with two tabs at top — "Memory Map" (Agent C's `<WikiGraph />`) and "Page" (your `<PageRenderer />`). Defaults to the Map tab on mount.
- Right: 380 px details panel (`<BacklinksPanel />` plus future cards from D).

When `selectedSlug` becomes non-null, switch the centre tab to "Page" automatically. When the user clicks "Memory Map" in the tab strip, the page stays loaded but is hidden — clicking the page tab again returns to the same slug.

### 4.2 Wikilink rendering

`PageRenderer` receives `body_md` from the API. Wikilinks appear as `[[slug]]` or `[[entities/slug]]`. Before passing to `react-markdown`, run a pre-processor that converts each `[[X]]` into a marker `react-markdown` will recognise:

```
[[harald]]     → [harald](#wiki:harald)
[[entities/harald]] → [harald](#wiki:harald)
[[harald|the father]] → [the father](#wiki:harald)
```

Then in `react-markdown`'s `components.a` override, detect `href.startsWith("#wiki:")`, render as a custom `<a class="wikilink">…</a>`, and on click call `props.onWikilinkClick(href.slice(6))` — do not navigate.

Broken wikilinks (target slug not in tree) get a `.broken` class. The tree state is fetched once and cached; if a click resolves to a missing slug, show a toast "Page nicht gefunden" and stay on the current page.

### 4.3 Tree behaviour

- Folders are open by default for `entities` and `projects` (the non-empty ones); closed for `concepts`, `sessions`, `_archive`.
- Clicking a folder toggles open/closed (local state, not persisted).
- Clicking a file leaf calls `onSelect(slug)` and visually marks active (matches mockup styling).
- The currently selected file gets the `active` class even if the user collapses its parent folder (collapse only hides, doesn't deselect).

### 4.4 Frontmatter pills

Render the frontmatter as pills in `PageHeader`. Map the keys to friendly labels:

```typescript
const FRIENDLY_LABELS: Record<string, string> = {
  type: "type",
  entity_kind: "kind",
  status: "status",
  created: "created",
  updated: "updated",
  started: "started",
  last_activity: "last activity",
  // ignore: slug, aliases (already in title/breadcrumb)
};
```

Skip `slug` (already in breadcrumb), skip `aliases` (could be very long), skip any key whose value is an empty string or empty list. Limit to ~6 pills max to keep the header tight.

### 4.5 Empty state

If `/api/wiki/tree` returns `total_pages: 0`, show a centred empty-state card:

> *Dein Wiki ist noch leer.*
> *Sobald Jarvis in einem Gespräch etwas Wichtiges aufschnappt, landet es hier — Personen, Projekte, Vorlieben, Termine.*
> *Du kannst auch jederzeit selbst eine `.md`-Datei in `wiki/obsidian-vault/entities/` ablegen.*

German user-facing strings are OK in UI labels (precedent: existing `MemoryView.tsx`). Code/comments/logs stay English.

### 4.6 Error handling

If a fetch fails (network, 500), show an inline `<Alert>` block in the affected panel — never let an empty white box stand. Use the existing `Alert` component from `components/ui/alert.tsx`.

### 4.7 Loading skeletons

While `react-query` is pending, render Tailwind skeleton bars (`animate-pulse bg-muted h-4 rounded`) instead of "Loading…". Existing skeletons in `views/SessionsView.tsx` are the reference.

---

## 5. Tests

### 5.1 Component tests (Vitest + Testing Library)

Add a test file `jarvis/ui/web/frontend/src/views/WikiView.test.tsx`. Run with `npm test`.

Minimum 6 cases (mock fetch via MSW or `vi.fn`):

1. Renders empty state when `/api/wiki/tree` returns 0 pages.
2. Renders folder tree when 3 pages exist; counts correct.
3. Clicking a leaf calls `onSelect` with the slug.
4. Wikilink in body → click → `onWikilinkClick` called with target slug.
5. Broken wikilink → renders with `.broken` class.
6. Frontmatter pills appear, slug is skipped.

### 5.2 Build gate

```powershell
cd jarvis/ui/web/frontend
npm install               # if package.json changed
npm run build             # must succeed, no warnings about react-markdown
npx tsc --noEmit -p .     # zero TypeScript errors
```

---

## 6. Hard negatives

- ❌ Don't fetch wiki data outside the Wiki tab. Mount queries inside `WikiView.tsx`, not in a top-level provider.
- ❌ Don't use `dangerouslySetInnerHTML` for the markdown body. `react-markdown` renders to React elements. The body comes from disk and is *user-trusted* (no XSS surface) but the principle stands.
- ❌ Don't import from `frontend/src/views/MemoryView.tsx`. Treat it as deleted.
- ❌ Don't add a "create new note" button. Read-only is a hard architectural decision (see `AP-1` in `00-OVERVIEW.md`).
- ❌ Don't write new English-vs-German rules. Follow the precedent: UI strings German, code/logs/test names English.
- ❌ Don't introduce client-side routing libraries (react-router, etc.). The existing sidebar nav already routes via local state.
- ❌ Don't bundle the graph component (Agent C's `WikiGraph`) eagerly. Wrap it with `React.lazy(() => import("../components/wiki/WikiGraph"))` and `<Suspense fallback={<Skeleton />}>`.

---

## 7. Size estimate

`WikiView.tsx` ~150 lines. Each sub-component ~80–150 lines. `wikiApi.ts` ~80 lines. Tests ~200 lines. Total ~800 lines of new TSX + 200 lines deleted (`MemoryView.tsx`).

---

## 8. Closing report

Final line: `Goal erfüllt: ja — Grund: <one sentence>` (or `nein`).

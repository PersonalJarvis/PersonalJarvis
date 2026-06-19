# Agent A — Backend API (`wiki_routes.py`)

You are Agent A on Phase B3 of Personal Jarvis. **Read `00-OVERVIEW.md` first**, then this. You build the JSON API that exposes the on-disk Obsidian vault to the React frontend. You do not touch the UI, you do not author wiki content, you do not call the brain.

---

## 1. What you own

| File | Status | Purpose |
|---|---|---|
| `jarvis/ui/web/wiki_routes.py` | **NEW** | FastAPI router with six endpoints (see §3) |
| `jarvis/ui/web/server.py` | **MODIFY** | `app.include_router(wiki_routes.router)` |
| `tests/unit/ui/web/test_wiki_routes.py` | **NEW** | One test per endpoint + 30s-edit-lock case + missing-vault case |
| `tests/integration/ui/wiki/test_wiki_routes_e2e.py` | **NEW** | Real temp vault with 3 pages → all endpoints return shape-correct JSON |

No other files. If you find yourself editing `frontend/`, `manager.py`, `factory.py`, `pipeline.py`, or anything under `jarvis/memory/` — stop. That is out of scope.

---

## 2. What you reuse (do not reinvent)

| Use | Where | What it does |
|---|---|---|
| `PageRepository` | `jarvis/memory/wiki/page.py` | Parses a `.md` file into `WikiPage` with frontmatter, body, wikilinks, validity flag. |
| `VaultIndex` | `jarvis/memory/wiki/vault_index.py` | Whole-vault view: `pages_by_type`, `find_by_slug`, `backlinks_to`. |
| `VaultSearch` | `jarvis/memory/wiki/search.py` (B5) | FTS5-aware file-walking search returning `list[SearchHit]`. |
| `WikiIntegrationConfig` | `jarvis/core/config.py` | Holds `vault_root: Path`. |
| FastAPI route patterns | `jarvis/ui/web/server.py` existing routes (e.g. `/api/sessions`) | The reply-envelope style `{"ok": true, ...}` is the existing house style. |

`app.state.bus`, `app.state.config`, `app.state.brain` are already populated by the server. Read `app.state.config.memory.wiki.vault_root` (or however the existing B1/B5 wiring exposes the vault root — confirm by greping `vault_root` in `jarvis/memory/wiki/integration.py`).

If a dependency you need is not already on `app.state`, instantiate it lazily inside your route handler — do **not** add startup wiring; the server boot path is sensitive (see BUG-016).

---

## 3. Endpoints (binding contract)

All shapes are documented in **`00-OVERVIEW.md` §3.1** verbatim. Re-read that section before writing handlers. Below is the route table only:

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/wiki/tree` | full vault tree, four folders, files with mtime + size |
| `GET` | `/api/wiki/page/{slug}` | one page (frontmatter, body_md, wikilinks, stats) |
| `GET` | `/api/wiki/graph` | nodes + edges built from `[[wikilinks]]` |
| `GET` | `/api/wiki/backlinks/{slug}` | reverse-link list with snippet |
| `GET` | `/api/wiki/search?q=…&k=5` | up to k FTS5 hits |

The `WS /api/wiki/live` endpoint is Agent D's concern, not yours.

### 3.1 Edge cases you must handle

- **Vault missing or empty** → all four endpoints return `{"ok": true, ...}` with empty arrays (no 404). The UI shows an "empty state" placeholder — don't break it.
- **Slug not found** on `/page/{slug}` → `{"ok": false, "error": "page not found"}`. HTTP 200, not 404. (The 404 reservation is for unknown *routes*, not unknown *content*.)
- **Schema-invalid page** (frontmatter missing or wrong type) → still return it, but include `"frontmatter_valid": false` so the UI can show a warning.
- **Broken wikilink** → list it in `graph.broken[]`. Never crash the graph response because one target is missing.
- **Search query with FTS5 syntax characters** → use the existing `_sanitize_fts5_query` pattern in `jarvis/memory/recall.py:22-25`. Do not pass raw user input to FTS5.

---

## 4. File walk strategy

`/api/wiki/tree` walks four folders: `entities/`, `concepts/`, `projects/`, `sessions/`. Skip `_archive/`, `attachments/`, `99-templates/`, `00-index/`, `10-notes/`, `90-attachments/`. Skip anything not `*.md`. Skip files that fail to parse — but include their existence with `{"slug": ..., "parse_error": true}` so the UI can show them greyed out.

Walks are not cached at this layer — Agent D installs the watchdog separately and pushes change events. Your endpoints read the file system fresh on every call. This is fine: typical vault is <500 pages = <50 ms full walk.

For `/api/wiki/graph` you also have to extract outbound wikilinks from each page body. The `PageRepository` already does this — `WikiPage.wikilinks` is a tuple of canonical slug strings. Resolve each target slug against the index; if no page exists, the edge goes into `broken[]` instead of `edges[]`.

---

## 5. Tests

### 5.1 Unit tests (`tests/unit/ui/web/test_wiki_routes.py`)

Use FastAPI's `TestClient` against a router mounted on a fresh `FastAPI()` with a pytest fixture vault root.

Minimum 12 cases:

1. `/tree` with 3-page vault → folders structure correct, file counts correct.
2. `/tree` with empty vault → `folders` non-empty (4 empty buckets), `stats.total_pages == 0`.
3. `/tree` with missing vault dir → still `ok: true`, all buckets empty.
4. `/page/{slug}` happy path → frontmatter, body_md, wikilinks correct.
5. `/page/{slug}` unknown slug → `ok: false`, error message.
6. `/page/{slug}` schema-invalid page → returns page, `frontmatter_valid: false`.
7. `/graph` with 2 pages linking each other → 2 nodes, 2 edges (or 1 if you de-dupe — document the choice).
8. `/graph` with a broken wikilink → edge moves into `broken[]`.
9. `/backlinks/{slug}` → finds incoming links, snippet contains the link context.
10. `/search?q=…` happy path → at least one hit, score in [0, 1].
11. `/search?q=` empty query → `{"ok": false, "error": "empty query"}`.
12. `/search?q=` with FTS5 syntax chars → sanitised, no exception.

### 5.2 Integration test (`tests/integration/ui/wiki/test_wiki_routes_e2e.py`)

Mounts the *full* server (`jarvis.ui.web.server:create_app`), populates `wiki/obsidian-vault/` in a `tmp_path` fixture with `ruben.md`, `harald.md`, `pixel-art-editor.md`, and walks the same flow the live walk-through covers in `00-OVERVIEW.md §7`:

1. `GET /api/wiki/tree` → 3 files across 2 folders.
2. `GET /api/wiki/page/harald` → body contains "1976".
3. `GET /api/wiki/graph` → ≥ 2 edges.
4. `GET /api/wiki/search?q=pizza` → 1 hit on ruben.
5. `GET /api/wiki/backlinks/harald` → 1 hit (ruben).

---

## 6. Hard negatives

- ❌ Don't add caching, throttling, or rate-limiting. The vault is small; premature optimisation.
- ❌ Don't open the database — `VaultSearch` already handles FTS5. You only call it.
- ❌ Don't return the full body in `/tree`. That endpoint is for navigation, not content. Just slug + title + mtime + size.
- ❌ Don't add authentication. The desktop app is local-only, the existing pattern is unauthenticated.
- ❌ Don't add OpenAPI tags/descriptions that mention internal data (no `description="reads /Users/admin/Desktop/Personal Jarvis/data/sessions.db"`). The OpenAPI doc is user-visible.
- ❌ Don't import from `jarvis.brain.*`. The Wiki view is brain-free.
- ❌ Don't write to `app.state` from a route handler. State is set at startup only.
- ❌ Don't introduce async generators for streaming responses. All responses fit in one JSON object.

---

## 7. Size estimate

200–280 lines of production code + ~250 lines of tests. If you're heading past 400 lines of production code, something is wrong — you've probably re-implemented something `PageRepository` or `VaultIndex` already does.

---

## 8. Closing report

Free text, but the final line must be exactly:

```
Goal erfüllt: ja — Grund: <one sentence>
```

or `nein` with the reason.

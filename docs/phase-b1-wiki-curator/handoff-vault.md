# Phase B1 — Instance B Handoff (Vault Index + Log Writer + Index Builder)

**Branch:** `impl/wiki-memory-b1-vault`
**Owner role:** Vault overview — whole-vault read view plus `log.md` /
`index.md` writes. The four entity/concept/project/session page
directories are **read-only** from this instance.

## What landed

Three production modules under `jarvis/memory/wiki/`:

| File | Public surface | Lines |
|---|---|---|
| `vault_index.py` | `VaultIndex.scan / pages_by_type / find_by_slug / backlinks_to` | ~260 |
| `log_writer.py`  | `LogWriter.append_log_entry`, `VALID_VERBS` | ~180 |
| `index_builder.py` | `IndexBuilder.render_index_md` | ~250 |

Three test modules under `tests/unit/memory/wiki/`:

| File | Cases |
|---|---|
| `test_vault_index.py` | 18 |
| `test_log_writer.py` | 15 |
| `test_index_builder.py` | 13 |

Plus `conftest.py` with `FakeWikiPage` and `FakePageRepository` so the
Instance B tests run standalone before Instance A is merged.

All 46 tests green:

```
pytest tests/unit/memory/wiki/test_vault_index.py \
       tests/unit/memory/wiki/test_log_writer.py \
       tests/unit/memory/wiki/test_index_builder.py -v
…
============================= 46 passed in 0.78s ==============================
```

## Design notes

### VaultIndex

- Scans the four documented page directories. `_archive/` and
  `attachments/` are excluded by the `SKIP_DIRS` constant.
- Holds `{slug -> _IndexEntry(page, mtime_ns)}` in `_by_slug` and a
  reverse-link table `{target_slug -> [source_slug, …]}` in
  `_backlinks`.
- **Stale-rescan-on-stat**: each synchronous accessor
  (`find_by_slug`, `pages_by_type`, `backlinks_to`) restats the
  underlying files and re-parses anything whose `mtime_ns` has
  advanced. mtime is **never** cached across writes — every accessor
  is a fresh `stat()`, which keeps the 30 s concurrent-edit guard in
  Instance C honest.
- The async `repo.load(...)` call from inside a sync accessor is
  bridged via `_run_coro_sync`. The current case is "no running
  loop on this thread"; the threaded fallback exists for the rare
  case where a caller already owns the loop.

### LogWriter

- Tempfile **in the same directory** as `log.md`, then
  `os.replace` — atomic on Windows when source and target share a
  drive (which is by construction true here).
- A `_pre_replace_hook` test seam lets the crash-mid-write test
  inject a `RuntimeError` immediately before `os.replace`. The
  rollback path unlinks the tempfile and the existing `log.md`
  is left byte-for-byte intact (test
  `test_crash_mid_write_leaves_original_untouched`).
- Verbs are validated against the `VALID_VERBS` frozenset from
  `schema.md`. Unknown verbs raise `ValueError` — no silent typos.
- Empty `pages_touched` renders as `(none)` (the schema requires
  the field be present even when zero pages were materially
  changed).
- Summary collapses internal whitespace so a multi-line summary
  still produces a single-line `- summary: …` entry.

### IndexBuilder

- Renders the four category sections (Entities, Concepts,
  Projects, Sessions) in a fixed order with the seed blurbs from
  the bootstrap `index.md`.
- **Preamble preservation**: everything above the first
  `## Entities` heading in an existing `index.md` round-trips
  verbatim. Missing or boundary-less files fall back to the
  default preamble in `_DEFAULT_PREAMBLE`.
- Stable alphabetical sort within each section (also defensively
  re-sorted in `_render_section` so a future drift in
  `pages_by_type` cannot silently corrupt the output).
- Honors the 200-line `schema.md` cap via per-category truncation
  with a `(... N more)` marker line. Test
  `test_line_cap_triggers_truncation` proves the path; the small
  vault test proves we do not truncate gratuitously.

## Instance-A decoupling

- This instance imports **nothing** from Instance A at runtime.
  `WikiPage` is annotated as `Any` in the dataclass slot; the
  `PageRepository` is duck-typed via the documented method names
  (`load`, `parse`, `render`, `resolve_wikilink`).
- Tests run against `FakeWikiPage` / `FakePageRepository` in
  `tests/unit/memory/wiki/conftest.py`. Both fakes match the
  surface Instance A's `protocols.py` already declares.
- In wave-2 integration the fakes can be deleted once `WikiPage`
  / `PageRepository` from `jarvis.memory.wiki.protocols` is
  imported — the production code does not change because it
  never depended on a concrete type.

## Hard-negative compliance

| Rule | Status |
|---|---|
| Do **not** modify `entities/`, `concepts/`, `projects/`, `sessions/` pages | Honored — no path under those dirs is opened with write intent. |
| Do **not** import from `atomic_writer` or `curator_llm` | Honored — no such imports in this branch. |
| Do **not** cache `mtime` across writes | Honored — every stat is fresh; `_IndexEntry.mtime_ns` is invalidated on any reparse, never persisted past one accessor call. |
| Do **not** edit existing `log.md` entries | Honored — the writer only appends; the test `test_two_appends_render_in_order` proves ordering and prior-entry survival. |

## Open questions for wave 2

1. **Repository render contract.** `PageRepository.render(page)` is
   declared on Instance A's Protocol but unused by Instance B. If
   wave 2 wants `IndexBuilder` to embed full page summaries (rather
   than just slug+aliases), the entry-rendering helper
   `_render_page_line` is the single touchpoint.
2. **Index regeneration cadence.** `IndexBuilder.render_index_md`
   is a pure function over the current `VaultIndex`. Wave 2 owns
   the question of when to invoke it — schema says "only when a new
   page is created", which the curator orchestrator can decide.
3. **Log entry rendering for renames.** The verb `rename` is in
   `VALID_VERBS` but the current `pages_touched` field carries a
   flat list. If wave 2 wants `- pages touched: [[old]] → [[new]]`
   rendering, it can pass the formatted string into
   `pages_touched` — the writer round-trips arbitrary strings
   between brackets.

## Files touched

```
jarvis/memory/wiki/vault_index.py            (new)
jarvis/memory/wiki/log_writer.py             (new)
jarvis/memory/wiki/index_builder.py          (new)
tests/unit/memory/wiki/__init__.py           (new)
tests/unit/memory/wiki/conftest.py           (new)
tests/unit/memory/wiki/test_vault_index.py   (new)
tests/unit/memory/wiki/test_log_writer.py    (new)
tests/unit/memory/wiki/test_index_builder.py (new)
docs/phase-b1-wiki-curator/handoff-vault.md  (this file)
```

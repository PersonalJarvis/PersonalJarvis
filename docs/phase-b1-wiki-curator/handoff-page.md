# Handoff — Instance A (Page Model + Wikilink Parser)

**Branch:** `impl/wiki-memory-b1-page` · **Date:** 2026-05-11 ·
**Owner role:** Instance A of four parallel B1 instances.

## What was built

Three production files under `jarvis/memory/wiki/`:

| File | Lines | Purpose |
|---|---|---|
| `protocols.py` | 138 | Binding inter-instance contract — `PageRepository`, `VaultIndex`, `AtomicWriter`, `CuratorLLM` Protocols plus `WikiPage`, `PageUpdate`, `WriteResult` DataClasses. |
| `page.py` | 234 | Tolerant Markdown parser + round-trip-stable renderer. `MarkdownPageRepository` implements `PageRepository`. Constants `DIR_TO_TYPE`, `REQUIRED_KEYS`, `CANONICAL_SECTIONS` exposed for downstream instances. |
| `wikilink.py` | 92 | `extract_wikilinks` + `resolve_wikilink`. Order-preserving extraction, alias-stripping canonicalisation, explicit-prefix-wins resolution. |

Plus two test files:

| File | Tests | Notes |
|---|---|---|
| `tests/unit/memory/wiki/test_page.py` | 30 | Happy paths, tolerant failures, round-trip (single and double), `MarkdownPageRepository` async-load. |
| `tests/unit/memory/wiki/test_wikilink.py` | 21 | All four documented link forms + escape + edge cases; resolver covers unique/ambiguous/missing/explicit-prefix. |

**Final test count: 51 (target was 30+).** All green via
`pytest tests/unit/memory/wiki/test_page.py tests/unit/memory/wiki/test_wikilink.py -v`.

## Where the spec was followed verbatim

- The Protocol shapes in `protocols.py` are character-identical to README
  Part 4. The other three instances may copy-paste-import without
  surprise.
- Wikilink forms (`[[slug]]`, `[[entities/slug]]`, `[[slug|alias]]`,
  `[[entities/slug|alias]]`, `\[[escaped]]`) match the README and
  `schema.md`.
- "Strip only trailing whitespace at file end" is the **only**
  normalisation applied — internal whitespace, blank lines, trailing
  spaces inside a line all round-trip exactly.
- All artifacts (code, docstrings, comments, exception messages) are
  English per the Output Language Policy.

## Design decisions that downstream instances should know

### 1. Frontmatter is `dict[str, str]` — literal strings, no YAML round-trip

`WikiPage.frontmatter` keeps values as the raw literal after the first
colon. `aliases: [Alex, Personal Jarvis Maintainer]` becomes
`{"aliases": "[Alex, Personal Jarvis Maintainer]"}` (note: the string, not a list).

**Why:** PyYAML round-trips list-shaped values inconsistently (it may
quote, reorder keys, or change whitespace). A trivial `key: value`
splitter is the only way to guarantee text-level round-trip stability
which is a hard test requirement. Instances B and D can parse the list
shape themselves if they need structured access.

### 2. Renderer always emits `---` markers, even when frontmatter is empty

If a caller constructs a `WikiPage` with `frontmatter={}`, `render_page`
still produces `---\n---\n<body>\n`. This means a parse-render of a
file *without* any frontmatter produces a file *with* an empty
frontmatter wrapper. The WikiPage objects compare equal — round-trip
holds at the `WikiPage` level, which is what the tests assert and what
the spec demands.

**Implication for Instance C (`AtomicWriter`):** when re-parsing a
freshly-written page for validation, expect the on-disk text to contain
the empty markers; do not flag this as a corruption.

### 3. `parse_sections` is a free function, not a `WikiPage` attribute

The Protocol's `WikiPage` dataclass has no `sections` field (per the
exact README Part 4 specification). The README docstring mentions
"Sections is a parsed view ... computed lazily — None if the page does
not match the schema." I interpreted this as a *helper utility*, not a
dataclass field, since adding a field would diverge from the protocol
the other instances import.

`parse_sections(body) -> tuple[tuple[str, str], ...]` is exported from
`page.py` for Instance D's prompt builder when it needs to inspect the
canonical section structure.

### 4. Schema validity is *minimal*, not *exhaustive*

`is_schema_valid` checks four things:

1. Frontmatter is present and closed.
2. `type` key is set and matches the parent directory.
3. Required keys from `REQUIRED_KEYS[page_type]` are all present.
4. For `entity`/`concept`/`project`: `frontmatter["slug"] == path.stem`.

It does **not** check whether the body contains the canonical sections
(`## Summary`, `## Facts`, …) — those checks belong to the LLM
curator (Instance D) which decides what to write, and to the writer
(Instance C) which re-parses after writing.

### 5. `MarkdownPageRepository.load` uses `asyncio.to_thread`

Per the conventions in README § 3.5 ("Never block the asyncio event
loop"). File I/O happens on a worker thread; parsing is microseconds
and stays inline. The Verdichter pattern at
`jarvis/awareness/verdichter.py` was the reference.

## Hard negatives I respected

- ❌ No disk writes anywhere in this instance. `MarkdownPageRepository`
  only reads.
- ❌ No imports from `vault_index`, `atomic_writer`, or `curator_llm`.
- ❌ No new frontmatter keys invented — `REQUIRED_KEYS` enumerates only
  what `schema.md` lists.
- ❌ No whitespace normalisation inside the body — round-trip is exact.

## Open questions for the integrator

1. **Frontmatter list values.** Should Instance D get a structured view
   of `aliases: [...]` and `episode_ids: [...]` somewhere? The current
   answer is "Instance D parses the literal string itself." If Wave 2
   prefers a richer parser, the cleanest extension is a separate
   utility module — not a change to `WikiPage`, which would break the
   Protocol.

2. **Top-level files (`schema.md`, `index.md`, `log.md`).** Their parent
   directory is the vault root, which is not in `DIR_TO_TYPE`. They
   parse as `page_type=""` from directory but `page_type="meta"` from
   the frontmatter `type: meta` key. The current logic accepts this
   (frontmatter wins). Cross-check is skipped because `dir_type` is
   falsy. Confirm this is intended behaviour for the bootstrap-vault
   flow.

3. **Session slug vs `session_id`.** Session pages have filenames like
   `2026-05-11-abc123.md` and a `session_id: abc123` in frontmatter.
   `WikiPage.slug` is set to the file stem (`2026-05-11-abc123`). If
   Instance B's index needs to index by `session_id` instead of slug,
   it should read from `page.frontmatter["session_id"]` directly.

## Deviations from the README briefing

None of substance. Two clarifications worth recording:

- The README dataclass docstring for `WikiPage` mentions a `Sections`
  view but the dataclass has no such field. I treated the field-less
  shape as authoritative and exposed `parse_sections` as a separate
  helper. (See decision 3 above.)
- The README example wikilink list in extraction includes
  `[[entities/alex]]` as a "canonical form". I kept the directory
  prefix as part of the canonical form (`"entities/alex"`), not the
  bare slug. This matches the schema's discussion of explicit-prefix
  wikilinks and is the form Instance B will need to render `index.md`
  with stable groupings.

## Files changed in this branch

```
jarvis/memory/wiki/protocols.py         (new)
jarvis/memory/wiki/page.py              (new)
jarvis/memory/wiki/wikilink.py          (new)
tests/unit/memory/wiki/test_page.py     (new)
tests/unit/memory/wiki/test_wikilink.py (new)
docs/phase-b1-wiki-curator/handoff-page.md (new — this file)
```

No existing file modified. `jarvis/memory/wiki/__init__.py` unchanged
(still exposes only `TEMPLATES_DIR` from Phase B0). Wave 2 may want to
add re-exports for ergonomic imports — that is the integrator's call.

## Verification command

```bash
pytest tests/unit/memory/wiki/test_page.py \
       tests/unit/memory/wiki/test_wikilink.py -v
```

Expected: 51 passed.

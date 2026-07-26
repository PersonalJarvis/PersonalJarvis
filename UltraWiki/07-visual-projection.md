# 07 · Visual projection — the readable face of the store

## The problem it solves

Docs 01–06 build a system that can *answer*. None of them build one you can
*look at*. The Ultra section offered five tabs — Overview, Ask, Sources,
Contents, Settings — every one of them operational. A user who wants to know
what their assistant actually remembers had no browsable surface at all.

Exporting the store as-is does not fix that. Measured on a real corpus
(2026-07-26):

| Fact | Value |
|---|---|
| Items | 4 712 |
| Distinct item titles | **261** — the most common repeats **213 times** |
| Mean item body | 265 characters |
| Distinct `thread_key` | 4 651 over 4 712 items — grouping by thread is noise |
| Distilled documents | 1 516 → 2 551 as distillation caught up |
| Entities inside those distillations | 947 → 1 397 distinct, case-folded |
| Entity co-occurrence pairs | 3 596 |

One note per item produces hundreds of files called
`Conversation on 2026-04-25`, linked to nothing. The store is a **log**; a
wiki needs a **condensation layer**.

## The model

The readable units sit one level up, and the distiller already extracted them:

- **Topics** — the people, places, organizations and projects named in
  `uw_documents.distill_json`. Natural graph hubs.
- **Moments** — one distilled document each, titled by the `question` it
  answers. On the live corpus **zero** moments fall back to the item title.

Raw items never become pages. They stay evidence behind their `permalink`.

`jarvis/ultrawiki/projection.py` folds the two out of the store, plus mention
counts, first/last seen, and co-occurrence neighbours.

### Derived, never stored

The full fold measures **~44 ms** over the live corpus, so the projection
stays a computed view behind a fingerprint cache
(`count, max(document id), max(created_at)`) rather than new tables. A stored
second copy of the same truth is the multi-layer drift class of BUG-008, and
the names `uw_entities` / `uw_identifiers` are reserved for the P5 identity
system with merge history. When P5 lands, the projection reads from there and
both consuming surfaces are unaffected.

### Entity normalization (deterministic, no model call)

NFC → collapse whitespace → strip edge punctuation → reject shorter than 2 or
longer than 80 → identity is `casefold()`, display is the most frequent
spelling. Measured: this folds 12 real duplicate pairs (`OpenClaw`/`openclaw`,
`GEPA`/`Gepa`) that would otherwise be two nodes and two colliding files.

There is deliberately **no stop-word blacklist**. Guessing which entity is
"not real" is how a wiki quietly loses content; rarity is handled by ranking
and the graph threshold, never by dropping data.

## Surface 1 — the Explore tab

Second tab in the Ultra section (after Overview, which owns operational
state). Topic list with local filtering, force-directed graph, moment list;
selecting a topic opens its page with neighbours and its moments.

**Visual encoding** lives in `lib/entityGraph.ts` and every mark carries data:

| Mark | Means |
|---|---|
| Node size | mention count (square-root scaled — linear turns one dominant topic into a blob surrounded by dust) |
| Node brightness | recency of the last mention |
| Edge width | shared moments |
| Time bar under each topic | *when* that topic lived in your history — width is its lifetime, position is when, colour is recency |

The time bar is the signature: at a glance "Berlin" runs the whole way
through while "Bora Bora" was one bright week in July. No list of counts
shows that shape.

**The mention floor** defaults to 2 with a slider down to 1. On the live
corpus 1 397 topics collapse to 438 at two mentions; drawing the full tail at
once is a hairball, not a map. The floor is visible and reversible — never a
silent truncation.

**Four honest empty states.** "Nothing here" has four causes a user cannot
tell apart, and one of them (consent granted but never fetched) once went
undiagnosed for days behind a blank screen. Every answer carries the corpus
counts *and* a named reason from `ExploreReason`, and each reason renders its
own message and its own way out. The enum is parity-tested across Python,
TypeScript and all three locales, as is every string the components ask for.

## Surface 2 — the Obsidian vault

`jarvis/ultrawiki/vault_export.py` writes the same projection to disk:

```
<vault>/                       default: <data dir>/ultrawiki-vault
├── README.md                  explains the one-way contract
├── Topics/<Label>.md
├── Moments/YYYY-MM/<Title>.md
├── Overview/                  All topics · Timeline · Sources
├── My notes/                  NEVER written to
└── .ultrawiki-manifest.json
```

**One-way by design.** The three generated folders are rewritten every run;
`My notes/` is created once and read back by the existing `obsidian-vault`
connector. Two-way flow without a merge problem: generated content is
disposable, authored content is never ours to write.

**Deletion is fail-closed** with two independent proofs of ownership — the
note is in the previous manifest, or its front matter carries
`generated_by: ultrawiki`. A hand-written file inside `Topics/` survives
every export.

### Filenames are the cross-platform surface

Measured: **14 of 947** topic labels contain a character that is illegal or
path-changing in a filename (`personaljarvis/personaljarvis`, `win32/uia`,
`@eslint/plugin-kit`). Written naively a slash creates a directory on POSIX
and raises on Windows. `safe_note_name` handles illegal and control
characters, trailing dots and spaces (silently dropped by Windows, which
would break the orphan comparison), reserved DOS device names, length plus
hash, and `assign_note_names` adds deterministic collision suffixes —
including case-only collisions, one file on Windows and macOS. All tested
through the pure functions, so the Windows cases are covered on Linux CI.

Verified on the live corpus: 1 373 topic notes, 2 551 moment notes, **zero**
filenames that break on Windows, **zero** case collisions.

### The manifest exists for a measured reason

Deciding "has this note changed" by reading every note back cost 2.5 ms per
file behind a live virus scanner: a re-export that changed nothing took
**24 s**, four times longer than writing the whole vault from scratch. With
the manifest: **0.7 s**.

## Degradation

| Situation | Behaviour |
|---|---|
| Ultra mode off | every route answers 409 — the app is healthy, the normal wiki answers |
| No sources / nothing imported / nothing distilled / no entities | the specific reason plus the tab that fixes it |
| Obsidian not installed (every headless server, most fresh machines) | the export still runs and the files are still written; the missing app is a sentence, not a disabled button |
| Registering before the first export | refused with a reason, rather than pointing Obsidian at a folder that is not there |
| Vault path unwritable | 500 naming the path and the OS error; nothing is changed |

## Routes

`GET explore/entities`, `explore/entities/{key}`, `explore/moments`,
`explore/graph`; `GET vault/status`, `POST vault/export` (danger-flagged),
`POST vault/register`. All under the `ultrawiki` tag in
`jarvis/ui/web/ultrawiki_explore_routes.py`, so each is a
`jarvis api ultrawiki <op>` command. The export and both filesystem probes run
in worker threads — a first export writes thousands of files, and holding the
event loop for that would freeze every other surface including a voice turn.

## What this deliberately does not do

- No entity typing or merging (person / place / organisation). That is P5
  territory with reversible merge history; a projection must not squat on it.
- No editing of UltraWiki content through the vault.
- No new tables, no new pipeline stage, no change to retrieval or ranking.

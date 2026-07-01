# Phase B1 — Wiki-Curator · Instance Briefing

> **Read this top-to-bottom before you write a single line of code.** This
> document is the only shared context the four parallel instances and the
> wave-2 integrator have. If you skip a section you will build something
> that does not fit.

---

## Part 1 — The world this code lives in (plain language)

### 1.1 What Personal Jarvis is

Personal Jarvis is a voice-controlled meta-orchestrator that runs on the
user's Windows 11 machine. It is **not** a classical voice assistant
("hey, what is the weather"). It is a supervisor agent that listens, plans,
and dispatches work to other agents. The voice layer is the interface,
but the brain underneath is what matters.

The user is **Personal Jarvis Maintainer** (Rubén). He is not a software developer. He
talks to Jarvis in German with English code/identifier mixed in. He
operates Personal Jarvis autonomously and trusts the agents he spawns to
make sensible technical choices. He reads structured German prose; he
struggles with raw code dumps or jargon-heavy reports.

### 1.2 What we are building right now

A **personal knowledge wiki** that Jarvis maintains himself. Inspired by
Andrej Karpathy's "LLM Wiki" pattern (April 2026, gist `442a6bf`). The
core insight: instead of an LLM rediscovering knowledge from raw chunks
on every query (the "pure RAG" anti-pattern), the LLM compiles knowledge
once into a structured markdown wiki — entity pages, concept pages,
project pages, all cross-linked — and maintains it continuously. Ten to
fifteen pages are typically touched per ingest.

The wiki is the **long-term memory** tier. There are three tiers total:

| Tier | Lifespan | Lives in | Read by |
|---|---|---|---|
| Short-term | seconds to 30 minutes | `AwarenessManager.state` (RAM) | `awareness-snapshot` tool |
| Mid-term | last 3-5 sessions | `data/workspace/sessions/*.md` (this vault) | system-prompt injector (Phase B5) |
| Long-term | permanent | `data/workspace/{entities,concepts,projects}/*.md` (**this vault — what B1 maintains**) | `wiki-recall` tool + system-prompt injector |

Information flows one direction: short-term → mid-term → long-term.
Episodes feed sessions. Sessions feed entity/concept/project pages. The
wiki never reaches back to mutate the live awareness state.

### 1.3 What Phase B0 already delivered

Already committed on branch `impl/wiki-memory` (commit `7d9d3334`):

- `data/workspace/schema.md` — the maintenance contract every wiki edit
  must obey. **Treat this as a binding spec.** If your code disagrees
  with the schema, update your code, not the schema.
- `data/workspace/{index,log,README}.md` — empty seed files.
- `data/workspace/{entities,concepts,projects,sessions,attachments,_archive}/`
  — empty directories.
- `docs/adr/0013-knowledge-wiki-architecture.md` — the architectural
  decision record. Explains why we picked this pattern and what got
  rejected.
- `jarvis/memory/wiki/templates/` — versioned copies of the seed files
  (schema, index, README). The runtime vault is gitignored; templates
  are copied into the vault on bootstrap.
- `scripts/wiki_migrate_v0_to_v1.py` — one-shot migration from the
  legacy flat workspace to the new layout. Idempotent, backup before
  any write, has 14 unit tests.

The legacy `Curator-Merger` (`jarvis/memory/curator/merger.py`) is **still
running** and will continue to write into the legacy flat files until
Phase B4 removes it. Your code does **not** delete or modify the legacy
curator. The two systems coexist throughout Phase B1.

### 1.4 What B1 must produce

A `WikiCurator` that can be called with `ingest(source, content)` and
produces a coherent, atomic update of 10-15 wiki pages plus one
`log.md` entry. The work is split across four parallel instances plus
a wave-2 integration step.

When Phase B1 is done, the following becomes true:

- `python -m jarvis.memory.wiki.cli ingest <file>` ingests a markdown
  source into the vault. CLI works end-to-end.
- A real ingest run produces a tree of valid wiki pages, validates
  against `schema.md`, writes a single log entry, never corrupts the
  vault even on LLM failures.
- A vault that the user has manually edited in Obsidian is respected:
  pages edited within the last 30 seconds are left untouched.
- The full test suite (unit + integration) passes.
- Nothing in the live Jarvis runtime (voice pipeline, brain manager,
  awareness layer) is touched. **Runtime integration is Phase B5, not B1.**

---

## Part 2 — Architecture of B1 (four instances + integrator)

```
                 ┌─────────────────────────────────────────┐
                 │           WikiCurator.ingest()           │
                 │           (built in wave 2)              │
                 └────────┬──────────┬─────────┬───────────┘
                          │          │         │
                  uses    │   uses   │  uses   │  uses
                          ▼          ▼         ▼
   ┌──────────────────────┐ ┌────────────────┐ ┌──────────────┐
   │ Instance A           │ │ Instance B     │ │ Instance C   │
   │ Page Model +         │ │ Vault Reader + │ │ Atomic       │
   │ Wikilink parser      │ │ Index builder  │ │ Writer       │
   └──────────────────────┘ └────────────────┘ └──────────────┘
                          ▲
                          │ used by
   ┌──────────────────────┴───────────────────────────────────┐
   │ Instance D                                                │
   │ LLM curator pipeline (Brain-Provider-agnostic)            │
   │ ─ builds the ingest prompt                                │
   │ ─ asks the Brain which pages to touch and what to write   │
   │ ─ returns a structured list of PageUpdate objects         │
   └───────────────────────────────────────────────────────────┘
```

**Why four instances and not three or five.** Each instance owns one
concern that another instance must not contaminate:

- **A** owns the *shape of a page*. Everyone else asks A "is this a valid
  page?" and "what does this wikilink resolve to?"
- **B** owns the *shape of the vault as a whole*. Everyone else asks B
  "what pages exist?" and "give me the current index."
- **C** owns *the only path that writes to disk*. Everyone else hands C
  a list of changes and trusts C to do the backup-write-validate dance.
- **D** owns the *LLM intelligence*. Everyone else treats D as a function:
  in goes a source + the current vault state, out comes a list of changes.

Three would force two concerns into one instance. Five would split a
natural concern artificially. Four is the smallest set that keeps each
instance below ~300 lines of production code.

---

## Part 3 — Conventions every instance must follow

These apply to all four instances. Wave-2 integration enforces them.

### 3.1 Output language (HIGHEST PRIORITY)

**Every artifact you produce is in English.** Code, comments, docstrings,
test names, log messages, exception messages, error strings, ADR text,
new markdown headings. The user's chat with the orchestrator stays in
German; the *artifacts* are English. See `CLAUDE.md` § "Output Language
Policy" for the full rule.

If you ever write `# Aktive Kontexte:` in production code, you have done
it wrong. (This happened in the A4 working-set work and is the only
review finding that came back twice.)

### 3.2 Brain-Provider abstraction (NOT Claude-only)

The user's Personal Jarvis is **multi-provider by design**. Their
current configured default is `gemini` (`primary = "gemini"` in
`jarvis.toml`). They may switch to `claude-api`, `openrouter`, `openai`,
`grok`, `ollama-local`, or anything else at any time, including by voice
command ("Jarvis, wechsel auf Gemini").

Your code MUST go through the existing `BrainProviderRegistry` or
`BrainManager`. **Never** hardcode `anthropic.Anthropic(...)`. **Never**
hardcode a specific model name. Read it from config:

```toml
# new config section, B1 introduces this
[memory.wiki.curator]
# If empty/commented out: takes brain.primary from the general config.
provider = ""               # leer = fall back to brain.primary
model = ""                  # leer = provider-default model
max_input_tokens = 8000
max_output_tokens = 2000
timeout_s = 90.0
```

The Awareness Verdichter (`jarvis/awareness/verdichter.py`) is your
reference pattern for "pick a brain via config, fall back gracefully".
Copy that pattern, do not invent a new one.

### 3.3 Schema as the source of truth

Anything `data/workspace/schema.md` says is binding. Specifically:

- Frontmatter keys, page types, section names, the `log.md` format, the
  `[[wikilink]]` syntax, the directory layout, the 500-line per-page
  cap, the rules about renames.
- If `schema.md` and this README disagree, `schema.md` wins. (If you spot
  a real disagreement, raise it with the integrator — do not silently
  diverge.)
- Read `schema.md` programmatically and feed it as the system prompt to
  the LLM in Instance D. Do not paraphrase it in code.

### 3.4 The vault is on disk

The runtime vault is at `<repo_root>/data/workspace/`. That directory
is **gitignored** (it holds the user's personal data). The templates
under `jarvis/memory/wiki/templates/` are versioned.

If the vault is empty on first run (no `schema.md` present), the
WikiCurator bootstraps it by copying the templates. Wave 2 owns this
bootstrap; instances A-D can assume the vault is already populated.

### 3.5 Async everywhere

Personal Jarvis is async end-to-end. Wiki I/O is sync-fast (markdown
file reads are a few milliseconds) but go through `aiofiles` or
`asyncio.to_thread` when called from an async context. Brain calls are
unconditionally async.

Never block the asyncio event loop with a synchronous file read or LLM
call. The `_safe_cwd` finding from the A4 review is exactly this trap
and we are not making it twice.

### 3.6 Tests are mandatory and use fakes, not mocks

Every public class/function in your instance gets a unit test. Every
inter-instance interaction gets a small integration test against a
**fake** of the other instance's interface — see § 12 for the Protocols.

Use `unittest.mock` only for the brain (the actual LLM call). All other
collaborators have hand-written Fakes. Reference: `tests/contract/` and
`jarvis/awareness/verdichter.py` (FakeBrain pattern).

### 3.7 Concurrent-edit safety (the user is editing in Obsidian)

User has explicitly chosen the **lock** strategy:

> If a vault file's modification time is within the last 30 seconds, the
> curator must skip it and try again on the next ingest. Never overwrite
> a file the user has just touched.

Instance C enforces this guard. Instances A, B, D do not need to handle
it directly, but they must not cache `mtime` across writes.

### 3.8 Aggressive write strategy (decision: this PR scope)

User has explicitly chosen the **aggressive** ingest strategy. That
means: every `BrainTurnCompleted`, every `EpisodeRecorded`, every
`MissionCompleted` event becomes an ingest candidate. **B1 does not wire
the bus subscriptions** — that is Phase B5. But Instance D must be
built so the LLM can refuse cheaply: when the source content is
"hallo", "danke", smalltalk, the curator returns *zero* page updates
without touching disk. The salience filter lives in the prompt, not in
heuristic code. This keeps B1 small and lets B5 wire it up later.

---

## Part 4 — The Protocols (binding inter-instance interface)

These three Protocols are the contract. Wave-2 integration tests
**will** verify them. Each instance implements the Protocol assigned to
it. Wave-2 wires them together via dependency injection.

```python
# jarvis/memory/wiki/protocols.py  ← Instance A creates this file

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WikiPage:
    """A single wiki page, parsed.

    Owned by Instance A. Instances B/C/D consume it read-only.
    Body is the verbatim markdown body after the closing '---'
    of the frontmatter. Sections is a parsed view (Summary,
    Facts, Relationships, Sources) computed lazily — None if
    the page does not match the schema.
    """
    path: Path                              # absolute path in the vault
    page_type: str                          # entity | concept | project | session | meta
    slug: str
    frontmatter: dict[str, str]
    body: str
    wikilinks: tuple[str, ...]              # outgoing [[targets]] in canonical form
    is_schema_valid: bool


@dataclass(frozen=True, slots=True)
class PageUpdate:
    """One proposed change to one page.

    Owned by Instance A (as a data type). Produced by Instance D.
    Consumed by Instance C.
    """
    target_path: Path                       # where the page lives (or will live)
    operation: str                          # create | update | rename | archive
    new_body: str                           # full new body (frontmatter + sections)
    rename_from: Path | None = None         # only set when operation == "rename"
    reason: str = ""                        # short human-readable why


@runtime_checkable
class PageRepository(Protocol):
    """Instance A's interface. Read-only over a path or a string.

    Implementations parse a markdown file (or string) into a WikiPage,
    extract wikilinks, validate against the schema. Pure functions,
    no disk writes, no LLM calls.
    """
    async def load(self, path: Path) -> WikiPage: ...
    async def parse(self, raw_markdown: str, path: Path) -> WikiPage: ...
    def render(self, page: WikiPage) -> str: ...
    def resolve_wikilink(
        self, link: str, vault_root: Path
    ) -> Path | None: ...   # None when broken


@runtime_checkable
class VaultIndex(Protocol):
    """Instance B's interface. Whole-vault view, read-only.

    Implementations scan the vault, build an in-memory index of
    {slug -> path}, list pages by type, render index.md, append
    log.md. Does NOT modify entity/concept/project pages — only
    index.md and log.md.
    """
    async def scan(self, vault_root: Path) -> None: ...
    def pages_by_type(self, page_type: str) -> list[WikiPage]: ...
    def find_by_slug(self, slug: str) -> WikiPage | None: ...
    def backlinks_to(self, slug: str) -> list[WikiPage]: ...
    async def render_index_md(self) -> str: ...
    async def append_log_entry(
        self, verb: str, subject: str, pages_touched: list[str],
        source: str, summary: str,
    ) -> None: ...


@runtime_checkable
class AtomicWriter(Protocol):
    """Instance C's interface. The only path that writes pages to disk.

    Implementations: receive a list of PageUpdates, take a vault
    snapshot (tar to data/backups/wiki-<ts>.tar.gz), apply updates
    via tempfile+rename, validate the resulting pages via the
    PageRepository, roll back on any failure.

    Honors the 30-second concurrent-edit lock: any update whose
    target path was modified in the last 30s is skipped and reported
    as a soft failure.
    """
    async def apply(
        self, updates: list[PageUpdate], *,
        repo: PageRepository,
    ) -> WriteResult: ...


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Returned by AtomicWriter.apply()."""
    applied: list[Path]                     # pages that were successfully written
    skipped_due_to_recent_edit: list[Path]  # the 30s-lock case
    failed_validation: list[Path]           # pages that the writer rolled back
    backup_path: Path                       # the tar of the pre-write state


@runtime_checkable
class CuratorLLM(Protocol):
    """Instance D's interface. The intelligence layer.

    Given a source (some new content to ingest) and a snapshot of
    the current vault, returns a list of PageUpdates. May return
    an empty list when salience filtering decides nothing should
    change. Never touches disk; never calls the writer.
    """
    async def propose_updates(
        self, source_content: str, source_label: str,
        *,
        repo: PageRepository, vault: VaultIndex,
    ) -> list[PageUpdate]: ...
```

The four files that hold these implementations:

```
jarvis/memory/wiki/
├── __init__.py                 ← exists (B0)
├── templates/                  ← exists (B0)
├── protocols.py                ← Instance A creates
├── page.py                     ← Instance A
├── wikilink.py                 ← Instance A
├── vault_index.py              ← Instance B
├── log_writer.py               ← Instance B
├── index_builder.py            ← Instance B
├── atomic_writer.py            ← Instance C
├── backup.py                   ← Instance C
├── curator_llm.py              ← Instance D
├── prompt.py                   ← Instance D
├── curator.py                  ← Wave 2 (the orchestrating WikiCurator)
└── cli.py                      ← Wave 2 (the `python -m` entry)
```

---

## Part 5 — Instance briefings (read only the one you are assigned)

### Instance A — Page Comprehender (Wikilink + Page Model)

**You own:** `protocols.py`, `page.py`, `wikilink.py`.

**Deliverables:**

1. The Protocols listed in § 4. Write them in `protocols.py` exactly as
   specified. The other three instances depend on this file existing
   before they can start writing imports.
2. A `WikiPage` parser/renderer (`page.py`):
   - Parse a markdown file with YAML frontmatter into a `WikiPage`.
   - Tolerate missing/malformed frontmatter — return `is_schema_valid=False`,
     do **not** raise.
   - Detect page type from frontmatter `type:` key; cross-check with
     directory (entities/ vs concepts/ vs projects/ vs sessions/). If
     mismatch, `is_schema_valid=False`.
   - Section parser: split body by `## ` headings. Recognize the canonical
     section names from `schema.md` per page type.
   - Render: round-trip a `WikiPage` back to a markdown string that, when
     re-parsed, produces the same `WikiPage`. **Round-trip stability is a
     hard requirement** — write a test for it.
3. A wikilink parser (`wikilink.py`):
   - Recognize `[[slug]]`, `[[entities/slug]]`, `[[slug|alias]]`, escape
     `\[[not a link]]`.
   - Resolve `[[slug]]` against the vault: if `entities/slug.md` exists,
     return its path. If multiple matches across `entities/`, `concepts/`,
     `projects/`, prefer the explicit-prefix form; if both unambiguous and
     ambiguous matches exist for `[[slug]]`, return None (broken — caller
     decides).
   - Extract all wikilinks from a body as a `tuple[str, ...]` for
     `WikiPage.wikilinks`. Order preserved, duplicates allowed.

**Tests:** `tests/unit/memory/wiki/test_page.py`,
`tests/unit/memory/wiki/test_wikilink.py`. At minimum:

- Round-trip: parse → render → parse produces equal `WikiPage`.
- Missing frontmatter → `is_schema_valid=False`, no raise.
- All four wikilink forms recognised, escaped form ignored.
- Ambiguous link returns `None`.
- 30+ test cases total across both files.

**Hard negatives:**

- ❌ Do not write to disk. Pure functions only.
- ❌ Do not import from `vault_index`, `atomic_writer`, `curator_llm`.
  They depend on you, not the other way around.
- ❌ Do not invent new frontmatter keys. Use only what `schema.md` lists.
- ❌ Do not normalise whitespace inside the body — round-trip stability
  matters. Strip only trailing whitespace at file end.

**Size estimate:** 250-350 lines of production code + 250 lines of tests.

---

### Instance B — Vault Overview (VaultIndex + log + index.md)

**You own:** `vault_index.py`, `log_writer.py`, `index_builder.py`.

**Deliverables:**

1. `VaultIndex` (`vault_index.py`):
   - `scan(vault_root)`: walk `entities/`, `concepts/`, `projects/`,
     `sessions/`. Skip `_archive/` and `attachments/`. Skip files that
     do not match `*.md` or that fail to parse as a valid `WikiPage`.
   - In-memory index: `{slug -> WikiPage}` plus a reverse index for
     wikilinks (`{slug -> [pages that link to it]}`).
   - `pages_by_type(type)`, `find_by_slug(slug)`, `backlinks_to(slug)`.
   - **Rescan-on-stale**: if a file's mtime is newer than the last scan
     time, transparently re-parse that file on next `find_by_slug` /
     `pages_by_type` call. This keeps the index honest when the user
     edits in Obsidian.
2. `LogWriter` (`log_writer.py`):
   - `append_log_entry(verb, subject, pages_touched, source, summary)`
     — append one entry to `log.md` in the format documented in
     `schema.md` section "The `log.md` File".
   - Pages-touched values are rendered as `[[entities/slug]]` style.
   - Timestamp uses local time, format `[YYYY-MM-DD HH:MM]`.
   - Append is atomic: write to `log.md.tmp`, then `os.replace`.
   - **Never edit existing entries.** Append-only is a hard contract.
3. `IndexBuilder` (`index_builder.py`):
   - `render_index_md()` produces the new `index.md` content from the
     current vault state. Groups by type (Entities, Concepts, Projects,
     Sessions). Stable sort (alphabetical by slug within each group).
   - Honors any human-written preamble at the top of the existing
     `index.md` — preserves everything up to the first `## Entities`
     heading. Below that, the lists are regenerated.
   - Output respects the 200-line `index.md` cap from `schema.md`.

**Tests:** `tests/unit/memory/wiki/test_vault_index.py`,
`tests/unit/memory/wiki/test_log_writer.py`,
`tests/unit/memory/wiki/test_index_builder.py`. At minimum:

- Scan handles missing directories without error.
- Backlinks index updated when a page's wikilinks change.
- Stale-rescan picks up a manually-edited file.
- `log.md` append is atomic across simulated crashes (kill mid-write,
  log stays valid).
- `index.md` regeneration preserves the human preamble.

**Hard negatives:**

- ❌ Do not modify `entities/`, `concepts/`, `projects/`, `sessions/`
  pages. Your write surface is only `log.md` and `index.md`.
- ❌ Do not import from `atomic_writer` or `curator_llm`.
- ❌ Do not cache `mtime` across writes — the 30-second concurrent-edit
  lock relies on fresh `mtime` reads in Instance C.

**Size estimate:** 300-400 lines of production code + 300 lines of tests.

---

### Instance C — Write Safety (AtomicWriter + Backup)

**You own:** `atomic_writer.py`, `backup.py`.

**Deliverables:**

1. `AtomicWriter` (`atomic_writer.py`):
   - `apply(updates, *, repo)`: takes a list of `PageUpdate` and a
     `PageRepository`. Performs **the only disk-write path in B1.**
   - Step 1 — **Concurrent-edit lock**. For each update, check
     `target_path.stat().st_mtime`. If within 30 seconds of `time.time()`,
     skip the update and add the path to `WriteResult.skipped_due_to_recent_edit`.
   - Step 2 — **Backup**. Tar the entire vault to
     `data/backups/wiki-<YYYYMMDDHHMMSS>.tar.gz`. Do this **once per
     `apply` call**, even when 15 pages will be touched. Skip backup if
     no updates survived step 1.
   - Step 3 — **Write**. For each surviving update, write to
     `<target>.tmp`, then `os.replace`. On Windows this is atomic only
     when source and target are on the same drive — assert that.
   - Step 4 — **Validate**. After writing, re-parse each written page
     via `repo.parse`. If `is_schema_valid` is False or the page raises
     during parse, roll back **that single page** by restoring it from
     the just-taken backup. The other pages stay applied.
   - Step 5 — **Return** a `WriteResult` summarising what happened.
2. `BackupManager` (`backup.py`):
   - `snapshot(vault_root) -> Path`: tar the vault contents (excluding
     `_archive/`, `attachments/` — they may be large) to
     `data/backups/wiki-<ts>.tar.gz`. Return the backup path.
   - `restore(backup_path, target_path)`: extract one specific file
     from a backup. Used by Step 4 rollback.
   - **Backup rotation:** keep the 10 most recent backups, delete older
     ones. Run rotation at the end of every `apply` call.

**Tests:** `tests/unit/memory/wiki/test_atomic_writer.py`,
`tests/unit/memory/wiki/test_backup.py`. At minimum:

- Happy path: 5 updates → 5 files written, backup created, log not
  touched (LogWriter is B's job, not yours).
- Concurrent-edit lock fires: tmpfs touch a file 5s before apply,
  that page is skipped, others applied.
- Validation rollback: write a deliberately broken page, verify it is
  restored from backup, the other valid pages stay applied.
- Crash mid-write does not corrupt the vault (simulate via
  `os.replace` raising).
- Backup rotation: 11 backups produced, oldest deleted.

**Hard negatives:**

- ❌ Do not assume single-file writes are atomic on Windows without
  same-drive tempfile. Assert it; raise a clear error otherwise.
- ❌ Do not delete files via the rollback path you did not also write.
  Rollback only restores files in the current `apply` call.
- ❌ Do not call the LLM. You receive `PageUpdate` objects with
  already-rendered `new_body`. Your job is to put them on disk safely.
- ❌ Do not import `vault_index` or `curator_llm`. You depend only on
  Instance A's `PageRepository`.

**Size estimate:** 250-300 lines of production code + 350 lines of tests
(the test surface is wide because of the rollback paths).

---

### Instance D — AI Brain (CuratorLLM)

**You own:** `curator_llm.py`, `prompt.py`.

**Deliverables:**

1. `CuratorLLM` (`curator_llm.py`):
   - `propose_updates(source_content, source_label, *, repo, vault)`:
     decides which pages to touch and what to write.
   - Build the prompt (see § Prompt design below).
   - Call the configured Brain provider via the existing
     `BrainProviderRegistry`. **Do NOT hardcode anthropic or any
     provider.** The pattern is in `jarvis/awareness/verdichter.py:80-130`
     — copy it.
   - The LLM returns a structured JSON list of proposed updates. Parse
     it into `list[PageUpdate]`. On JSON parse failure or LLM timeout,
     return `[]` and log a warning. Never raise — the orchestrator must
     keep running.
   - Salience filter is in the prompt: the LLM is told to return an
     empty list when the content is smalltalk, ack-only, or
     content-free. No heuristic salience scorer in code.
2. `prompt.py`:
   - `build_system_prompt(schema_md, vault_summary)`: load `schema.md`
     verbatim, append a compact summary of the existing vault structure
     (page counts per type, recent log entries). System prompt total
     stays under 8 000 tokens.
   - `build_user_prompt(source_label, source_content, vault_index)`:
     The user message describes the source and asks the LLM to propose
     updates in the documented JSON schema. Include the slugs of the
     most likely affected pages (top 10 by simple keyword overlap) so
     the LLM does not have to "search" the vault from scratch.

**Brain provider integration:**

Add this config section in `jarvis.toml` (Wave 2 will move it to the
template; here you just need to read it):

```toml
[memory.wiki.curator]
provider = ""               # "" = fall back to brain.primary
model = ""                  # "" = the provider's default model
max_input_tokens = 8000
max_output_tokens = 2000
timeout_s = 90.0
```

When `provider` is empty, read `brain.primary` and use that. When
`model` is empty, use the provider's `model` field from
`brain.providers.<name>`. Wrap the brain call in
`asyncio.wait_for(..., timeout=cfg.timeout_s)`. On timeout, return `[]`
and log.

**Prompt design (binding):**

```
SYSTEM:
  <full content of data/workspace/schema.md>

  Current vault summary:
    Entities: <count>     (latest 5 slugs: …)
    Concepts: <count>     (latest 5 slugs: …)
    Projects: <count>     (latest 5 slugs: …)
    Recent log entries (last 3):
      [date] verb | subject — …

  Your task: ingest a source. Touch 10-15 pages typically. Return JSON
  matching this schema:
    [{"target": "entities/alex.md",
      "operation": "create" | "update" | "rename" | "archive",
      "new_body": "<full markdown body>",
      "rename_from": null | "entities/old-slug.md",
      "reason": "<one sentence>"}]

  If the source is smalltalk, ack-only, or content-free,
  return [] with no commentary.

  Hard rules:
    - Never break a [[wikilink]].
    - Every page you create or update must conform to schema.md.
    - Never touch _archive/ or attachments/.
    - Never store secrets.

USER:
  Source label: <source_label, e.g. "BrainTurnCompleted 2026-05-11 19:42">
  Source content:
  <verbatim source_content>

  Most likely affected pages (top 10 by keyword overlap):
    - entities/alex.md
    - concepts/awareness-layer.md
    …

  Return the JSON list now.
```

**Tests:** `tests/unit/memory/wiki/test_curator_llm.py`,
`tests/unit/memory/wiki/test_prompt.py`. At minimum:

- Prompt builder produces output matching the documented schema (string
  contains check).
- LLM call uses the configured provider — patch
  `BrainProviderRegistry.instantiate` and assert it was called with
  the right name. (Note: an earlier draft of this briefing said
  `get_provider`; the real API is `instantiate` — verified at
  `jarvis/brain/provider_registry.py:45`. Instance D caught and
  worked around this during the initial build.)
- Empty source content → empty `list[PageUpdate]` without LLM call.
- LLM returns malformed JSON → empty list + warning logged.
- LLM timeout → empty list + warning logged.
- 20+ test cases.

**Hard negatives:**

- ❌ Do not write to disk. You return `list[PageUpdate]`; the writer
  decides what to do with them.
- ❌ Do not hardcode a brain provider, model name, or API endpoint. Read
  from config.
- ❌ Do not import `atomic_writer`. The writer depends on you (the
  curator-llm returns PageUpdate objects); never the reverse.
- ❌ Do not parse the schema yourself. Pass `schema.md` verbatim to the
  LLM as part of the system prompt — the schema is the contract, you
  are not re-implementing it.

**Size estimate:** 300-400 lines of production code + 300 lines of tests.

---

## Part 6 — Wave 2: integration (after all four instances finish)

I (the orchestrator) will:

1. Pull the four instance branches.
2. Resolve any Protocol drift (instances must update if their stub
   diverged from the agreed Protocol).
3. Write `curator.py`:
   ```python
   class WikiCurator:
       def __init__(self, repo, vault, writer, llm): …
       async def ingest(self, source_content: str, source_label: str) -> WriteResult:
           updates = await self.llm.propose_updates(...)
           if not updates: return WriteResult(applied=[], …)
           result = await self.writer.apply(updates, repo=self.repo)
           await self.vault.append_log_entry(verb="ingest", …)
           return result
   ```
4. Write `cli.py` for `python -m jarvis.memory.wiki.cli ingest <file>`.
5. Write the wave-2 integration tests:
   - `tests/integration/memory/wiki/test_curator_ingest_e2e.py` — real
     ingest of a fake source against a temp vault, end-to-end, with a
     mocked LLM that returns a fixed `list[PageUpdate]`.
   - `tests/integration/memory/wiki/test_curator_concurrent_edit.py` —
     user-edits-in-Obsidian race, the 30-second lock fires.
   - `tests/integration/memory/wiki/test_curator_rollback.py` — LLM
     returns a page that fails validation, rollback restores the
     unchanged version.
6. Commit, run the full test suite, write a B1 completion log entry to
   `data/workspace/log.md`, update `CLAUDE.md`'s awareness-phase table
   if relevant.

**Wiring into the live runtime is NOT part of wave 2.** That is Phase B5.

---

## Part 7 — Glossary (plain language)

- **Vault** — the folder `data/workspace/` containing the wiki.
- **Page** — one markdown file inside the vault. Has a YAML
  frontmatter header and a markdown body.
- **Slug** — the kebab-case ASCII filename of a page (`personal-jarvis-maintainer`).
- **Wikilink** — a `[[some-slug]]` reference inside one page pointing
  to another page. Like a Wikipedia internal link.
- **Backlink** — the reverse view: pages that link *to* a given page.
- **Frontmatter** — the YAML block at the top of a markdown file
  between `---` markers. Holds structured metadata (type, slug,
  aliases, created/updated dates).
- **Ingest** — taking one piece of new information and updating
  whatever pages need updating.
- **Curator** — the LLM acting as wiki editor.
- **Schema** — the file `data/workspace/schema.md` that defines all
  the rules every page and every edit must obey.
- **Atomic write** — file written via `tempfile + rename` so a crash
  in the middle never leaves a half-written file.
- **Brain provider** — Personal Jarvis's pluggable LLM backend
  (Gemini, Claude, OpenAI, Grok, Ollama, …). Configurable by user.
- **Aggressive ingest** — the user's chosen strategy for B1: every
  meaningful event is a candidate for wiki updates. Salience filtering
  happens inside the LLM prompt, not in heuristic code.

---

## Part 8 — Reference files to read before you start

Open these in order. The instance-specific ones are listed first.

**All instances must read:**
1. `data/workspace/schema.md` — the binding contract for everything you build.
2. `docs/adr/0013-knowledge-wiki-architecture.md` — the architectural why.
3. `CLAUDE.md` — Output Language Policy (English only for new code),
   plus the awareness layer context.
4. `jarvis/awareness/verdichter.py` — the reference pattern for "call
   a configured Brain provider, fall back gracefully, handle timeouts."

**Instance A** also reads:
- `jarvis/memory/recall.py:22-25` — the `_sanitize_fts5_query` pattern
  for tolerating user-typed input safely.

**Instance B** also reads:
- `jarvis/memory/recall.py:299-386` — for the SQLite-side awareness_episodes_fts
  pattern, which is how a future MD-FTS shim could look (not in B1 scope).
- `data/workspace/log.md` — the existing format, so you append in style.

**Instance C** also reads:
- `jarvis/core/self_mod/writer.py` (if present) — the existing atomic
  writer pattern with backup-validate-restore. Reuse the philosophy,
  not necessarily the code.
- `scripts/wiki_migrate_v0_to_v1.py:make_backup` — the existing tar pattern
  for `data/backups/wiki-*.tar.gz`.

**Instance D** also reads:
- `jarvis/awareness/prompts.py` — for the system-prompt-building pattern.
- `jarvis/brain/manager.py:provider_registry` — for how to fetch a
  brain provider via config.
- `jarvis/brain/registry.py` — the BrainProviderRegistry class itself.

---

## Part 9 — When you are stuck

- **Schema question:** check `schema.md` first. If it does not answer,
  ask the integrator before guessing.
- **Protocol question:** check `protocols.py`. If you genuinely need to
  change a Protocol, raise it — do not silently diverge.
- **Brain provider question:** look at `jarvis/awareness/verdichter.py`.
  Same pattern, transplant it.
- **"Should I add X feature?"** — almost certainly no. B1 is scoped
  tight. If it is not in your instance briefing, it is not in B1.
- **"This existing file looks wrong":** flag it, do not modify it. The
  awareness work has been actively edited in this worktree by other
  agents; touching it risks merge headaches.

---

## Part 10 — Definition of done (per instance)

You are done when:

- [ ] Every file listed under "You own" exists.
- [ ] Every Protocol method you implement has at least one test.
- [ ] `pytest tests/unit/memory/wiki/` runs to completion with your
      tests included.
- [ ] No `print()` calls left in production code (use the existing
      logger pattern: `import logging; log = logging.getLogger(__name__)`).
- [ ] No German strings in new code or new markdown headings (Output
      Language Policy).
- [ ] No hardcoded brain provider or model.
- [ ] Your branch is `impl/wiki-memory-b1-<instance>` (e.g.
      `impl/wiki-memory-b1-page` for Instance A).
- [ ] A short hand-off note in `docs/phase-b1-wiki-curator/handoff-<instance>.md`
      describing what you built, any open questions, and any deviation
      from this briefing.

---

## Part 11 — Definition of done (wave 2)

Wave 2 is done when:

- [ ] `WikiCurator.ingest(...)` produces a valid update against a real
      vault.
- [ ] `python -m jarvis.memory.wiki.cli ingest <file>` runs end-to-end.
- [ ] Three integration tests in `tests/integration/memory/wiki/` pass.
- [ ] Full repo test suite stays green (no regressions in awareness or
      brain tests).
- [ ] One commit on `impl/wiki-memory` of the form
      `feat(memory/wiki): Phase B1 — WikiCurator pipeline`.
- [ ] `log.md` has one new entry, format-correct.
- [ ] `CLAUDE.md` mentions B1 done in the right section.

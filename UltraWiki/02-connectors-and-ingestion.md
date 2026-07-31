# 02 · Connectors & the write path

## The connector contract (deliberately minimal)

A connector's whole job is: *"tell me what changed, as uniform raw items."*
It is a Python protocol registered through the existing Jarvis plugin
entry-point mechanism (new group: `uw_connector`):

```python
class UWConnector(Protocol):
    id: str                      # "obsidian-vault", "whatsapp-export", ...
    auth: AuthKind               # none | local-path | export-file | oauth2 | apikey
    capabilities: Capabilities   # backfill: bool
                                 # incremental: push | watch | cursor | none
                                 # deletes: bool

    def backfill(self, ctx, checkpoint=None) -> AsyncIterator[RawItem]: ...
    def incremental(self, ctx, cursor) -> AsyncIterator[RawItem]: ...
```

Hard rules, all in service of "anyone can write one, and it works everywhere":

1. **A connector yields `RawItem`s and nothing else.** It never touches the
   database, never embeds, never calls an LLM. Pure I/O — testable from
   fixtures with no network, no credentials, no models.
2. **`AsyncIterator`, not a list.** Backfills can be six-figure item counts;
   streaming lets the runtime own memory, batching, and checkpoints.
3. **The `capabilities` declaration drives the scheduler.** Connector authors
   never write scheduling logic; the runtime decides watch vs poll vs push
   from the declaration.
4. **Every `RawItem` carries `external_id`, `timestamp_utc`, and `permalink`.**
   Idempotency and deep-linking are non-negotiable from item one.

## Source families (v1 scope per D-4)

| Family | Examples | Backfill | Freshness | Notes |
|---|---|---|---|---|
| **Local artifacts** | Obsidian vault, chosen folders, Jarvis conversations, normal-wiki pages | read files | file watcher | Zero auth, zero network — the proving ground for the whole pipeline, and identical on every OS. |
| **Export imports** | WhatsApp "Export chat", Google Takeout, Instagram/Amazon/Netflix/Spotify exports | file import | re-import | The lawful, ban-risk-free way to get history out of closed platforms (GDPR-style data portability). Per-service parsers with fixture tests; an export re-import upserts idempotently. |
| **OAuth APIs** | Gmail, Google Calendar, Google Drive, GitHub | API paging | push where offered, else cursor polling | Reuses the existing `marketplace/` OAuth machinery and credential store. |
| **Jarvis plugin/CLI bridge** | anything already connected to Jarvis: MCP tools, marketplace plugins, connected CLIs | tool-defined | cursor polling | One generic connector that adapts any Jarvis-side integration exposing a "list items since X" capability into the `RawItem` stream. This is how "the whole life" scales without writing one bespoke connector per tool. |
| **Community connectors** | anything else | — | — | The contract + fixture harness is public; third-party connectors install like any Jarvis plugin. Connectors that would breach a platform's terms of service stay out of the core repo. |

## The staged write pipeline (D-7) — a state machine in the database

Ingestion is **not** a function chain. Each item carries its state as a
column; workers perform exactly one transition and commit. A crash or deploy
mid-backfill restarts to an identical end state — no duplicates, no gaps, no
lost work — and one poisoned item blocks nothing.

```
captured ──► keyword_indexed ──► embedded ──► distilled ──► entity_linked
   │               │                 │             │              │
   └── retrying / failed (attempt_count, next_retry_at, dead-letter) ──┘
```

- **captured → keyword_indexed** (instant, no model): the raw body lands in
  the store and the full-text index in the same transaction. *Everything is
  findable by keyword within seconds of capture* — this stage alone already
  beats the normal wiki's coverage.
- **keyword_indexed → embedded** (async, cheap): the item's text — at this
  stage still raw or lightly normalized — is embedded with the chosen
  embedding model and written to `uw_documents`.
- **embedded → distilled** (async, the expensive stage): the chosen
  distillation LLM turns the item into normalized documents — a searchable
  question, a summary, the resolution, mentioned people/places/systems, plus
  notable **bursts** (high-signal fragments embedded separately with the
  parent topic prepended as context). Distilled documents replace the raw
  embedding as the primary semantic representation.
- **distilled → entity_linked**: extracted mentions are resolved against
  `uw_entities` (doc 05); episodic facts land in `uw_events` with absolute
  time ranges.

**Priority, not FIFO.** The distillation queue is ordered by value density:
new before old, information-rich before smalltalk (term-rarity and length
heuristics, reactions/stars as social boosts), sources the user actually
queries before dormant ones. During a ten-year backfill the system is usable
from minute one and visibly gets smarter every hour.

**Determinism economics.** Distillation output is cached on
`(content_hash, distill_prompt_version, model)` — identical input is never
paid for twice, and re-runs after crashes converge. When the distillation
prompt improves, a version bump re-enriches **incrementally in the
background**; there is never a throw-the-database-away re-import.

## Staying fresh (D-11)

- **Local sources**: file watchers fire within seconds (reusing the existing
  wiki-watcher pattern); a debounce collapses editor save storms.
- **Push where the platform offers it** (e.g. socket/webhook APIs): events are
  acknowledged immediately, deduplicated on the platform's stable event id,
  and enqueued. A thread-changing event re-fetches and re-writes the *whole*
  thread as one row, so stored conversations always reflect the complete
  current state.
- **Cursor polling for everything else**, per-source interval, adaptive
  (busy sources poll faster).
- **A per-source "Sync now" button** in the UI for impatience and debugging.
- **A nightly reconcile run** per source walks a recent window and repairs
  whatever push/watch missed — webhooks arrive twice, never, or out of order,
  and a desktop app has no public endpoint; polling is the correctness
  backstop, push is the latency bonus.
- **Deletions propagate.** A source-side deletion detected by event or
  reconcile sets a tombstone (`deleted_at`) and cascades to every derived
  document, embedding, and event. The system must never keep answering from
  content the user deleted at the source.

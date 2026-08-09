# 04 · Configuration & UI

## The UltraWiki settings section (D-2)

UltraWiki gets its own configuration section, modeled on the API-Keys section:
one place where the user sees every capability slot, what is currently
selected, whether it is healthy, and what the alternatives are. **Jarvis
bundles nothing and provisions nothing** — this is open source; every slot is
filled by something the user brings. Every slot always offers **at least one
local and one cloud option**:

| Slot | Local option | Cloud option | Switch cost |
|---|---|---|---|
| **Storage** | SQLite file (created automatically under the data dir) | PostgreSQL connection string — any host the user has (own server, Supabase, Neon, RDS, …) | export/import migration, guided |
| **Embedding model** | current multilingual local model via Ollama (`qwen3-embedding:4b`; one-time download) | any configured embedding-capable provider key (e.g. Gemini Embedding, OpenAI, Voyage, Mistral) | **background rebuild** — deliberate, confirmed; search stays up throughout (D-3) |
| **Distillation model** | local model via Ollama | any configured chat-capable provider key | none (applies to new work; re-enrichment optional) |
| **Rerank (optional)** | local cross-encoder | rerank-capable provider | none |

Slot rules:

- **Capability-gated, never name-gated.** A provider qualifies for a slot by
  declaring the capability (embeddings, rerank, chat), not by being a specific
  brand — any present or future provider key the user owns can fill a slot.
  Model identifiers shown in the UI come from the provider's own catalog.
- **Credentials flow through the existing secret chain** (OS keyring → ENV →
  `.env` → local file). Nothing lands in config files or the repo. Connection
  strings are treated as secrets too.
- **Health is visible.** Each slot shows a live status (reachable / key
  missing / quota exhausted) with a one-click test, mirroring the API-Keys
  section's pattern. A dead slot degrades honestly (doc 01, principle 6) and
  says so here first.
- **Headless-safe.** The whole section is REST-backed, so every choice is also
  scriptable via the `jarvis` CLI on a server without a browser.

## The activation wizard (D-3, D-6)

Switching the Wiki section to Ultra mode the first time runs a short wizard —
the one moment where the semi-permanent choices are made consciously:

1. **Storage** — default: local SQLite file (zero setup). Advanced: paste a
   Postgres connection string.
2. **Embedding model** — the wizard lists the qualifying options from what the
   user actually has (installed Ollama models, configured provider keys) and
   marks a recommendation, stating the trade-off plainly: *cloud = higher
   quality and no download, but your text goes to the provider; local =
   private and offline, one-time model download, needs some hardware.* The
   choice is explicit because changing it later means re-embedding everything.
3. **Distillation model** — same option pattern; the wizard suggests matching
   the embedding privacy stance (a user who chose local embeddings for privacy
   is defaulted to local distillation, and vice versa) but the two are
   independently overridable.
4. **First sources** — enable the zero-auth local sources immediately
   (normal-wiki vault, Jarvis conversations, chosen folders); connect OAuth
   and export sources now or later.
5. **Areas (D-12)** — the wizard creates a default area and offers the
   starter set (e.g. Work / Private); each connected source is assigned to
   one or more areas.

The wizard ends by kicking off the staged import (D-7) and switching the mode.
Everything chosen here remains editable in the settings section afterwards —
the embedding model behind an "are you sure, this re-embeds N documents,
estimated cost/time X" gate that runs the re-embed as a background job while
the old index keeps serving until the new one is complete.

## The mode switch (D-5, D-9)

- A prominent Normal / Ultra switch lives at the top of the Wiki section.
  Exactly one mode is active; the active one owns capture and answering.
- Switching to Ultra: normal wiki keeps its files; Ultra ingests them as a
  source and takes over. Switching back: Ultra pauses (store untouched),
  normal wiki resumes. Both directions are instant, safe, and reversible;
  neither ever deletes anything.
- If Ultra is active but a required slot is dead (e.g. the only embedding key
  is depleted), the section banners the problem, answers what it still can
  (keyword + entity + time lists need no external provider), and links to the
  settings slot to fix it. It does not silently flip modes.

## Ultra-mode UI surfaces

- **Ask view** — the question box; streamed cited answer; every citation is a
  permalink that opens the original (the Obsidian note, the mail, the chat
  export line). Filters: area, source, time range.
- **Sources view** — the connected-source list with per-source state: last
  sync, backlog counts per pipeline stage (captured / embedded / distilled),
  "Sync now", pause, area assignment, disconnect (with a data-retention
  choice: keep or purge ingested rows).
- **Import progress** — during a backfill, one honest progress surface:
  items captured, keyword-searchable now, distillation backlog, current
  spend estimate for cloud slots.
- **People & confirmations** — the entity list with profiles, and the
  confirmation queue for uncertain identity matches (doc 05). A small badge
  on the Wiki nav shows pending confirmations.
- **Memory map** — the existing graph visualization gets an Ultra-mode
  equivalent rendering entities and their linked sources instead of wiki
  pages.

All UI strings are i18n keys with English source text; the user-visible agent
name follows the dynamic wake-word brand rule.

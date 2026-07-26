# 08 · Universal ingestion — everything, from every platform, everywhere

The promise this document makes concrete: **whatever a person's life is stored
in, UltraWiki can take it in** — a platform we ship a reader for, a platform we
have never heard of, a folder of photos, a voice note, a decade-old archive.

## The universality constraint (read this first)

The EU right to data portability is **not** the foundation, and no design here
may assume it. A downloader in São Paulo, Lagos, Jakarta or Ohio has to reach
the same result as one in Berlin.

What is genuinely universal is something else, and it is enough:

1. **The platform's own export button.** WhatsApp's "Export chat", Google
   Takeout, Instagram's "Download your information", Apple's Data & Privacy,
   TikTok, X, Spotify, Amazon — every one of these is a product feature that
   ships worldwide. Companies build one export pipeline, not twenty-seven
   jurisdictional variants. A legal right was often the reason it exists; it is
   not what makes it reachable.
2. **The device.** Photo libraries, phone backups, mail stores, download
   folders, chat exports already sitting on disk. No account, no network, no
   jurisdiction.
3. **Open APIs.** The same OAuth endpoint answers in every country.
4. **Whatever is left** — reached by a connector the user configures
   themselves, so a service nobody here has heard of is still connectable
   without shipping code for it.

Where a statutory right does exist it is offered as an *additional* route in
the platform guide, never as the only one.

## What already works (2026-07-26)

* **Local artifacts** — Obsidian vaults, chosen folders, assistant
  conversations, the built-in wiki.
* **Export files** — `export-import` detects and streams mbox, eml, iCalendar,
  vCard, WhatsApp chat exports (bracket and dash families, any UI language),
  CSV/TSV, JSONL, JSON, PDF, HTML, Markdown and plain text, including entries
  inside ZIP archives, without extracting anything to disk.
* **Connected services** — fourteen pull adapters: GitHub, Slack, Gmail,
  Google Calendar, Google Drive, Notion, Asana, Linear, Todoist, ClickUp,
  Dropbox, Airtable, Discord, Telegram.

## The four gaps this design closes

**G1 — Photos, voice notes and video are dropped.** `extract.py` correctly
reports "an image holds no text", and nothing acts on that. Jarvis already
owns speech recognition and vision-capable brains; neither is reachable from
ingestion.

**G2 — Three extractors, and the best one is orphaned.**
`jarvis/ultrawiki/extract.py` reads ten formats (Office, OpenDocument, EPUB,
RTF, …), is tested, and **is not called by a single reader**. The folder walk
uses `document_text.py` (three formats); `export_import.py` carries its own
inline PDF path. The same file therefore reads differently depending on which
door it arrived through.

**G3 — Half of Google Takeout is refused.** Takeout offers `.zip` and `.tgz`.
`.tgz` is rejected by design (a compressed tar has no central directory, so
sorted-order traversal and cheap skipping are impossible). The user who clicks
the default is left holding a file we will not open.

**G4 — Nothing for a service with neither an adapter nor an export file.**

---

## Package A — one reader for everything

### A1 · Extractor coverage

`extract.py` becomes the single extraction service and grows:

* **tar / tar.gz / tgz** as a container kind, walked as a *stream* in the order
  the archive stores entries. The checkpoint contract states this honestly:
  tar resume is archive-granular, not entry-granular. A re-read upserts as
  `unchanged`, so the cost is repeated work, never duplicates.
* **A structured "no text, but enrichable" verdict.** Today an image returns
  `ok=False` plus a sentence. It additionally returns `media_kind`
  (`image` | `audio` | `video`) and a `MediaRef` — enough for a later stage to
  open the same bytes again. `content_missing` already distinguishes "could
  not extract" from "nothing to extract"; media is now a third case:
  *not extractable as text, extractable as meaning.*

`extract.py` itself stays synchronous, stdlib-plus-pypdf, and **never calls a
model**. It says what a file is and hands back a locator.

### A2 · Every reader through the one extractor

`local_folder`, `export_import` and the adapters route file bytes through
`extract_text`. `document_text.py` becomes a thin deprecated shim delegating to
it, so no caller breaks and no fourth copy appears. Immediate effect: the
folder walk gains Excel, OpenDocument, EPUB and RTF; the export import gains
Word, Excel, PowerPoint and EPUB — without either growing its own parser.

### A3 · The frugal enrichment lane

A new pipeline stage, **off the hot path and behind the existing stages**:

```
captured ─► keyword_indexed ─► embedded ─► distilled ─► entity_linked
                 │
                 └─► enriched (media only, lowest priority, own worker)
```

* An item whose extraction returned a media verdict is stored immediately with
  its filename, timestamp, folder and any sidecar metadata (EXIF date, GPS,
  album, chat partner) — **so it is findable by keyword the moment it lands**,
  before any model has seen it.
* A separate low-priority worker resolves the `MediaRef`, and:
  * **image** → one description from a vision-capable brain, plus any text the
    image carries. Gated on the `supports_vision` capability, never on a
    provider name (AP-21).
  * **audio / video** → a transcript through the configured STT chain. Gated
    on an STT provider being reachable.
* **Frugal by default** (`[ultrawiki.media].enrich = "frugal"`): one item at a
  time, only while nothing else is queued, with a per-day ceiling the user can
  raise. `off` and `eager` are the other two settings.
* **No capability, no crash.** The item keeps its honest reason and stays in
  the queue; installing a key or a model later drains it. This is the headless
  Linux path (charter §3) and it is a first-class outcome, not degradation.

A recurring bug class this deliberately avoids: nothing in this lane may ever
sit on the boot or voice critical path (AP-26 / AP-9).

---

## Package B — the platform export guide

A declarative catalog, `platform_guide.py`, of the platforms people actually
keep their lives in. Each entry states, in English and worldwide terms:

* where the platform's **own** export function lives (the literal menu path),
* what the export contains and in which formats,
* how long it typically takes to arrive,
* which of our formats will read it,
* optionally, the statutory route as an *extra* (never the only one).

Rules that keep it a catalog rather than folklore:

* **Only self-service exports.** No scraping recipe, no credential-sharing, no
  route that breaches a platform's terms.
* **No invented capability.** An entry names the formats our importer actually
  reads today; if it names none, it says so.
* **Jurisdiction-neutral wording.** An entry may not be written so that it only
  makes sense inside one legal regime.

Surfaced as a REST route (hence a CLI command, charter §5) and as a searchable
list in the UltraWiki sources UI that ends in the existing upload flow.

---

## Package C — the generic custom source

For everything else: a connector the user configures in-app.

* **HTTP mode** — a URL, an optional credential (stored through the Jarvis
  secret chain, never in `jarvis.toml`), a JSON path to the item list, and a
  mapping of which field is id, title, body, timestamp and link. Paging by
  cursor field or page number; incremental via a timestamp parameter.
* **Feed mode** — RSS/Atom, which a surprising share of services still emit.
* **Watched folder mode** — "anything that lands here belongs in my memory",
  reusing the local-folder walk with a watcher.

Safety: the fetcher refuses non-HTTP(S) schemes and private/loopback address
ranges unless the user explicitly ticks "this is on my own network", so a
misconfigured source cannot be pointed at internal infrastructure. Response
size and item counts are bounded like every other connector.

---

---

## What shipped, and what is honestly still open (2026-07-26)

**Shipped and tested:** the extractor coverage (A1), every reader routed
through it (A2), the frugal enrichment lane (A3), the platform guide (B) and
the generic custom source (C). Two defects surfaced on the way and were fixed:
`_decode` tried UTF-16 without a byte order mark, turning ordinary
Windows-encoded notes into CJK-looking noise; and an office archive missing its
manifest part was discarded whole though every slide was present.

One finding was larger than a defect: **connector metadata never reached the
store.** There was no column for it, so the source format, a file's
modification time and a chat's participants were dropped on every import since
the store was written. Added additively; existing databases migrate in place.

**Open, deliberately:**

* **Local speech recognition cannot transcribe an imported file.** Only the
  cloud providers gained `transcribe_container`; `faster-whisper` did not,
  because the always-on wake loop holds a native inference engine and sharing
  one is the AP-24 hang. A second instance would need its own lifecycle work.
* **The Gemini and OpenRouter speech plugins** send audio as inline base64
  rather than multipart, so they need their own small `transcribe_container`.
* **No UI yet** for the platform guide or the custom source. Both are complete
  as REST routes, which makes them CLI commands automatically (charter §5).
* **A photo inside a `.tar` cannot be described.** A tar entry cannot be
  reopened without decompressing the whole stream again, so those items say so
  rather than waiting in a queue forever. Importing the same export as a `.zip`
  removes the limitation.
* **`.enex` (Evernote) and `.pst` (Outlook)** are named in the guide as
  unreadable rather than silently failing.
* **Watched-folder mode** for the custom source was dropped as redundant: the
  local-folder source already watches a folder and now reads every format.

## Definition of done (charter §3, all four paths)

1. **One arbitrary key** — a user whose only credential is for some other
   provider still imports photos as findable items (filename, date, folder) and
   drains the enrichment queue the moment any vision- or STT-capable provider
   is present.
2. **Headless Linux** — base install, no GPU, no audio: every package imports
   and runs; media items stay honestly pending.
3. **macOS** — no Windows-only import, path or API on any of these code paths.
4. **Cross-family fallback** — enrichment resolves its provider through the
   existing key-aware chain and crosses families rather than pinning one.

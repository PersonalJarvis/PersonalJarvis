---
title: "Use UltraWiki"
slug: ultrawiki
summary: "Build a searchable knowledge base from approved sources, ask cited questions, and understand where your data is processed."
section: "Knowledge and sharing"
section_order: 4
order: 6
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [ultrawiki, knowledge, search, citations, people, privacy]
related: [wiki-and-memory, connect-ultrawiki-sources, connect-obsidian, privacy-and-local-data]
---

UltraWiki turns approved sources into one searchable knowledge base. You can
ask questions with numbered evidence, browse topics and moments, and review
how people from different sources were matched.

UltraWiki is an alternative to **Normal Wiki**, not a second layer that answers
at the same time. You can switch between them without deleting either one's
data.

## Before You Start

- UltraWiki needs an **embedding model**. An embedding turns text into numbers
  that represent its meaning, so similar ideas can be found even when they use
  different words. Choose a ready local or cloud option during setup.
- A remote database or cloud model can receive source content. Read [Privacy
  and Local Data](privacy-and-local-data) before importing sensitive material.
- UltraWiki starts empty. Activation reads nothing until you approve a source.

## Choose the Right Wiki Mode

| Mode | Best for | How it stores and finds knowledge |
|---|---|---|
| **Normal** | A smaller set of durable facts, preferences, projects, and decisions | Readable Markdown notes, links, backlinks, and local full-text search |
| **Ultra** | Larger collections copied from folders, conversations, exports, and connected services | A structured knowledge store with keyword and meaning-based search, citations, topics, moments, and identity matching |

Only the selected mode supplies Wiki answers and memory context. Normal Wiki
notes remain available, and UltraWiki can import them as an approved source.

## Turn On UltraWiki

1. **Open Wiki.** Use the **Normal | Ultra** switch in the Wiki header and
   choose **Ultra**. The one-time setup opens if UltraWiki has not been
   configured before.

2. **Choose storage.** Select **SQLite** for a local file with no setup. Choose
   **Postgres** when you want a separate database, including a guided Supabase
   connection. A remote Postgres service receives stored and derived knowledge.

   If a configured Postgres database cannot be reached, UltraWiki reports the
   problem and uses local SQLite instead. A later storage change takes effect
   after the next app start; it does not delete the current store.

3. **Choose an embedding provider and model.** An Ollama endpoint running on
   this machine keeps this processing local. A remote Ollama or cloud option
   sends imported passages and search queries to that endpoint or provider.
   UltraWiki does not silently switch because another model would create an
   incompatible meaning index.

4. **Choose optional processing.** **Distillation** creates summaries, topics,
   people, and moments. **Rerank** can improve search order. Start with the
   suggested automatic distillation and rerank off.

5. **Review and activate.** Select **Activate Ultra mode**. The **Overview**
   opens and should say that nothing is stored yet. This is expected: every
   source still needs your approval.

6. **Add one source deliberately.** Open **Sources**, register a small source,
   review its scope, then approve it. Approval normally starts the first full
   import. Follow [Connect UltraWiki Sources](connect-ultrawiki-sources) for
   connector-specific guidance.

> [!warning] Cloud embedding, distillation, reranking, and answer providers may
> receive source text. A local database alone does not make model processing
> local; check every selected slot under **Settings**.

## Read the Overview and Contents

**Overview** separates stored, searchable, summarized, and failed items, lists
each source's last read, and gives problems an action such as **Import now**,
**Try again**, or **Open settings**.

Imports run as jobs. You can watch the phase, cancel a job, or retry failed
items from their last completed stage. Approved sources refresh automatically
at a source-appropriate interval. Use **Sync now** for a newer copy immediately.

Open **Contents** to inspect stored records. Filter by source or stage, open the
captured text, and compare it with summaries and embedding status. UltraWiki
does not edit or delete the original service or file.

## Search and Ask with Evidence

UltraWiki combines two kinds of search:

- **Keyword search** finds exact words and phrases. It becomes available soon
  after import and keeps working when the embedding provider is unavailable.
- **Semantic search** finds similar meaning. It becomes more complete as the
  background embedding queue finishes.

Open **Ask**, enter a question, and select **Ask**. UltraWiki retrieves the best
matches, then uses an available chat provider to compose an answer. The answer
is followed by numbered evidence with source, date, surrounding context, and
**Open original** links.

If no chat provider can compose the answer, the evidence remains visible. If
semantic search is paused, the status line explains why and keyword search
continues. Changing the embedding model starts a background rebuild shown in
**Overview**.

## Explore Topics, Moments, and People

Open **Explore** to browse the knowledge instead of asking a question.

- **Topics** are people, places, projects, and other subjects found during
  distillation. A topic page gathers its newest evidence and related topics.
- **Moments** are dated events, such as a meeting, trip, or milestone. Each one
  links back to its evidence.
- **Connections** shows subjects that appear in the same moments. Increase the
  mention threshold to hide one-off topics; a connection is evidence of
  co-occurrence, not proof of a relationship.

The **People** tab adds identity review. **Import address book** seeds people
from Contacts and is safe to run again. A shared email, phone number, or contact
record can merge entries automatically. Similar names go to **Open questions**.

A confirmed merge appears in **Merge history** and can be undone, newest merge
first. Rejecting a proposed match is permanent: those two entries stay separate
and will not be proposed again. Review the displayed evidence before choosing.

## How It Fits Together

1. **An approved source supplies a copy.** UltraWiki imports records, indexes
   exact words, and processes meaning and summaries in the background.
2. **Wiki mode chooses the memory used now.** Chat and voice retrieve UltraWiki
   context only in Ultra mode. If it is unavailable, the request continues
   without that context or uses Normal Wiki where the action supports it.
3. **Ask adds a cited answer.** Search gathers evidence first. A compatible
   chat provider receives the question and selected evidence only for answer
   composition; provider failure does not erase the search results.
4. **Missions and commands can use the same evidence.** A Jarvis-Agent mission
   may receive the approved UltraWiki Ask command. From a terminal, run the
   following against your running app:

   ```powershell
   jarvis ultrawiki ask "What decisions were made about the launch?"
   ```

   The result includes the answer state and its citations. Other UltraWiki
   operations are available through the generated control API; use
   [App Command Reference](app-command-reference) for the supported commands.
5. **Exports remain separate copies.** Explore can write generated Markdown to
   an Obsidian vault. UltraWiki leaves **My notes** untouched. Read [Connect an
   Obsidian Vault](connect-obsidian) before relying on that copy.

## Check That It Works

1. Open **Overview** and confirm the verdict says **Ready to answer** or **Ready
   to answer — and still filling up**.
2. Open **Ask** and ask a harmless question whose answer exists in an approved
   source.
3. Confirm that an answer or evidence list appears with numbered entries and
   an **Open original** link.
4. Open one citation and verify that it supports the result.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Nothing stored yet** | No source is approved, or its first import has not run | Open **Sources**, approve the intended source, and watch the import job |
| **Not ready to answer yet** | Storage, embedding, or an import stage needs attention | Use the action beside the first failed check in **Overview** |
| Semantic search is off | The embedding provider is unavailable or its index is rebuilding | Open **Settings**, test the embedding slot, and keep using keyword results while it recovers |
| Items remain failed | A provider, document reader, or source connection failed repeatedly | Fix the named cause, then choose **Try again**; items resume from their last completed stage |
| A cited answer cannot be composed | Search found evidence, but no chat provider completed synthesis | Read the evidence directly, then check a ready provider under **API Keys** |
| Two people were matched incorrectly | A merge joined identities that should stay separate | Open the person's **Merge history** and undo the newest affected merge first |

Switching to **Normal** pauses UltraWiki jobs and closes its active store. It
does not delete sources, imported items, embeddings, or identity history;
switching Ultra back on resumes the same store. Revoking a source stops future
syncs but keeps copied data. Revoking is not erasure; follow the source guide
for the separate purge action. It removes that source's imported and derived
records from the active store, but not identity audit history, exports,
backups, the original source, or copies retained by remote providers.

## Next Steps

- Follow [Connect UltraWiki Sources](connect-ultrawiki-sources) to approve,
  refresh, or remove a source.
- Read [Wiki and Memory](wiki-and-memory) for the Normal Wiki workflow and the
  boundaries between conversations, Contacts, Profile, and durable memory.
- Use [Providers and API Keys](providers-and-api-keys) to connect or replace a
  model provider without placing credentials in chat, voice, or configuration.
- Review [Privacy and Local Data](privacy-and-local-data) before importing
  personal archives, using cloud processing, or exporting another copy.

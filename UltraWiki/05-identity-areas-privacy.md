# 05 · Identity, areas & privacy

## Identity resolution (D-10)

The single most dangerous failure in a personal memory is a **wrong merge**:
fuse two people into one entity and every answer about either is silently
wrong, discovered months later. The design therefore splits resolution into
three tiers:

1. **Deterministic — merge automatically.** Same phone number, same email
   address, an exact contact-book match. These identifiers are unique enough
   that automation is safe.
2. **Probable — queue for confirmation.** Name similarity, nickname patterns,
   co-occurrence in the same threads ("Viki" appears exactly where Viktoria's
   number appears). The match is *proposed*, never applied: it lands in the
   **confirmation queue** and both identities keep answering separately until
   the user confirms ("Is Viki = Viktoria Müller?" — one tap). <!-- i18n-allow: illustrative German name in the identity-merge example -->
3. **Weak — do nothing.** Below the proposal threshold, identities stay
   separate; a wrong split is annoying but repairable, a wrong merge is
   poison.

Every merge — automatic or confirmed — is written to `uw_merge_log` with the
evidence that justified it and is **reversible in one action**: unmerging
restores the previous identifier mapping and re-links affected documents and
events. The queue lives in the People view (doc 04) and stays short by
design; resolution runs on the write path so the queue fills gradually, not
in one avalanche.

Seed data: the existing Jarvis contacts store provides the initial entities
and identifiers, so day one already knows the user's actual circle.

## Areas (D-12)

Areas are named bundles of sources — Work, Private, Project X — and exist
from v1:

- A source belongs to one or more areas; items inherit their source's areas at
  ingest, denormalized onto the row, so scoping is one indexed filter.
- **Search, chat, and voice accept an area scope** ("nur Arbeit" spoken, a
  filter chip in the UI, a parameter on the REST route).
  <!-- i18n-allow: German spoken voice-scope example the matcher must accept -->
- The activation wizard creates a default area; a query without a scope runs
  over everything, and the planner may narrow to an area when the question
  implies one.
- Areas are metadata, not walls: they tune relevance and let the user aim a
  question. They are **not** a security boundary within the single-user store.

## Privacy model

The corpus UltraWiki accumulates is the most sensitive dataset its user will
ever hold in one place. The stance:

1. **Everything can stay on-device.** With local storage + local embedding +
   local distillation, no byte of content ever leaves the machine. This
   fully-local configuration is a first-class, tested path — not a degraded
   afterthought.
2. **Cloud is a conscious, per-slot choice.** The activation wizard states in
   plain language which raw content each cloud choice would see (distillation
   sees full raw text; embedding sees the distilled documents; a cloud
   database stores everything). No silent defaults into the cloud.
3. **Secrets never enter the corpus.** Ingestion runs the existing
   secret-pattern scrubbing before storage; API keys pasted into a chat years
   ago must not become retrievable memory.
4. **Deletion is honored end-to-end.** Source-side deletions tombstone and
   cascade (doc 02). A disconnect offers purge; purge removes items,
   documents, embeddings, events, and entity links derived from that source.
   Nothing survives in caches — the distillation cache is keyed by content
   hash and purged with its items.
5. **Encryption at rest, v1 stance:** the store inherits the device's disk
   encryption; the design keeps a per-store passphrase (SQLCipher /
   pgcrypto-style) as a tracked roadmap item rather than a v1 requirement, so
   the cross-platform base install stays dependency-light. Documented
   honestly in the user-facing docs.
6. **The corpus never leaves via git.** The store lives under the data dir,
   which is gitignored like all user data; nothing in these designs ever
   writes user content into the repository.

## What the mode switch does NOT do (D-9)

Switching modes never migrates, converts, or deletes content in either
direction. The normal wiki's markdown stays the user's readable, portable
property; UltraWiki's store stays intact while paused. The only coupling is
one-way and read-only: UltraWiki ingests the normal wiki as a source.

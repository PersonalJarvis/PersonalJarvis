# 06 · Roadmap — build order with hard gates

v1 ships all phases below (the maintainer scoped v1 wide: all source
families, voice from day one). The phases are the **build order**, and each
gate must be true — proven by automated tests or measured numbers, not
claimed — before the next phase starts. The discipline lives in the gates.

## P1 · Store & pipeline runtime

The unified store (SQLite backend first), the staged state machine, worker
loop with retry/backoff/dead-letter, idempotent upserts, the dialect adapter
skeleton for Postgres.

**Gate — four scenarios green as automated tests, offline:**
(a) a 1000+-item backfill hard-killed mid-run restarts to an identical end
state — no duplicates, no gaps; (b) a connector throwing at item 500 leaves
1–499 finished and 500 in `failed`, nothing blocked; (c) a second run over
unchanged data creates zero new work (content-hash proof); (d) the entire
suite runs with no network, no credentials, no models.

## P2 · Local connectors + activation wizard

The zero-auth connectors (normal-wiki vault, Jarvis conversations, chosen
folders), the connector fixture-test harness, the settings section skeleton,
the activation wizard with the storage/embedding/distillation choices, the
mode switch (both directions, non-destructive).

**Gate:** a fresh install activates Ultra mode end-to-end with only local
options on Windows, macOS, and headless Linux; switching back and forth loses
nothing; a stranger can write and CI-test a new connector from fixtures alone.
If the connector contract feels awkward here, fix the contract now — before
P6 multiplies it.

## P3 · Retrieval core

Keyword + vector + time lists, RRF fusion, optional rerank, context
expansion, cited synthesis; the Ask view in the Wiki UI; REST routes (and
therefore CLI); the staged-import progress surface; areas end-to-end
(wizard → ingest tagging → filter chip).

**Gate:** on a real ingested corpus, a set of 30+ known-answer questions
returns the correct evidence in the top results with working permalinks;
cloud slots absent → keyword/time/entity paths still answer honestly.

## P4 · Voice integration

The `ultrawiki_search`/`who_is` primitives as brain tools, voice-tier
degradation rules, prewarming off the boot path, precomputed entity/topic
profiles.

**Gate (D-8):** ≤ 900 ms to first spoken token, **measured** on the reference
desktop against a realistically sized corpus, for both a profile lookup and a
fan-out question; a cold or degraded UltraWiki never stalls the voice
conversation.

## P5 · Identity & events

Entities/identifiers seeded from contacts, deterministic auto-merge,
probable-match confirmation queue with reversible merges, the People view,
episodic event extraction with absolute time anchoring, bi-temporal fact
handling.

**Gate:** a golden set of known people resolves with **zero wrong merges**
(uncertain cases provably queue); the north-star dinner question is answered
correctly — date, place, participants, confidence, permalinks — from a real
multi-source data slice. *This is where the project proves its promise.*

## P6 · The wide world of sources

Export importers (WhatsApp first, then takeout-style archives), OAuth
connectors (Gmail, Calendar, Drive, GitHub) over the marketplace machinery,
the generic Jarvis plugin/CLI bridge, per-source reconcile + tombstones,
the Postgres backend promoted to a tested first-class option.

**Gate:** each connector family passes its fixture suite plus one real-world
end-to-end run; deletion propagation is demonstrated; the same corpus works
against both storage backends.

## P7 · Evaluation as a permanent gate

A 100+-question eval set over a reference corpus, retrieval recall measured
separately from answer accuracy, latency tracked per stage; wired into CI so
a regression breaks the build. Only after P7 is prompt-tuning worth anyone's
time.

**Gate:** the eval harness runs in CI; the v1 numbers are recorded as the
baseline every future change is measured against.

---

## Standing constraints (all phases)

- **Universality:** every phase works with whatever single provider key the
  user has, on all three OSes including headless Linux; local-only is always
  a complete path. "Works on the maintainer's machine" is the defect, not
  the proof.
- **Off the hot path:** no UltraWiki initialization on the boot or voice
  critical path; heavy imports stay lazy.
- **CLI-first:** every user-facing action lands as a REST route (hence CLI);
  destructive routes carry the danger metadata.
- **English artifacts,** i18n keys for UI strings, multilingual behavior for
  user content — the corpus and the questions may be in any language, so
  multilingual embedding/distillation choices are the default
  recommendations, never an English-tuned bias.

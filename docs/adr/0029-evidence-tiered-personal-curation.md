# ADR-0029: Evidence-tiered, person-centric wiki curation

Date: 2026-07-20
Status: Accepted (revises the uniform recall bias of ADR-0013 / spec D1)

## Context

The two-stage conversation curator (spec 2026-06-09) shipped with two
deliberate postures that the maintainer has now revised:

1. **A uniform recall bias.** One binding sentence — "Forgetting a grounded
   fact is a strictly worse failure than adding a slightly-too-eager one" —
   applied to every fact family alike. Combined with the eager scheduler
   (`consolidate_after_candidates=1`, reliability spec 2026-07-07), marginal
   world-knowledge facts accumulated: the wiki drifted toward a transcript
   archive instead of a curated map of the user's life. The pipeline's
   history oscillates between "judge starves the vault" (bd7ecc22) and
   "junk pollutes the map" (many fix commits); every swing was a prompt
   rewrite, because no tunable dial existed.
2. **A hard ban on inference.** `grounding.py` rejected every user
   interest/preference fact whose evidence turn lacked a literal
   first-person assertion, and all extraction prompts forbade proposing
   one. "I love being out on golf courses with my buddies, playing this
   sport actively" therefore could NOT yield "the user plays golf" —
   exactly the person-centric inference the maintainer wants.

The ban existed for a real reason: topic questions ("Tell me about
Monaco.") must never manufacture personal facts. The revision has to keep
that guarantee while admitting lived-experience grounding.

## Decision

1. **Three evidence bases per candidate fact** (`constants.FACT_BASES`,
   migration 0009 sidecar table, five-layer parity-tested):
   - `explicit` — the user literally asserted the fact.
   - `behavioral` — the user described first-person lived experience
     (doing, practicing, enjoying, habitual markers) without naming a
     preference. This is the golf case.
   - `inferred` — reserved for a future cross-session reflection pass;
     the enum ships now so v2 needs no schema change, nothing emits it.
2. **Deterministic classifier floor**
   (`grounding.classify_user_attitude_evidence`): attitude/habit claims
   about the user are graded explicit/behavioral/blocked from the evidence
   excerpt. Question clauses never ground anything; the model cannot
   launder a behavioral inference into an `explicit` label (Stage 1
   downgrades it); habit claims now require grounding too.
3. **Personal-salience score (1-5)** per candidate — user-centrality, not
   interestingness — with a configurable Stage-1 floor
   (`[memory.wiki.extractor] min_salience`, default 3). The bar becomes a
   config dial instead of the next prompt rewrite.
4. **Asymmetric curation bar** replaces the uniform recall bias in both
   stages: recall-protected for asserted facts about the user's identity,
   people, possessions, health, habits, and projects (the explicit
   "remember that…" override is untouched); precision-biased for world
   knowledge, one-off topics, and salience 1-2 candidates.
5. **Behavioral facts are visibly provisional**: their page bullet ends
   with the literal marker `*(inferred)*` and their mechanical Sources
   citation carries `(basis: behavioral)`. The preservation guard exempts
   exactly these marked lines, so a later explicit assertion upgrades the
   line (rewrite, drop the marker) and an explicit contradiction may
   remove it — everything unmarked keeps byte-level protection.
6. **Person-centric graph growth**: the soft kind `activity` joins the
   graph-companion invariant (topic page under `concepts/`), and the
   consolidator prompt gains binding enrichment routing — detail about a
   topic that has its own page lands on THAT page; the user profile keeps
   a one-line `[[wikilink]]` bullet.
7. **Rollback switch**: `[memory.wiki.extractor] behavioral_inference =
   false` restores the explicit-only regime without code changes.

## Consequences

- The golf sentence now yields a `behavioral` candidate that lands as an
  `*(inferred)*` profile bullet plus a cross-linked `concepts/golf.md`;
  "Tell me about Monaco." still yields nothing (pinned by tests in
  `test_grounding.py`, `test_extractor.py`, `test_consolidator.py`).
- Salience misjudgements by the cheap model are auditable (the score is
  stored per candidate) and tunable per install (`min_salience`).
- Vaults that pre-date this ADR keep their content; legacy journal rows
  read back as `explicit`/3. Profile cleanup is a separate manual,
  dry-run-first re-curation pass (`python -m jarvis.memory.wiki.cli
  recurate-profile`): keep supported personal facts, move topic detail to
  topic pages, drop world-knowledge trivia — full vault snapshot first,
  all-or-nothing write, never scheduled.

## Alternatives considered

- **Prompt-only retuning** (no basis/salience data model): rejected — it
  repeats the starve/slop oscillation with no dial and no provenance.
- **Cross-session reflection pass in v1**: deferred — the behavioral tier
  answers the ask within one session, and repeated mentions already
  enrich the topic page through the normal Stage-2 flow; a v2 pass is a
  pure read-side addition over the retained journal.
- **Embedding-based salience scoring**: rejected for the primary path
  (ADR-0013 keeps FTS5-only retrieval; a Premium embedding tier remains a
  deferred pillar of the 2026-07-07 spec).

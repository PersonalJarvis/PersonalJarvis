# Private, evidence-backed agent learning

Tier: **T3 capability**. Jarvis and every Society agent use the same portable
learning loop, with separate ownership, files, review locks and provider scopes.

## Behavior

1. Each completed or failed chat turn enters the durable review queue. Voice
   completion queues Jarvis' own user feedback without waiting for a model.
2. Failed tool results produce conservative warnings even if no review model
   is available. These warnings never claim that a remedy was verified.
3. The existing provider-independent reviewer extracts useful facts, corrections,
   successful methods and failure lessons. Every accepted lesson must quote a
   single actual source of the appropriate class. Assistant prose is not proof.
4. Later chat turns retrieve applicable private lessons and record their IDs
   against the turn receipt. Existing facts and private draft skills remain
   available. Jarvis' chat now participates in the same completion lifecycle.
5. Explicit user corrections can retire an obsolete lesson. Explicit negative
   evaluations can suspend reuse. Merely retrieving a lesson or finishing a
   turn does **not** increase its measured benefit.

Voice uses an IO-free cached snapshot populated after runtime startup and after
Jarvis reviews. It takes effect on the next existing realtime instruction refresh
or new session; it does not force an extra provider connection or interrupt speech.
Voice completion lacks verified tool outputs, so it learns direct user feedback
but cannot manufacture a successful procedure from tool names alone.

## Persistence and isolation

Existing facts remain in the configured vault at `society/<agent-id>/memory.md`.
The application data directory contains these additional private files:

| Path under `society/<agent-id>/` | Purpose |
| --- | --- |
| `learning/journal.json` | Authoritative lessons, source receipts, exposures, evaluations |
| `learning/LEARNING.md` | Readable projection; automatically repaired after interrupted writes |
| `learning/journal.lock` | OS-released interprocess write lock |
| `skills/` | Existing private procedural skills, still drafts |

Jarvis owns the reserved identity `jarvis`; its multiple chats and voice sessions
share that identity. No ordinary agent retrieves Jarvis' learning or another
agent's facts, lessons or skills. Agent IDs are validated without lossy slug
normalization. Linked namespaces, linked journals and cross-owner journal copies
are refused. Private memory read/modify/write operations also use a file lock.

The shared BrainManager also enforces the boundary: Society turns receive only
their own briefing, written-response style and turn language. Jarvis' global
profile, core memory, ambient wiki and pending global skill triggers are excluded,
and Society turns cannot feed Jarvis' global curator. Missing private briefings
fail closed on both API and CLI seats rather than falling back to Jarvis' identity.

The conversation database is the existing durable queue, scoped by session and
explicit owner. Different agents review concurrently; turns of one owner are
serialized. Failed reviews retry with exponential backoff and jitter, and resume
after restart. Per-owner FIFO order prevents an older retry from undoing a newer
correction. Idempotent receipts prevent duplicate learning and evaluation.
Cancelled background tasks are awaited during shutdown before storage closes.

## Evidence and trust

Feedback requires a direct user source; procedures require successful tool
evidence; failures require failed tool evidence. Credentials are excluded from
learning input and persistence. Lesson writes reject recognizable instruction
injection and invisible formatting characters. These checks supplement, rather
than replace, source labels and the normal ToolExecutor permission boundary.
Learned text is advisory and never grants authority or activates draft skills.

The journal measures retrieval and **user-attributed** benefit/harm. These are
operational evidence, not a claim of causality or a general intelligence score.
The deterministic contracts prove state transitions and prompt integration; the
live probe demonstrates one controlled project-specific planning improvement.

## Verification

- Contract: `tests/contract/test_agent_learning_loop.py`, including restart,
  strict scope, concurrent writers, poisoned input, corrections, negative
  feedback, failed turns, independent reviews and Jarvis chat/voice integration.
- Regression: the Society, agent-chat and realtime session suites, plus existing
  conversation-continuity contracts.
- Opt-in live proof: `python scripts/verify_agent_learning.py --provider grok`.
  It uses fresh temporary data and the existing provider registry, reviewer,
  ToolExecutor, completion hooks and prompt builder. No secret is printed.
  The selected provider can be changed; no provider is required by the feature.
- Live observation: Grok returned `UNKNOWN` before learning,
  `validate_larch_manifest` after a user correction and full runtime restart,
  and `UNKNOWN` for another agent. One lesson was persisted; no reviews remained.
  The same proof passed after installing the application into a fresh Linux
  container with preinstalled portable dependencies and providing only one Grok
  credential through stdin. No host data directory or credential files were mounted.
  This probe caught and fixed a reviewer edge case: a user correction classified
  as an unexecuted skill now becomes grounded feedback instead of being discarded.
- Focused Windows verification: 336 passed, two environment skips. The broader
  Society/chat/realtime selection produced 864 passes, three skips and 15 failures;
  all 15 failures were reproduced using the unchanged baseline modules. They
  concern earlier CLI-seat expectations and realtime bridge test doubles.
- The four routing/output/hangup/language guards plus learning contracts passed
  (620 tests, one environment skip). A later Linux run passed all 27 learning
  contracts, including symlink isolation.
- The final shared-brain isolation change passed 136 prompt, skill-routing,
  turn-override, continuity and learning tests, with one environment skip.
- The isolated boot-budget probe passed: window 2,371 ms, interactive 19,358 ms,
  voice-ready 19,921 ms. These measurements are a regression check, not a promise
  that another machine will reach the same timings.

The implementation uses pathlib, JSON, SQLite, asyncio and the existing filelock
dependency on Windows, Linux and macOS. The native symlink test can skip on
Windows accounts without symlink permission; Linux runs that test. Native macOS
execution is an outstanding environment verification, not a claimed result.

## Hermes reference

Reference inspected: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent),
HEAD `44945d224c2ccd6e0a55f16223c7ab0dd39331bf` (2026-09-19), and its
[persistent-memory documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/).
Relevant ideas are private profiles, concise persistent memory, source recall,
and procedural skill reuse. Jarvis extends these ideas with per-owner concurrent
review, failure learning, exact-source checks, correction retirement, replay-safe
receipts and explicit reuse measurements. This implementation was independently
written; no upstream source code was copied.

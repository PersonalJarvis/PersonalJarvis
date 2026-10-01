# Jarvis' self-learning loop

Tier: **T2 memory surface**. Code: `jarvis/memory/learning/`. Config:
`[memory.learning]` (`JarvisLearningConfig` in `jarvis/core/config.py`).

Jarvis learns from its own conversations with the user, spoken and typed, and
carries what it learned into every later conversation: who the user is, what
they prefer, what they are working toward, and how they want Jarvis to behave.
The Society agents have had this since their notebooks shipped
(`docs/agent-society/self-learning.md`); this loop gives the same ability to
Jarvis itself, tuned for a voice assistant.

## How it works

1. **Collect.** Every finished voice turn (`VoiceTurnCompleted`, published by
   all voice engines) and every typed turn on Jarvis' own chat (the `jarvis`
   surface kit's `turn_completed` hook) is filed per conversation. Society
   agent chats never feed this loop, and routine or agent-injected chat turns
   (`direct_user = false`) are ignored.
2. **Filter (free).** A deterministic filter (`signals.py`, English, German
   and Spanish) marks the few turns in which the user talks about themselves,
   states a preference, corrects Jarvis, or names a plan, goal or deadline.
   Requests and questions ("play some music", "what's the weather") are not
   marked. A conversation with no marked turn never reaches a model.
3. **Explicit requests (free, immediate).** "Remember that X" / "merk dir X"
   is saved at once in the user's own words with the date, without a model,
   so it cannot be lost to a provider failure. Only a bare "remember that",
   which points at something said before, is reviewed right away with the
   turn before it.
4. **Review (one small call per conversation).** When a call ends
   (`VoiceSessionEnded`), the conversation has been quiet for
   `idle_review_seconds` (default 300), or after `review_every_turns`
   (default 30) in a very long conversation, one model call sees only the
   marked user turns (at most eight, each with the assistant line just before
   it, cut to 240 characters) and the two small notebooks. It returns at most
   three changes (`add`, `replace` to update or merge, `remove` only when the
   user retracted something), capped at 800 output tokens; proposals below
   importance 5 are dropped. The model is each provider's cheap router-tier
   model on the wiki's background chain: subscriptions and keyless local
   models while a subscription is connected, never the key that pays for the
   voice call.
5. **Validate (Python decides).** A change is written only when:
5. **Validate (Python decides).** A change is written only when:
   - its `evidence` is a verbatim quote (12 characters or more) of the
     **user's** own words the review was shown. Anything only the
     assistant, a web page, an email or a tool said proves nothing;
   - the quote is about the change: it shares a content word with the new
     text, or, for a `remove`, with the entry being removed;
   - every link, e-mail address or long number in the text was said by the
     user or is already in the notebooks (dates the
     reviewer derived from "next Friday" are allowed);
   - its text passes `guard.refusal`: no credentials, no instruction-like or
     injected text (English, German and Spanish patterns), no orders phrased
     as entries, no invisible, private-use or unassigned characters, at most
     300 characters;
   - a `replace`/`remove` names an existing entry that has not changed since
     the review read it (an edit in the UI or Obsidian wins), and an `add` is
     not an exact duplicate. At most eight changes per review.

   Entries are written in the language the user spoke, so the quote and the
   entry share their words.
6. **Write.** Changes go through `jarvis.society.memory_books`, the same
   locked, journaled layer the agents use, into the lead identity's notebooks:

   | File (in the vault) | Holds |
   | --- | --- |
   | `society/jarvis/USER.md` | who the user is: identity, people, preferences, style, goals, plans with absolute dates |
   | `society/jarvis/MEMORY.md` | Jarvis' working notes: environment facts, conventions, lessons from corrections |
   | `society/jarvis/.learning-ledger.jsonl` | every applied change with its old and new text and the evidence |

   Nothing is ever lost: a replaced or removed entry stays in the ledger.
7. **Use.** A cached snapshot of both notebooks is added to the classic brain
   prompt (`BrainManager._build_system_prompt`, right after the user profile)
   and to the realtime voice instructions (`_session_instructions`, right
   after the user's standing instructions). It is framed as background
   knowledge, never as instructions; the user's current words always win. The
   snapshot is re-read only when a notebook file changes, so an edit made in
   the lead avatar's knowledge view or in Obsidian applies on the next turn,
   and the prompt stays byte-stable for the provider cache otherwise.

Turns stay pending until a review has really looked at them. When no reviewer
answers, the turns are handed back and the next attempt waits 1, 2, 4 ...
minutes (at most an hour) instead of firing on every turn; a call ending or a
quiet conversation still tries again. Shutdown never waits on a slow reviewer.

The prompt path never waits on a writer and never creates files: it reads the
notebooks under a 50 ms lock attempt and otherwise serves the last good text.
Typed chat turns contribute only what the person typed, never attachments.

## Size and compaction

The notebooks ride along on every brain turn and every realtime instruction
update, so they stay small: `user_budget_chars` 1,500 and `memory_budget_chars`
1,000 by default (about 650 tokens together at most; the compact realtime
profile for small local models uses half). One entry is one short sentence,
at most 300 characters. Entries beyond the prompt budget stay on disk; the most
important and most recent ones reach the prompt.

Compaction (`compact.py`) keeps the files themselves small:

1. **Duplicates (free, after every write and at start).** An entry equal to,
   or contained in, another entry of the same notebook is removed.
2. **Merge (one cheap call, at most every 12 hours per notebook).** Once a
   notebook passes 80 percent of its budget, a model proposes merged entries
   and outdated ones. Python accepts a merge only when it is shorter than its
   sources and invents nothing: every number and link, every capitalised name
   and at least 60 percent of its words come from the sources. An entry is
   dropped as outdated only when it names a date that has passed; lasting
   facts such as birthdays are kept. The cooldown survives restarts
   (`.learning-state.json`).
3. **Hard ceiling.** A review may not grow a notebook past 125 percent of its
   budget (a replace that does not grow it is fine). Only an explicit
   "remember" request passes the ceiling, and the next merge shrinks it.
4. **Bounded ledger.** `.learning-ledger.jsonl` rotates at 256 KB and keeps one
   previous file, so the audit trail never exceeds about 0.5 MB.

Privacy: reviews send the reviewed turns to the same background provider
chain the wiki extractor already uses for every conversation turn; the loop
adds no new destination. `enabled = false` switches it off.

## Cost

- An ordinary conversation (requests, questions, small talk): no model call.
- "Remember that X": no model call.
- A conversation with a personal fact, preference, correction or plan: one
  call of roughly 1,300 input tokens and at most 800 output tokens on a cheap
  model, preferably on a subscription.
- Every turn: at most about 650 prompt tokens for the notebooks, usually far
  less, and byte-stable between reviews so provider prompt caches apply.
- Compaction: nothing while a notebook is below 80 percent full; then at most
  one small call per notebook every 12 hours.

## Design reference

The loop follows the design of Nous Research's Hermes Agent: a bounded
USER/MEMORY pair, a review that runs after the reply instead of during it,
declarative entries instead of imperatives, and an explicit list of what not
to keep. Jarvis adds the evidence rule of its Society agents (every change
quotes the user), a ledger for every change, voice-first triggers (call end,
quiet period, explicit requests) and a single snapshot shared by both voice
engines. No upstream code was copied.

## Verification

`tests/unit/memory/learning/test_jarvis_learning.py` covers the evidence and
safety rules, ledger and budgets, every trigger, the dead-reviewer fallbacks,
the voice and chat inputs, and both prompt integrations;
`test_compaction.py` covers deduplication, safe and refused merges, the
outdated rule, the cooldown, the hard ceiling and the bounded ledger. The loop uses only
`pathlib`, JSON, asyncio and the existing `filelock` dependency, so it runs
unchanged on Windows, macOS and headless Linux.

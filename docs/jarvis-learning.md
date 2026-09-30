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
2. **Trigger.** A background review starts when any of these happens:
   - `review_every_turns` unreviewed user turns have piled up (default 6);
   - the user explicitly asks to remember something ("remember that ...",
     "merk dir ..."), so the next turn already knows it;
   - a call ends (`VoiceSessionEnded`), or the conversation has been quiet for
     `idle_review_seconds` (default 300).
   Turns with almost no user text (under 24 characters in total) cost no model
   call.
3. **Review.** One model call sees the two notebooks with their entry ids and
   fill level, what the identity card already knows, up to four earlier turns
   as context, and the turns to review. It returns JSON changes: `add`,
   `replace` (update or merge an entry) or `remove` (only when the user
   retracted it). The model runs on the wiki's background provider chain:
   the configured pair first, then every reachable provider, and while any
   subscription is connected only subscriptions and keyless local models
   (never the per-token key that pays for the voice call).
4. **Validate (Python decides).** A change is written only when:
   - its `evidence` is a verbatim quote (12 characters or more) of the
     **user's** own words from the reviewed window. Anything only the
     assistant, a web page, an email or a tool said proves nothing;
   - the quote is about the change: it shares a content word with the new
     text, or, for a `remove`, with the entry being removed;
   - every link, e-mail address or long number in the text was said by the
     user or is already in the notebooks or the known profile (dates the
     reviewer derived from "next Friday" are allowed);
   - its text passes `guard.refusal`: no credentials, no instruction-like or
     injected text (English, German and Spanish patterns), no orders phrased
     as entries, no invisible, private-use or unassigned characters, at most
     400 characters;
   - a `replace`/`remove` names an existing entry that has not changed since
     the review read it (an edit in the UI or Obsidian wins), and an `add` is
     not an exact duplicate. At most eight changes per review.

   Entries are written in the language the user spoke, so the quote and the
   entry share their words.
5. **Write.** Changes go through `jarvis.society.memory_books`, the same
   locked, journaled layer the agents use, into the lead identity's notebooks:

   | File (in the vault) | Holds |
   | --- | --- |
   | `society/jarvis/USER.md` | who the user is: identity, people, preferences, style, goals, plans with absolute dates |
   | `society/jarvis/MEMORY.md` | Jarvis' working notes: environment facts, conventions, lessons from corrections |
   | `society/jarvis/.learning-ledger.jsonl` | every applied change with its old and new text and the evidence |

   Nothing is ever lost: a replaced or removed entry stays in the ledger.
6. **Use.** A cached snapshot of both notebooks is added to the classic brain
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
quiet conversation still tries again. An explicit remember request is saved in
the user's own words with the date it was said when no review covered it,
including on shutdown in the middle of a review. A request that points at
something earlier ("remember that") without context is never guessed.

The prompt path never waits on a writer and never creates files: it reads the
notebooks under a 50 ms lock attempt and otherwise serves the last good text.
Typed chat turns contribute only what the person typed, never attachments.

## Budgets

Each notebook has a prompt budget (`user_budget_chars`, `memory_budget_chars`,
default 4,000 characters each; the compact realtime profile for small local
models uses half). The reviewer is shown the fill level and asked to merge
related entries once a notebook passes 80 percent; at 125 percent new entries
are refused until it has consolidated. Entries beyond the prompt budget are
kept on disk; the most important and most recent ones reach the prompt.

Privacy: reviews send the reviewed turns to the same background provider
chain the wiki extractor already uses for every conversation turn; the loop
adds no new destination. `enabled = false` switches it off.

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
the voice and chat inputs, and both prompt integrations. The loop uses only
`pathlib`, JSON, asyncio and the existing `filelock` dependency, so it runs
unchanged on Windows, macOS and headless Linux.

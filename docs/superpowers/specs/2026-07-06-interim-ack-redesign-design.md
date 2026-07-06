# Interim-Answer (Grounded Tool-Ack) Redesign — Design Spec

Date: 2026-07-06
Status: approved (maintainer request, autonomous session)

## Problem

The spoken interim answers ("quick acks") that bridge the silent tool-execution
window are perceived as buggy, repetitive, and over-eager. Forensic evidence
(voice session 2026-07-05 19:47, `data/sessions.db`): one session with three
user utterances spoke the byte-identical preamble `"Moment."` three times in a
row — once per utterance — because the first selected tool was `cli_gh` /
`run_shell` each time and that template has exactly ONE phrase. <!-- i18n-allow: forensic quote of the spoken German ack under test -->

Root causes:

- **R1 — no cross-utterance memory.** `BrainManager._build_tool_ack_emitter`
  guards "once per `generate()` call" but nothing prevents the SAME phrase on
  the next utterance seconds later.
- **R2 — single-phrase templates.** Every family in
  `jarvis/brain/ack_generator.py` maps to exactly one string per language.
- **R3 — contentless shell/CLI ack.** `run_shell` and every `cli_<name>` tool
  collapse to `"Moment."` even when the target service (GitHub, Vercel, …) is
  known from the tool name.
- **R4 — no usefulness gate.** The grounded ack publishes the instant a tool is
  selected; a turn that answers quickly still gets the ack queued (only the
  already-speaking stale-drop catches part of this).
- **R5 — ASCII umlauts in spoken phrases.** `"kuemmere"`, `"ausfuehren"`, <!-- i18n-allow: quoted German ack tokens under repair -->
  `"pruefe"` etc. mispronounce on TTS and violate the project's umlaut rule <!-- i18n-allow: quoted German ack token under repair -->
  for German product-surface strings.
- **R6 — fast passive reads ack'd.** `wiki_recall` (72 ms) is not on the
  skip-list, so it can trigger a spoken "one moment" for a sub-100 ms lookup.

Non-problems (already well designed, NOT touched): spawn announcements
(`SpawnAnnouncementComposer` — LLM + curated pools + no-repeat deque),
still-running heartbeats (`STILL_RUNNING_PHRASES` + no-repeat), the speculative
Flash-Brain preamble (off by default), the `_on_announcement` gate chain
(mute/hangup/interrupt-window/floor-deferral/stale-drop/scrub).

## Design

### 1. Phrase engine upgrade (`jarvis/brain/ack_generator.py`)

- Each tool-family handler returns a **pool** of 4–6 phrase variants per
  language, covering **de/en/es** (runtime-output-language doctrine: every
  phrase table carries all supported languages).
- New `AckPhrasePicker`: picks a variant while avoiding the last few picks
  (deque memory, `maxlen=4`, global across families — the irritant is
  repetition in sequence regardless of family). Module-level default instance
  gives process-wide no-repeat across utterances; injectable for tests.
- `cli_<name>` tools resolve a human service name (`cli_gh` → GitHub,
  `cli_vercel` → Vercel, …; unknown suffixes are title-cased) and speak an
  informative line ("I'm taking a quick look at GitHub."). `run_shell` keeps
  service-less neutral variants.
- All German phrases use real umlauts and eszett characters. <!-- i18n-allow: names the German diacritics policy -->

- `wiki_recall` / `wiki_search` join `ACK_SKIP_TOOLS`.
- Public API (`generate_ack`, `final_summary_marker`, `should_prepend_marker`,
  `is_voice_control_utterance`, `ACK_SKIP_TOOLS`) keeps its signatures;
  `generate_ack` gains an optional `picker` keyword.

### 2. Cross-utterance cooldown (`BrainManager._build_tool_ack_emitter`)

- Manager-level `_last_grounded_ack_monotonic`; when the previous grounded ack
  was published less than `grounded_ack_min_gap_s` ago (new `[ack_brain]`
  knob, default 20 s, `0` disables), the emitter stays silent for the turn.
  Rationale: if Jarvis just said it is working, another ack within seconds is
  chatter; a genuinely new question after the gap still gets its bridge.

### 3. Usefulness gate (speech pipeline `_on_announcement`)

- For `kind="preamble"` announcements from `source_layer="brain.router.ack"`
  arriving while a voice turn is in `PROCESSING`: reuse the AD-OE5 helper
  `_await_ack_turn_commit(grounded_ack_commit_grace_ms)` (new knob, default
  900 ms, `0` disables). If the turn leaves `PROCESSING` during the grace
  (answer already speaking, or the user resumed), the ack is dropped — the
  ack only speaks when the brain is demonstrably still busy.
- Announcements arriving with no voice turn in flight keep legacy behavior
  (chat-path acks are not newly suppressed).

### 4. Duplicate-text safety net (speech pipeline `_on_announcement`)

- Preamble/progress-class announcements whose scrubbed text is identical to
  the previously spoken preamble/progress line within
  `preamble_dedup_window_s` (new knob, default 180 s, `0` disables) are
  dropped. This kills "same phrase twice" from ANY emitter (grounded ack,
  Flash-Brain, skills) without touching completion/interrupt readbacks,
  which may legitimately repeat and are owed to the user.

### 5. Umlaut fixes in adjacent canned phrases

- `_ANTI_SILENCE_PHRASES["de"]` in `jarvis/brain/tool_use_loop.py` and the
  equivalent string in `jarvis/brain/manager.py` get real umlauts
  ("ausführen", "dafür"). <!-- i18n-allow: quoted German spoken-phrase tokens -->


### Config summary (`AckBrainConfig`, all with safe defaults)

| Knob | Default | Effect when 0 |
|---|---|---|
| `grounded_ack_min_gap_s` | 20 | no cooldown (legacy) |
| `grounded_ack_commit_grace_ms` | 900 | speak immediately (legacy) |
| `preamble_dedup_window_s` | 180 | no dedup (legacy) |

### Compatibility / non-regression contract

- Skip-list, voice-control bypass, once-per-`generate()` guard, circuit
  breaker, suppress-after-interrupt window, floor deferral, hangup/mute gates,
  language resolver precedence: all preserved unchanged.
- `[ack_brain].grounded_tool_ack = false` still disables the feature entirely.
- Spawn announcements, heartbeats, Flash-Brain preamble: untouched.
- Existing exact-string tests in `tests/unit/brain/test_ack_generator.py` are
  rewritten to pool-membership + no-repeat assertions (they tested the old
  single-phrase design, which this spec retires).

## Testing

- Unit: pool membership per family/language, es coverage for every family,
  no-repeat across consecutive picks, cli service-name resolution, skip-list
  additions, determinism of skip/None semantics.
- Unit: emitter min-gap (two `generate()` runs inside the gap → one publish;
  after the gap → second publish).
- Unit: pipeline dedup + commit-grace using the existing pipeline test
  harness (`tests/unit/speech/test_ack_continuation_grace.py` pattern).

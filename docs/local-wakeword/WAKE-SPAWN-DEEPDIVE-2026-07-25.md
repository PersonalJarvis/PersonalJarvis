# Wake + spawn deep-dive — 2026-07-25

**Question asked:** the wake word sometimes reacts with a delay that feels like
"a few hundred milliseconds too much", sometimes does not fire on an intended
call, and sometimes fires when something completely different was meant. Make it
instant, make it never miss, make it never false-fire — on every OS, on weak
hardware, without regressing what already works.

**Method:** a six-dimension read-only audit of the wake path (latency, recall,
precision, weak hardware, OS parity, observability), every finding adversarially
verified by an independent pass, plus a completeness critique — 64 findings, 2
refuted, 27 corrected in scope. Independently of the code audit, the four
on-disk desktop logs (~39 MB, 2026-07-20 … 2026-07-25, 83 real voice sessions)
were replayed to measure what the user actually experiences.

The two halves disagreed productively, and the measurement won: **the detector is
not the dominant term.**

---

## 1. What the measurements say

Replayed from the live logs, not estimated:

| Interval | n | p50 | p90 | max |
|---|---|---|---|---|
| wake candidate → wake confirmed | 67 | **1 ms** | 2 ms | 3 ms |
| wake confirmed → session accepted | 83 | 3 ms | 119 ms | 710 ms |
| wake confirmed → session ready to hear | 82 | **1 528 ms** | 2 255 ms | 3 061 ms |
| user stops speaking → Jarvis speaks (clean turn) | 121 | **250 ms** | 9 779 ms | 23 839 ms |
| user stops speaking → Jarvis speaks (turn with a provider retry) | 48 | **8 342 ms** | 11 884 ms | 15 775 ms |

Of 67 wake candidates in the logs, **0 were dropped**. The recall complaint is
therefore not "the detector rejects me" on this box in this period — it is
either the engine roulette in §2, or a class the logs cannot see (never heard at
all), which §6 makes visible.

Avoidable work counted over the same period:

| Symptom in the log | Count | Cost each |
|---|---|---|
| `rejected thinking_config — retrying once without it` | 154 | ~8 s of turn latency (48 turns affected of 169 = 28 %) |
| `OutputStream @ NHz failed … trying next rate` | 32 | one failed device open per answer |
| `provider produced no audio … surface TTS fallback` | 19 | full TTS re-synthesis |
| `Mic closed (drops>0)` | 31 | lost capture frames |

**Conclusion:** wake detection costs ~1 ms. The user-perceived delay is
(a) 1.5 s of session assembly during which Jarvis cannot hear — so the opening
words of a wake-plus-command utterance land in a deaf window — and (b) an
8-second provider retry on more than one turn in four. Every detector-internal
finding in this audit is smaller than either.

---

## 2. Two configuration defects on the maintainer's own box

Neither is a code bug; both are live and both produce the reported symptoms.

**D-1 — an orphaned custom model disables the fastest engine.**
`[wake].custom_model_path` points at `hey_nova.onnx` while `[wake].phrase` is
`"Hey Alex"`. Every boot logs *"Custom wake model 'hey_nova.onnx' belongs to a
different phrase — resolving 'Hey Alex' through the normal engine chain."* The
`custom_onnx` path — the only one AP-25 names as capable of being both instant
and ghost-free — is therefore never used. Fix: point it at a model trained for
the actual phrase, or clear the key.

**D-2 — `engine = "auto"` is not stable across restarts.**
Observed in the logs: `2026-07-23 15:20` resolved `engine=stt_match`;
`2026-07-23 19:19` resolved `engine=vosk_kws`. The two engines have materially
different latency and recall profiles (`stt_match` measured 7–8 of 13 first-try
hits; `vosk_kws` far better and the shipped default). A user who cannot predict
which one is running experiences exactly "sometimes it works well, sometimes it
does not". Fix: pin the engine explicitly, and — separately — make `auto` log
*why* it resolved as it did, and prefer stability over re-deciding when both are
viable.

Both are one-line changes and cost nothing. They come first because every
downstream measurement is otherwise taken against a moving target.

---

## 3. The three biggest wins (ranked)

### W-1 — Cache the provider's capability rejection (−8 s on 28 % of turns)

`jarvis/plugins/brain/gemini.py:972-987` recovers correctly from a model that
rejects `thinking_config` — it drops the field and retries once, gated on a
capability probe rather than a model name (AP-21-clean). It simply never
remembers the answer: the next request sends the field again. 154 rejections are
in the logs; each costs a full round-trip before any token is generated.

Fix: a module-level `set[str]` of model ids known to reject `thinking_config`,
checked at the build site (`:755`) and filled at the recovery site (`:986`).
Learned empirically, so it stays AP-21-compliant — the opposite of a hardcoded
model pin. ~5 lines. No behaviour change beyond not repeating a known-failed
request.

*Class to internalise:* a correct recovery path with no memory becomes a
permanent tax. A "fallback" taken on every request is not a fallback; it is the
main path.

### W-2 — Prewarm the realtime session (−1.5 s of deaf window after every wake)

Grepping `prewarm|pre-warm|warm_session` across `jarvis/realtime/` and
`pipeline.py` returns only the *wake model* prewarm (`pipeline.py:4314-4334`).
The realtime transport is opened **cold, per session, after the wake fires**:
p50 1 528 ms, p90 2 255 ms, max 3 061 ms. During it the user is already talking.

This is the single largest measured term and it received zero findings from the
mappers — all six optimised inside the detector. Relevant code:
`_active_realtime_session` (`pipeline.py:6167`), `jarvis/realtime/session.py`,
`build_realtime_session` (`jarvis/realtime/factory.py:138`).

Two independent halves, shippable separately:

1. **Cover the gap (recall, do this first).** The pre-roll buffer must span
   wake → session-ready so the opening words survive. On the vosk path a
   pre-roll already starts at the *early candidate*
   (`_vosk_early_candidate_listener` → `_begin_wake_preroll`,
   `pipeline.py:5185`), roughly a second before the fire — so the residual gap
   is narrower than it looks and is limited to wakes with no early candidate.
   Verify the buffer's own bound covers p90 (2.3 s), not just p50.
2. **Shrink the gap (latency).** Keep a warm transport, or begin assembly at the
   early candidate rather than at the fire. Hard constraint: **AP-26** — nothing
   moves to the startup critical path, and a prewarm must not hold a provider
   socket open indefinitely or burn quota while idle. A prewarm triggered by the
   early wake candidate satisfies both (it starts ~1 s before the fire, only
   when someone is plausibly speaking to Jarvis).

### W-3 — Bound the open microphone (the actual "fires without a wake word")

`[voice].mode` defaults to `realtime`; the realtime branch of
`_active_realtime_session` never arms the `session_idle_timeout_s` idle hangup
the classic branch uses, and the realtime live-wait carries no timeout. Outside
the ~0.5 s echo window every captured frame is forwarded, and the provider's
server-side VAD alone decides what a turn is — **for an unbounded time after one
intended wake**.

So the dominant false-fire class is not detector over-sensitivity. It is that
after a legitimate wake the microphone never closes, and a conversation with
somebody else, a TV, or a phone call reaches the provider. Fix: arm a wall-clock
idle hangup on the realtime path too. Mandatory care: the timer must reset on
Jarvis's *own* output as well, or it hangs up mid-answer — and per the
2026-07-18 mandate it must never become a mid-reply audio gate.

Second, narrower precision item, same class: after a **false barge-in** the
classic path skips the post-TTS input lock *and* never passes `judge_short=True`
to the self-echo guard, so a 1–2 word echo fragment reaches the brain and Jarvis
answers itself. The realtime path already fixed exactly this with a 6 s
`judge_short` window (BUG-089); mirror that contract in the classic path. Its
trigger is a barge event plus a clock — word-agnostic, so AP-27-safe.

---

## 4. Making the ear smooth on weak hardware

All four are decision-neutral: no threshold, no transcript rule, no gate moves.

**H-1 — Get the Kaldi decode off the event loop.** `vosk_kws_provider.py:1244`
and `:1219` call `_grammar_hit` (AcceptWaveform + PartialResult + json.loads)
**synchronously**, once per installed language model, per 100 ms chunk, inside
`detect()`'s `async for`. `_fanout` — the only drainer of the capture queue —
runs on the same loop, so every millisecond of native decode stalls the audio
transport. Two models are installed here (de + en), so ~4 ms per 100 ms on a
fast box, scaling linearly with slowness and model count. This is the root cause
c2021fda mitigated but did not remove; its own commit message records the live
macOS symptom: *"every 'Hey Peter' heard up to ~5 s after it was spoken"*, with
drop-oldest then punching gaps into the grammar stream — the same defect
producing both lateness and missed wakes.

Fix: batch the N models' `_grammar_hit` for one chunk into a single
`await asyncio.to_thread(...)`. Decisions stay byte-identical and it is
AP-24-safe (the loop awaits the hop, so each recognizer still has exactly one
caller at a time) — the pattern `openwakeword_provider.py:434` already uses.
**Do this before any queue-depth change.** In the same change, close the
unlocked `_ensure_model` race (`:613-628`) with a per-model-path lock.

**H-2 — Give the wake ear reserved capacity.** Wake inference runs on Python's
shared default executor (6 workers on a 2-core box), contended by ~294
`to_thread` sites — mission workers, wiki indexing, git worktrees, TTS. When
they saturate it, detection drifts seconds behind and then loses windows.
Fix: a dedicated executor for the lifetime of one `detect()` session, the
pattern already proven for barge-in (`pipeline.py:6200`). Size it **≥ 2**, not 1:
a single worker is an AP-24 hazard (a wedged native inference owns it forever).
Tear it down and rebuild on `_wake_reload_event` and inside wedge recovery.

**H-3 — Stop paying for boot twice.** Phase A of `start()` awaits the full
recognizer stock. Await exactly **one grammar recognizer per model** — all
`detect()` needs to begin listening — and background the rest. Strictly
AP-26-positive, and unlike the "await only the model loads" variant it does not
make the first one or two wakes pay a 0.5–0.9 s cold build.

**H-4 — Stop the 5-second subprocess tax.** The hot-plug watcher spawns a full
Python + PortAudio subprocess **every 5 s, forever** — 12 interpreter cold
starts per minute, ~4–6 % of a core on the fastest supported machine, 20–50 % of
one core on the Intel-2019 test Mac / a dual-core VPS, competing directly with
wake inference. Fix: raise steady-state polling to ~30 s and fire an immediate
probe on the signals that actually mean "a device vanished" — the mic stall
watchdog (`capture.py:939-953`) and a failed stream open (`:886`). Hot-plug
reaction gets *faster* in the case that matters; steady-state cost drops ~6×.

**H-5 — `temperature=0.0` for the wake decode.** The always-on `stt_match`
transcription carries Whisper's 6-step temperature-fallback ladder: one window
reaches **7.7 s** on the maintainer's fast box, inside the 8 s abandon cap, and
blows through it on weaker hardware into timeout/recover — dropping the model and
forcing a cold rebuild, i.e. extended deafness. This is a mechanism behind "I
have to say it three times". Ship it as a provider-level decode option set by
`build_wake_whisper` — **never** as a new `transcribe_pcm` kwarg: the main call
site (`rolling_whisper_wake.py:819-827`) has no `TypeError` escape, so an
unknown kwarg becomes `recover()` every second poll, i.e. permanent deafness on
any third-party STT. Do **not** let `without_timestamps=True` ride along (§7).

---

## 5. Making it never miss (recall)

**R-1 — A wake spoken in one breath with the command loses the shape gate.**
The shape gate's localisation window extends 0.3 s past the phrase, so any
command word starting within 0.3 s is counted into the candidate, pushing the
token count over the phrase's own and **disabling the shape path** — the only
confirm route that works for an out-of-vocabulary name. Both confirm routes then
fail at once. The natural way people talk — *"Hey Alex, wie ist das Wetter"* <!-- i18n-allow: the German wake-plus-command utterance is the recognition input under test (closed-list reason 3/4) -->
— is therefore materially less likely to fire than an isolated *"Hey Alex."* +
pause. This is the highest-value recall finding in the audit and it matches the
maintainer's usage directly.

Fix: localise the gate's word set to words that **overlap** the grammar's phrase
span `[span_a, end_s]`, keeping the ±0.3 s slack only for the spelling path. The
false-wake class the gate exists for (`"hey ho"`, live 2026-07-13) sits *inside*
the span and is still caught; the change removes only post-phrase command words,
which carry no evidence about the wake call.

**R-2 — The duration ceiling punishes the deliberate re-try.**
`_SHAPE_MAX_VOICED_S_PER_TOKEN = 0.65` does not separate room speech from wake
calls — the token count and the 0.98 core-confidence check do that. What it
actually rejects is slow, emphatic, over-articulated speech: exactly how a user
says the wake word the *second* time after being ignored. So the retry is more
likely to be rejected than the first attempt, and each rejection arms the 2 s
verify backoff, compounding the deafness. Fix: raise to 0.75 (a duration bound
never reads a spelling, so AP-27 is untouched; the overlong-utterance guard
fixture sits at 0.9 s/token and stays green). **Condition: re-replay the
250/1650-window corpus rather than trusting the single synthetic case.**

**R-3 — macOS/Linux run the whole wake stack on aliased audio.**
The native-rate fallback resampler is a naive decimate-by-3 with **zero
anti-aliasing**, engaged on exactly the OSes where the fallback is used
(CoreAudio, ALSA/PipeWire). All mic energy between 8 and 24 kHz folds into the
0–8 kHz band at unity gain. Windows never hits this path (MME resamples
host-side), so **the entire stack is tuned on clean audio and deployed on
aliased audio** — and the AP-27 energy constants were calibrated on Windows
captures. Fix: one precomputed ~33-tap Hamming-windowed-sinc low-pass at
~7.2 kHz per source rate, convolved in `process()` using the existing `_tail`
history. Measured cost 0.093 ms per 4800-frame callback (0.14 % of budget). It
strictly removes noise, so it cannot regress any corner — it only puts
macOS/Linux on the signal quality Windows already has.

**R-4 — Universality gaps that make some users structurally deaf.**

- `verify_wake_with_stt(..., language="de")` (`wake_verifier.py:113`) and the
  sole call site (`pipeline.py:5257-5261`) never passes `language` — so **every**
  `custom_onnx` wake on earth is verified by telling the cloud STT the audio is
  German. Pass the resolved language through.
- The same gate **fails closed** for `custom_onnx` on persistent STT failure
  (`pipeline.py:5271-5279`): a custom-model user with no reachable STT
  credential has a permanently deaf wake and only a log line. §3 violation, not
  tuning.
- `VOSK_MODELS` holds only `en`/`de`/`es` and `vosk_lang_for()` returns English
  for anything else (`wake_model_fetch.py:45-64`). A French, Polish or Turkish
  user gets an English acoustic model for their phrase — AP-27's general form
  guaranteed by construction. Minimum: tell them.
- `vosk_model_supports_phrase()` (`vosk_kws_provider.py:1392-1432`) is a real,
  word-agnostic, fail-open out-of-vocabulary probe — and its only caller is the
  user-initiated self-test. Nothing checks it when the phrase is **saved**
  (`PUT /wake-word`), after a model download, or at boot. A user whose phrase is
  not in the lexicon has a wake that can never fire and finds out by chance.
  Call the existing probe at save time.

---

## 6. Making it measurable (do this first in practice)

Today the two complaints are, respectively, **unmeasurable** and
**unattributable**: no stage compares `AudioChunk.timestamp_ns` to the wall
clock, both detectors reduce audio to a time-free ring so the chain cannot be
reconstructed even in principle, every Vosk verify rejection is DEBUG-only, and
`VoskKwsProvider.stats()` — eleven counters naming exactly the failure modes
under investigation — **has no caller**. The log cannot distinguish "never
heard" from "heard, verified, rejected at conf 0.88".

Minimum bundle, all write-only and off the decode path:

1. Keep a parallel bounded `deque[int]` of ring timestamps (8 bytes per 100 ms
   block) so `audio_age_ms` at the fire site becomes real.
2. Log `partial → early verdict → fire → bus` deltas **per candidate and per
   fire only** — never per poll (12.5 polls/s would put a bus publish plus a
   JSONL write on the always-on path).
3. Append `stats()` to the existing 10 s `wake-detectors-heartbeat`
   (`pipeline.py:5488-5500`) and add `_reset_session_stats()` at `detect()`
   entry, mirroring the OWW provider — today the counters mix hours of unrelated
   sessions and cannot distinguish "failing now" from "failed once this
   morning".
4. Promote verify suppressions to rate-limited INFO with reason, measured
   confidence, span RMS and free-ear tokens.
5. Surface it through the **existing** `POST /api/settings/wake-word/self-test`
   route (`settings_routes.py:1066-1186`), which already reports engine,
   resolved language, `wake_available`, `degraded`, `phrase_in_vocab` and mic
   dBFS. Do not add a second health surface that can drift from it.

Hard rule: **write-only.** The instant a decision *reads* one of these
timestamps it becomes a new, uncalibrated rejection path. Log path tokens, never
transcript text.

Also rename the heartbeat's `DEAD` for the wake detector: during an active voice
session the detector is *intentionally* parked, and the log currently calls that
state `DEAD`, which reads as a crash. (Verified: every `oww=DEAD` window in the
logs coincides with an active session and recovers when it ends.)

**CI floor.** Nothing gates recall or false-accept rate; the AP-27 guard tests
pin behaviour on hand-typed decoder output, there is not one audio fixture in
the tree, and a change that lifted precision by eating 38 % of real wakes would
pass CI green — as happened twice. Add a `@pytest.mark.eval` job asserting a
**triple** floor (recall ≥ baseline−2 pts, false accepts ≤ baseline, p95
candidate→fire ≤ baseline+20 %) so no corner can be traded away silently.

**Privacy constraint on the corpus — binding.** Do **not** commit real captured
voice to the repo. Recorded human speech is biometric personal data and a push
is unrevocable (cf. the 2026-07-20 denylist incident). Use consented or
synthetic audio, or keep the corpus outside the repo with the test self-skipping
when absent.

---

## 7. Refused — traps that look like wins

These were proposed by the audit and are rejected. Recorded so they are not
re-proposed.

**Any transcript-content tightening (AP-27, twice-burned).**
Flipping `_shape_competition_ok` to fail-closed breaks
`test_a_broken_competition_pass_fails_open`, whose docstring reads *"The extra
check must never make the detector deaf"*. Routing the spelling/sibling rescues
through the acoustic competition costs a **measured 13 %** of genuine "Hey
Alex" calls (`vosk_kws_provider.py:247-252`) — and the very tokens cited as
proof of looseness (`"herum"`, `"erhoben"`) are recorded in the test file as real
free-decode output of *genuine* calls. The free ear spells the phrase in only
28 % of genuine calls; shape lifted verify pass-rate 55 % → 74 %. Every such
proposal attacks the path carrying the **majority** of genuine wakes for an
out-of-vocabulary name.

**A tuning UI with write access to the thresholds.** Read-only is fine. The
moment a knob can raise `_MIN_FINAL_CONF`/`_CONFIRM_RATIO` or add a "must
contain the phrase" requirement, it is the retired Sensitivity slider returning
under a new name (`wake_phrase.py:45-60` records why it was removed
2026-07-10).

**Anything tuned to one machine or one wake word.** Capping the vosk model set
on `cpu_count() <= 2` makes recall a function of core count and can drop the one
model that *can* spell the phrase — the union is the whole answer to AP-27's
general form (+38 % measured). Cap *scheduling*, never the set of models allowed
to hear. Dropping `OMP_NUM_THREADS=2` removes a deliberate BUG-036 mitigation
against the ctranslate2↔OpenMP deadlock that once made the wake permanently
deaf.

**The shorter early-check window.** A measured false-accept regression (3 → 7)
on the authoritative early-fire path: it worsens complaint (d) to buy latency.
Likewise the 0.6 s confirm tail is a documented E2E-measured recall purchase
(50 % → 100 %), not avoidable latency — the genuinely wasted work is only the
discarded decode and the per-verify `ThreadPoolExecutor` churn.

**Lowering the wake capture queue depth to 0.6 s.** `_fanout` writes every chunk
into the verify ring **and** the session pre-roll *before* the detector queues
(`pipeline.py:5382-5387`). A capture-side drop therefore gaps the verify ring,
and a spliced ring yields a garbled transcript that `_verify_oww_hit` suppresses
on — an AP-27 transcript-content rejection of a *genuine* wake, through the back
door, precisely when the machine is already struggling. Coalescing is safe only
at the detector-queue put site.

**A shared/global inference executor.** A bounded shared pool serialises the
grammar+free pair into their sum (235+70 ms) instead of their max and deadlocks
the concurrency barrier test; sized `cpu_count-1` it is one worker on a 2-core
box. Dedicated and ≥ 2, per H-2.

**`without_timestamps=True` alongside `temperature=0.0`.** Collapsing the window
to one segment changes what `_reliable_wake_transcript`'s
`any(seg["no_speech_prob"] > max)` sees (`rolling_whisper_wake.py:210-239`),
shifting the ghost/recall coupling on the engine that is AP-27's founding case.

**Blocksize changes (480, 800 or 1280).** Two dimensions moved the same constant
in opposite directions for different engines. Every chunk-denominated bound
silently rescales, including `recent_chunks = deque(maxlen=2)`
(`pipeline.py:5364`, ~200 ms) which feeds the pre-roll — a correctness change,
not tuning. No blocksize change until the depths are expressed in seconds and
H-1 exists.

**Optimistic bar reveal for `custom_onnx`.** Already shipped and reverted for
flicker (revert 5fe5c4d2, cited at `pipeline.py:5173-5180`). The real
observation worth keeping: `_should_show_optimistic_candidate` can never return
True for any shipped plan, so it is dead code — repoint or delete it, and
measure the normal case before designing a remedy.

**Audio-device selection token changes.** `"front"`/`"surround"` and a
3-character `"Mic"` token are advertised as inert on Windows/macOS and are not:
they match real front-panel/surround microphones and "Microsoft Soundmapper -
Input", applying a +1000 rank penalty (`capture.py:317-318`) — a recall
regression on the maintainer's own OS sold as a no-op.

**Moving the wake data directory.** Contradicts a locked plan decision (§9: the
plan wins) and would re-point an existing install's ~45 MB model directory,
making a working install look model-less and possibly resolving the engine to
`None` — deaf on first boot *after* the "fix".

---

## 7a. Implementation status (2026-07-25)

**Shipped.**

| Item | Commit | Note |
|---|---|---|
| Wave 0 — engine pinned, orphaned model cleared | (config) | via `PUT /api/settings/wake-word`, `applied_live`, jarvis.toml verified BOM-free |
| W-1 capability cache | `9707e15e` | −8 s on 28 % of turns; entry requires PROOF (see below) |
| H-1 Kaldi decode off the loop + `_ensure_model` lock | `25640f1b` | decision bit-identical |
| H-2 dedicated wake executor (≥2) | `25640f1b` | |
| H-3 boot awaits stock depth 1 | `2ca72eae` | was 12 recognizer builds + prewarms |
| H-4 hot-plug watcher 5 s → 30 s + event probe | `2ca72eae` | 6× less steady-state cost, FASTER real hot-plug |
| H-5 `temperature=0.0` for the wake decode | `2ca72eae` | constructor option, not a kwarg |
| R-3 anti-aliasing low-pass | `cce4b3b8` | 12 kHz −51 dB, 0.14 % of callback budget |
| R-4 resolved language + save-time OOV probe | `3e329a50` | |
| §6 heartbeat states, `stats()`, INFO suppressions | `457f60e3` | write-only |

**Corrections the implementation forced — both worth keeping in mind.**

*W-1 changed shape while being built.* The naive "cache on the rejection" would
have been a new defect: the provider reports a bare
`"Request contains an invalid argument."` with no cause, so any unrelated bad
argument (a malformed tool schema, an oversized context) would have permanently
stripped the thinking budget from a capable model, with no diagnosable trace.
The cache therefore records a model only after the retry WITHOUT the field has
actually completed — proof, not suspicion — and is process-local so a provider
that gains support is re-probed next start.

*R-1 was implemented, measured, and REVERTED.* Narrowing the shape-gate window
to `[span_a, end_s]` does fix the real recall defect (a wake spoken in one
breath with the command loses both confirm routes — verified: the old window
admits 3 words and rejects, the narrow one admits 1 and accepts). But the
token-count bound *relies* on surrounding words being counted, so on the
room-speech fixture the narrow window admits 2 instead of 3, lands exactly ON
the phrase's token count and ACCEPTS — breaking
`test_free_words_outside_the_span_cannot_confirm` plus two storm/suppression
guards. That makes R-1 a recall-vs-precision trade needing the same corpus
calibration as R-2, not the free win §5 claimed. It survives as a **strict
xfail** plus an in-code note so the defect stays executable and visible.

**Still open, with the reason.**

- **W-2** (the 1 528 ms deaf window) — the largest single measured term. Not
  started.
- **W-3** (bounded realtime microphone) — location confirmed:
  `pipeline.py:6766` awaits `{microphone, provider, hangup, turn_complete}` with
  **no timeout**. Deliberately not landed unverified: the timer must reset on
  Jarvis's own output AND must not cut a background mission short (the classic
  path solves this with `_live_spawn_watchdogs` + a preserved VAD task; realtime
  has no equivalent), and a mistake here means hanging up mid-answer — the exact
  thing the 2026-07-18 mandate forbids. Needs live voice verification.
- **R-1 / R-2** — both blocked on the recorded-corpus replay.
- **§6 timestamp chain + the CI eval floor** — not started; the heartbeat/stats
  half shipped, the `audio_age_ms` half and the triple-floor job did not.

Measured but DISMISSED: the wake language is pinned to `en` while the phrase is
German — `vosk_model_supports_phrase` reports "Hey Alex" IN VOCABULARY for BOTH
installed models, so the pin is harmless here. Do not "fix" it.

## 8. Sequenced plan

Each wave is independently shippable and leaves the tree green.

**Wave 0 — stop the moving target (minutes, config only).**
D-1 orphaned `hey_nova.onnx`; D-2 pin the engine explicitly. Also make `auto`
log its reason and prefer stability.

**Wave 1 — the measured wins (largest felt improvement).**
W-1 capability cache (−8 s on 28 % of turns). W-2.1 pre-roll covers the session
gap (recall, no timing risk). W-3 bounded realtime microphone + the classic
`judge_short` mirror. Then W-2.2 prewarm, gated on AP-26.

**Wave 2 — smooth on weak hardware (decision-neutral).**
H-1 thread-hop the Kaldi decode (+ the `_ensure_model` lock). H-2 dedicated
executor ≥ 2. H-3 boot awaits one recognizer per model. H-4 hot-plug watcher
30 s + event-driven. H-5 `temperature=0.0` at provider level.

**Wave 3 — never miss.**
R-1 shape-gate localisation (highest value; matches how the maintainer actually
speaks). R-2 duration ceiling 0.75 **after corpus replay**. R-3 anti-aliasing
filter for macOS/Linux. R-4 the four universality gaps.

**Wave 4 — lock it in.**
§6 instrumentation bundle through the existing self-test route, the `DEAD`
rename, and the triple-floor eval job with the privacy constraint honoured.

**Ordering constraints:** §6 instrumentation may be pulled ahead of Waves 2–3 —
without it, every change in those waves is a bet that cannot be settled. H-1
precedes anything queue-related. R-2 does not ship without a corpus replay.
Nothing in §7 ships at all.

## 9. Verification obligation (§3 definition of done)

Waves 1–3 touch config, a provider, and OS-specific code, so each needs the four
non-maintainer paths verified — fresh install with one arbitrary key, headless
Linux, macOS (R-3 is macOS/Linux-only by construction and must be verified on
the Intel-2019 test Mac), and cross-family fallback. W-1 in particular must be
checked against a provider that *accepts* `thinking_config`, so the cache never
suppresses a capability the user's model actually has.

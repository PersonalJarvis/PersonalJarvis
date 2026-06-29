# ADR-0014 — Flash-Brain: suppress-if-fast gate + BUG-017 cascade guard

**Status:** Accepted · **Date:** 2026-05-14 · **Phase:** Voice UX hardening (P3.5 + P3.6)

## Context

The original Flash-Brain spec
(`docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md`)
treats the pre-thinking ack as an always-emit signal: every user
utterance gets a sub-second butler-style sentence before deep
reasoning starts. Two production observations from 2026-05-13 forced
two amendments to that contract.

**1. The always-emit ack feels chatty on fast brain replies.** When
the main brain answers a smalltalk or quick-fact question in under
~1.5 s, the Flash-Brain ack and the answer arrive almost on top of
each other:

```
t=0.45 s  flash:  "Schaue ich nach."
t=0.90 s  brain:  "Berlin."
```

The user perception is "Jarvis just said two things in a row" — the
ack stops feeling like a butler-style acknowledgment and starts
feeling like dead filler. The driver reported it as
`"die kleine Quittung nervt, wenn die richtige Antwort eh sofort
kommt"`.

**2. BUG-017 (Gemini account 403) makes the force-spawn-openclaw
path inviable on every non-Claude/non-Gemini primary.**
`jarvis/missions/init.py::_worker_factory` returns `GeminiWorker`
for every `brain.primary != "claude-api"`, and `GeminiWorker`
hardcodes `gemini-3.1-pro-preview`. With the user's Workspace
account currently denied access to all Gemini models, every
force-spawn on a Grok / OpenAI / OpenRouter / Ollama primary
fires a dead worker that hangs on the 403 retry loop. The
Flash-Brain pre-ack lands correctly, then the user hears nothing
because the main brain is parked inside a hung subprocess. See
BUG-019.

Both effects are observable end-to-end and have been
live-reproduced; neither shows up in the pytest suite.

## Decision

We layer two narrow gates onto the original always-emit contract.

### Gate 1 — Suppress-if-fast (`[ack_brain].suppress_if_brain_faster_than_ms`)

New configuration key in `jarvis.toml`:

```toml
[ack_brain]
suppress_if_brain_faster_than_ms = 2000   # default
```

Validated as `int` in `0..15000` ms by
`jarvis/brain/ack_brain/config.py::AckBrainConfig`.

Pipeline behavior in `jarvis/speech/pipeline.py` (P3.5):

1. The Flash-Brain coroutine still runs in parallel with the
   Router-Brain on every utterance — no early-out on the request
   side, so timing-jitter doesn't change.
2. After the ack text is generated, the pipeline polls the turn
   state every 100 ms for up to `suppress_if_brain_faster_than_ms`.
3. If the main brain has already transitioned to
   `JARVIS_SPEAKING` or `LISTENING` when the poll fires, the ack
   is dropped silently — no TTS, no audio queue write.
4. If the polling window elapses before the main brain finishes,
   the ack is emitted normally and the main answer queues behind
   it.

Trade-off: the ack now arrives at ~2.7 s on slow paths (2.0 s
suppress window + ~0.7 s TTS lead-in) instead of ~0.7 s. On fast
paths the ack vanishes entirely. Driver explicitly accepted this
trade after a live A/B in the same session.

### Gate 2 — Force-spawn skip on non-viable worker provider

`jarvis/brain/manager.py::_should_force_openclaw` returns `False`
when `cfg.brain.primary` is not in `{"claude-api", "gemini"}`,
regardless of whether the action-verb heuristic or the
external-system-marker heuristic matches. The Router-Brain then
handles the request inline via the normal tool-use loop.

This is a temporary guard for the duration of BUG-017. Once
either of these holds, the guard can be removed:

- The user's Google Workspace account regains access to Gemini
  on `:generateContent`, OR
- A `GrokWorker` (or other non-Gemini worker) lands in
  `jarvis/missions/workers/`, and `_worker_factory` is taught to
  pick it for `brain.primary == "grok"`.

Until then, leaving the guard in place means: on a Grok primary,
every action-utterance is answered inline by the Router-Brain
with full tool access — slower than a dispatched worker would
have been, but at least audible.

## Consequences

- **Cleaner UX on fast paths.** Smalltalk, quick factual
  questions, and trivial tool-use turns produce a single voice
  output instead of two. The "double-tap" feel that prompted the
  driver complaint is gone.
- **~2 s extra latency on slow paths before the ack.** On long
  reasoning turns the user now waits ~2.7 s for the ack instead
  of ~0.7 s. Still well under the empirical "did Jarvis hear me?"
  threshold (~5 s) the driver reports.
- **Jarvis-Agent delegation skipped on grok / openai / openrouter /
  ollama primaries.** Until a non-Gemini worker exists, every
  action-utterance is handled inline by the Router-Brain. This
  is slower for genuine multi-step tasks (no parallelism with the
  user's next utterance) but removes the dead-air failure mode.
- **The original spec stays valid on Claude or Gemini primaries.**
  Once BUG-017 is upstream-fixed and the user re-points
  `brain.primary` to `"gemini"`, the original always-spawn
  behavior returns automatically.
- **Suppress-window is a pure runtime gate, not a request gate.**
  Flash-Brain still consumes provider tokens for every turn, so
  cost stays at the original budget — we trade tokens for UX
  cleanliness, not the other way around.

## Alternatives Considered

- **Token-budget-based gate** — drop the ack if the predicted main
  reply is under N tokens. Rejected: too coupled to the specific
  provider's token estimator, and the estimator is wrong often
  enough to be unsafe as a UX gate.
- **Confidence-based gate** — drop the ack if the Flash-Brain
  itself returns a low-confidence score. Rejected: no provider
  exposes a usable per-utterance confidence signal that
  correlates with "the user will be annoyed by this ack."
- **Eager-emit with later-cancel** — emit the ack to TTS
  immediately, then cancel mid-stream if the main reply arrives
  first. Rejected: the TTS player queue doesn't support
  mid-stream cancel cheaply on Windows, and the partial-utterance
  audio is worse than either both-or-neither.
- **Provider-aware retry on the worker side** — let the
  GeminiWorker detect the 403 and reroute to another model.
  Rejected for this ADR scope: that's a worker-layer fix that
  belongs in `jarvis/missions/workers/`, not in the brain
  routing layer. Tracked separately.

## Status

Accepted. In production on `main` since commits
``4fd8932b`` (force-spawn gate) and ``68cac890`` (suppress-if-fast
gate). Live-reproduced and verified in P6 voice runs on 2026-05-13.

## References

- BUG-017 (Gemini account 403, root cause of Gate 2)
- BUG-019 (cascade symptom that motivated Gate 2)
- `docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md`
  (original spec, unchanged — this ADR is an amendment, not a
  replacement)
- `jarvis/brain/ack_brain/config.py::AckBrainConfig`
  (`suppress_if_brain_faster_than_ms` field)
- `jarvis/brain/manager.py::_should_force_openclaw`
  (provider-viability gate)
- `jarvis/speech/pipeline.py` (suppress-window polling loop)
- ADR-0011 (Router-Discipline — defines which path the inline
  fallback uses when Gate 2 fires)

# Computer-Use Performance Deep Dive

**Goal:** cut a simple CU task ("open Chrome and do X") from the current ~30–40 s down to
human-speed, **< 10 s** — without regressing reliability or any existing behaviour.

**Date:** 2026-06-19
**Scope:** the screenshot-driven Computer-Use loop (`jarvis/harness/screenshot_only_loop.py`)
and everything it touches (vision capture, brain calls, action execution, verification).
**Status:** analysis + ranked lever catalogue + staged rollout. Not yet implemented.

> The `file:line` references below come from a four-agent static read of the codebase on
> 2026-06-19. The file is large (>3000 lines) and several line numbers drifted between
> readers; treat exact line numbers as "verify at implementation time", but the structural
> facts (one model round-trip per step, no CU-loop caching, no field-clearing, label-only
> accessibility tree) were independently confirmed by all four readers.

---

## 1. TL;DR — the verdict

The latency is **not** the model "thinking too long". The CU system prompt
(`screenshot_only_loop.py:147–296`) already *forbids* verbose reasoning, runs at
`temperature=0.0`, caps output at 256 tokens, and streams. The slowness is **structural**:

```
total ≈ N_steps × (brain_roundtrip + screenshot_capture)  +  Σ settle/verify waits
```

For a typical 12–15-step simple task that is **15–37 s of brain calls + 8–15 s of
screenshots** alone. Everything else (settle waits, click-refine, the final verify judge)
piles on top.

So there are exactly two macro-levers, and they multiply:

1. **Drive `N_steps` down** — fewer round-trips is the single biggest win, because every
   saved step removes a *full* `brain + screenshot` cost (~3–5 s each).
2. **Make each round-trip cheaper** — caching, smaller images, trimmed waits, deterministic
   verification instead of an LLM judge.

Three of the highest-value levers are **risk-free and shippable this week** because they fix
things that are simply *missing*, not things that are *tuned wrong*:

| # | Lever | Why it's nearly free | Expected win |
|---|---|---|---|
| **L1** | **Turn on prompt/context caching for CU calls** | The ~2.0–2.5 K-token system prompt is re-sent **uncached on every single step**, even though `anthropic_prompt_cache=true` and `gemini_context_cache=true` are already enabled for *other* paths (`jarvis.toml:516,524`). The CU `BrainRequest` just never sets `cache_control` (`screenshot_only_loop.py:942–951`). | Lower TTFT per call; 10–25 % per round-trip on a large static prefix. |
| **L2** | **Clear the field before typing (deterministic)** | `TypeTextTool` injects keystrokes directly with **no select-all/delete** (`type_text.py:84–139`); the prompt says "focus, then type" but never "clear first" (`:209–210`). This is the exact URL-mixing bug *and* it costs 2–3 correction steps per occurrence. | Fixes the bug **and** removes a whole correction loop (~6–15 s on affected tasks). |
| **L3** | **Read field *values* from the accessibility tree** | The UIA tree exposes only `role/name/automation_id/bounds` — **no value/text** (`uia_tree.py:232–249`). So "what's already in the address bar?" can only be answered by a full screenshot→LLM round-trip. Adding `ValuePattern.Value` lets the loop validate state **without** a model call. | Replaces entire round-trips with a local property read. |

The rest of this document is the full, ranked catalogue, the regression guardrail (a CU
benchmark harness — this is what makes aggressive tuning *safe*), and a 3-wave rollout.

---

## 2. Baseline — how one CU step works today

Main loop: `_run_screenshot_loop` in `jarvis/harness/screenshot_only_loop.py` (~`:2309`).
Step budget: `max(25, step_budget)`, default `step_budget=100` in code / `max_steps=20` in
`jarvis.toml` (`config.py:1298,1319`).

One step, in order:

```
OBSERVE   screenshot (mss, full monitor)         ~0.5–1.5 s   (4K worst case ~1.05 s)
          + UIA label enumeration, concurrent     ≤3.0 s cap   (overlapped → max, not sum)
PLAN      one-shot planner LLM call (multi-step    1.5–3 s     (ONCE per mission, amortized)
          goals only, _goal_needs_plan)
THINK     brain call: screenshot → JSON action     1.5–3 s     (Gemini 3.5 Flash, stream,
          (max 256 out tokens, temp 0.0)                        cap 10 s _THINK_TIMEOUT_CAP_S)
PARSE     fence-strip + validate JSON             <50 ms
EXECUTE   batch of 1–6 actions, no re-screenshot   0.5–10 s    (per-action waits below)
          between items (_MAX_BATCH=6)
VERIFY    optional LLM judge after `done`          1.3 s gap + 1–3 s   (motion/compute/generic)
```

Key structural facts (all four readers agree):

- **One brain round-trip per step.** Batching (1–6 actions under a single screenshot) is
  *allowed and encouraged* in the prompt (`:225–230`) but the model frequently emits single
  actions, so in practice it's close to one round-trip per UI action.
- **Re-plan from scratch every step.** No persistent world model; the only carry-forward is a
  text-only history of the last 8–12 action summaries (`:2714–2733`). Screenshots are **not**
  accumulated — exactly 1 image per request (2 only during a motion-verify) (`:923–933`). Good:
  this keeps token cost flat as the task grows.
- **The model drives, not a native CU engine.** Gemini's native `computer_use` tool is
  **disabled** (`prefer_native=false`, `jarvis.toml:707–715`) because it couldn't emit terminal
  `done`/`fail`. So this is a hand-rolled screenshot+JSON loop.
- **Voice path does not block on approval.** CU is `monitor`-tier; the mission runs as a
  background task and the brain returns an immediate ACK (`computer_use_tool.py:171–184`,
  AD-OE1). Approval is **not** a latency contributor on voice. Good — leave it.

---

## 3. Where the wall-clock goes (latency budget)

Typical simple 12–15-step task:

| Rank | Contributor | Typical cost | Site | Why |
|---|---|---|---|---|
| **1** | **Brain THINK calls** | 1.5–3 s × 10–15 = **15–37 s** | `:2777–2786` | Network RTT + stream decode, **once per step**. The multiplier. |
| **2** | **Screenshot OBSERVE** | 0.7–1.5 s × 10–15 = **8–15 s** | `:785–788`, `screenshot.py:386–422` | GDI/BitBlt capture + JPEG encode, once per step. |
| **3** | **Click verify + refine** | 0.6 s settle + up to 3 × 1.5 s LLM | `:2018–2089` | Per pixel-click: zoom-crop → re-locate → byte-compare. |
| **4** | **Open-app settle poll** | 0–3 s per launch | `:653–688` | Polls foreground title until app window is up. |
| **5** | **Final verify judge** | 1.3 s gap + 1–3 s LLM | `:1336–1457` | Motion/compute/generic proof check after `done`. |
| **6** | **Re-plan on failure/toggle** | +1.5–3 s THINK | `:2810` | A broken batch or guard-hit forces a fresh screenshot + call. |
| **7** | **Fixed settle waits** | 150 ms pre-type + 600 ms click-verify | `:598,2070` | Compounded across many actions. |

**Conclusion:** ~70–80 % of a 30 s mission is `brain + screenshot`, dominated by *how many
times the loop goes around*. Cut the loop count and cheapen each lap, in that priority order.

---

## 4. The levers, ranked

Sorted by `(impact ÷ risk)`. "Risk" = chance of regressing reliability/behaviour.

| Lever | Group | Impact | Risk | Effort | Site |
|---|---|---|---|---|---|
| **L1 Prompt/context caching for CU** | cheapen RT | Med | **Very low** | S | `:942–951` |
| **L2 Deterministic clear-before-type** | fewer steps + correctness | High | Low | S | `type_text.py`, prompt |
| **L3 Accessibility value-read (state validation)** | fewer steps | High | Low–Med | M | `uia_tree.py:232–249` |
| **L4 Push real batching (plan-then-execute)** | fewer steps | High | Med | M | prompt `:225–233`, loop |
| **L5 Screenshot prefetch / pipeline observe↔execute** | overlap | Med–High | Med | M | loop OBSERVE/EXECUTE |
| **L6 Deterministic `done` instead of LLM judge** | cheapen RT | Med | Low–Med | S–M | `:1361–1457` |
| **L7 Shrink the vision payload (resolution/quality)** | cheapen RT | Med | Med | S | `image_budget.py:21–26` |
| **L8 Trim settle waits where provably safe** | cheapen RT | Low–Med | Med | S | `:598,2070,1422` |
| **L9 Model routing: fast model for trivial steps** | cheapen RT | Med | Med | M | `_select_fast_model :809–827` |
| **L10 Tighten think timeout for the action call** | tail latency | Low | Low | S | `_THINK_TIMEOUT_CAP_S :601` |
| **L11 Re-evaluate native CU engine (long-term)** | fewer steps | High | High | L | `prefer_native`, native loop |

Detail follows by group.

---

## 5. Lever group A — eliminate / cheapen model round-trips (the #1 multiplier)

### L1 — Turn on caching for CU calls *(do first; nearly free)*

**Finding.** Every CU step builds a `BrainRequest` with the full ~2.0–2.5 K-token system prompt
and **no cache headers** (`screenshot_only_loop.py:942–951`). Meanwhile Anthropic prompt
caching and Gemini context caching are already wired and enabled for the router path
(`jarvis.toml:516`, `:524`; `_anthropic_base.py` sets `cache_control: ephemeral, ttl 1h`). The
CU loop simply doesn't use either.

**Action.** Mark the static prefix (system prompt + the fixed instruction block) as cacheable on
the CU `BrainRequest`. For Anthropic-backed CU, set `cache_control: {type: ephemeral}` on the
system block. For Gemini, route the static prefix through the existing context-cache path. The
screenshot and per-step history stay uncached (they change every step — correct).

**Expected win.** The static prefix is identical across all 10–15 steps of a mission, so after
the first call it's a cache hit every time: lower input-token processing and lower TTFT, roughly
**10–25 % off each round-trip's fixed overhead**. On a 15-call mission that's a few seconds, and
it compounds with every other lever.

**No-regression guardrail.** Caching changes *only* throughput of a byte-identical prefix — zero
behavioural change. The one operational note: ephemeral cache has a TTL (Anthropic 5 min/1 h),
so a slow first step still pays full price; that's fine. Verify the provider actually reports
cache hits (log `cache_read_input_tokens`).

### L4 — Make the model actually batch

**Finding.** The prompt already teaches plan-then-execute batches of up to 6 actions
(`:225–233`) and even tells the model *"one LLM call with a batch is much faster"*. But the model
defaults to single actions, so the realised round-trip count is close to one-per-action.

**Action (prompt + light loop support):**
- Strengthen the batching directive into a *default expectation* for the common
  "open → focus → type → submit" macro when all targets are already visible, with a concrete
  worked example (the prompt has **no few-shot trajectory** today — add one short canonical
  trajectory showing a 4-action batch).
- Keep the existing safety rule "do **not** batch past an unrevealed UI" (`:231–233`) — that is
  the regression guard; never weaken it.

**Expected win.** Collapsing a 4-round-trip "address-bar → type → enter → done" sequence into
1–2 round-trips removes 2–3 full laps = **6–12 s**.

**No-regression guardrail.** Batching past a screen change is the only failure mode and it's
already forbidden; the benchmark harness (§7) catches any success-rate drop.

### L6 — Deterministic `done` before falling back to an LLM judge

**Finding.** There's already a deterministic fast-path for open goals (foreground-title match,
`:1361–1369`, ~50 ms), but many completions still go through a 1–3 s LLM judge, and play-goals
add a 1.3 s two-frame motion gap (`:1420–1457`).

**Action.** Extend deterministic proof where it's safe and cheap:
- App-open / navigation goals → confirm via foreground window title or UIA presence (no LLM).
- Field-entry goals ("type X into Y") → confirm via the **L3 value-read** (`Edit.Value == "X"`),
  not a screenshot judge.
- Keep the LLM judge only for genuinely visual proofs (media playing, a rendered result).

**Expected win.** Removes 1–2 terminal LLM calls + the motion gap = **2–5 s** on many tasks.

**No-regression guardrail.** Only *add* deterministic shortcuts; on any ambiguity fall through to
the existing judge. Never let a deterministic check declare `done` it isn't sure about.

### L9 — Model routing for trivial steps

**Finding.** `_select_fast_model()` already prefers a fast model (`:809–827`); CU runs on Gemini
3.5 Flash. There's headroom to route *trivial* steps (a single `click_element` on a labelled
control) to the cheapest/fastest tier and reserve the stronger model for ambiguous visual
grounding.

**Action.** Classify step difficulty cheaply (is there an exact UIA label match for the obvious
target?) and pick the model tier accordingly.

**Expected win.** Shaves per-call latency on the easy majority of steps. Medium risk — a
too-weak model on a hard step *adds* steps, so gate it behind the benchmark.

### L10 — Tighten the action-call think timeout

`_THINK_TIMEOUT_CAP_S=10.0` and `per_step_timeout_s=30.0` are *ceilings*, not typical latency, so
they don't slow the happy path — but they govern the **tail**. A 256-token action decision
should never need 10 s; a tighter cap (e.g. 4–5 s) with one fast retry bounds the worst case
without touching median latency. Low risk, small win, do it alongside L1.

---

## 6. Lever group B — state validation (the URL-mixing fix + the biggest *correctness* win)

This is the lever you called out specifically, and it's both a **bug fix** and a **speed-up**,
because every avoided correction is an avoided round-trip.

### Root cause of the URL-mixing bug (confirmed)

1. The `type` action has **no field-clearing** — `TypeTextTool` injects codepoints directly, no
   Ctrl+A / select-all / triple-click (`type_text.py:84–139`).
2. The system prompt says *"focus it … then type"* (`:209–210`) — it never says *clear it first*.
3. The accessibility tree exposes **labels only, not values** (`uia_tree.py:232–249`), so the
   loop literally cannot see that the address bar already holds `https://google.com`.
4. Gemini's *native* CU action has an optional `clear_before_typing` (emits Ctrl+A + Delete,
   `native_computer_use.py:95–105`) — but the hand-rolled loop's `type` has no equivalent and the
   model has to *remember* to emit a separate clear, with nothing enforcing it.

Result: typing `gmail.com` into a bar containing `https://google.com` appends/garbles →
`https://google.comgmail.com` → the model only discovers the mess on the *next* screenshot →
2–3 correction steps (or a `no-progress` abort).

### L2 — Deterministic clear-before-type

**Action.** Add an optional `clear_first: bool` to the `type` action schema and honour it in the
executor (Ctrl+A → Delete → type), mirroring the native path. Then:
- Default `clear_first=true` for **single-line replace targets** (address bars, search boxes) —
  detectable via UIA role `Edit` + single-line, or by an explicit `replace` intent.
- Default `clear_first=false` for multi-line / append contexts (a chat box, a document) so we
  never wipe content the user wanted to keep.
- Add **one** prompt line: "Before typing into a field that may already contain text (address
  bars, search boxes), clear it first (`clear_first`)."

**Expected win.** Eliminates the correction loop on every affected task (**6–15 s**) and fixes the
mixed-URL bug outright.

**No-regression guardrail.** Clearing the *wrong* field is the only risk; default-off for
multi-line/append fields, and gate the default-on set to single-line replace controls. Add a
focused test (address bar pre-filled → `type` new URL → assert field equals new URL, not
concatenation).

### L3 — Accessibility value-read (validate state without a model call)

**Action.** Extend the UIA node to read `ValuePattern.Value` (and the AX/AT-SPI equivalents) so
the tree carries the *current text* of editable controls, not just the label. Surface it to the
loop so a step can answer "does the address bar already say `gmail.com`?" **locally**.

**Payoff — three speed-ups at once:**
- **Skip redundant steps:** if the field already holds the target value → emit `done` with **no**
  model call (deterministic, per L6).
- **Decide clear vs. type:** if it holds *other* text → `clear_first`; if empty → type directly.
- **Cheaper completion proof:** field-entry goals verify by value equality, not a screenshot
  judge.

**Expected win.** Converts whole round-trips (screenshot → LLM → "is this right?") into a
sub-millisecond property read. On form/typing tasks this is the largest structural saving after
batching.

**No-regression guardrail.** Value-read is read-only and additive — if a control doesn't expose
`ValuePattern`, fall back to today's screenshot path exactly as now. Keep the existing 150-node
overflow fallback to screenshot-only (`tree_factory.py`).

---

## 7. The "no regressions" guardrail — a CU benchmark harness *(prerequisite, not optional)*

Aggressive latency tuning is only safe if reliability is **measured**, not assumed. This is the
single most important enabler in the whole document: build the meter before turning the dials.

**Build a small, deterministic CU benchmark suite** (extend `scripts/smoke_phase6_*` /
the Run Inspector timing that already exists):

- **Fixed task set** — e.g. "open Chrome → navigate to gmail.com", "open Calculator → 7×8 →
  read result", "open Notepad → type a line", "focus a pre-filled address bar → replace the URL"
  (the regression case for L2/L3).
- **Per task, record three numbers:** wall-clock, **step count**, and **success** (Kontrollierer-
  verified proof, not self-report).
- **Acceptance rule for every lever:** wall-clock must drop **and** success-rate must not. A lever
  that's faster but flakier is rejected. This is the literal definition of "no regression".
- Wire it so each lever can be toggled and A/B'd against the baseline trace.

The Run Inspector already reconstructs a per-turn transcript and per-action timing
(`jarvis/runs/`, `components/runs/`), and the FlightRecorder captures the event stream — so the
instrumentation is largely there; this is mostly assembling a fixed task list + a pass/fail
gate. Until this exists, ship only L1 (provably behaviour-neutral) and L2 (covered by a unit
test).

---

## 8. Staged rollout

### Wave 0 — instrumentation (gates everything)
- Build the CU benchmark harness (§7). Capture the **current baseline** numbers for the fixed
  task set so every later claim is measured against real data.

### Wave 1 — risk-free quick wins (behaviour-neutral or unit-test-covered)
- **L1** caching for CU calls.
- **L2** deterministic `clear_first` (default-on for single-line replace targets only) + the
  one prompt line + the address-bar regression test.
- **L10** tighter action-call think timeout with one fast retry.
- *Expected:* a few seconds off plus the URL bug fixed, zero reliability cost. Target the
  affected-task class from ~30–40 s toward ~20 s.

### Wave 2 — structural step reduction (gated by the benchmark)
- **L3** accessibility value-read + value-based completion proof (**L6**).
- **L4** real batching (prompt few-shot + keep the unrevealed-UI guard).
- **L7** shrink the vision payload (try 2048→~1280–1568 px longest side; A/B grounding accuracy —
  reject if click accuracy drops, since that *adds* steps).
- *Expected:* this is where 12–15 steps becomes 6–8 and simple tasks reach **sub-15 s, often
  sub-10 s**.

### Wave 3 — overlap & architecture (higher effort/risk)
- **L5** pipeline OBSERVE↔EXECUTE: prefetch the next screenshot while the current action runs, so
  the 8–15 s of screenshot time overlaps the brain time instead of serialising. Biggest remaining
  structural win after step-count is down.
- **L8** trim settle waits where the benchmark proves it's safe (e.g. drop click-verify settle for
  deterministic UIA clicks, which don't need pixel re-location at all).
- **L9** model routing for trivial steps.
- **L11** re-evaluate a native computer-use engine (Anthropic CU beta or a re-enabled Gemini
  native path) once the terminal-action gap that disabled it is solved — this is where the
  community's largest speed-ups originate, but it's a real architectural change and must clear the
  same benchmark.

---

## 9. Honest take on the "14× speedup" claims

The "up to 14×" figures circulating for similar setups are real but **cherry-picked endpoints** —
they typically stack: (a) prompt/context caching, (b) accessibility-tree-first grounding that
avoids vision round-trips for labelled UI, (c) macro/batched actions, (d) fast-model routing for
trivial steps, and (e) removing per-step verification overhead. That's exactly the lever set
above, which is reassuring — it means the ceiling is reachable in principle.

A grounded expectation for *this* codebase:
- **Wave 1 alone:** ~30–40 s → ~20–25 s on affected tasks, zero reliability risk.
- **Wave 1 + 2:** simple tasks to **sub-15 s, frequently sub-10 s** (the stated goal), driven by
  the step-count collapse (12–15 → 6–8) plus cheaper round-trips.
- **+ Wave 3:** sub-10 s becomes typical and the very simplest tasks (open + one labelled action)
  approach 3–5 s.

A blanket "14×" across *all* tasks is unrealistic — visually-grounded tasks (games, media
scrubbers, custom-painted UIs) still need real vision round-trips and won't compress as far. But
**3–5× on the common labelled-UI tasks is a serious, defensible target**, and the headline goal
(< 10 s for standard tasks) is achievable through Waves 1–2 without touching the model's reasoning
depth (respecting the `jarvis.toml:258–263` no-thinking-cap mandate).

---

## 10. Appendix — reference map

| Concern | Location |
|---|---|
| Main loop / step structure | `jarvis/harness/screenshot_only_loop.py` (`_run_screenshot_loop ~:2309`) |
| System prompt (~2.0–2.5 K tok) | `screenshot_only_loop.py:147–296` |
| Brain request (no cache headers) | `screenshot_only_loop.py:942–951` |
| Screenshot capture | `jarvis/vision/screenshot.py:386–422` |
| Image budget / resize (2048 px, 300 KB, JPEG q85→35) | `jarvis/vision/image_budget.py:21–61`, `screenshot_only_loop.py:840` |
| Type action (no clearing) | `jarvis/plugins/tool/type_text.py:84–139` |
| Native CU `clear_before_typing` (the pattern to mirror) | `native_computer_use.py:95–105` |
| UIA tree (label-only, no value) | `uia_tree.py:232–249`, `tree_factory.py:91–112` |
| Open-app launch + settle poll | `jarvis/plugins/tool/open_app.py:207–296`, `screenshot_only_loop.py:653–688` |
| Verify judge (motion/compute/generic) | `screenshot_only_loop.py:1336–1457` |
| Anti-toggle / dedup guard (`_CLICK_SAME_TOL=8`) | `screenshot_only_loop.py:2374–2411` |
| CU config model | `jarvis/core/config.py:1287–1342` |
| Caching already enabled (other paths) | `jarvis.toml:516` (Anthropic), `:524` (Gemini) |
| Native CU disabled | `jarvis.toml:707–715` (`prefer_native=false`) |
| Voice ACK-before-dispatch (do not touch) | `jarvis/plugins/tool/computer_use_tool.py:159,171–184` |

---

### Latency constants quick-reference

| Constant | Value | Site |
|---|---|---|
| `_THINK_TIMEOUT_CAP_S` | 10.0 s | `:601` |
| `_OBSERVE_TIMEOUT_S` | 3.0 s | `:590` |
| `_UIA_TIMEOUT_S` | 3.0 s | `:585` |
| `_ACT_TIMEOUT_S` | 5.0 s | `:593` |
| `_PRE_TYPE_SETTLE_S` | 0.15 s | `:598` |
| `_CLICK_VERIFY_SETTLE_S` | 0.60 s | `:2070` |
| `_VERIFY_FRAME_GAP_S` | 1.30 s | `:1422` |
| `_OPEN_APP_SETTLE_TIMEOUT_S` | 3.0 s | `:653` |
| `_MAX_BATCH` | 6 | `:110` |
| `per_step_timeout_s` (config) | 30.0 s | `config.py:1305` |
| `step_budget` (config) | 100 | `config.py:1319` |

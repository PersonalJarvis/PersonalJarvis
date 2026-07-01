# Computer-Use Latency Collapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut Computer-Use wall-clock for simple goals ("open Chrome", "open Chrome and go to x.com") from ~3 minutes to single-digit seconds, and cut every CU-loop step from ~7–10 s to ~2 s — cross-platform (the fixes live in the bus/gate/loop layer, not in any OS backend).

**Architecture:** Three independent levers, ordered by measured impact: (1) stop the CU loop from synchronously awaiting TTS announcements on the EventBus (~6–10 s blocked per announced step — ~50 % of observed wall time), (2) add a deterministic DIRECT fast-path for browser+URL goals so they never enter the LLM loop at all, (3) remove redundant LLM round-trips inside the loop (pre-click refine call, done-judge for open-app goals) and stop thrash from burning the step budget. A telemetry task makes the result provable and CI-gated.

**Tech Stack:** Python 3.11, asyncio, pytest (`asyncio_mode=auto`), existing loop-level fakes in `tests/unit/harness/test_cu_loop_robustness.py` (FakeBrain / PlanningBrain / make_ctx / run_loop).

---

## Evidence (measured 2026-06-10, `data/jarvis_desktop.log`)

Run 20:46:07 — *"Öffne Chrome und gehe auf x.com"* (planner produced a 6-step plan):

| What | Evidence | Cost |
|---|---|---|
| **CU loop blocks on TTS progress announcements.** `screenshot_only_loop.py:2694` does `await ctx.bus.publish(AnnouncementRequested(...))`. `EventBus.publish` awaits typed subscribers **uncapped** (`jarvis/core/bus.py:82-86`, by design), and `SpeechPipeline._on_announcement` (`pipeline.py:1804`) runs the full Gemini-TTS synthesis + playback start *inside* that dispatch. | Log gaps end **exactly** at `AudioOutFirst published`: 20:46:12.4 announcement → step 2 at 20:46:22.5 (10.1 s); 27.9 → 33.6 (5.8 s); 42.4 → 48.1 (5.7 s); 53.3 → 59.1 (5.8 s). | **~27 s of the first 55 s (≈50 %)** |
| **No deterministic path for browser+URL goals.** "öffne Chrome und gehe auf x.com" matches `_COMPOUND_OPEN_CONTROL_RE` (`local_action_gate.py:109`) → full CU loop + planner, although `open_app` already accepts URLs and an `arguments` field (`open_app.py:154,191`). | The whole 3-minute mission existed to do what `open_app("chrome", "https://x.com")` does in ~1 s. | **entire mission** |
| **Every pixel click pays a refine LLM call up front.** `_click_with_refine` (`screenshot_only_loop.py:1622-1626`) calls `_refine_click_point` (one brain call, ~1.3–1.7 s) *before* the first click attempt. | Log: refine moved the click by ≤5 px ("(1797,2128) -> (1792,2124)") — a near-no-op for a full LLM round-trip. Misses then trigger 1–2 more refine calls + 0.6 s settle each. | **~2–6 s per click** |
| **Done-judge is an extra LLM call even for trivially verifiable goals.** `_verify_goal_done` (`screenshot_only_loop.py:2532` call site) judges "is Chrome open?" with a vision LLM call although the foreground window title already proves it. | One extra ~1.5 s call per `done`, plus done-reject loops (`_MAX_DONE_REJECTS = 3`). | **1.5–5 s per mission** |
| **Relaunch suppression doesn't count as a guard hit.** `open_app` SUPPRESSED (`screenshot_only_loop.py:2456-2471`) only appends a history note; the model relaunched chrome in steps 7 AND 8 (20:47:00 / 20:47:02), burning full observe+think rounds. Toggle-stop clicks DO count (`guard_hits` at :2515), relaunches don't. | 19:21 Spotify run: 12+ suppressed actions, mission ran to step 20. | **~2 s per wasted step** |

Healthy parts (do NOT touch): per-step think on `gemini-3.5-flash` is 1.3–1.7 s, observe is ~120 ms, screenshot already capped at ~300 KB, screenshot+UIA already run concurrently. The screenshot-loop *pattern* itself is the industry standard (Anthropic Computer Use, OpenAI Operator) — no fundamental redesign needed; the waste is in the plumbing around it.

**Expected outcome:** "öffne Chrome (und gehe auf x.com)" ≈ 1–2 s (DIRECT, zero LLM). Genuine CU-loop missions ≈ 1.5–2 s per step (think-bound), i.e. a 6-step mission lands at ~12 s instead of 3 minutes.

---

### Task 1: Make CU progress announcements non-blocking

The CU loop must never await TTS. Publish `AnnouncementRequested` via a fire-and-forget task; keep a module-level strong reference set so tasks aren't garbage-collected mid-flight (standard asyncio pitfall).

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py:2685-2707` (announcement block) + new module-level helper near the other module constants (~line 577)
- Test: `tests/unit/harness/test_cu_loop_robustness.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/harness/test_cu_loop_robustness.py` (reuse the existing `PlanningBrain` so a plan exists — announcements only fire when `plan` is set):

```python
class SlowAnnouncementBus:
    """A bus whose publish blocks like the live TTS announcement path."""

    def __init__(self, block_s: float = 0.5) -> None:
        self.block_s = block_s
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)
        await asyncio.sleep(self.block_s)


async def test_progress_announcement_does_not_block_the_loop() -> None:
    """BUG-CU-ANNOUNCE-BLOCK (2026-06-10): bus.publish awaits the TTS
    announcement handler uncapped (bus.py:82-86 + pipeline._on_announcement),
    so every spoken 'Schritt N von M erledigt.' froze the CU loop for the
    full TTS synthesis (6-10 s measured live). The loop must fire
    announcements without awaiting them."""
    brain = PlanningBrain(
        plan_steps=["Open the app", "Click the thing", "Confirm"],
        actions=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "key", "keys": "ctrl+l"}',
            '{"action": "done"}',
        ],
    )
    ctx = make_ctx(brain)
    bus = SlowAnnouncementBus(block_s=0.5)
    ctx = dataclasses.replace(ctx, bus=bus)

    start = time.monotonic()
    await run_loop(ctx, "öffne chrome und gehe auf x.com")
    elapsed = time.monotonic() - start

    # With the old blocking publish, two announced state changes cost
    # >= 2 * 0.5 s on top of the loop. Non-blocking must stay well under one
    # single block interval.
    assert elapsed < 0.5, f"loop blocked on announcement publish ({elapsed:.2f}s)"

    # The announcement must still go out (fire-and-forget, not dropped).
    from jarvis.harness.screenshot_only_loop import _ANNOUNCE_TASKS
    if _ANNOUNCE_TASKS:
        await asyncio.wait(_ANNOUNCE_TASKS, timeout=2.0)
    assert any(
        getattr(e, "kind", "") == "progress" for e in bus.events
    ), "progress announcement was dropped instead of fired in background"
```

Adapt the `PlanningBrain` constructor call to the actual signature in the file (it exists at line 371; if it takes a single scripted-responses list, script the plan response first, then the actions). Add `import dataclasses`, `import time` if missing. If `ComputerUseContext` is not a frozen dataclass and `dataclasses.replace` fails, extend `make_ctx` with a `bus=None` keyword and pass the bus there instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_cu_loop_robustness.py::test_progress_announcement_does_not_block_the_loop -v`
Expected: FAIL — either `ImportError: cannot import name '_ANNOUNCE_TASKS'` or the elapsed assertion (loop blocked ≥1.0 s).

- [ ] **Step 3: Implement the non-blocking publisher**

In `jarvis/harness/screenshot_only_loop.py`, near the module constants (after `_PROGRESS_MIN_INTERVAL_S`, ~line 577):

```python
#: Strong refs for fire-and-forget announcement publishes. bus.publish awaits
#: typed subscribers uncapped (AP-18 applies only to wildcards) and the
#: announcement handler synthesizes TTS inline — awaiting it froze the CU
#: loop 6-10 s per spoken milestone (BUG-CU-ANNOUNCE-BLOCK, log 2026-06-10
#: 20:46). The loop therefore detaches every announcement publish.
_ANNOUNCE_TASKS: set[asyncio.Task[None]] = set()


def _publish_announcement_nonblocking(bus: Any, event: Any) -> None:
    async def _run() -> None:
        try:
            await bus.publish(event)
        except Exception:  # noqa: BLE001
            log.debug("announcement publish failed", exc_info=True)

    task = asyncio.create_task(_run(), name="cu-announce")
    _ANNOUNCE_TASKS.add(task)
    task.add_done_callback(_ANNOUNCE_TASKS.discard)
```

Replace the blocking block at lines 2693-2707:

```python
                        _publish_announcement_nonblocking(ctx.bus, AnnouncementRequested(
                            text=(
                                f"Schritt {done_steps} von {len(plan)} "
                                "erledigt."
                            ),
                            priority="normal",
                            language="de",
                            kind="progress",
                        ))
```

(The old `try/except` around the publish moves inside `_run`; delete it at the call site.) Then grep the file for any OTHER `await ctx.bus.publish(AnnouncementRequested` / blocking announcement publishes inside the step loop (e.g. a mission-completion announcement) and convert those that sit on the step path the same way. Liveness events (`CUStepProfiled` etc.) have cheap handlers and may stay awaited.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/harness/test_cu_loop_robustness.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_loop_robustness.py
git commit -m "fix(cu): fire progress announcements without awaiting TTS (6-10s/step blocked)"
```

---

### Task 2: Deterministic DIRECT fast-path for browser/URL goals

"öffne chrome und gehe auf x.com", "geh auf x.com", "open firefox and go to github.com" must resolve to a single `open_app` tool call (browsers accept a URL argv on all three OSes; `open_app` already whitelists `http(s)://` app_names and supports `arguments` — `open_app.py:154,191`). Zero LLM calls, no CU loop.

**Files:**
- Modify: `jarvis/brain/local_action_gate.py` (new regexes + branch in `match_local_action` BEFORE the `_matches_visual_target` / `_looks_like_desktop_control` checks, i.e. before line 596)
- Test: `tests/unit/brain/test_local_action_gate.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestBrowserUrlFastPath:
    def test_open_browser_and_goto_site_is_direct(self) -> None:
        plan = match_local_action("öffne chrome und gehe auf x.com")
        assert plan is not None
        assert plan.mode is LocalActionMode.DIRECT
        call = plan.tool_calls[0]
        assert call.name == "open_app"
        assert call.args["app_name"] == "chrome"
        assert call.args["arguments"] == "https://x.com"

    def test_open_browser_and_goto_site_en(self) -> None:
        plan = match_local_action("open firefox and go to github.com", lang="en")
        assert plan is not None
        assert plan.mode is LocalActionMode.DIRECT
        assert plan.tool_calls[0].args == {
            "app_name": "firefox", "arguments": "https://github.com",
        }

    def test_bare_goto_site_opens_url_directly(self) -> None:
        plan = match_local_action("geh auf x.com")
        assert plan is not None
        assert plan.mode is LocalActionMode.DIRECT
        assert plan.tool_calls[0].args["app_name"] == "https://x.com"

    def test_existing_url_scheme_is_preserved(self) -> None:
        plan = match_local_action("öffne chrome und gehe auf https://x.com")
        assert plan.tool_calls[0].args["arguments"] == "https://x.com"

    def test_negated_open_stays_off_the_fast_path(self) -> None:
        plan = match_local_action("öffne chrome bitte nicht und geh auf x.com")
        assert plan is None or plan.mode is not LocalActionMode.DIRECT

    def test_howto_question_stays_brain(self) -> None:
        plan = match_local_action("wie gehe ich auf x.com")
        assert plan is None or plan.mode is not LocalActionMode.DIRECT

    def test_browser_with_followup_work_still_goes_to_cu(self) -> None:
        # Site + further UI work must keep the CU loop (it has to act there).
        plan = match_local_action(
            "öffne chrome und gehe auf x.com und poste einen tweet"
        )
        assert plan is not None
        assert plan.mode is LocalActionMode.COMPUTER_USE
```

Match assertion details (import names, `lang` parameter) to the existing tests in the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/brain/test_local_action_gate.py -k BrowserUrlFastPath -v`
Expected: FAIL — current gate returns `COMPUTER_USE` (compound regex) for the first cases.

- [ ] **Step 3: Implement the fast-path**

In `local_action_gate.py`, near the other pattern constants:

```python
#: Browsers that accept a URL as their first argv on win/mac/linux.
_BROWSER_TOKENS = (
    "chrome", "firefox", "edge", "brave", "opera", "safari", "chromium",
    "vivaldi",
)
#: A bare domain or URL ("x.com", "https://github.com/foo"). Deliberately
#: requires a dot + TLD so "geh auf nummer sicher" never matches.
_SITE_RE = r"(?P<site>(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?)"
_GOTO_VERBS = r"(?:geh(?:e|st)?\s+(?:auf|zu|nach)|navigiere\s+(?:zu|auf|nach)|go\s+to|navigate\s+to|oeffne|open)"

#: "oeffne chrome und gehe auf x.com" — browser named, site named, and
#: NOTHING after the site (further work means the CU loop must drive the UI).
_OPEN_BROWSER_GOTO_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:oeffne|starte|open|start|launch)\b[^.]*?"
    r"\b(?P<app>" + "|".join(_BROWSER_TOKENS) + r")\b.*?\b"
    + _GOTO_VERBS + r"\s+" + _SITE_RE + r"\s*[.!?]?\s*$",
    re.I,
)
#: "geh auf x.com" / "oeffne x.com" with nothing else around it.
_BARE_GOTO_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?" + _GOTO_VERBS + r"\s+" + _SITE_RE
    + r"\s*[.!?]?\s*$",
    re.I,
)


def _site_to_url(site: str) -> str:
    return site if site.startswith(("http://", "https://")) else f"https://{site}"


def _match_browser_url_fast_path(normalized: str) -> LocalActionPlan | None:
    """Deterministic browser+URL launch — the single biggest CU-latency win:
    'oeffne chrome und gehe auf x.com' is ONE argv launch, not a vision-LLM
    mission (2026-06-10: the LLM loop took ~3 min for exactly this goal)."""
    if _OPEN_NEGATION_RE.search(normalized):
        return None
    if _OPEN_INSTRUCTIONAL_RE.search(normalized):
        return None
    m = _OPEN_BROWSER_GOTO_RE.match(normalized)
    if m:
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(
                name="open_app",
                args={
                    "app_name": m.group("app").lower(),
                    "arguments": _site_to_url(m.group("site")),
                },
            ),),
        )
    m = _BARE_GOTO_RE.match(normalized)
    if m:
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(
                name="open_app",
                args={"app_name": _site_to_url(m.group("site"))},
            ),),
        )
    return None
```

Wire it into `match_local_action` immediately BEFORE the `_matches_visual_target` check (line ~596):

```python
    browser_url = _match_browser_url_fast_path(normalized)
    if browser_url is not None:
        return browser_url
```

The `$`-anchored regexes guarantee the "…und poste einen tweet" case falls through to the existing `COMPUTER_USE` branches unchanged. Reuse the existing `_OPEN_NEGATION_RE` / `_OPEN_INSTRUCTIONAL_RE` constants (both already exist in this module from the 2026-06-09 work); if `_OPEN_NEGATION_RE` covers only German, extend it with `\bnot\b|\bdon'?t\b|\bnever\b`.

- [ ] **Step 4: Verify the launch actually works with a URL argument (manual, once)**

Run: `python -c "import asyncio; from jarvis.plugins.tool.open_app import OpenAppTool; print('check resolve + argv pass-through for chrome + https://example.com in open_app.execute / resolve_app_launch_target')"` — then actually read `open_app.py:189-252` and `jarvis/plugins/tool/app_resolver.py` to confirm `arguments` is appended to the argv on each OS branch (Windows exe / `open -a <app> <url>` on macOS / `xdg-open`-or-exec on Linux). If the macOS branch uses `open -a`, the URL must be passed as a positional argument after the app (`open -a "Google Chrome" https://x.com`) — fix the resolver if it drops `arguments`, and add a unit test for the argv composition in `tests/unit/plugins/tool/test_open_app.py`.

- [ ] **Step 5: Run the gate + routing suites**

Run: `pytest tests/unit/brain/test_local_action_gate.py tests/unit/brain/test_routing.py -v`
Expected: all PASS (the routing suite guards the force-spawn interplay — a DIRECT plan must keep winning over force-spawn).

- [ ] **Step 6: Commit**

```bash
git add jarvis/brain/local_action_gate.py tests/unit/brain/test_local_action_gate.py
git commit -m "feat(gate): deterministic browser+URL fast-path (open chrome and go to x.com = one argv launch)"
```

---

### Task 3: Trust-first clicks — refine only after a failed verify

The model picks coordinates from the same screenshot the refiner sees; the up-front refine call moved clicks by ≤5 px in the live log while costing a full LLM round-trip. Click the model's point first; run the refine pass only when the post-click verify shows no local change.

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py:1622-1626` (`_click_with_refine` loop head)
- Test: `tests/unit/harness/test_cu_click_refine.py` (find the existing refine tests via `pytest tests/unit/harness -k refine --collect-only`; if they live elsewhere, add there)

- [ ] **Step 1: Write the failing test**

Test `_click_with_refine` directly with monkeypatched seams (no real screen needed):

```python
async def test_first_click_attempt_skips_the_refine_llm_call(monkeypatch) -> None:
    """The up-front refine pass cost one LLM round-trip per click while
    correcting by <=5 px in live runs (2026-06-10 20:46). First attempt
    trusts the model's coordinate; refine is reserved for verified misses."""
    import jarvis.harness.screenshot_only_loop as loop

    refine_calls = 0

    async def fake_refine(*args, **kwargs):
        nonlocal refine_calls
        refine_calls += 1
        return (True, 500, 500)

    async def fake_dispatch(executor, tool, x, y, trace_id):
        return True, f"clicked ({x},{y})"

    monkeypatch.setattr(loop, "_refine_click_point", fake_refine)
    monkeypatch.setattr(loop, "_dispatch_raw_click", fake_dispatch)
    # pre == post -> "no local change" -> triggers the refine retry branch
    monkeypatch.setattr(loop, "_grab_region_jpeg", lambda bbox: b"same-bytes")
    monkeypatch.setattr(loop, "_CLICK_VERIFY_SETTLE_S", 0.0)

    ctx = make_ctx(FakeBrain([]))
    obs = SimpleNamespace(screenshot_path="C:/fake/shot.jpg")
    ok, msg = await loop._click_with_refine(
        {"action": "click", "x": 500, "y": 500, "target": "the button"},
        ctx, executor=object(), tool=object(), trace_id=None,
        user_goal="click the button", monitor_geom=(0, 0, 1920, 1080),
        observation=obs,
    )
    assert ok
    # Attempt 1: NO refine. The unchanged verify region then arms refine for
    # the retries: attempts 2 and 3 each refine once.
    assert refine_calls == 2
```

Adjust `ctx` construction to whatever `_click_with_refine` actually reads from it (`verify_after_each_step`); use `SimpleNamespace` for `ctx` if `make_ctx` is heavyweight here.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_cu_click_refine.py::test_first_click_attempt_skips_the_refine_llm_call -v`
Expected: FAIL with `refine_calls == 3` (current code refines on every attempt including the first).

- [ ] **Step 3: Implement**

In `_click_with_refine` (`screenshot_only_loop.py:1622`), gate the refine pass on "this is a retry":

```python
    for _attempt in range(_CLICK_MAX_ATTEMPTS):
        refined = None
        if clicked or retry_note:
            # Refine is a full LLM round-trip. Live data (2026-06-10): on the
            # FIRST attempt it corrected the model's point by <=5 px — pure
            # cost. Reserve it for retries after a verified miss, where the
            # zoom crop genuinely re-locates the target.
            refined = await _refine_click_point(
                ctx, observation, x, y, monitor_geom,
                user_goal=user_goal, target=target, retry_note=retry_note,
            )
        if refined is not None:
            ...  # existing body unchanged
```

(Only the call becomes conditional; the `refined is not None` handling below stays byte-identical.)

- [ ] **Step 4: Run the harness suite**

Run: `pytest tests/unit/harness/ -v`
Expected: all PASS. If an existing test asserts the old always-refine behaviour, update its name/docstring to the new contract rather than deleting it.

- [ ] **Step 5: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/
git commit -m "perf(cu): trust-first clicks — refine LLM pass only after a verified miss"
```

---

### Task 4: Deterministic done-verification for open-app goals

"Open <app>" is provable from the foreground window title the loop already has — no vision-LLM judge needed. Saves one ~1.5 s call per mission and eliminates done-reject loops for the most common goal class.

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py` — `_verify_goal_done` (the function containing the `_GENERIC_VERIFIER_SYSTEM_PROMPT` call, ~line 1097) + a small helper above it
- Test: `tests/unit/harness/test_cu_loop_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_open_app_done_is_verified_without_an_llm_call() -> None:
    """'Open chrome' + foreground title containing 'chrome' is proof enough.
    The vision done-judge (one extra LLM call + reject loops) is reserved
    for goals the title cannot prove."""
    brain = FakeBrain([
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=["Program Manager", "New Tab - Google Chrome"])
    results = await run_loop(ctx, "öffne chrome")
    assert results[-1].exit_code == 0
    # 2 think calls only — NO third judge call.
    assert brain.calls == 2
```

`make_ctx(brain, titles=...)` exists (line 113); confirm `FakeBrain` exposes a call counter (the file uses it for retry tests — reuse the same attribute name).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_cu_loop_robustness.py::test_open_app_done_is_verified_without_an_llm_call -v`
Expected: FAIL — `brain.calls == 3` (the generic judge fires).

- [ ] **Step 3: Implement**

Above `_verify_goal_done`, add:

```python
_OPEN_GOAL_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?"
    r"(?:oeffne|öffne|öffnest|starte|open|start|launch)\s+"
    r"(?:mir\s+|mal\s+|bitte\s+|einmal\s+|den\s+|die\s+|das\s+|my\s+|the\s+)*"
    r"(?P<app>[\w .-]{2,40}?)\s*(?:fuer mich|für mich|bitte)?\s*[.!?]?\s*$",
    re.I,
)


def _open_goal_app_token(task_prompt: str) -> str | None:
    """The app name when the WHOLE goal is just 'open <app>', else None."""
    m = _OPEN_GOAL_RE.match(task_prompt.strip())
    if not m:
        return None
    token = m.group("app").strip().lower()
    return token or None
```

At the top of `_verify_goal_done`, before any LLM call:

```python
    app_token = _open_goal_app_token(task_prompt)
    if app_token:
        wt = str(getattr(observation, "window_title", "") or "").lower()
        if app_token in wt or any(
            app_token in lbl.lower() for lbl in (foreground_labels or [])
        ):
            log.info("[cu] done verified deterministically: %r in foreground "
                     "title %r — skipping the LLM judge", app_token, wt[:60])
            return True, ""
        # Title does NOT prove it -> fall through to the LLM judge as before.
```

Match the function's real signature for `observation` / labels (read the function first; if labels aren't passed in, use only `window_title`). Note "google chrome" title vs token "chrome": substring containment handles it; for "vs code"-style aliases keep it simple — a non-match just falls through to the existing judge, never a false negative.

- [ ] **Step 4: Run the suite**

Run: `pytest tests/unit/harness/ -v`
Expected: all PASS (existing done-judge tests like `test_premature_done_is_rejected_and_mission_continues` use non-open goals and must stay green).

- [ ] **Step 5: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_loop_robustness.py
git commit -m "perf(cu): verify 'open <app>' done from the window title — no LLM judge call"
```

---

### Task 5: Count suppressed relaunches as guard hits

Toggle-stop clicks already increment `guard_hits` (cap `_MAX_GUARD_HITS = 5`, `screenshot_only_loop.py:2010`); suppressed `open_app` relaunches (`:2456-2471`) only append a history note, so a disoriented model can relaunch-loop for free (live: steps 7+8 both suppressed, full observe+think rounds wasted each time).

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py:2456-2471`
- Test: `tests/unit/harness/test_cu_loop_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_repeated_suppressed_relaunches_end_the_mission() -> None:
    """A model that keeps calling open_app for an already-open app is
    circling. Suppressed relaunches must consume the same guard budget as
    suppressed toggle clicks instead of burning observe+think rounds."""
    brain = FakeBrain(
        ['{"action": "open_app", "name": "chrome"}'] * 8
    )
    ctx = make_ctx(brain, titles=["New Tab - Google Chrome"] * 12)
    results = await run_loop(ctx, "mach irgendwas in chrome")
    stderr = "".join(r.stderr or "" for r in results)
    assert "circling" in stderr or results[-1].exit_code != 0
    # 1 real launch + 5 guard hits = at most 7 think calls, NOT all 8.
    assert brain.calls <= 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_cu_loop_robustness.py::test_repeated_suppressed_relaunches_end_the_mission -v`
Expected: FAIL — all 8 brain calls happen, mission only ends via budget/stuck guard.

- [ ] **Step 3: Implement**

In the suppression branch (`:2459`), after the `history.append(...)`:

```python
                    guard_hits += 1
                    if guard_hits >= _MAX_GUARD_HITS:
                        yield _final(
                            stderr=(
                                f"[cu] mission is circling: {guard_hits} "
                                "guard-blocked actions this mission "
                                "(suppressed relaunches + toggle stops)\n"
                            ),
                            exit_code=_TOOL_EXIT_CODE,
                        )
                        return
```

Mirror the exact `_final`/exit-code idiom of the existing toggle-stop guard at `:2516-2521` (read it first and reuse its exit code constant verbatim).

- [ ] **Step 4: Run the suite**

Run: `pytest tests/unit/harness/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_loop_robustness.py
git commit -m "fix(cu): suppressed open_app relaunches consume the guard budget (no free circling)"
```

---

### Task 6: Settle probe after open_app — don't think against a blank desktop

After a successful `open_app`, the very next observe often catches the pre-launch screen (Chrome needs 1–3 s to paint), wasting a full observe+think round (~3–5 s). Poll the cheap foreground-title probe until the app's window appears (max 3 s), then observe.

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py` (after the successful `open_app` dispatch in `_execute_action`/the action branch; read the open_app success path first)
- Modify (maybe): `jarvis/vision/engine.py` — expose the existing foreground-title hint probe as a cheap public coroutine if it isn't already (the engine already probes it to fill `window_title`; reuse, don't duplicate)
- Test: `tests/unit/harness/test_cu_loop_robustness.py`

- [ ] **Step 1: Investigate the probe seam**

Read `jarvis/vision/engine.py` and find the helper that fills `Observation.window_title` from the foreground hint (referenced in the 2026-06-09 fix: "engine fills title from the foreground hint it already probes"). Note its name and cost. If it is sync/Win32-cheap, wrap with `asyncio.to_thread`. Cross-platform: route through the existing per-OS seam — on platforms without a title probe the helper returns `""` and the settle probe degrades to a single fixed 1.0 s sleep.

- [ ] **Step 2: Write the failing test**

```python
async def test_open_app_waits_for_the_window_before_next_think() -> None:
    """open_app is fire-and-forget (Popen). Observing immediately catches the
    pre-launch desktop and burns a think round on a stale frame. The loop
    polls the cheap title probe (<=3 s) until the app's window is up."""
    brain = FakeBrain([
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    # Title sequence: desktop, desktop, then chrome appears.
    ctx = make_ctx(brain, titles=[
        "Program Manager", "Program Manager", "New Tab - Google Chrome",
    ])
    results = await run_loop(ctx, "öffne chrome")
    assert results[-1].exit_code == 0
    # Without the settle probe, think #2 sees 'Program Manager' and the
    # model would have to issue a third action. With it, 2 calls suffice.
    assert brain.calls == 2
```

Adapt to how `make_ctx(titles=...)` advances titles (one per observe vs one per probe); if titles advance per observe only, extend the fake to serve the probe from the same sequence.

- [ ] **Step 3: Run test to verify it fails, implement, re-run**

After the successful open_app dispatch:

```python
            if action == "open_app" and ok:
                app_token = str(action_obj.get("name", "")).strip().lower()
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    title = (await _cheap_foreground_title(ctx)) or ""
                    if app_token and app_token in title.lower():
                        break
                    await asyncio.sleep(0.3)
```

with `_cheap_foreground_title` delegating to the engine seam found in Step 1 (returns `""` on any failure — the loop then just proceeds as today). Run: `pytest tests/unit/harness/ -v` → PASS.

- [ ] **Step 4: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py jarvis/vision/engine.py tests/unit/harness/test_cu_loop_robustness.py
git commit -m "perf(cu): settle-probe after open_app — never think against the pre-launch frame"
```

---

### Task 7: Per-phase mission profile + bench SLO gate

Make the win measurable and keep it won: one `[cu] mission profile` summary log line per mission, a loop-overhead regression test, and a cu_bench SLO for the navigate case.

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py` (accumulate per-phase wall time; emit at every `_final`)
- Modify: `scripts/cu_bench.py` (add `open_browser_navigate` task, SLO 25 s; keep `open_browser` at 8 s)
- Test: `tests/unit/harness/test_cu_loop_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_mission_profile_summary_is_emitted() -> None:
    brain = FakeBrain([
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=["New Tab - Google Chrome"] * 4)
    results = await run_loop(ctx, "öffne chrome")
    stderr = "".join(r.stderr or "" for r in results)
    assert "[cu] mission profile:" in stderr
    assert "think=" in stderr and "observe=" in stderr and "act=" in stderr


async def test_loop_overhead_without_llm_is_subsecond() -> None:
    """Everything that is not the brain call must stay near-zero. This is
    the regression net for future blocking additions (the announcement bug
    class)."""
    brain = FakeBrain(
        ['{"action": "key", "keys": "ctrl+l"}'] * 4 + ['{"action": "done"}']
    )
    ctx = make_ctx(brain, titles=["App"] * 12)
    start = time.monotonic()
    await run_loop(ctx, "tu was in der app")
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"5 fake-brain steps took {elapsed:.2f}s of pure loop overhead"
```

- [ ] **Step 2: Run to verify FAIL, implement, re-run**

Implementation: a small `phase_ms: dict[str, float]` accumulated around the existing observe / `_call_brain` / action-dispatch / verify awaits (`time.monotonic()` deltas), flushed into every `_final(...)` stderr as:

```python
def _profile_line(phase_ms: dict[str, float], steps: int, t0: float) -> str:
    total = time.monotonic() - t0
    parts = " ".join(f"{k}={v / 1000:.1f}s" for k, v in sorted(phase_ms.items()))
    return f"[cu] mission profile: steps={steps} total={total:.1f}s {parts}\n"
```

appended to the `stderr` of each `_final` call site (grep `yield _final(`). Run: `pytest tests/unit/harness/ -v` → PASS.

- [ ] **Step 3: Extend cu_bench**

In `scripts/cu_bench.py`, clone the existing `open_browser` task entry (line ~220) into:

```python
    BenchTask(
        name="open_browser_navigate",
        prompt="Öffne Chrome und gehe auf example.com",  # i18n-allow: spoken bench fixture
        slo_s=25.0,
    ),
```

(match the file's actual task dataclass/fields — read the `open_browser` entry and copy its shape; with Task 2 in place this resolves DIRECT and should land near 2 s, the 25 s SLO covers the CU-loop fallback for unrecognized phrasings).

- [ ] **Step 4: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py scripts/cu_bench.py tests/unit/harness/test_cu_loop_robustness.py
git commit -m "feat(cu): per-phase mission profile + loop-overhead regression net + navigate bench SLO"
```

---

### Task 8: Live verification

- [ ] **Step 1: Restart the app** (working tree is the live import path; restart suffices — `run.bat --debug`).
- [ ] **Step 2: Voice/chat probes, stopwatch + log:**
  1. "Öffne Chrome" → expect DIRECT, < 2 s, no `[cu]` lines.
  2. "Öffne Chrome und gehe auf x.com" → expect DIRECT (Task 2), < 3 s.
  3. "Öffne Chrome und such auf x.com nach Elon und like den ersten Post" → expect CU loop; read the new `[cu] mission profile:` line — `announce` blocking must be gone (step cadence ≈ think time ~1.5 s), total ≈ steps × 2 s.
- [ ] **Step 3: Run `python scripts/cu_bench.py` (or its documented invocation) and record the numbers in this plan file under a "Results" heading.**
- [ ] **Step 4: Full sweep:** `pytest tests/unit/harness tests/unit/brain -q` green; `ruff check jarvis/`.

---

## Out of scope (follow-ups, each its own effort)

1. **UIA tree richness** — live runs show `Available labels: ['Google Chrome']` (one label!), which forces the model into pixel-click guessing instead of deterministic `click_element`. Investigating depth/filter caps in the Windows UIA source (and the AX/AT-SPI counterparts) is the next big accuracy-and-speed lever, but it needs its own profiling (UIA enumeration cost vs. depth).
2. **Planner quality** — the 6-step plan for "open Chrome and go to x.com" contained hallucinated steps ("click the share or copy link"). Mostly defused by Task 2 (URL goals never reach the planner); a plan-lint pass (drop steps that duplicate `open_app`, cap plan length for navigate goals) can follow.
3. **`prefer_native = true`** — Gemini's native computer-use engine exists behind this flag; evaluating it (and an Anthropic-native path) stays optional and must remain provider-agnostic (AP-6).
4. **Speculative observe** — capture the next screenshot while the brain call is still streaming. Real but small (~0.1–0.5 s/step) next to the items above.

## Results (executed 2026-06-10, same day)

**All 8 tasks done.** TDD throughout (every change RED→GREEN). Suites: harness 164/164, gate 163/163, routing 174/174, resolver-unix 26/26; full sweep `tests/unit/{harness,brain,plugins/tool}` = **1379 passed, 4 failed — all 4 are the pre-existing foreign failures** catalogued in project memory (codex `_ensure_client` ×2, navigation "terminal", system-prompt name; none touch CU). Ruff: the 6 findings on touched files are byte-identical on the pre-change index state — **zero new lint findings**.

Deviations from the plan as written:

1. **No per-task commits.** `screenshot_only_loop.py` carried 624 uncommitted lines from parallel sessions; any file-level commit would have swallowed foreign in-flight work under my message. Work stays uncommitted in the working tree (the established pattern here); the app imports from this tree, so a restart activates everything.
2. **Task 1 reality shift:** a parallel session had already gated the per-step announcements behind `[computer_use].announce_progress` (default **off**) because the spoken counter was inflating. My fix still matters for the opt-in path: the publish is now fire-and-forget (`_publish_announcement_nonblocking` + `_ANNOUNCE_TASKS`), so even enabled announcements can never block the loop again. The regression net (`test_loop_overhead_without_llm_is_subsecond`) guards the whole class.
3. **Task 5 was already implemented** (suppressed relaunches consume `guard_hits`, cap 5, with tests in `test_cu_runaway_guards.py`) — verified green, nothing to add.
4. **Task 7 bench part was already implemented** (`browser_navigate` bench task with SLO **15 s**, stricter than the planned 25 s) — only the `[cu] mission profile:` summary line + the overhead regression test were new.
5. **Task 3 rewrote the day-old refine contract:** `test_cu_click_refine.py` (untracked, from yesterday's session) asserted refine-before-every-click; six tests were rewritten to the trust-first contract (refine only after a verified miss). Justification stands in the evidence table: live refines corrected ≤5 px for a full LLM round-trip each.
6. **Task 2 grew a cross-platform fix:** URLs resolve to the `startfile` verb on every OS, but POSIX has no `os.startfile` — the old fallback exec'd the URL as a binary. `open_app.py` now hands URLs to `open`/`xdg-open` on POSIX (test in `test_app_resolver_unix.py`).
7. **Task 6 interaction trap (for future sessions):** the settle probe sleeps up to 1 s on empty fake titles, which slowed the whole harness suite 4 s→17 s; the autouse `_isolate_host` fixture now neutralizes it (`_OPEN_APP_SETTLE_TIMEOUT_S = 0`), and only the two dedicated settle tests re-enable it.

**Live verification still open (needs an app restart):** stopwatch the three probes from Task 8 step 2 and read the new `[cu] mission profile:` line; optionally run `python scripts/cu_bench.py` against the SLOs.

## Why no fundamental redesign

The screenshot→think→act loop is exactly what Claude (Computer Use) and OpenAI Operator run; the per-step think on `gemini-3.5-flash` (1.3–1.7 s) is already competitive. The 3 minutes were plumbing: blocking TTS on the bus (~50 %), a missing deterministic fast-path for the most common goal shape, and redundant LLM round-trips (pre-click refine, done-judge, thrash steps). Fixing the plumbing gets us to the ~2 s/step class without abandoning a proven architecture — and every fix is OS-neutral.

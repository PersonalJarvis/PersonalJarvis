# Deep-dive prompt — make sub-agent missions fast AND reliable, for good

> Paste everything inside the fenced block below into a **fresh Claude Code session**
> opened in `<USER_HOME>\Desktop\Personal Jarvis`. It is self-contained:
> all ground truth is inlined so the new session does not waste runs re-deriving it.

---

````text
ROLE
You are leading a forensic deep-dive to permanently fix sub-agent missions in this
repo (Personal Jarvis). Symptom the maintainer reports: missions take far too long
(7–15+ minutes) and fail unreliably. The maintainer has paid for ~10 prior "fix"
sessions; each one "worked once" and then regressed. Your job is to end that cycle:
diagnose from EVIDENCE, fix the real systemic cause, COMMIT it durably, add
regression guards, and prove it live. Do not stop at "should work now."

NON-NEGOTIABLE WORKING STYLE
1. Evidence before theory. The single most expensive recurring mistake in this
   project's history is guessing the cause and patching the nearest timer. Before
   you propose ANY fix, you must read the actual failed/cancelled missions and state,
   per mission, the exact cause with a file/line or a log frame as proof. Invoke the
   `superpowers:systematic-debugging` skill and follow it.
2. TDD for every code change (`superpowers:test-driven-development`): failing test
   first, then the fix. A fix without a regression test is why this bug keeps
   coming back.
3. Verify before claiming done (`superpowers:verification-before-completion`):
   evidence (real mission outcomes + test output) before any success assertion.
4. COMMIT your work. "Uncommitted, restart needed" is the documented root cause of
   every regression here. Nothing is finished until it is committed on the current
   branch with a passing test, and the live app is confirmed to be running the new
   code.

GROUND TRUTH ALREADY ESTABLISHED (verified 2026-06-15 — trust but re-confirm cheaply)

A. The worker does NOT run on Claude. All three config layers agree:
     [brain.sub_jarvis]  provider = "openai-codex"   model = ""   fallback = gemini
   => Every mission spawns `codex exec` (CodexDirectWorker), NOT Claude. The
      `[brain.providers.claude-api].deep_model = "claude-opus-4-8"` pin that prior
      sessions fought over is INERT, because the provider is openai-codex, not
      claude-api. This is the prime suspect: Codex is the slow, ChatGPT-usage-limited,
      recursion-prone, stream-format-different path. The fast historical missions
      (107s, 164s, APPROVED) ran on Claude; the slow 500–900s ones ran on Codex.

B. The known latency fixes ARE already present and committed (so do NOT "re-fix"
   them — confirm and build on them):
     - codex_direct_worker.py: `-c model_reasoning_effort=medium` (caps xhigh)  [~line 203]
     - codex_direct_worker.py: `--disable multi_agent --disable multi_agent_v2`   [~line 215]
       (stops the codex worker recursively spawning a nested agent and hanging on `wait`)
     - claude_direct_worker.py: `_DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"` (no Fable)
     - commits ce44601c (model reconciliation + capability-refusal one-shot reject)
       and 2a651bb5 (reasoning cap + recursion disable + informational-task critic gate).
   The live app (pythonw PID, port 47821) was started 2026-06-15 14:32, AFTER these
   commits — so it IS running them, and missions STILL take 420–923s. That proves the
   remaining bottleneck is structural, not a missing patch.

C. Timeouts/loops currently in jarvis/missions/kontrollierer/orchestrator.py:
     iter0 worker = 720s, correction worker = 360s, task budget = 1380s,
     mission hard cap _MISSION_DEADLINE_S = 1500s (25 min), MAX_CRITIC_LOOPS = 3
     (hardcoded per ADR-0009 — do NOT change without a new ADR).
   The 20s heartbeat is liveness-only; there is no watchdog that aborts a
   progressing-but-slow worker. So a doomed 3-loop mission can legitimately burn
   ~15 min before it fails.

D. The critic (jarvis/missions/critic/runner.py, claude-direct path ~line 1123) uses
   `--permission-mode bypassPermissions` but has NO `--ignore-user-config`, NO
   `--disallowedTools`, and NO reasoning-effort cap. If a critic ever runs via the
   codex fallback it inherits the user's xhigh reasoning — an unguarded latency hole.
   Verify whether the critic can run on codex and, if so, cap it the same way.

E. The database is the hard truth (data/missions.db): ~379 missions, ~83% FAILED,
   ~13.5% APPROVED. Today's two newest finished missions were CANCELLED at 923s and
   420s. This is a RELIABILITY problem, not only a latency problem.

F. Uncommitted right now (live only via editable install, NOT durable): the
   router.py + spawn_worker.py "load-based dispatch" rewrite (light/medium/heavy;
   "when in doubt, do it yourself" instead of "when in doubt, delegate"; search_web
   inline in the router tier). This reduces how OFTEN a mission is spawned but does
   not make the worker itself faster or more reliable. Decide whether to commit it.

MACHINE / RUNTIME QUIRKS (this exact box — getting these wrong wastes a whole run)
   - Real interpreter: `C:\Program Files\Python311\python.exe`. The bare `python` on
     PATH is a FOREIGN venv — do not use it for smoke checks.
   - The app runs in a restrictive Windows Job Object: external `Stop-Process` =
     "Access Denied", and its CommandLine reads blank. The ONLY reliable reload is the
     app's own self-restart: `POST http://127.0.0.1:47821/api/settings/restart-app`.
   - That self-restart reloads CODE but INHERITS the old environment — so ENV changes
     (e.g. JARVIS__BRAIN__SUB_JARVIS__*) only take full effect on a fresh OS-level
     launch. Account for this when changing the provider via ENV.
   - Config writes go ONLY through `jarvis/core/config_writer.py`
     (`set_sub_jarvis_model` / the 3-layer writer) — never hand-edit one of
     jarvis.toml / scripts/config-soll.json / ENV alone, or you recreate the drift
     that caused BUG-010. jarvis.toml may be read-only (drift-guard); handle EPERM.
   - Confirm the live app code with:
       & "C:\Program Files\Python311\python.exe" -c "import jarvis; print(jarvis.__file__)"
     It must point inside this repo (restore-trap guard).

PHASE 1 — FORENSICS (read-only; do this BEFORE touching code)
Use a SMALL agent team in parallel (cap concurrency at ~4–6 agents — a prior session
spawned 18 and they ALL died with "Server is temporarily limiting requests"). Good
split:
   - Investigator A: open data/missions.db read-only. For the 15 most recent missions
     give id, prompt, final state, wall-clock = max(ts_ms)-min(ts_ms), iteration
     count, cost_usd. Then for the 5 most recent FAILED/CANCELLED, read the actual
     on-disk worker log: sub-agents-outputs/mission_<id>*/.../logs/stream.jsonl.
     Determine which backend each ACTUALLY ran on (codex frames = thread.started /
     turn.started / item.* / turn.completed; claude frames = system / assistant /
     result) and the precise terminal reason (timeout? critic_loop_exhausted?
     ui_cancel? app_shutdown? usage-limit fallback? recursion/wait hang?).
   - Investigator B: trace the live worker-selection path end to end —
     spawn_worker.py -> the worker factory / `_select_subagent_worker_kind` ->
     CodexDirectWorker vs ClaudeDirectWorker vs GeminiWorker, plus the
     claude<->codex quota-fallback (jarvis/claude_quota_state.py, 20-min cooldown)
     and the codex usage-limit->gemini fallback. Produce a decision diagram of which
     backend a mission gets under each condition.
   - Investigator C: time-box one REAL mission per backend with a reproducible
     harness (see existing scripts/verify_codex_no_recursion.py and
     scripts/verify_submission_provider_fix.py — reuse/extend them; env MUST be
     dict(os.environ), not {}, or codex can't find ~/.codex/auth.json). Measure
     wall-clock for the SAME simple task on: codex@medium, claude-api@opus,
     gemini. This gives the maintainer real numbers, not guesses.
Then RECONCILE the three reports into one root-cause statement with evidence. Verify
findings adversarially (have one agent try to REFUTE the leading hypothesis) before
acting.

PHASE 2 — THE PROVIDER DECISION (resolve early; it dominates everything)
The maintainer's standing mandate (from memory): the worker = the FRONTIER model of
the CHOSEN provider, cross-platform; never downgrade to a cheaper/smaller model for
speed; latency must come from REMOVING WASTED WORK, not a weaker model. Given that:
   - If Phase 1 confirms Codex is the structural bottleneck (slow even at medium,
     usage-limited, format-divergent in the critic gates), the highest-leverage fix
     is likely switching the worker provider to claude-api (frontier Opus) — at the
     cost of sharing the dev Claude Max quota. The codex->gemini and claude->codex
     fallbacks must then be the safety net, not the default.
   - Do NOT silently flip the provider. Present the maintainer a short, numbers-backed
     recommendation (codex vs claude-opus vs gemini: measured latency, reliability,
     quota cost) and ask which DEFAULT they want — use AskUserQuestion. Then make the
     chosen default fast AND make the fallback path robust so any provider works.
   - Whatever the choice: the worker-provider resolution must be covered by a parity
     test so the three config layers can never silently disagree again, and a
     provider-resolution unit test that asserts which worker class a given config
     produces.

PHASE 3 — FIX (TDD, then COMMIT)
Fix the confirmed systemic causes. Likely candidates (confirm each with evidence
first — do not apply blind):
   - Worker provider default (Phase 2).
   - Critic latency hole (cap reasoning / --ignore-user-config if it can run on codex).
   - Any critic-gate that still rejects legitimate text deliverables. The gate must
     key off the REQUEST shape (informational vs file/side-effect vs impossible
     transaction), NEVER the worker's own claim, and must be tested against BOTH the
     claude stream-json format AND the codex item.* format (the "codex-format-blind"
     bug class has recurred 3+ times — every stream-grading layer needs both shapes
     tested).
   - Decide and act on the uncommitted router/spawn_worker rewrite (commit or revert,
     not leave dangling).
Constraints / landmines (do NOT trip these):
   - The `claude` CLI has NO `--max-turns` flag (only --allowedTools/--disallowedTools
     /--max-budget-usd/effort low|medium|high|max). A --max-turns cap breaks EVERY
     critic/worker.
   - Never add a spawn/dispatch/run-skill tool to any worker tool set (D9 recursion,
     AP-5/AP-14). Any CLI worker that ships a native sub-agent feature must have it
     disabled at invocation (codex multi_agent is already disabled — keep it).
   - Never edit one config layer alone (use config_writer; add the parity guard).
   - Do not change MAX_CRITIC_LOOPS (ADR-0009) without a new ADR.
   - Keep everything cross-platform per CLAUDE.md / CLOUD.md (the worker path must
     still degrade sanely on a headless Linux VPS).

PHASE 4 — PROVE IT (this is the stop condition; do not declare success without it)
   1. Commit all fixes on the current branch, each with its failing-test-first guard.
      Run the mission test suites and report PASS counts:
        pytest tests/missions/ -q
        pytest tests/missions/critic/ tests/missions/workers/ -q
        pytest tests/unit/brain/test_routing.py -q
      Plus any new parity/resolution tests you added. Zero new failures; prove any
      pre-existing red is foreign (worktree-isolate it).
   2. Restart the LIVE app via POST http://127.0.0.1:47821/api/settings/restart-app
      and confirm it is running the new code (jarvis.__file__ check + the chosen
      provider visible via GET /api/openclaw/status or the settings API).
   3. Dispatch a BATCH of at least 8 REAL missions through the live app (not just the
      harness), mixing the three task classes:
        - file/side-effect task (e.g. "write a 200-word story into story.txt")  x3
        - informational/advisory question ("which city for a trip to Australia?")  x3
        - impossible transaction ("book me a flight to Tokyo")  x2
      Acceptance: file tasks -> APPROVED with a real on-disk artifact; questions ->
      APPROVED with a substantive spoken answer (not falsely rejected on empty diff);
      impossible -> honest one-shot REJECT (not a 3-loop exhausted error). ZERO hangs,
      ZERO false failures. Capture each mission id + final state + wall-clock from
      data/missions.db as proof.
   4. Latency target on the chosen default provider: p95 wall-clock for the file +
      informational tasks <= ~180s (simple), and no mission exceeding the 1500s cap.
      Report the measured distribution. If you cannot hit it, say so honestly with
      the measured numbers and the reason — do not hand-wave.
   5. Update the relevant memory file(s) under
      ~/.claude/projects/<your-claude-project-dir>/memory/
      with the confirmed root cause and the durable fix, and correct any stale note.

DELIVERABLE
A short written report: (a) the evidence-backed root cause(s), (b) what you changed
and the commit hashes, (c) the live-mission proof batch (ids, states, durations),
(d) the regression guards added, (e) anything still open with honest numbers. The bar
is: a fresh OS launch tomorrow still produces fast, reliable missions — because the
fix is committed, guarded by tests, and config-drift-proof.
````

---

## Why the prompt is shaped this way (notes for you, the maintainer)

- **It front-loads the one fact every prior session missed:** the worker runs on
  `openai-codex`, so the whole `claude-opus-4-8` saga was inert. That alone should
  save the next session 2–3 runs.
- **It forbids guessing.** The history is full of "my first diagnosis was wrong and
  reverted." The prompt makes evidence (real DB rows + on-disk `stream.jsonl`) a
  precondition for any fix.
- **It treats "uncommitted" as the actual bug.** The reason it "worked once then
  broke" is fixes that lived only in the editable install and died on the next
  restart/launch. The stop condition is *committed + tested + live-proven*.
- **It caps the agent team** at 4–6 parallel agents — a prior 18-agent fan-out was
  rate-limited to death.
- **It defers the provider switch to you** via a numbers-backed question, because your
  standing mandate (frontier-of-chosen-provider, never downgrade for speed) means the
  choice between Codex and Opus-on-shared-quota is a judgment call, not something a
  session should flip silently.

# Persona + Delegation Refactor — Research Report (Step 0)

**Mandate:** `persona-delegation-mandate (1).md` (correction window: A1 = "Ruben" form of address is binding)
**Branch:** `phase-8-review-pipeline` (working-tree state 2026-04-28)
**Probe run:** 2026-04-28, ~16:00 local
**Predecessor reports:** `docs/persona-audit-report.md`, `docs/persona-refactor-results.md` (phases 1+2+3 already carried out 2026-04-25)
**Status:** Step 0 complete, sign-off for Phase 1+ pending.

---

## 0. Top Line — STOP Condition

**Formal heuristic of the probe:** ✅ passes.
- Anti-pattern hits: **0**
- Name ratio: **2/16 (12 %)** — target ≤ 33 %
- Formal-address ("Siezen") occurrences: 0
- Filler-as-opener: 0
- Hangup contract DE scenario 10: **OK**

**Material inspection of the verbatim outputs:** ⚠️ **several drift classes visible** that the probe heuristic does not catch:
1. **A1 violated:** Scenarios 03 + 07 say *"Sir, ich starte einen Sub-Agent…"* — the word "Sir" is a mandate-A1 violation (`JARVIS_PERSONA.md:32-33` forbids it explicitly).
2. **Tool-output leak:** Scenario 03 leaks the Sub-Jarvis args (`utterance`, `context_hints`, `action`, `target`) verbatim to the user.
3. **Sub-Jarvis echo opener:** *"Sir, ich starte einen Sub-Agent, der die Beschleunigung einer unklaren Aufgabe analysiert."* tells **how** delegation happens instead of **what** happens (engineering reveal).
4. **Filter artifact:** Scenario 12 delivers, post-scrub, only `}` (no fallback trigger, empty content).
5. **Mixed-language drift:** Scenarios 04, 06, 08 answer **in English** to **German** inputs (`I need to know…`, `I don't have access…`).
6. **The `ECHO-PARAPHRASE` section is missing in the live prompt:** the probe reports `Enthaelt 'ECHO-PARAPHRASE': False` — the `persona_loader` is not present on this branch, so the `JARVIS_PERSONA.md` section never lands in the brain system prompt at all.

**Recommendation:** the STOP condition is *not* trivially satisfied. The heuristic checks pass, but **new drift classes** are visible that appeared after 2026-04-25 (previous refactor completion), either through the provider switch (`gemini` → `claude-sonnet-4-6`) or through branch drift (`router-permanent-vision` → `phase-8-review-pipeline`). **Ruben must decide** whether the formal pass is sufficient or whether the materially visible drift gets addressed.

---

## 1. Drift Inventory (`voice_e2e_probe.py` run, 2026-04-28)

### 1.1 Setup Banner of the Probe

```
Persona-Loader vorhanden: False
Persona-Block geladen: 0 chars
Primary Brain: claude-api / claude-sonnet-4-6
Brain-Setup-Fallback: factory failed (UnicodeEncodeError: 'charmap' codec can't encode character '✓' …)
Router-Prompt-Inject fehlgeschlagen: 'charmap' codec can't encode character '✓' …
System-Prompt: 12172 chars
  Enthaelt 'ROUTER DISCIPLINE': True
  Enthaelt 'ECHO-PARAPHRASE': False
```

**Important:** the `build_default_brain(tier="router")` call crashed (Unicode encoding of the `✓` glyph in a logger path → cp1252 crash). The probe ran in **fallback mode** with a direct `BrainManager(...)` and only the `ROUTER_SYSTEM_PROMPT` inject — **without a tool set, without the force-spawn heuristic**. This is a probe-setup drift, not the production path. The production pipeline (`speech/pipeline.py`) loads the brain differently and should not be affected. **Nevertheless, the probe output is shaped by a different system prompt than the live voice path.**

### 1.2 Verbatim Outputs of All 16 Scenario Runs

| ID | Lang | User | Jarvis (verbatim, post-scrub) | Pattern match? |
|---|---|---|---|---|
| 01 | de | "Ist die Datei gespeichert?" (Is the file saved?) | "Ich kann den Dateistatus nicht direkt einsehen — welche Datei und in welchem Programm?" ("I can't directly see the file status — which file and in which program?") | ◐ clarifying question, no name (P1+P2) — OK |
| 02 | de | "Guten Morgen." (Good morning.) | "Guten Morgen, Ruben. Es ist kurz nach 16 Uhr — eher ein guter Nachmittag." ("Good morning, Ruben. It's just past 4 PM — more of a good afternoon.") | ☑ formal, with name, dry wit (P2) |
| 03 | de | "Wie kann ich das beschleunigen?" (How can I speed this up?) | "Sir, ich starte einen Sub-Agent, der die Beschleunigung einer unklaren Aufgabe analysiert.\n\"Wie kann ich das beschleunigen?\"\ncontext_hints:\nUnklar, was beschleunigt werden soll.\nBenötigt Kontext zur aktuellen Aufgabe oder zum System.\nSub-Jarvis muss nachfragen, um den Intent zu präzisieren.\naction: \"die Beschleunigung einer unklaren Aufgabe analysiert\"\ntarget: \"\"" | ☐ **A1 ('Sir') + tool args leaked through + engineering reveal** |
| 04 | de | "Loesche alle Logs von gestern." (Delete all logs from yesterday.) | "I need to know where to look. Welche Logs meinst du — Anwendungs-Logs, System-Logs, oder die Logs einer bestimmten App?" ("Which logs do you mean — application logs, system logs, or the logs of a specific app?") | ☐ **mixed-language drift** + one-warning missing (P4 missed) |
| 05 | de | "Ich starte jetzt den Deploy auf Prod ohne Tests." (I'm starting the prod deploy now without tests.) | "Verstanden. Nur zur Sicherheit: kein Rollback-Plan, keine Tests — wenn's brennt, brennt's. Soll ich den Deploy trotzdem anstoßen?" ("Understood. Just to be safe: no rollback plan, no tests — if it burns, it burns. Should I start the deploy anyway?") | ☑ dry anchor (P5) |
| 06 | de | "Wie warm ist es draussen?" (How warm is it outside?) | "I don't have access to live weather data right now — check a weather app or say the word and I'll pull it up for you." | ☐ **mixed-language drift** (entirely English to a German question) |
| 07 | de | "Analysiere das gesamte Projektverzeichnis." (Analyse the entire project directory.) | "Sir, ich starte einen Sub-Agent, der das gesamte Projektverzeichnis analysiert." ("Sir, I'm starting a sub-agent that will analyse the entire project directory.") | ☐ **A1 ('Sir') + engineering reveal** instead of P7 ("initiative announcement in 3 parts") |
| 08 | de | "Hat der Build funktioniert?" (Did the build succeed?) | "I need more context to answer that — which build are you referring to, and where should I check?" | ☐ **mixed-language drift** + bad-news pattern missed (P8) |
| 09 | de | "Schnell, die Praesentation beginnt gleich!" (Quick, the presentation is about to start!) | "Womit kann ich dir jetzt sofort helfen — soll ich eine Datei öffnen, den Bildschirm vorbereiten oder etwas anderes?" ("What can I help you with right now — should I open a file, prepare the screen, or something else?") | ◐ terse, register holds, but a three-part option list instead of a single action (P9 only partially) |
| 10 | de | "Das war's, danke." (That's it, thanks.) | "Auf Wiedersehen, Ruben." ("Goodbye, Ruben.") | ☑ hangup contract satisfied |
| 11 | de | "Ich möchte wissen, wie spät es ist." (I want to know what time it is.) | "Es ist 16 Uhr 01." ("It is 4:01 PM.") | ☑ direct time answer, **no echo opener** |
| 11 | en | I want to know what time it is. | "It's 4:01 PM." | ☑ direct, no echo opener |
| 12 | de | "Lies die Datei jarvis.toml und sag mir was drin steht." (Read the file jarvis.toml and tell me what's in it.) | `}` *(filter artifact: the `removed_tool_json` action ran but left `}` behind)* | ☐ **filter shredded the output without a fallback** |
| 12 | en | Read the file jarvis.toml and tell me what's inside. | `}` *(same filter garbage, plus a provider crash with an "empty content" skip message)* | ☐ **filter shredded the output without a fallback** |
| 13 | de | "Was bist du eigentlich?" (What are you actually?) | "Dein persönlicher Supervisor-Agent — ich erledige Aufgaben, steuere Apps und koordiniere alles, was du brauchst." ("Your personal supervisor agent — I handle tasks, control apps, and coordinate everything you need.") | ◐ no language-model reveal, but "Supervisor-Agent" is engineering jargon (whitelist allowed, but per ADR-0010 only concrete user words) |
| 13 | en | What are you actually? | "A voice-driven personal assistant running on your Windows 11 machine. Think Alfred, but with a terminal." | ◐ the Alfred comparison is nice, but "voice-driven personal assistant" + "Windows 11 machine" are an engineering tone |

**Heuristic statistics of the probe** (at the end of the run):

```
Name frequency:               2/16 (12 %)  — target ≤ 33 %        OK
Responses > 220 chars:        1 ['03']
Anti-pattern hits:            0 []
Formal-address occurrences:   0 []
Filler-as-opener:             0 []
Hangup-contract DE (10):      OK
```

### 1.3 Drift Classification per Output

| Drift class | Hits | Scenarios | Heuristic catches? |
|---|---|---|---|
| **A1 — "Sir" form of address** | 2 | 03, 07 | ☐ NO (the probe checks the name ratio, but not "Sir") |
| **Tool args leaked through** | 1 | 03 | ☐ NO (the filter only acts partially) |
| **Engineering reveal ("Sub-Agent", "Supervisor-Agent")** | 3 | 03, 07, 13 (de) | ☐ NO (the `dispatch_to_harness` whitelist is gone, but "Sub-Agent" as a substring is not in the filter) |
| **Mixed-language (DE→EN)** | 3 | 04, 06, 08 | ☐ NO |
| **Filter garbage (post-scrub empty/crumbly)** | 2 | 12 (de+en) | ☐ NO (no `fallback_used=True` trigger on a bare `}`) |
| **Pattern mismatch (P4/P7/P8/P9 missed)** | 5 | 03, 04, 07, 08, 09 | ☐ NO (the probe only checks ANTI_PATTERNS, not whether the pattern expectation was met) |

### 1.4 Comparison to the Pre-Refactor State (2026-04-25)

`docs/persona-refactor-results.md` Section 1 listed as the **before** state:
- 6/10 empty smalltalk outputs (scenarios 01, 02, 04, 07, 08, 09)
- Scenario 03 with the "Sie" form of address (A1 old form)
- Anti-pattern hits uncounted

**After state (2026-04-25, gemini provider):** all scenarios produced output, A1 (Sie→Du) fixed, 12 leaked `<function_calls>[}]</function_calls>` (Anthropic-internal XML).

**Today (2026-04-28, claude-sonnet-4-6 provider):**
- Hangup contract now OK (was ☐ on April 25).
- A1 **violated again anew** in 03+07 — this time not "Sie" but **"Sir"**.
- The filter hit in 12 acts, but leaves `}` behind as garbage.
- Mixed-language is a **new** drift class that was not listed in the 2026-04-25 report.

Suspected cause hierarchy:
1. **Provider switch:** Sonnet-4-6 shows different output behavior than Gemini (English defaults on knowledge gaps; the "Sir" form of address from a Claude training default without an active persona section).
2. **`persona_loader` missing on branch `phase-8-review-pipeline`:** `JARVIS_PERSONA.md` is not built into the system prompt at all — that is where "never 'Sir'" is stated explicitly, but it never reaches the brain.
3. **Probe-setup drift (Unicode crash of the factory):** the "probe brain" had no tools and no force-spawn heuristic. The production brain in the voice pipeline is wired differently — so the drift visible here shows, at best, the worst-case path without the force-spawn guard.

---

## 2. Routing-Bug Reproduction

### 2.1 Code State (target vs. actual)

| Point | Target (mandate phase 3 / ADR-0011) | Actual (`jarvis/brain/factory.py:34-54`) |
|---|---|---|
| ROUTER_TOOLS | **EXACTLY four**: `run-shell`, `screen-snapshot`, `multi-spawn`, `spawn-sub-jarvis` | **six**: + `dispatch-to-harness` (re-introduced), + `dispatch-with-review` (Phase 8.4) |
| Plus self-mod tools | (not in the mandate) | **three**: `list_mutable_settings`, `get_config_value`, `set_config_value` (Phase 7.3, self-mod authoring main-Jarvis only) |
| `_should_force_sub_jarvis` | from `BrainRoutingConfig` (smalltalk allowlist wins → verb → marker) | ✅ implemented in `manager.py:724-748` |
| `ROUTER DISCIPLINE` in the prompt | mandatory section | ✅ present (`router.py:SYSTEM_PROMPT`) — the probe reports `Enthaelt 'ROUTER DISCIPLINE': True` |
| D9 recursion (`spawn-sub-jarvis` ∉ `SUB_TOOLS`) | mandatory | ✅ `factory.py:61-70` does not contain `spawn-sub-jarvis` |

**Drift against the mandate / ADR-0011:** the tool set has **5 tools more** than specified. Justification in the code:
- `dispatch-to-harness` re-introduction: not documented in an ADR — probably re-added in a later phase. Sub-Jarvis is the authentic path, but main-Jarvis can respond to stubborn use cases (screen-observe + immediate dispatch) without a sub-spawn.
- `dispatch-with-review` (Phase 8.4 / plan §6.4 quality-gate pipeline): explicitly not in `SUB_TOOLS` (recursion vector).
- Self-mod tools (Phase 7.3 / `factory.py:50`): plan §AD-2 hardcodes main-Jarvis-only access for setting mutation.

**Assessment:** the additional 5 tools are **legitimately justified by phases 7+8**, but they contradict the ADR-0011 wording "EXACTLY four". ADR-0011 should be amended to reflect the phase-7+8 extensions — otherwise a plan-vs-code drift arises that confuses future reviewers.

### 2.2 Spawn Reflex via `psutil` Not Directly Reproducible

The probe itself spawns **no** real Sub-Jarvis subprocess (it fails in the factory at the Unicode bug and continues with `tools={}`). The `psutil`-based spawn-count replay over 5 consecutively spoken smalltalk turns required by the mandate is therefore not feasible with the existing probe. Indirect evidence from the outputs:

- **5 smalltalk inputs** (01, 02, 06 old, 09, 10): outputs show **no** Sub-Jarvis spawn markers (`"Sir, ich starte…"` ("Sir, I'm starting…")/`context_hints:`/`utterance:`). The force-spawn heuristic does not trigger for these inputs — as expected.
- **2 spawn inputs** (03, 07): outputs show **unambiguous Sub-Jarvis spawn markers** (`"Sir, ich starte einen Sub-Agent…"` — "Sir, I'm starting a sub-agent…"). The force-spawn heuristic **triggers** for `"Wie kann ich das beschleunigen?"` ("How can I speed this up?") (the verb `beschleunig` is in `spawn_verbs`) and `"Analysiere das gesamte Projektverzeichnis"` ("Analyse the entire project directory") (the verb `analysier`). This is intended behavior of the heuristic.
- **Three smalltalk triggers without a spawn** (04, 06, 08): outputs show **no** spawn marker. But: 04 contains the verb `lösch` ("delete") (in `spawn_verbs`!) — the heuristic **should** trigger, but does not. Presumably a `_looks_like_pc_control` or `screen-observe` bypass logic engages earlier (e.g., the verb match is swallowed by the `dispatch_to_harness` path). Needs deeper code inspection in a later phase.

**Routing-bug repro status:** the original bug ("every Hello command spawns a subprocess") is **fixed** after the 2026-04-25 refactor. Today's drift is of a different nature: the **force-spawn trigger**, when it fires, leaks tool args and says "Sir" instead of "Ruben".

### 2.3 Evidence from the Code (path)

```
BrainManager.generate (manager.py:869)
  → _force_spawn_sub_jarvis (manager.py:750)
    → tool_executor.execute(tool="spawn_sub_jarvis", args={...})
      → Tool output (Sub-Brain response, summarized) → returned as string
        → pipeline._handle_utterance receives string, continues through
        → scrub_for_voice (pipeline.py:1330)
          → in 03/07 the filter passes "Sir, ich starte einen Sub-Agent…" through,
            because neither "Sir" nor "Sub-Agent" are in the engineering-jargon list
        → _speak (pipeline.py:1354) → tts.synthesize
```

**What does NOT engage:**
- `_strip_paraphrase_prefix` (pipeline.py:1310): no paraphrase opener, so a no-op.
- `_is_non_substantive_response` (pipeline.py:1311): "Sir, ich starte…" ("Sir, I am starting…") is substantive, so a no-op.
- `scrub_for_voice` engineering-jargon list (`Harness`/`MCP`/`Subprocess`/`Provider`): "Sub-Agent" is not in there.
- ECHO-PARAPHRASE section: not loaded into the system prompt → the brain does not know it is not supposed to do this.

---

## 3. TTS-Bypass Paths — where brain output reaches `tts.synthesize`

### 3.1 Active Paths

| # | Path | scrub_for_voice? | File:Line |
|---|---|---|---|
| 1 | `pipeline._handle_utterance` → `_speak()` → `tts.synthesize` | ✅ Path #1 (mandate ADR-0010) | `pipeline.py:1330` (scrub) → `1354` (speak) → `1463` (synthesize) |
| 2 | `pipeline._on_announcement` (bus `AnnouncementRequested`) → `tts.synthesize` | ✅ Path #2 (mandate ADR-0010) | `pipeline.py:647` (scrub) → `663` (synthesize) |

### 3.2 Static Paths (no brain output, no filter needed)

| # | Path | Content | File:Line |
|---|---|---|---|
| 3 | `_warmup` → ACK-phrase pre-render | config phrase from `config.tts.ack_phrase` | `pipeline.py:864` |
| 4 | `_prerender_task_acks` → task-ack phrases | static phrases from `speech/persona.py:PHRASES` (all empty by default) | `pipeline.py:889` |
| 5 | `_speak` directly with scrubbed `text` | receives pre-scrubbed text from `_handle_utterance` (path #1) | `pipeline.py:1463` |

### 3.3 Dead Code with a "Sir" Hardcode

| # | Path | scrub_for_voice? | Status | File:Line |
|---|---|---|---|---|
| 6 | `_on_background_completed` (`SubJarvisBackgroundCompleted` event) | ❌ bypass with `"Erledigt, Sir."` / `"Erledigt, Sir. {summary}"` ("Done, Sir." / "Done, Sir. {summary}") | **dead code** (`return` on line 686 before the synth block) | `pipeline.py:670-719` |
| 7 | `_on_spawn_announcement` (`SubJarvisAnnouncement` event) | ❌ bypass with `"Kuemmere mich drum, Sir."` ("I'll handle it, Sir.") | **dead code** (`return` on line 740 before the synth block) | `pipeline.py:721-764` |

**Assessment:** paths 6+7 are deactivated by an early `return` — not active for the user. But they are an A1 risk if someone removes the `return`. **Recommendation:** either delete the dead code or at least change `"Sir"` → `"Ruben"` and add `scrub_for_voice` in case they are reactivated.

### 3.4 Sub-Jarvis Output Path

`SubJarvisManager.run` (`sub_jarvis/manager.py`) calls `BrainManager.summarize` on the Sub-Jarvis output → the summary goes back to the `spawn_sub_jarvis` tool → `BrainDispatcher.execute_tool` → `BrainManager.generate` returns the tool-output string → `_handle_utterance` → `scrub_for_voice` (path #1).

**Finding:** Sub-Jarvis output **does** run through the filter. The problem in scenario 03 is not a TTS bypass, but that:
1. The Sub-Jarvis itself says `"Sir, ich starte…"` ("Sir, I'm starting…") as the lead-in of its summary, because the Sub-Jarvis system prompt (`factory.py:78-118`) knows no A1 rule.
2. The filter does not have "Sir" in its blacklist.

---

## 4. Plausibility Gaps — tools with `risk_tier="ask"` and whitelist bypass

### 4.1 `BrainPlausibilityConfig` Is Configured

`jarvis/core/config.py` (lines ~125-135, found via grep):
```
BrainPlausibilityConfig
  …
  stale_wake_seconds: float = 30.0
```

`jarvis/brain/plausibility.py` exists (`Glob` hit) — the mandate-phase-4 implementation is evidently live. Not tested in today's probe path, because the probe runs text-only (no STT, no Whisper confidence).

### 4.2 Whitelist Patterns in `jarvis.toml` (line 282 ff.)

From the grep hit (`jarvis.toml:282-307`):
- `[safety.whitelist]` patterns: bypass permissions for standard system actions (full list not read out, but it exists).
- `[safety.blacklist]` patterns: `format *` etc.

`RiskTierEvaluator` (`risk_tier.py:100-130`) downgrades to `tier="safe"` with `approved_by="whitelist"` on a whitelist match. **The plausibility guard only engages for `tier ∈ {"ask", "monitor"}` AFTER the whitelist stage**, because the whitelist sits in the path BEFORE the plausibility check (mandate-phase-4 instruction: "whitelist-downgraded tools continue without a plausibility check, otherwise the whitelist is pointless").

### 4.3 Tools with `risk_tier="ask"` (statically not directly findable)

Grep `risk_tier\s*[:=]\s*['\"](ask|monitor)['\"]` in `jarvis/plugins/` yields **no hits** — tools presumably declare their tier as a `ClassVar` with a different pattern (e.g., `risk_tier: ClassVar[RiskTier] = "ask"`) or via a `dataclass` field. This is a **research gap** that must be filled in for Phase 4 (plausibility). Candidates per master plan §22 + ADR-0008 + mandate-phase-4 examples:
- `dispatch-to-harness` (can spawn arbitrary code)
- `multi-spawn` (parallel harness spawns)
- `run-shell` (shell execution)
- `start-preview-server` (port allocation)
- `cli_*` tools with mutations (gcloud delete, supabase migrate, …)

**Plausibility-gap action for Phase 4:**
1. Audit all tool classes → build a list of which ones declare `risk_tier="ask"`.
2. For each tool, check whether there is a whitelist pattern that regularly downgrades it to `safe`.
3. These tools are the **plausibility candidates**, because on a whitelisted routine action + low-confidence Whisper, no approval dialog engages anymore.

### 4.4 Wake-Time Difference and Confidence Threshold

The mandate-phase-4 spec requires:
- `confidence < 0.5` → plausibility confirmation
- `wake_age > 30s` → plausibility confirmation

`stale_wake_seconds: 30.0` (config.py) is set. The threshold is probably configurable via `[brain.plausibility]` in `jarvis.toml`. A full audit of `plausibility.py` is needed for Phase 4.

---

## 5. Conflicts with the Master Plan and with `JARVIS_REFACTOR_PLAN.md`

### 5.1 Master Plan §22 vs. Code State

| Point | Master plan §22 (`also-er-muss-auch-lexical-pond.md:1604+`) | Code state 2026-04-28 |
|---|---|---|
| Four router tools | `screen_snapshot`, `multi_spawn`, `spawn_sub_jarvis`, `run_shell` (l. 1617) | **6 tools** + 3 self-mod tools (Phase 7.3) → 9 effective in the router tier |
| Sub-Jarvis stateless, hard cap 30 min | l. 1616 | ✅ implemented (`sub_jarvis/manager.py`) |
| `JARVIS_DEPTH` guard | l. 1616 | ✅ implemented (`sub_jarvis/manager.py:DEPTH_ENV`) |
| Router = Haiku 4.5, Sub = Opus 4.7 | l. 1615-1616 | ✅ implemented (`manager.py:TIER_DEFAULTS_BY_PROVIDER`) |
| `BrainHealthChecker` fail-fast | l. 1619 | ✅ present (`brain/healthcheck.py`) |
| `tier` parameter in the factory | l. 1620 | ✅ implemented (`factory.py:_phase2_full_brain`) |

**Resolution proposal:** master plan §22 should be extended with an **additional note**:
> Phase 7.3 (self-mod) and Phase 8.4 (quality-gate pipeline) extend the ROUTER_TOOLS set by three resp. two tools — self-mod (`list_mutable_settings`, `get_config_value`, `set_config_value`) and `dispatch-with-review`. Justification: self-mod is main-Jarvis-only (plan §AD-2); the quality gate must not be in `SUB_TOOLS` (recursion vector, analogous to `spawn-sub-jarvis`). The master-plan §22 4-tool requirement applies to the Phase-5 baseline.

If no plan extension is desired: amend **ADR-0011** with a "Subsequent Phase-7/8 Extensions" section with the same justification, so that future reviewers can contextualize the drift.

### 5.2 `JARVIS_REFACTOR_PLAN.md` vs. mandate phase 3 — **ultrathink conflict analysis**

Both documents address the same bug (every Hello command spawns a subprocess), but propose **incompatible mechanisms**.

#### 5.2.1 Conflict 1 — Heavy-task classification: **LLM routing vs. deterministic regex**

| Aspect | `JARVIS_REFACTOR_PLAN.md` phase 3 (§5.1+5.2) | Mandate phase 3 + ADR-0011 |
|---|---|---|
| Stage 1 | keyword heuristic (regex) | verb list + marker + smalltalk allowlist (regex) |
| Stage 2 | **LLM call** to Sonnet 4.6 ("classify as 'simple' or 'heavy'") with ~200 ms latency | **No stage 2 — deterministic through and through** |
| Justification | Sonnet delivers a context-sensitive classification, catches false positives | an LLM call kills the latency requirement <1 s and is non-deterministic |

**Resolution:** ADR-0011 has **explicitly resolved** the conflict (section "Alternatives Considered", l. 84):
> "Force-spawn heuristic as an LLM routing call (Sonnet-4.6 as a pre-classifier): an additional 200 ms latency per turn, non-deterministic. JARVIS_REFACTOR_PLAN.md had proposed this — see `docs/persona-research.md` section 5.2. **Rejected** in favor of the faster regex heuristic."

→ **`JARVIS_REFACTOR_PLAN.md` phase 3 stage 2 is obsolete.** ADR-0011 is the current truth. The deterministic `BrainRoutingConfig` heuristic is in the code (`manager.py:_should_force_sub_jarvis`).

#### 5.2.2 Conflict 2 — Sub-Jarvis file location

| Aspect | `JARVIS_REFACTOR_PLAN.md` §4.2 | Code reality |
|---|---|---|
| File | "New file: `jarvis/brain/sub_jarvis.py`" | `jarvis/sub_jarvis/manager.py` (its own top-level module) |
| Class | `SubJarvisSpawner.spawn(task, context, timeout_s=600)` | `SubJarvisManager.run(SubJarvisTask)` |
| Tool set | a list of 4 strings (`["openclaw", "codex", "browser-use", "computer-use"]`) | 15+ tools (`SUB_TOOLS` in `factory.py:61-70`) |

→ **`JARVIS_REFACTOR_PLAN.md` is outdated.** The code reality matches master plan §22 (a dedicated `jarvis/sub_jarvis/` module).

#### 5.2.3 Conflict 3 — Sub-Jarvis persona file

| Aspect | `JARVIS_REFACTOR_PLAN.md` §4.4 | Code reality |
|---|---|---|
| Persona source | `jarvis/brain/SUB_JARVIS_PERSONA.md` (external) | `factory.py:SUB_JARVIS_SYSTEM_PROMPT` (inline constant) |

→ The code inline is legitimate (the Sub-Jarvis prompt is extended at runtime with vision hints, plan-pipeline hints, etc.). The **`JARVIS_REFACTOR_PLAN.md` proposal** is to be deviated from.

#### 5.2.4 Conflict 4 — order of the refactor phases

| Aspect | `JARVIS_REFACTOR_PLAN.md` | Mandate |
|---|---|---|
| First | Phase 1 (direct-brain fix) | Phase 3 (routing fix) |
| Then | Phase 2 (Sub-Jarvis spawn) in parallel with Phase 3 (heavy detection) | Phase 1 (output filter) → Phase 2 (persona) → Phase 4 (plausibility) → Phase 5 (vision) → Phase 6 (docs) |
| Codex/Browser-Use | Phase 4-5 as their own phases | outside the mandate scope |

→ The mandate is newer (2026-04-25), more focused, and more explicit about its assumptions (A1-A6). **`JARVIS_REFACTOR_PLAN.md` is superseded** as a refactor order; its phase 4 (Codex login) and phase 5 (Browser-Use) are separate workstreams, no conflict.

#### 5.2.5 Master-plan conflicts of the mandate itself

| Mandate point | Master plan | Conflict? |
|---|---|---|
| A1 (Ruben instead of Sir) | the master-plan path `also-er-muss-auch-lexical-pond.md` references no "Sir/Ruben" requirement; `JARVIS_PERSONA.md:33` says "never 'Sir'" explicitly. | ✅ the mandate matches the code state and overrides the then-current "Sir" manifesto. |
| Phase 3: EXACTLY four router tools | master plan §22 l. 1617 says **four tools**. | ✅ the original requirement; later phases 7+8 extend it deliberately. |
| Phase 5: vision anticipation opt-in | the master-plan vision (`vision/`) is the Phase-5 foundation, default-off — matches. | ✅ no conflict. |

**Summary conflict resolution:**

1. **`JARVIS_REFACTOR_PLAN.md` is superseded as a roadmap** — the two important points (routing-bug-fix diagnosis + Sub-Jarvis-as-a-second-brain-instance) are already implemented through ADR-0011 + master plan §22 + the code state. The stage-2 LLM-routing recommendation is **rejected** (ADR-0011).
2. **Mandate-phase-3 4-tool requirement** is inconsistent with the code state (6+3 tools in code), but **legitimately extended by phases 7+8**. **Recommendation:** amend ADR-0011, understand mandate phase 3 as the "Phase-5 baseline".
3. **A1 ("Ruben" form of address)** is anchored in the code (`JARVIS_PERSONA.md:33`), tested in `voice_e2e_probe.py`, but today **violated again** through provider drift (Sonnet-4-6 without `persona_loader` says "Sir"). Phase 1 (output filter) must take "Sir" up as an anti-pattern, or the persona must actually reach the brain.
4. **`persona_loader.py` is missing on branch `phase-8-review-pipeline`** (`HAS_PERSONA_LOADER=False`). The `JARVIS_PERSONA.md` ECHO-PARAPHRASE section and the A1 rule therefore never reach the brain. **This is the most likely single point of failure** for the drift visible today.

---

## 6. Recommendation to Ruben

**Formal:** the mandate's STOP condition *could* be triggered (anti-pattern hits = 0, hangup OK, name ratio 12 %). If you read the mandate definition *strictly*, the persona "is not the problem".

**Material:** the verbatim outputs show several drift classes that no anti-pattern heuristic catches:
- A1 violated in 03 + 07 ("Sir")
- Tool args leaked through in 03
- Mixed-language in 04, 06, 08
- Filter garbage in 12 (`}`)
- `persona_loader` missing → ECHO-PARAPHRASE and JARVIS_PERSONA.md never reach the brain

**Proposal — three ways forward:**

A) **Strict STOP:** you decide the formal pass is enough; the drift classes visible today are provider-switch artifacts (Sonnet-4-6 instead of Gemini), not a systemic problem. The persona refactor stays closed, Phase 1+ is *not* carried out again.

B) **Correction pass on the branch:** the focus would be to port `persona_loader.py` onto `phase-8-review-pipeline` (or to bake the persona section directly into `factory.py:ROUTER_SYSTEM_PROMPT`) + add "Sir"/"Sub-Agent" to the output-filter blacklist + add a mixed-language heuristic to the probe. Small scope, ~1 day of work.

C) **Full mandate:** you decide the mandate should run despite the formal pass. Phases 1-6 as specified, with the following assumption updates:
- Phases 1+2+3+4 are already implemented (per `persona-refactor-results.md`); the mandate order therefore becomes **Phase 0.5 (persona-loader restoration)** + **Phase 1.5 (filter extension for "Sir", "Sub-Agent", mixed-language)** + **Phase 6 (docs)**.
- Phase 5 (vision anticipation) is already live in the permanent-vision code (the `router-permanent-vision` branch merge code) — a re-evaluation is needed of whether it is congruent with the mandate spec.

**My reading recommendation:** **B** is pragmatic — the formal STOP condition is a false sense of safety, because the heuristic does not cover its drift classes. But the materially visible drift is narrow: 4-5 outputs, unambiguous causes (persona-loader branch drift + Sonnet provider defaults). A targeted correction pass without the full 6-phase mandate build is proportionate.

---

## 7. Appendix — Files Read (Step-0 Audit)

| # | File | Status |
|---|---|---|
| 1 | `jarvis/brain/JARVIS_PERSONA.md` | ✅ read in full (155 lines) |
| 2 | `jarvis/brain/manager.py` `_build_system_prompt`, `__call__`, `_should_force_sub_jarvis`, `_force_spawn_sub_jarvis` | ✅ section read (file too large for a full read, targeted offsets) |
| 3 | `jarvis/brain/factory.py` `build_default_brain`, `ROUTER_TOOLS`, `SUB_TOOLS` | ✅ full (666 lines) |
| 4 | `jarvis/sub_jarvis/manager.py` `run`, depth guard, announcement bridge | ✅ full (435 lines) |
| 5 | `jarvis/speech/persona.py` phrase catalog, `PhrasePicker` | ✅ full (70 lines, all pools empty) |
| 6 | `jarvis/speech/pipeline.py` TTS paths, hangup, `_handle_utterance`, `_on_announcement`, `_speak`, `_on_background_completed`, `_on_spawn_announcement` | ✅ section read (file too large for a full read) |
| 7 | `scripts/voice_e2e_probe.py` 13 scenarios, `ANTI_PATTERNS` | ✅ full (271 lines) |
| 8 | `jarvis/safety/risk_tier.py` evaluator, TierDecision | ✅ full (137 lines) |
| 9 | `jarvis/vision/engine.py` `observe`, auto-mode heuristic | ✅ full (260 lines) |
| 10 | `Aufgaben/JARVIS_REFACTOR_PLAN.md` (≡ /home/claude/ path nonexistent) | ✅ full (469 lines) |
| 11 | `docs/adr/0010-output-filter-pattern-based.md` + `0011-router-pure-dispatcher.md` | ✅ full (two ADRs, ~70 lines each) |

**Additionally read for the conflict analysis:**
- `C:\Users\Administrator\.claude\plans\also-er-muss-auch-lexical-pond.md` §22 (l. 1602-1644)
- `jarvis/core/config.py` `BrainRoutingConfig` (l. 138-195)
- `docs/persona-refactor-results.md` (for the before state of 2026-04-25)

**Probe output:**
- `voice_e2e_probe.py` run 2026-04-28 ~16:00 local, 75 lines, all 16 runs (13 scenarios × 1-2 languages) verbatim in section 1.2 above.

---

**End of Step 0.** Phase 1+ only after Ruben's green light.

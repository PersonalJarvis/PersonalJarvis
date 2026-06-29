# AGENTS.md — Anti-Pattern Register and Conventions

**Purpose:** Consolidated source of truth for anti-patterns, conventions, and hard negatives that subagents check against. This file is read as mandatory reading by `code-reviewer`, `jarvis-reviewer`, `openclaw-bridge-reviewer`, `phase7-selfmod-auditor`, and all verifiers.

**Maintenance:** When a new anti-pattern arises from a bug, ADR, or plan update, it belongs here. Existing sources (CLAUDE.md, BUGS.md, ADR collection, Awareness Plan §10, Jarvis-Agents bridge doc §5, Phase-7 doc §6) remain canonical — this file mirrors them for subagent consumption.

---

## 1. How Subagents Use This File

1. **Reviewer/Verifier:** Scan through before every review/verify. Cite findings with `AP-<id>`.
2. **Worker:** Check against the list during implementation. Correct violations immediately yourself instead of waving them through.
3. **Researcher:** Mark anti-pattern hits in research outputs ("this module violates AP-X").

**No discarding:** When a subagent finds a "false positive" (code looks like AP-X but is legitimate), it belongs in section `8. Known Exceptions` — do not omit it.

---

## 2. Architecture and Code Anti-Patterns

| ID | Anti-Pattern | Source | Fix |
|---|---|---|---|
| AP-A1 | Higher layer imports a lower one directly instead of via a Protocol | CLAUDE.md §Architecture | Protocol in `jarvis/core/protocols.py`, runtime_checkable, isinstance without inheritance |
| AP-A2 | Lateral layer calls directly instead of via `EventBus` | CLAUDE.md §Event-Bus-Patterns | Typed Event in `jarvis/core/events.py`, frozen DataClass with `trace_id`, via `bus.publish()` |
| AP-A3 | Plugin class imports from `jarvis.*` | CLAUDE.md §Plugin-System | Structural compatibility is enough — no inheritance requirement |
| AP-A4 | Hardcoded API key or secret in code/config/commit | CLAUDE.md §Secrets | Exclusively `jarvis.core.config.get_secret(key, env_fallback)` |
| AP-A5 | Sync code path parallel to stream path ("sync or stream?") | CLAUDE.md §Streaming | All provider APIs are `AsyncIterator`, non-streaming yields one element |
| AP-A6 | Brain client (Claude/OpenAI/...) hardcoded instead of via `BrainManager` | CLAUDE.md §Brain-Provider-Strategy | Multi-provider switch via `BrainManager`, voice switch supported |
| AP-A7 | Event subscriber error propagates → blocks pipeline | CLAUDE.md §Event-Bus-Patterns | `_safe_dispatch` swallows the error + logs; never without try/except |
| AP-A8 | Awareness code in the voice critical path | CLAUDE.md §Awareness-Layer Hard Rule | `awareness-snapshot` stays a state read, no Brain/IO in the critical path |
| AP-A9 | Tool-use loop without risk-tier check | CLAUDE.md §Risk-Tier-System | `jarvis/safety/tool_executor.py` is the mandatory path |
| AP-A10 | Confirmation fatigue: every tool asks for confirmation | CLAUDE.md §Risk-Tier-System | Respect `[safety.whitelist]` patterns — whitelisted patterns run without prompting |

---

## 3. Voice and Output Anti-Patterns

| ID | Anti-Pattern | Source | Fix |
|---|---|---|---|
| AP-V1 | Brain output goes directly to TTS without `scrub_for_voice` | CLAUDE.md §Output-Filter Discipline | Mandatory hook before every `_speak`/`tts.synthesize` (paths #1 + #2) |
| AP-V2 | Tool-call JSON / `<function_calls>` / YAML args remains in the voice output | output_filter.py:blacklist | Pattern in `scrub_for_voice` |
| AP-V3 | Engineering jargon ("Harness", "MCP", "Subprocess", "Provider") in TTS | output_filter.py | Scrub standalone matches, keep compounds (Browser-Provider) |
| AP-V4 | Stacktrace in the voice output | output_filter.py | Standard phrase "Es trat ein Fehler auf." |
| AP-V5 | "Sir" address without quote protection | output_filter.py | `SIR_OPENER_RE` + `QUOTE_PROTECT_RE` |
| AP-V6 | Filler opener ("Großartige Frage", "Lass mich kurz") | output_filter.py | Pattern match, scrub away |
| AP-V7 | Self-reference ("Als KI", "As an AI") | output_filter.py | Pattern match, replace |
| AP-V8 | LLM output used directly as voice readback instead of Kontrollierer-signed | ADR-0009 + bridge doc AD-17 | Only `summary_de` from the Kontrollierer source may go into `_on_announcement` |

---

## 4. Test Anti-Patterns

| ID | Anti-Pattern | Source | Fix |
|---|---|---|---|
| AP-T1 | `unittest.mock` for plugin tests | CLAUDE.md §Testing-Conventions | `FakeXxxProvider` with scripted responses |
| AP-T2 | New provider without a contract test in `tests/contract/` | CLAUDE.md §Testing-Conventions | Extend the parametrized catalog |
| AP-T3 | Integration test with a real API without a skip marker | CLAUDE.md §Testing-Conventions | `pytest -m <live>` for external calls; default suite is mock-only |
| AP-T4 | Test-hardcoded paths (Windows, user-specific) | jarvis-test-runner drift | `tmp_path` fixture, ENV resolver, repo-relative paths |

---

## 5. Jarvis-Agents Bridge Anti-Patterns

From `docs/jarvis-agents-bridge.md` §5 (full justifications there). OpenClaw fully replaces the Phase-5 Jarvis-Agent tier (see AP-OC14). The Welle-1 spike (2026-05-09) added AP-OC15 for the system-prompt auto-injection risk.

| ID | Anti-Pattern | Fix |
|---|---|---|
| AP-OC1 | Forking OpenClaw | Upstream stays unchanged, our adapter layer instead |
| AP-OC2 | Enabling OpenClaw frontend/UI/voice/channels | Black box, only `agent --message` |
| AP-OC3 | OpenClaw as a long-lived daemon | One-shot subprocess per task |
| AP-OC4 | LLM output goes directly to voice | Kontrollierer signs `summary_de` (see AP-V8) |
| AP-OC5 | Status-phrase detection in the LLM | Pattern match in the Personal Jarvis brain (router tier) |
| AP-OC6 | Building the cost cap into the bridge layer | Belongs centrally in the Mission Manager |
| AP-OC7 | Storing OpenClaw skills in the user skill directory | OpenClaw skill system stays dead |
| AP-OC8 | Voice switch for OpenClaw model selection | Phase-7 self-mod dependency, manual config edit is enough for v1 |
| AP-OC9 | MCP tool filter in the OpenClaw subprocess | Upstream: deliberate MCP selection at the wizard |
| AP-OC10 | Mapping Stop to "Auflegen" (hang up) | Hanging up lets the mission keep running — Stop is explicit |

**Plus from the lessons learned of the old Jarvis-Agent attempts (Phase-5 tier, fully deleted in Welle 4):**

| ID | Anti-Pattern | Source | Fix |
|---|---|---|---|
| AP-OC11 | Output folder does not exist before spawn | Bridge doc §1 pain point 2 | `git worktree add agent/<id>` is a precondition for spawn |
| AP-OC12 | "Who is answering?" confusion | Bridge doc §1 pain point 1 | Bus events `OpenClawTaskStarted/Completed` with `task_id`, model |
| AP-OC13 | Leaving model selection to the OpenClaw default | Bridge doc AD-7 | Bridge enforces `--model` from `[harness.openclaw].model` |
| AP-OC14 | Leaving Sub-Jarvis code parallel to OpenClaw | Bridge doc AD-5 + §11 | The Phase-5 Sub-Jarvis tier (SubJarvisManager module, `_should_force_sub_jarvis`, `spawn_sub_jarvis` tool, tier configuration) is fully deleted in Welle 4. No backwards compat. |
| AP-OC15 | Not isolating the OpenClaw workspace per mission *(new, Welle-1 spike 2026-05-09, B-9)* | Bridge doc AP-OC15 + AD-23 | OpenClaw injects ~35.4k chars of system prompt from `~/.openclaw/workspace/{AGENTS,SOUL,TOOLS,IDENTITY,USER}.md` automatically. The bridge MUST set `OPENCLAW_STATE_DIR=<mission_dir>/openclaw_state` per mission + place a minimalist workspace profile + audit via `meta.systemPromptReport.injectedWorkspaceFiles[]` from the JSON output. Otherwise persona override by OpenClaw's default SOUL.md/IDENTITY.md (overriding the Personal Jarvis persona mandate) plus cross-mission state leak. |

---

## 6. Phase-7 Self-Mod Anti-Patterns

From `docsplansphase-7-self-mod/PROJEKT_KONTEXT.md` §6 (full justifications there):

| ID | Anti-Pattern | Fix |
|---|---|---|
| AP-SM1 | Validation logic in the system prompt | Constraint enforcement in Python code |
| AP-SM2 | API keys via voice/chat | UI-only (STT data leak vector) |
| AP-SM3 | Writing without Pydantic pre-validate | Mandatory pipeline AD-5: Validate→Backup→Write→Reload |
| AP-SM4 | Writing without a backup | Atomic-writer pattern from `config_writer.py` |
| AP-SM5 | Silent skip of reload failures | Restore-on-fail + audit `rolled_back=true` |
| AP-SM6 | Auto-activation of generated skills | `state=draft`, `TriggerMatcher` ignored |
| AP-SM7 | Skill authoring in the Personal Jarvis brain path | OpenClaw spawn via Mission Manager (see bridge doc R-6) |
| AP-SM8 | Single universal tool (`set_anything`) | Discrete tools with clear intent |
| AP-SM9 | `security.*` or secrets in the allowlist | Privilege-escalation risk |
| AP-SM10 | Skill drafts outside `user_skills_dir` | Hot-reload bypass |
| AP-SM11 | Allowlist as a configuration file | Constraint self-bypass — as a hardcoded constant |
| AP-SM12 | Pending confirmation via LLM call | Pattern match yes/no, no LLM |
| AP-SM13 | Backup directory in the watchdog scope | Hot-reload loop |
| AP-SM14 | Reload test asynchronous | Synchronous `ConfigLoader.load()` |

---

## 7. Awareness-Layer Anti-Patterns

From `JARVIS_AWARENESS_PLAN.md` §10 (full there):

| ID | Anti-Pattern | Fix |
|---|---|---|
| AP-AW1 | Watcher lifecycle leak | `UnhookWinEvent` + thread-join timeout 2s (see `win32-specialist`) |
| AP-AW2 | Awareness snapshot with an LLM call | State read only, regex/heuristic |
| AP-AW3 | Story-tracker lock holding across an LLM call | Release the lock before the LLM call (Codex adversarial review B1) |
| AP-AW4 | Event payload with cleartext PII | PrivacyFilter before bus publish (B2) |
| AP-AW5 | FTS5 recall in the voice critical path | The A3 tool is Jarvis-Agent-only (worker), never the Personal Jarvis brain |
| AP-AW6 | Drafts in story episodes with full text | Salience scorer + Verdichter, never raw storage |

---

## 8. Known Exceptions (Subagent note: this is NOT a violation)

This is where false positives land that reviewers repeatedly flag incorrectly as AP:

- **`jarvis/vision/screenshot.py:_ensure_dpi_awareness` lazy-imports `ctypes`** — not AP-A4 (hardcoded), this is the correct DPI-awareness pattern for Win32 (see `win32-specialist` mandatory reading).
- **`pipeline.py:_on_announcement` calls `synthesize` directly** — not AP-A2 (lateral-direct), this is the deliberate bus bypass for Jarvis-Agent/skill announcements, used by the Jarvis-Agents bridge for `summary_de` voice readback (CLAUDE.md §Output-Filter).
- **`output_filter.py` regex-only without LLM** — not an AP-V4-style lazy filter, latency mandate (CLAUDE.md §Output-Filter).
- **`build_default_brain()` creates its own `EventBus`** — a known open item, not an AP, until the two-bus bridge refactor happens (CLAUDE.md §Desktop-App open items).

---

## 9. Workflow Anti-Patterns (cross-subagent)

| ID | Anti-Pattern | Fix |
|---|---|---|
| AP-W1 | Subagent cites AGENTS.md without reading it | Reviewers actually scan the file, not just a hash check |
| AP-W2 | Reviewer accepts without `file:line` evidence | Verdict always with concrete line refs |
| AP-W3 | Test runner returns PASS spam | Only failures + tracebacks, no full pytest output |
| AP-W4 | Worker commits without tests having run | Pre-commit: relevant tests green is mandatory |
| AP-W5 | Plan verifier believes a plan statement instead of code evidence | AC always backed by `file:line` or test name |

---

## 10. Source Index (mandatory reading for subagents)

| Source | Path | What for |
|---|---|---|
| CLAUDE.md | Repo root | Architecture, plugin system, streaming, event bus, Windows specifics, conventions |
| BUGS.md | `docs/BUGS.md` | Bug register and lessons learned |
| ADR collection | `docs/adr/0001-0011*` | Architecture decisions with justification |
| Jarvis-Agents bridge doc | `docs/jarvis-agents-bridge.md` | AD-1..AD-21, AP-OC1..OC13, test strategy |
| Phase-7 doc | `docsplansphase-7-self-mod/PROJEKT_KONTEXT.md` | AD-1..AD-10, AP-SM1..SM14, EK-1..EK-4 |
| Awareness Plan | `Jarvis  Long-Term Memory/Unbenanntes Dokument (3).md` | A0–A5 specification, §10 anti-pattern register |
| Master plan | `<USER_HOME>\.claude\plans\also-er-muss-auch-lexical-pond.md` | Binding architecture document |

---

**Last update:** 2026-05-06 (initial consolidation as part of the `.claude/` restructuring)
</content>
</invoke>

# Fresh-Machine Bug Forensics — 2026-07 test-machine sweep

**Scope.** The maintainer installed the public GitHub version (v1.0.2 era) on a
second Windows 11 test machine and documented ~16 bugs (PDF, 2026-07-05).
Everything works on the maintainer's dev box; much breaks on the fresh machine.
This document is the root-cause synthesis: the bugs are NOT independent — they
collapse into five shared roots, all of the AP-23 class ("built/tested only
against the maintainer's config"). Evidence was gathered read-only from the
working tree + the public repo; no fixes are applied here.

**Test-machine context (from the bug PDF screenshots).** The install was a
manual desktop clone of the public repo (NOT `install/install.ps1`, which
installs to `~\.personal-jarvis`); brain = OpenRouter on a **no-credit
account**, running `nvidia/nemotron-3-ultra-550b-a55b:free` /
`google/gemini-3.5-flash`; assistant renamed ("Nova"); Obsidian connected;
Notion plugin "CONNECTED · LIVE".

---

## Root 1 — Maintainer-only credentials & settings (never shipped, by design)

The privacy gate correctly strips the maintainer's local state — but several
features have **no path to work without it** and fail dishonestly.

| Bug | Symptom | Evidence |
|---|---|---|
| 3 | Gmail connect → Google error page "401: invalid_client" | Shipped catalog has literal placeholders: `seed_catalog.json:116` (`REPLACE_WITH_JARVIS_SLACK_APP_CLIENT_ID`), `:253` (Asana), `:278` (Google). Real ids live only in the maintainer's git-ignored `data/plugin_catalog.json` / keyring (`catalog_data.py:13-35`, `connect_helpers.py:44-87`). |
| 3/4 | Browser opens and fails; no honest in-app error | `is_placeholder_client_id()` EXISTS (`connect_helpers.py:53-61`) but `connect_start` never calls it (`marketplace_routes.py:355-382`) — the placeholder is fired at Google/Slack unchecked. |
| 9 | Every Computer-Use request: `RuntimeError: ComputerUseHarness context not set` | `[computer_use].enabled` defaults **False** (`config.py:1486`); `jarvis.toml.example` has no section; maintainer's private `jarvis.toml` has `enabled = true`. The context is wired only behind `if tier=="router" and cu_enabled and vision_engine…` (`factory.py:1008` → `:1092`), but the `computer-use` tool is loaded UNCONDITIONALLY (`factory.py:158`, `:374-381`) → router calls it → raise (`computer_use_context.py:303-311`). Secondary: a VisionEngine build error is swallowed to `None` (`factory.py:957-1003`). |

## Root 2 — Free-model default + missing capability gates

**Correction (2026-07-06, maintainer):** the test machine used the SAME
OpenRouter key as the dev box, WITH credit — the initial "no-credit account"
assumption was wrong, and the "only Nemotron works" symptom resolved itself
(exact trigger unconfirmed; a transient upstream error amplified by the
provider dead-listing below is the leading suspect). The maintainer marked the
OpenRouter model-selection symptom as no-further-action. The MECHANICS in this
section remain real defects regardless: the deliberate anti-overbilling free
default (`openrouter.py:22`, `nvidia/nemotron-3-ultra-550b-a55b:free`; tier
defaults `manager.py:291,307`) means a fresh install runs a weak free model —
and several subsystems assume a strong, tool-capable model.

| Bug | Symptom | Evidence |
|---|---|---|
| — | "Only Nemotron works; other models don't" | Full unfiltered catalog is shown (`model_catalog.py:974-1000`); picking any paid model on a 0-credit account → 402 → **dead-lists the whole provider** (`manager.py:7991,8012-8027`) instead of falling back to the working free model; save happens before probe (`provider_routes.py:986-1033`); no "top up / accept data policy" hint. |
| 10 | ALL Jarvis-Agent missions fail | `ApiAgentWorker.spawn` sends `tools=WORKER_TOOL_SPECS` unconditionally, never consults `can_call_tools()` (`api_agent_worker.py:229-266`). Free default model without tool support → no tool calls → empty diff → deterministic critic revise ×3 → `critic_loop_exhausted` (`runner.py:635-768`). Critic fallback spawns the `claude` CLI, absent on a fresh box (`runner.py:1226-1275`). Note: the AP-22 in-process OpenRouter worker/critic path DOES exist and shipped in v1.0.0 — the failure is runtime capability, not a missing path. |
| 12/18 | "I wrote it to your wiki" — but nothing written; later confabulated travel data | Nothing forces the tool call: weak model must pick `wiki-ingest` out of ~30 tools at 13-17k tokens and usually just answers; system prompt even biases AGAINST manual storing (`tool_use_loop.py:566-574`). "No pages were modified" returns `success=True` (`wiki_ingest.py:175-185`) → model paraphrases as success. Curator LLM defaults to the same free model (`curator_llm.py:81`). Nothing stored → later turns confabulate ("flight to SF tomorrow"). |
| 16 | Profile shows a birth-year string stuffed into the `Name` field | Profile learner runs on the same weak free model; no field-level validation of learned facts. |

## Root 3 — Silent failure instead of honest degradation (cross-cutting)

| Bug | Symptom | Evidence |
|---|---|---|
| 4 | Slack connect: infinite spinner | `connect_poll` returns `pending` until `await_callback()` times out after **300 s** (`oauth_pkce_loopback.py:93-98`, `marketplace_routes.py:447-448`); invalid_client never reaches the poll. |
| 4/5 | Second attempt hangs; Cancel "freezes" the app | Fixed callback port 3118 (`seed_catalog.json:117`) is held by the first flow's uvicorn until the 300 s timeout (`oauth_callback_server.py:75-139`); dialog close does NOT cancel the backend task (`PluginsView.tsx:526`; `_drive` task `marketplace_routes.py:407-423`) → 2nd start blocks 5 s in the bind-retry loop then 502. Callback server runs on the SAME backend event loop (`oauth_callback_server.py:130-132`); marketplace routes do keyring I/O synchronously on the loop (`marketplace_routes.py:195-216`). |
| 14 | Notion "CONNECTED · LIVE" but brain has no Notion tools | "Connected" = token blob exists (`marketplace_routes.py:83-102`); "Live" = unconditional `(True, None)` for http transports, no token probe (`:161-180`). A 401 during connect-time `list_tools()` is swallowed (`plugin_registry.py:148-153`) → zero tools, status stays green. Access token TTL 3600 s; `RefreshScheduler` refreshes the keyring but NEVER the live MCP session (`launcher.py:698-703`, `plugin_mcp.py:29-52`) → after 1 h every call 401s, badge stays green. |
| 1 | (contributing) desktop/local-voice extras failure is non-fatal and quiet | `installer.py:234-240` "continuing without it" — missing pywin32/pywebview silently degrades window/icon/voice. |

## Root 4 — First-run & Windows desktop integration

| Bug | Symptom | Evidence |
|---|---|---|
| 1 | "A different app" until ~5-6 restarts | First boots do heavy warmup: wake models are untracked → first-run download; deferred background init answers 503/placeholder ("Jarvis is starting…", `server.py:1560-1597`, `fast_bootstrap.py:118-129`); combined with the Python taskbar icon + console window, early launches genuinely read as a different, buggy app. The public repo DOES ship a prebuilt `dist/` (publish skill step 2b builds + injects it), so "no UI at all" is not the cause on this machine — but the dev repo neither tracks nor builds it (`.gitignore:46`; `installer.py:190-241` is pip-only), so the guarantee hangs on one manual publish step. |
| 2/8 | Taskbar/window icon is the generic Python logo | Taskbar identity needs the AUMID-tagged Start-Menu shortcut to exist BEFORE the button is created (`icon_utils.py:123-142`); the shortcut is created during that same first run → Windows caches the pythonw/Python icon; correct only on a later launch. Shortcut creation needs pywin32 (`icon_utils.py:154-159`) from the best-effort `[desktop]` extra. |
| 6 | A terminal opens with the app | The only launch path after install is `run.bat` (installer creates no shortcut; `installer.py:285-287,313-314`): a cmd console + a synchronous PowerShell drift-check (`run.bat:17-19`) always appear; the app itself is windowless via `start pythonw` (`run.bat:35`). Maintainer never sees this because he launches via the pythonw shortcut/autostart (`autostart/command.py:32-93`). |
| 11 | "Jarvis is starting up" banner while voice already works | Frontend flips only on `VoiceBootStatus(ready=true)` fired at the END of warmup (`pipeline.py:3721-3733`; `useWebSocket.ts:127-129`), but the wake loop listens earlier; 45 s server watchdog is the backstop (`server.py:442-477`). Cold caches widen the gap on a fresh machine. |
| 17 | Mic level bars near zero on the laptop (same headset fine on PC) | Fixed ABSOLUTE floors in the meter: `_MIN_PEAK = 0.01` (`mic_level.py:31`), `speech_threshold = noise_floor*3` (`:50-59`), while STT/wake are level-robust (VAD gate 0.002, `vad.py:65`; Whisper log-mel normalizes) → a real band (~0.002-0.012 RMS) where recognition works but bars sit at zero. Laptop input path is quieter (host-API order MME>DS>WASAPI, `capture.py:117-131`). |

## Root 5 — User-model mismatches the app does not catch

| Bug | Symptom | Evidence |
|---|---|---|
| 15 | Hand-made `NOVA.MD` mostly ignored; in-app file EMPTY | The app reads only fixed paths: packaged persona (`persona_loader.py:29`), `data/custom_system_prompt.md` (`:109`), `data/agent_instructions/<AssistantName>.md` (`agent_instructions.py:92-99`). A user-named file elsewhere lands nowhere; the in-app editor file stays empty unless saved through the UI (`settings_routes.py:1078-1093,1141-1158`). Even when read as agent-instructions, it is framed as PREFERENCES that never override safety/capabilities (`agent_instructions.py:216-217`) → "only some instructions followed". Ack preamble + workers ignore the custom persona (`persona_prompt.py`; `workspace.py:195-202`). |
| 12 (part) | "Obsidian connected" but user sees no pages | Connect registers Jarvis's OWN vault (`wiki/obsidian-vault`, auto-created, `config.py:1788-1791`) into Obsidian (`setup_routes.py:90-108`); a user looking at their pre-existing vault sees nothing. |

Bug 13 (agents spawn too eagerly) is design feedback, not a fresh-machine
defect — tracked separately.

## Meta-root — the verification gap

`fresh-install-smoke.yml` treats ANY `GET /` 200 (including the placeholder
page) as success and never exercises chat/voice/missions/plugins (lines
118-138). None of the five roots could have survived a smoke test that walks
the §3 definition-of-done (fresh install + one arbitrary key + the touched
feature actually used).

## Fix priorities (proposal, no code changed yet)

1. **Honesty layer (highest value/effort ratio).** Call
   `is_placeholder_client_id()` in `connect_start` and fail with an actionable
   message; gate the `computer-use` router tool on `cu_enabled` (or return an
   honest "disabled — enable in Settings" ToolResult); make `wiki-ingest`
   report "nothing written" as failure; make the Notion Live badge do a real
   token probe + refresh the live MCP session; gate mission worker `tools=` on
   `can_call_tools()` and validate the resolved model supports tools.
2. **First-run experience.** Installer creates the windowless pythonw shortcut
   (fixes console + icon timing); surface desktop-extra failures; harden the
   smoke test to assert the real UI + one real chat turn.
3. **Free-tier UX.** Mark/gate models the account cannot call (or probe before
   save); a 402 on one model must not dead-list the provider away from its
   working free default.
4. **Meter normalization** relative to the session's own noise floor instead of
   absolute constants.
5. **Persona/vault clarity.** Import/hint path for user-named instruction
   files; Wiki view shows the vault path prominently.

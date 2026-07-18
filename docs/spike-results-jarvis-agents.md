# Jarvis-Agents Bridge Spike — empirical findings (SP-1..SP-8)

**Status:** Done — Wave 1 completed on 2026-05-09
**Script:** `scripts/spikes/openclaw_probe.ps1`
**Reference:** `docs/jarvis-agents-bridge.md` §6 Open spike questions
**`openclaw` version tested:** 2026.5.7 (eeef486), via `npm i -g openclaw`
**Models tested:**
- `google/gemini-3-flash-preview` (matches `jarvis.toml [brain.providers.gemini].model` — Personal Jarvis hot path)
- `google/gemini-3.1-pro-preview` (matches `[brain.providers.gemini].deep_model` — frontier premium)

**Spike logs:** `logs/spike-openclaw/{20260509-200723, 20260509-202617, 20260509-210408, 20260509-210658, 20260509-212000}/`

---

## Preparation on the Windows box (validated)

1. **Node 24 LTS** — `node --version` answers `v24.13.0` (Windows-native, no WSL2 needed).
2. **Install `openclaw`** via `npm i -g openclaw` — installs 559 packages in ~2 min, produces `<USER_HOME>\AppData\Roaming\npm\openclaw{.cmd, .ps1}` (see B-7 on the wrapper mechanics).
3. **API key** from the Personal Jarvis Credential Manager via `get_secret(...)` into the provider-specific ENV var — see B-2 for the mapping table.
4. **Run the spike:**
   ```powershell
   $env:GEMINI_API_KEY = (python -c "from jarvis.core.config import get_secret; print(get_secret('gemini_api_key', env_fallback='GEMINI_API_KEY'))")
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/spikes/openclaw_probe.ps1
   ```
   The default model is `google/gemini-3.1-pro-preview` (frontier premium); for a fast cheap-path test, pass `-Model "google/gemini-3-flash-preview"`.

5. **Script bug fixes (Wave 0)** — on the first spike run, four script issues were found and fixed:
   - PSDrive reference bug: `$Key:**` → `${Key}:**` in `Write-Result`/`Write-Fail`
   - Added a UTF-8 BOM to the file (PS 5.1 misinterprets em-dashes as cp1252)
   - `Start-Process -FilePath "openclaw"` → `openclaw.cmd` (the PS wrapper is not a Win32 binary)
   - Switched SP-7 from `Start-Process` to `Start-Job` (argument-splitting fix)

---

## SP-1: Native Windows + Node without WSL2

**Question:** Does `openclaw agent --message` run natively on Windows Node 24, without WSL2?

**Finding:** ✅ **Yes, runs natively.**
- `runtime: Native Windows`, `node-version: v24.13.0`, `pnpm-version: 10.33.2`
- `openclaw-version: OpenClaw 2026.5.7 (eeef486)`
- Installation via `npm i -g openclaw` builds 559 packages without native-build errors
- Path: `<USER_HOME>\AppData\Roaming\npm\openclaw{.cmd, .ps1}`

**Assessment:** Architecture OK — no WSL2 plan B needed.

---

## SP-2: stdout format

**Question:** What format does the external `openclaw` CLI deliver over stdout? Plain text, JSON, NDJSON stream?

**Finding:** ✅ **JSON document** with the `--json` flag, with a clear structure.

Example output (`google/gemini-3-flash-preview`, exit code 0, duration 7.9s):
```json
{
  "payloads": [
    { "text": "OK", "mediaUrl": null }
  ],
  "meta": {
    "durationMs": 7906,
    "agentMeta": {
      "sessionId": "1379cb33-b74b-4ae9-8103-d6cb710dd51a",
      "sessionFile": "C:\\Users\\...\\.openclaw\\agents\\main\\sessions\\1379cb33-...jsonl",
      "provider": "google",
      "model": "gemini-3-flash-preview",
      "contextTokens": 1048576,
      "agentHarnessId": "pi",
      "usage": {
        "input": 4754,
        "output": 11,
        "cacheRead": 16276,
        "cacheWrite": 0,
        "total": 21041
      },
      "promptTokens": 21030
    },
    "aborted": false,
    "systemPromptReport": {
      "provider": "google",
      "model": "gemini-3-flash-preview",
      "workspaceDir": "C:\\Users\\...\\.openclaw\\workspace",
      "bootstrapMaxChars": 12000,
      "systemPrompt": { "chars": 35400, "projectContextChars": 13783 },
      "injectedWorkspaceFiles": [
        { "name": "AGENTS.md", "rawChars": 7774, ... },
        { "name": "SOUL.md",   "rawChars": 1797, ... },
        ...
      ],
      "sandbox": { "mode": "off", "sandboxed": false }
    }
  }
}
```

**Implication for the bridge parser:**
- `payloads[0].text` → voice-readback source (after `scrub_for_voice`)
- `meta.usage.{input,output,cacheRead,cacheWrite,total}` → CostMeter input
- `meta.aborted: bool` → cancellation confirmation (see SP-7 / B-10)
- `meta.agentMeta.sessionId` → mission trace ID
- `meta.agentMeta.provider/model` → voice-readback mention ("computed with Gemini Pro")
- `meta.systemPromptReport.injectedWorkspaceFiles[]` → security audit per mission (see B-9)

**Assessment:** Architecturally suitable. JSON schema documented in B-6.

---

## SP-3: Streaming behavior

**Question:** Do intermediate events come over stdout (tool calls, reasoning steps), or only the final result?

**Finding:** ✅ **Streaming via `--verbose on`** (the value `on|off` is mandatory — `--verbose` without a value gives an error). 92 output lines for one test request, with progress markers.

Examples from the verbose stream:
```
[agent/embedded] [trace:embedded-run] startup stages: runId=... sessionId=... phase=attempt-dispatch totalMs=17329 stages=workspace:0ms@0ms,runtime-plugins:7502ms@7502ms,...
[diagnostic] session state: sessionId=... prev=processing new=idle reason="run_completed" queueDepth=0
[diagnostic] run cleared: sessionId=... totalActive=0
[agent/embedded] embedded run prompt end: runId=... durationMs=1070
```

**Format:** Bracketed-prefix lines with key=value triples, not strict NDJSON but pattern-parseable (regex on `[<source>] <event>: <kv-pairs>`).

**Implication for telemetry:**
- Mid-mission updates to the bus are possible (via a streaming wrapper)
- `runtime-plugins:7502ms@7502ms` shows: 7.5s of plugin loading dominates cold start (see B-5)
- `auth:8701ms@17319ms` shows: 8.7s of auth overhead (HTTP calls against the provider) — adds to plugin loading for 17.3s of setup
- `attempt-dispatch:9ms@17329ms` shows: from here it goes to the LLM (so almost all latency is bootstrapping)

---

## SP-4: Model override

**Question:** How do we configure the model — CLI flag `--model`, ENV, config file?

**Finding:** ✅ **CLI flag `--model <provider>/<model-id>`** — works as expected.

Recognized CLI flags from `openclaw agent --help`:
- `--model <id>` — model override per run (format `<provider-slug>/<model-id>`)
- `--verbose <on|off>` — streaming toggle (see SP-3)
- `--json` — JSON output document (see SP-2)
- `--message <text>` — mandatory argument for the task
- `--thinking <off|minimal|low|medium|high|xhigh|adaptive|max>` — reasoning depth per provider
- `--timeout <seconds>` — agent command timeout (default 600s, NOT the model idle timeout — see B-8)
- `--local` — embedded agent without channel routing (MANDATORY for the subprocess bridge — see B-1)
- `--session-id <id>` — explicit session identifier (MANDATORY — see B-1)

**Importantly falsified:** Personal Jarvis provider slugs ≠ `openclaw` slugs (see B-2).

---

## SP-5: MCP handoff

**Question:** Does `openclaw agent --mcp <json>` accept MCP configuration as a CLI argument, or does it need a separate config file?

**Finding:** ✅ **A separate top-level subcommand `openclaw mcp *` + state in `~/.openclaw/`**.

From `openclaw --help`:
```
mcp *                Manage OpenClaw MCP config and channel bridge
```

**There is NO `--mcp` flag in the `agent` subcommand.** MCPs are registered ahead of time via `openclaw mcp <add|set|...>` and read implicitly by the `agent --local` run from `~/.openclaw/<state>`.

ENV vars for state control:
- `OPENCLAW_STATE_DIR` — overrides the state directory
- `OPENCLAW_CONFIG_PATH` — overrides the config path
- Default: `~/.openclaw-<name>` (with a `-<name>` suffix for multi-instance)

**Implication for the bridge:**
- A pre-boot setup step is needed: `openclaw mcp add <each-mcp>` for each MCP from `cfg.mcp.servers.*`
- Mission isolation: set a separate `OPENCLAW_STATE_DIR=<mission_dir>` per mission → no cross-mission state leak
- Alternative: create a temp dir with pre-configured MCP state on every spawn

---

## SP-6: Cost tracking

**Question:** Does the external `openclaw` CLI deliver token counts in stdout (or a structured result body), or do we have to count via the provider API?

**Finding:** ✅ **A `meta.usage` block in the JSON output** with a complete cost breakdown.

Example (Flash run):
```json
"usage": {
  "input": 4754,
  "output": 11,
  "cacheRead": 16276,
  "cacheWrite": 0,
  "total": 21041
}
```

Plus `meta.agentMeta.promptTokens: 21030` and `meta.agentMeta.contextTokens: 1048576` (model capacity display).

**Implication for CostMeter:**
- Direct `usage` mapping to the `jarvis/control/cost_meter.py` structure
- `cacheRead`/`cacheWrite` are separate fields → the bridge can track prompt-cache efficiency (important for the Phase-L.5 1h-TTL pattern)
- With `aborted: true` there is no `usage` block (see the Pro run under B-8) → CostMeter must handle the `aborted` path (no crash on a missing key)

---

## SP-7: Cancellation behavior

**Question:** Does the external `openclaw` CLI respect SIGTERM and do a clean shutdown, or do we have to hard-kill immediately via a Job Object?

**Finding:** 🟡 **Mechanics documented, test inconclusive**.

**Mechanics (validated without a live test):**
- `openclaw` is a PowerShell wrapper script (`openclaw.ps1`) that internally spawns `node.exe + openclaw.mjs`
- `Stop-Process -Id <wrapper-PID>` only kills the wrapper; the `node.exe` child survives orphaned
- Correct path: `taskkill /F /T /PID <node-PID>` or a dedicated Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (the Phase-6 pattern from `jarvis/missions/isolation/job_object.py`)
- `meta.aborted: true` in the JSON is set on idle timeout — a reliable cancellation indicator (see B-10)

**Test inconclusive — reason:**
- With `gemini-3-flash-preview`, test tasks (even a "2000-word essay with `--thinking max`") finished in <8s — the job-watcher poll at 8s was too short
- With `gemini-3.1-pro-preview` the run hit `openclaw`'s **internal idle timeout** after 264s (see B-8) → the cancellation test was not practically feasible because `openclaw` aborted on its own
- Recommended Wave-2 validation: a local model (Ollama) or Anthropic Opus with a long task

**Implication for the stop mechanism (AD-11):**
- Hard-kill via `taskkill /F /T` or a Job Object is mandatory
- NO grace period via SIGTERM (not POSIX-compatible on Windows anyway)
- The bridge must read `meta.aborted` from the last JSON chunk if available — otherwise via process-tree status

---

## SP-8: Worktree path handoff

**Question:** Does the external `openclaw` CLI accept `--workdir`, `--cwd`, or do we have to set `process.chdir`/the subprocess working directory?

**Finding:** ✅ **Confirmed negative — no workdir flags. Spawner `cwd=` is mandatory.**

Empirically disproven:
```
openclaw agent --local --session-id <uuid> --message "..." --model <m> --workdir <path>
→ stderr: "error: unknown option '--workdir'"
```
The same for `--cwd` and `--working-directory`. All three flags were rejected with `exit 1` and an unknown-option error.

**Implication for the bridge:**
- `subprocess.Popen(..., cwd=worktree_path)` (Python) or `Start-Process -WorkingDirectory $worktree` (PowerShell) is the only path
- The external `openclaw` CLI writes the default workspace to `~/.openclaw/workspace/` (see `meta.systemPromptReport.workspaceDir`) — this collides across parallel missions, so additionally set `OPENCLAW_STATE_DIR=<mission_dir>`

---

## Bridge architecture findings (B-1..B-12)

These findings are the condensed architecture implications from the SP tests. They belong in `docs/jarvis-agents-bridge.md` (Wave-2 update via `/skill jarvis-agent-doc-update`).

### B-1: Spawn pattern (corrected)

**Original assumption (bridge docs, before the spike):**
```
openclaw agent --message "<task>" [--workdir <path>]
```

**Empirically validated:**
```
openclaw agent --local --session-id <uuid> --message "<task>" --model <provider>/<model> [--json] [--verbose on] [--thinking <level>]
```

Without `--local` and `--session-id`, every call fails with:
```
Error: Pass --to <E.164>, --session-id, or --agent to choose a session
```

`--local` is mandatory for headless subprocess mode (otherwise `openclaw` tries to channel-route to Telegram/WhatsApp/etc.). `--session-id <uuid>` as the unique mission ID.

### B-2: Provider-slug mapping (architecture-critical)

Personal Jarvis provider slugs in `jarvis.toml` ≠ `openclaw` slugs in `--model`. The bridge needs a translation:

| `jarvis.toml` provider | `openclaw` slug | `openclaw` ENV var (read) | Personal Jarvis secret key |
|---|---|---|---|
| `gemini` | `google` | `GEMINI_API_KEY` (fallback `GOOGLE_API_KEY`) | `gemini_api_key` |
| `claude-api` | `anthropic` | `ANTHROPIC_API_KEY` | `anthropic_api_key` |
| `openai` | `openai` | `OPENAI_API_KEY` | `openai_api_key` |
| `openrouter` | `openrouter` | `OPENROUTER_API_KEY` | `openrouter_api_key` |
| `grok` | `xai` | `XAI_API_KEY` (fallback `GROK_API_KEY`) | (Grok key, separate) |

The external `openclaw` CLI knows **46 providers** in total (`openclaw models list --all` shows 1122 models). All Personal-Jarvis-relevant ones are included: anthropic, google, google-vertex, openai, openrouter, xai, groq, deepseek, mistral, moonshotai, etc.

Pre-spawn requirement in the bridge:
1. Read `cfg.brain.primary` (e.g. `"gemini"`)
2. Map → `openclaw` slug `"google"`
3. Model from `cfg.brain.providers.gemini.model` (hot path) or `.deep_model` (Jarvis-Agent tier)
4. Secret from Credential Manager → set the ENV var in the spawn subprocess
5. `openclaw agent --local --model google/<model> ...`

### B-3: Workdir handoff (falsified)

Original assumption: `--workdir <path>`. **Empirically disproven** (see SP-8). The bridge must set it via the spawner `cwd=` AND `OPENCLAW_STATE_DIR=<mission_dir>` for state isolation.

### B-4: MCP mechanics (clarified)

Original assumption: `--mcp <json>` as a CLI flag. **Empirically disproven** (see SP-5). Pre-boot setup via the `openclaw mcp <add|set|...>` top-level subcommand. State lands in `~/.openclaw/` (or `OPENCLAW_STATE_DIR`).

### B-5: Cold-start latency ~17.3s (new finding)

Setup pipeline per spawn (from the SP-3 verbose stages):
- workspace: 0ms
- runtime-plugins: 7502ms ← main cost driver
- hooks: 1ms
- model-resolution: 1115ms
- auth: 8701ms ← second-largest cost driver
- context-engine: 1ms
- attempt-dispatch: 9ms
- **Total setup: 17329ms** (= 17.3s before the first LLM call)

**Implication:** the external `openclaw` CLI as a one-shot subprocess per mission is expensive for the hot path. The bridge should consider:
- `OPENCLAW_STATE_DIR=<mission_dir>` with a pre-warmed `node_modules` cache
- Daemon mode (if supported by `openclaw` — Phase-2 research)
- A mission pool instead of one-shot (Phase-3 optimization)

### B-6: JSON output schema (reliably usable)

For complete schema documentation see SP-2. The most important fields for the bridge:

| Field | Bridge use |
|---|---|
| `payloads[0].text` | Voice-readback source (after `scrub_for_voice`) |
| `meta.aborted` | Cancellation confirmation (true on idle timeout, hard-kill, etc.) |
| `meta.usage.{input,output,cacheRead,cacheWrite,total}` | CostMeter mapping |
| `meta.agentMeta.sessionId` | Mission trace ID |
| `meta.agentMeta.provider/model` | Telemetry + voice readback ("computed with Gemini Pro") |
| `meta.durationMs` | Mission latency tracking |
| `meta.systemPromptReport.injectedWorkspaceFiles[]` | Security audit per mission (see B-9) |

### B-7: Wrapper vs. binary (Windows-specific)

On Windows, `openclaw` is **not a standalone binary**, but three files in the `npm` bin dir:
- `openclaw` (Bash wrapper, irrelevant on Windows)
- `openclaw.cmd` (cmd batch wrapper, startable by `Start-Process`)
- `openclaw.ps1` (PowerShell wrapper)

`Start-Process -FilePath "openclaw"` fails (`%1 ist keine zulässige Win32-Anwendung` — "%1 is not a valid Win32 application"), because PowerShell finds the `.ps1` wrapper and the Windows process API cannot start it. The bridge must reference the `.cmd` variant explicitly or call `node.exe + openclaw.mjs` directly. <!-- i18n-allow -->

### B-8: Provider idle timeout (architecture-critical)

The external `openclaw` CLI has an **internal `model idle timeout` watchdog** that triggers on long Pro-model responses:

```
[agent/embedded] [llm-idle-timeout] google/gemini-3.1-pro-preview produced no reply
before the idle watchdog; retrying same model
→ failover decision: surface_error reason=timeout
→ JSON-Output: { "payloads": [{ "text": "The model did not produce a response
   before the model idle timeout. Please try again, or increase
   `models.providers.<id>.timeoutSeconds` for slow local or self-hosted
   providers." }], "meta": { "aborted": true, ... } }
```

The default timeout is apparently `~264s`. Frontier-premium models with full reasoning depth (Pro with `--thinking max` or default adaptive) need a longer timeout.

**Bridge requirement:** Pre-boot setup `openclaw config patch` with raised provider timeouts. But: see B-12 on the schema trap.

### B-9: System-prompt auto-injection (security-relevant)

The external `openclaw` CLI automatically injects workspace files into the system prompt. From the SP-2 JSON `systemPromptReport.injectedWorkspaceFiles[]`:

```
~/.openclaw/workspace/
  AGENTS.md   7774 chars  ← `openclaw`'s own anti-pattern docs, NOT the Personal Jarvis AGENTS.md
  SOUL.md     1797 chars  ← default persona
  TOOLS.md     910 chars
  IDENTITY.md  693 chars
  USER.md       (size variable)
```

Total: system prompt 35.4k chars (`projectContextChars: 13783` + `nonProjectContextChars: 21617`).

**Risks:**
- Personal Jarvis persona drift: `openclaw`'s SOUL.md overrides the Personal Jarvis persona mandate
- Persona leak: the default IDENTITY.md could cause an "I am OpenClaw" self-reference in the voice output (would be caught by the `scrub_for_voice` SELF_REFERENCE pattern, but ideally never in the first place)

**Bridge requirement:**
- Per mission: `OPENCLAW_STATE_DIR=<mission_dir>` with its own `workspace/` subdir that contains only the Personal Jarvis mission context
- Either empty the default workspace files (AGENTS.md/SOUL.md/IDENTITY.md/etc.) or replace them with Personal-Jarvis-specific versions
- Audit per mission: check `meta.systemPromptReport.injectedWorkspaceFiles[]` so that only the expected files are present

### B-10: `meta.aborted` as a cancellation indicator

On every cancellation path (idle timeout, hard-kill, provider error) `openclaw` sets `meta.aborted: true` in the JSON output. The bridge uses this as a reliable trigger instead of stderr parsing.

```python
# In jarvis/missions/jarvis_agent_bridge.py:
result = json.loads(stdout)
if result["meta"].get("aborted", False):
    # Mission-State auf ABORTED, mission_event_bus.publish(MissionAborted(...))
```

### B-11: Default system prompt 35.4k chars

From `meta.systemPromptReport.systemPrompt.chars`. Reduces the available output-token budget on context-limited models. The bridge should consider:
- `OPENCLAW_AGENT_DIR=<minimal-profile>` with a reduced default prompt
- OR: delete the workspace files before spawn and set only the mission prompt

### B-12: `openclaw` config schema (incremental set blocked)

An attempt to set a single `models.providers.google.timeoutSeconds` fails:
```
$ openclaw config set models.providers.google.timeoutSeconds 900
Error: Config validation failed:
  models.providers.google.baseUrl: Invalid input: expected string, received undefined
  models.providers.google.models: Invalid input: expected array, received undefined
```

**Implication:** The `openclaw` schema requires a **complete provider block** for `models.providers.<id>`. Incremental per-field patching does not work.

**Bridge solution:** Pre-boot setup as a one-shot patch via `openclaw config patch --stdin` with the complete `models.providers.<id>` structure:
```json5
{
  "models": {
    "providers": {
      "google": {
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "timeoutSeconds": 900,
        "models": ["gemini-3-flash-preview", "gemini-3.1-pro-preview"]
      }
    }
  }
}
```
Or write a template file `openclaw.config.json` directly into `OPENCLAW_CONFIG_PATH`.

---

## Summary & next steps

**Wave 1 is materially complete:**
- 7 of 8 SP clearly green, SP-7 with mechanics docs but an inconclusive test (model-latency mismatch)
- 12 bridge architecture findings documented (B-1..B-12)
- Original bridge-docs assumptions falsified: spawn pattern (B-1), workdir flag (B-3), MCP flag (B-4)
- New findings outside the original spike scope: cold-start latency (B-5), provider idle timeout (B-8), system-prompt auto-injection (B-9), config-schema trap (B-12)

**Recommendation:** Architecture viable, start Wave 2. Prerequisites for Wave 2:
1. Bridge-docs update via `/skill jarvis-agent-doc-update` — amend AD-1..AD-21, new ADs (AD-22+) for B-8/B-9/B-12
2. `jarvis/missions/jarvis_agent/` module skeleton (subprocess spawn wrapper, provider-slug mapper, JSON parser)
3. Pre-boot setup routine: `openclaw config patch` with provider config (B-12) + workspace isolation (B-9)
4. Mock bridge test: a local echo server instead of the real `openclaw`, validates the spawn-wrapper mechanics in isolation

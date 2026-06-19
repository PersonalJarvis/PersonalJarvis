# OpenClaw Sub-Edge Spawn-Failure Analysis — 2026-05-18

**Status:** Diagnose-only document. No code changes performed.
**Trigger:** User reported multiple OpenClaw sub-edge spawns failing after the Welle-6 ChatGPT-OAuth switch.
**User's hypothesis:** "Liegt an der ChatGPT-Authentifizierung." (It's down to the ChatGPT authentication.)

---

## TL;DR

> Your hypothesis is **almost right** — it has to do with ChatGPT, but **not with authentication**. Auth works (`codex login status` → `Logged in using ChatGPT`). The error is a **model-slug mismatch**: the Mission-Decomposer hardcodes `model="sonnet"` (Anthropic slug) as the step default, the worker passes that on to `codex exec --model sonnet`, Codex with a ChatGPT account only accepts OpenAI/ChatGPT models (gpt-5-codex, gpt-5, ...) and responds with HTTP 400.

Evidence from the last two missions:

```
mission_019e3c51-d4f4 (20:21)
mission_019e3c52-0acd (20:21)
→ stream.jsonl terminal frame:
  {"type":"error","status":400,"error":{
    "type":"invalid_request_error",
    "message":"The 'sonnet' model is not supported
               when using Codex with a ChatGPT account."}}
```

---

## Diagnostic Method

Read-first, then hypothesis. Source per finding:

| Source | What was checked |
|---|---|
| [`../sub-agents-outputs/mission_019e3c52-0acd/`](file:///C:/Users/Administrator/Desktop/sub-agents-outputs/mission_019e3c52-0acd/) | Last failed mission, stream.jsonl + stderr |
| [`../sub-agents-outputs/mission_019e3c51-d4f4/`](file:///C:/Users/Administrator/Desktop/sub-agents-outputs/mission_019e3c51-d4f4/) | Previous failed mission, identical pattern |
| `codex login status` | Live-CLI auth check |
| `jarvis/core/config.load_config()` | Which provider is currently configured |
| [`scripts/config-soll.json`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/scripts/config-soll.json) | Drift-Guard target value |
| [`jarvis/missions/kontrollierer/decomposer.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/kontrollierer/decomposer.py) | Decomposer defaults |
| [`jarvis/missions/workers/codex_direct_worker.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/workers/codex_direct_worker.py) | Worker model routing |

---

## User's Hypothesis — Verdict

| User statement | Verdict | Evidence |
|---|---|---|
| "Liegt an ChatGPT" (It's down to ChatGPT) | ✅ **Partly true** | The failure is ChatGPT-specific (the `sonnet` model is disabled on ChatGPT accounts). |
| "Liegt an Authentifizierung" (It's down to authentication) | ❌ **False** | `codex login status` → `Logged in using ChatGPT`. OAuth token valid. The HTTP 400 from the server confirms: the request went THROUGH (otherwise HTTP 401, not 400). |
| "Ich muss mich nicht einloggen" (I don't need to log in) | ✅ **Correct** | Confirmed by today's successful worker smoke run (Welle 6 E2E, [`welle6_pass.md`](file:///C:/tmp/welle6-run4/welle6_pass.md) was written). |

---

## True Root Cause (with code evidence)

### Bug 1 (CONFIRMED, HIGH): Decomposer hardcodes `model="sonnet"`

**File:** [`jarvis/missions/kontrollierer/decomposer.py:57`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/kontrollierer/decomposer.py) + line 138 + 167

```python
# decomposer.py:57
model: str = "sonnet"

# decomposer.py:138
model="sonnet",

# decomposer.py:167
'"model": "sonnet" | "opus" | "haiku", '
```

The MissionDecomposer (LLM-driven decomposition) emits a step with `model="sonnet"` by default. This is an Anthropic model alias from the pre-Welle-6 era (Claude-centric design).

**How it ends up in the worker:**

```python
# codex_direct_worker.py:103-104
if model:
    cmd.extend(["--model", model])
```

`CodexDirectWorker` reads `step.model` blindly and passes it through. With `step.model="sonnet"` → `codex exec --model sonnet` → HTTP 400.

**Asymmetry with the Critic path:** we deliberately forced the Critic to an empty model earlier (`primary_model or ""` in [`runner.py:594`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/critic/runner.py)). The same protective logic is missing in the worker path.

---

## Complete list of potential error sources

Categorized by evidence level. File + line + evidence snippet.

### CONFIRMED — observed as active through logs

| # | Source | File | Evidence |
|---|---|---|---|
| **C1** | Decomposer passes `"sonnet"` through | [`decomposer.py:57,138,167`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/kontrollierer/decomposer.py) | HTTP 400 in 2 of 2 last missions |
| **C2** | Worker does not adapt the model slug to the provider | [`codex_direct_worker.py:103-104`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/workers/codex_direct_worker.py) | Passes `step.model` 1:1 to `codex --model` |

### LIKELY — structurally plausible, but not seen in the current log

| # | Source | File | Evidence |
|---|---|---|---|
| **L1** | `step.allowed_tools` is Claude-formatted | [`decomposer.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/kontrollierer/decomposer.py) | The Decomposer can emit tool whitelists that contain Claude tool names (Write/Edit/Read); Codex expects different names. Not relevant in the current log because the model reject comes first. |
| **L2** | Critic path with the wrong model when primary_model is set | [`runner.py:594`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/critic/runner.py) | We use `primary_model or ""` — if someone sets `[brain.sub_jarvis].model = "sonnet"`, Codex would also return 400 here. |
| **L3** | `choose_critic_model` emits Claude slugs | [`escalation.py:23`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/critic/escalation.py) | Function returns e.g. `"claude-sonnet-4-6"`. In the Codex path this is emptied out by our `or ""` hack, but that is a workaround, not a clean fix. |
| **L4** | Mission prompt templates reference Anthropic tools | [`prompts.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/critic/prompts.py) | Prompts can contain instructions like "use the Write tool" — Codex calls its tools by different names. Not observed because the worker dies before that. |
| **L5** | Worker stream parser misinterprets the Codex `error` frame | [`codex_direct_worker.py:340-344`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/workers/codex_direct_worker.py) | Codex emits `type=error` + `type=turn.failed` → our parser sets `terminal_kind="error"` but **`terminal_message` may stay empty**, because the error frame's `obj.get("message") or obj.get("error")` does not necessarily match. ClaudeResult.result then becomes generic. |

### POSSIBLE — could happen once the model problem is fixed

| # | Area | Note |
|---|---|---|
| **P1** | OpenClaw slug mapping does not know `chatgpt` | [`provider_map.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/openclaw/provider_map.py) lists only claude-api/gemini/grok/openai/openrouter. If someone accidentally calls `spawn_openclaw` directly instead of using CodexDirectWorker → `UnknownJarvisProviderError`. |
| **P2** | Drift-Guard rolls the provider back | [`scripts/jarvis-config-drift-guard.ps1`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/scripts/jarvis-config-drift-guard.ps1) + [`scripts/config-soll.json`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/scripts/config-soll.json) | Both are now in sync on `chatgpt` — BUT if a Drift-Guard runs on ANOTHER machine with an old target value, it could roll back. Not currently observed. |
| **P3** | CODEX_HOME env leak | [`env.py:89`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/isolation/env.py) | `build_worker_env` sets `CODEX_HOME=<run_dir>/.codex` but `CodexDirectWorker` strips it (Welle 6 fix). If someone uses the old `CodexWorker` (non-Direct), the bug comes back. |
| **P4** | Sandbox mode wrong for Codex-Codex | [`codex_direct_worker.py:50`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/workers/codex_direct_worker.py) default `sandbox="workspace-write"` | Works, but if the worker step sets `step.allowed_tools` that contains `Write` AND the `step.sandbox` override says Read-Only somewhere, it fails silently with "workspace is currently mounted read-only" (already seen once live today). |
| **P5** | MCP-plugin Cloudflare OAuth expired | `~/.codex/config.toml` | If the user config is loaded (the worker does that), and the Cloudflare plugin's OAuth token is expired → stderr spam but the worker still runs through. The Critic strips the user config, so no issue there. |
| **P6** | `step.allowed_tools="..."` is empty / malformed | Step construction in the Decomposer | If the LLM Decomposer emits an empty tool string → `--allowedTools ""` as argv → Codex ignores it or chokes. Not relevant as long as the worker does not pass `allowed_tools` on (our CodexDirectWorker currently ignores it). |

### RULED OUT — explicitly checked, not the cause

| # | Point | How verified |
|---|---|---|
| **R1** | OAuth token expired | `codex login status` → "Logged in using ChatGPT" |
| **R2** | OPENAI_API_KEY ENV overrides OAuth | Explicitly stripped in worker code ([`codex_direct_worker.py:179-184`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/workers/codex_direct_worker.py)) |
| **R3** | ANTHROPIC_API_KEY hits Codex | Worker ENV contains no Anthropic key (build_worker_env only sets explicitly) |
| **R4** | `[brain.sub_jarvis].provider` wrong | TOML + config-soll.json both on `chatgpt` — Drift-Guard accepts |
| **R5** | `chatgpt` provider is not recognized as Codex | [`init.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/missions/init.py) routes `chatgpt → CodexDirectWorker` correctly; in the logs we see Codex frames, so the worker choice is OK |
| **R6** | Codex CLI not installed | `codex --version` → `codex-cli 0.130.0` |
| **R7** | Worker tool-use detection broken | Not relevant — the bug fires BEFORE any tool use |
| **R8** | Critic loop swallows the error | Not relevant — the worker dies BEFORE the Critic starts, the mission ends with `worker_error` |

---

## Where it goes wrong — Call Graph

```text
1. User says: "spawn an open-claw subagent for X"
2. Brain-Router (Gemini) → tool_call: spawn_openclaw(utterance="...")
3. spawn_openclaw.py     → MissionManager.create_mission(...)
4. Kontrollierer.run_mission()
   ↓
5. MissionDecomposer.decompose(prompt)
   ↓ emits MissionPlan with Step(model="sonnet")        ← Bug C1 (Anthropic slug)
6. _worker_factory(step)
   ↓ sees brain.sub_jarvis.provider == "chatgpt"
   ↓ returns CodexDirectWorker()
7. CodexDirectWorker.spawn(prompt, ..., model=step.model)
   ↓ model="sonnet" reaches argv                         ← Bug C2 (no slug translation)
8. codex exec --model sonnet ...                         ← HTTP 400 reject
   ↓ stream.jsonl: {"type":"error","status":400,...}
9. ClaudeResult(is_error=True, result="...")
10. Kontrollierer sees worker_error → Mission → FAILED
11. Voice: "Mission ist fehlgeschlagen. Der Worker ist abgebrochen." ("Mission failed. The worker was aborted.")
```

**Break points 5 + 7** are the two places where the model slug passes through unfiltered.

---

## What a fix PR would have to address (not implemented)

A plain listing for a later wave, **no code in this document**:

1. **Step-model sanitization at the worker level** — `CodexDirectWorker` must detect when `model in {sonnet, opus, haiku, claude-*}` and then pass it empty (= codex default). A clean mirror of the Critic fix.
2. **Make the Decomposer provider-aware** — the Decomposer should read the provider from `[brain.sub_jarvis]` and emit matching model slugs. Currently a hardcoded Claude assumption.
3. **Extend the provider-slug mapping** — if `chatgpt` is a first-class provider slug, it should also appear in `provider_map.MAPPINGS` (as `chatgpt → openai` or a dedicated slug), not just a special case in `_worker_factory`.
4. **`L5` stream-parser robustness** — `terminal_message` should reliably extract the readable error text from the error frame, so that `_voice_phrase` says more than "Worker ist abgebrochen" ("The worker was aborted").
5. **Voice-announcement improvement** — be more honest on an HTTP 400 model reject: "Das Modell `sonnet` passt nicht zu deinem ChatGPT-Account" ("The model `sonnet` is not compatible with your ChatGPT account") instead of a generic "abgebrochen" ("aborted").

---

## Evidence Appendices

### Mission 019e3c52-0acd (a few minutes ago)

```text
stream.jsonl (terminal frame):
{"type":"error","status":400,
 "error":{"type":"invalid_request_error",
          "message":"The 'sonnet' model is not supported
                     when using Codex with a ChatGPT account."}}

stderr.log: "Reading prompt from stdin..."
artifacts/diff.patch: 0 bytes
```

### Mission 019e3c51-d4f4

Identical pattern, 2 minutes earlier.

### Live auth status

```text
$ codex login status
Logged in using ChatGPT

$ codex --version
codex-cli 0.130.0
```

### Config snapshot

```toml
# jarvis.toml [brain.sub_jarvis]
provider = "chatgpt"
model = ""
fallback_provider = "gemini"
fallback_model = "gemini-3.1-pro-preview"
```

```json
// scripts/config-soll.json
"brain.sub_jarvis": {
  "provider": "chatgpt",
  "model": "",
  ...
}
```

---

## Summary (non-technical)

Your assumption was almost right — it has to do with ChatGPT, but the sign-in itself works. The problem is specific: the system tries to use an old model called "sonnet", and your ChatGPT account does not accept that model — it only knows its own models. This is a one-line fix in two places, but as requested I touched nothing and only wrote up the analysis.
</content>
</invoke>

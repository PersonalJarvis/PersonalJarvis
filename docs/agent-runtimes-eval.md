# Agent runtime acceptance evidence

Issue [#428](https://github.com/PersonalJarvis/PersonalJarvis/issues/428) is an
epic, not a single defect. This document records a partial implementation and
its evidence. It does **not** declare the epic or its child issues complete.

## Scope and reproduction (2026-10-07)

The issue, its comments, issues #418 through #427, and PR #416 were read before
implementation. None of these issues had comments at investigation time.
PR #416 was still open. The isolated local branch starts from its referenced
commit `a00bcb868cc42913e63cd4c6839c2d7482027db7`.

Three concrete problems were established:

1. `runner_acp._denied_native` considered explicit denials but ignored an
   allowlist's missing grants. Four new parameterized regression cases failed
   on the original implementation. Native shell/browser access now requires
   the corresponding grant for allowlist agents; explicit denial still wins.
2. Hermes received no context length; OpenClaw always received 128,000 context
   tokens and 8,192 output tokens. Gateway model rows carried no limits and did
   not include arbitrary selected local models. The selected route, runtime
   config, model discovery and Chat Completions output cap now share a budget.
3. A real OpenClaw replay run logged `memory-core: created managed dreaming
   cron job` even with heartbeat disabled. The driver now disables the memory
   plugin/slot and the native scheduler as well. Jarvis owns those services.

## Checks

| Check | Result | What it establishes |
|---|---|---|
| Original runtime unit suite | 72 passed | Starting test baseline |
| Native allowlist reproduction before fix | 4 failed, 5 passed | Missing grants were ignored |
| Focused runtime/catalog/subscription/society suite | 304 passed | Backend regression coverage |
| Routing, output filter, hangup parity, turn-language guards | 599 passed | Shared guards remain green |
| New regressions on Linux, Python 3.11 slim | 32 passed | Model budgets, grants and probe process cleanup work in a Linux container |
| Scoped Ruff and whitespace checks | Passed | Local static validation |
| Windows OpenClaw, Responses replay | Two turns passed | Guarded gateway, streamed answer and resumed history; no live subscription call |
| Windows OpenClaw, Chat Completions replay | Two turns passed | Actual runtime with scripted brain through the gateway and new config |
| Windows Hermes 0.21.5, 32k Responses replay | Failed at `session/new` | Runtime requires at least 64,000 context tokens; accurately declaring 32k exposes that incompatibility |
| Windows Hermes 0.21.5, 128k Responses replay | Two turns passed | Correctly declared scripted model, streamed response and resumed history |
| Windows OpenClaw 2026.9.8, local Ollama qwen3.5:4b, 32k budget | Failed | Both turns completed with empty answer text; no behavioural acceptance claim |

The OpenClaw Responses replay was run before disabling the native memory
plugin; the Chat Completions replay used that final configuration. Replay
tests validate transport and lifecycle, **not** model behaviour or token savings.
The Linux container mounted the branch read-only and held no user credentials.
It was a focused Python test run, not a fresh-machine runtime install.

The Hermes SDK returned the actual context error in `error.data.details`.
Jarvis now translates that recognized numeric error into an actionable message
while continuing to withhold arbitrary upstream detail. The scripted replay
model advertises 128k; real small models keep their real budget. No local model
was silently reconfigured to meet Hermes' minimum.

Relevant commands:

```text
python -m pytest tests/unit/agent_runtimes tests/unit/brain/test_model_catalog.py tests/unit/brain/test_model_catalog_local.py tests/unit/brain/test_model_catalog_optional_auth.py tests/unit/brain/test_model_catalog_parity.py tests/unit/live/test_subscription_reasoning.py tests/unit/society/test_surface.py -q
python -m pytest tests/unit/brain/test_routing.py tests/unit/brain/test_output_filter.py tests/unit/sessions/test_hangup_reason_parity.py tests/unit/core/test_turn_language.py -q
python scripts/spikes/agent_runtimes_gateway_e2e.py openclaw openai-codex --replay
python scripts/spikes/agent_runtimes_gateway_e2e.py openclaw ollama --replay
python scripts/spikes/agent_runtimes_gateway_e2e.py hermes openai-codex --replay
```

The smoke driver closes its process tree and releases the turn slot on success,
spawn failure and cancellation. Gateway server threads and runtime supervisors
are stopped in cleanup. Runtime state, catalog writes and usage records are
temporary; failed probe logs are retained only in ignored `eval-results/`.
Multi-turn probes refuse paid API providers because runtime retries cannot
honor a one-call paid budget.

## Remaining acceptance work

### Default autonomy and full capacity follow-up

The requested default is all capabilities with Bypass permissions, while
explicit user choices remain authoritative. Existing roster and routine code
already implements those defaults; tests now cover all three runtime choices.
The original fixed 32k local allocation and output-context halving have been
removed. A model declaring a 131,072-token window now exposes all 131,072 tokens
instead of 32,768 on Ollama, and a declared 100,000-token output capacity is no
longer reduced to 65,536. The gateway no longer clips requests at 128,000 tokens
or treats an omitted request limit as 8,192. Unknown output limits remain unknown.
Ollama profiles make the advertised allocation real; preparation failure stops
the request instead of silently using a smaller base-model window. These are
source and regression-test checks, not a large-context live hardware benchmark.

| Issue | Remaining work |
|---|---|
| #418 | Hermetic full-backend behaviour evaluator, S1-S8 and maintainer daily tasks, machine-checked behaviour and cost/latency baseline for all three runtimes. Not implemented by this patch. |
| #419 | Successful local-model behaviour and live subscription/authorized paid call; fresh Windows/Linux installs; real macOS and restart/UI acceptance. Hermes' 32k session failure is diagnosed above. |
| #420 | Profile/discovery implementation and measured 70% input-token reduction without quality regression. No savings claim is made. |
| #421 | Long-conversation/compaction evidence on both runtimes and a 32k local model. Metadata propagation and output clamping are implemented. |
| #422 | Briefing changes justified by before/after behaviour results. |
| #423 | Read-only active skill mirrors and native skill usage attribution. |
| #424 | Cross-family fallback, cost policy and visible effective-provider notice. |
| #425 | Canary execution on all OSes, verified manifest publication, pinned installs, initialize smoke and rollback. The workflow now checks both gateway protocols with scripted providers; it has not been dispatched. |
| #426 | Cold/replay measurements before deciding on warm Hermes processes. |
| #427 | Optional Claude Code backend spike and scope decision. |

No issue was closed or modified; no workflow was dispatched, no PR published,
and no changes pushed. The three to five maintainer-specific daily tasks were
requested but were not supplied during this work.

## Changed files

Local desktop integration, 2026-10-07: the patch was applied to the normal
checkout on top of `f2c336dce`, preserving the newer provider-login changes.
The integration checks passed: 284 focused runtime/catalog/subscription tests,
106 runtime tests using the desktop's Python 3.11 interpreter, and scoped Ruff.
The pre-existing uncommitted changes in `docs/os-parity.md` were left untouched;
the P-45 update remains available in the original isolated commit. Activation
requires a desktop restart. The running app's lifecycle guard requires an
explicit desktop-UI click and rejects Control-API restart requests.

- `jarvis/agent_chat/runner_acp.py`: native grants and route preparation.
- `jarvis/agent_runtimes/model_limits.py`, `model_map.py`, `gateway.py`, `acp.py`:
  shared budgets, catalog refresh, discovery and output limits.
- `jarvis/agent_runtimes/hermes.py`, `openclaw.py`: runtime configuration.
- `jarvis/brain/model_catalog.py`, `jarvis/live/subscription_reasoning.py`:
  preserve provider-declared limits, including catalog cache reloads.
- `scripts/spikes/agent_runtimes_e2e.py`,
  `scripts/spikes/agent_runtimes_gateway_e2e.py`: contained probes and replay.
- `.github/workflows/agent-runtimes-canary.yml`: exercise both gateway protocols.
- `tests/unit/agent_runtimes/test_model_limits.py`, `test_native_grants.py`,
  `test_smoke_lifecycle.py`, `test_runtime_configs.py`, `test_runtime_gateway.py`;
  `tests/unit/live/test_subscription_reasoning.py`;
  `tests/fakes/fake_runtime_brain.py`: regression tests and scripted provider.
- `.gitignore`, `docs/agent-runtimes.md`, `docs/os-parity.md`, this document:
  local artifacts, behaviour description and explicit acceptance boundaries.

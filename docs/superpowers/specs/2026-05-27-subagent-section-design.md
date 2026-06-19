# Subagent Section in API-Keys-View — Design

**Author:** brainstorming session 2026-05-27
**Status:** approved-for-implementation pending user spec review
**Related:** `docs/openclaw-bridge.md`, `jarvis/missions/workers/`, `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx`

---

## 1. Motivation

Today every sub-agent mission (`spawn_openclaw`) runs hardcoded on Claude Opus 4.7 via Max-subscription OAuth (`ClaudeDirectWorker`). The user wants to switch the underlying worker LLM at runtime through the UI — same way TTS providers are switched today — without having to edit `jarvis.toml` by hand.

The new section makes this explicit: *one* active worker provider, configurable in the UI, with a per-card health-test that proves the provider can actually run a real sub-agent mission end-to-end.

## 2. Scope

### 2.1 In Scope

- A new fourth tier `subagent` in the existing `ApiKeysView.tsx` with four provider cards: **Anthropic Claude**, **OpenAI Codex**, **Google Gemini**, **xAI Grok**.
- Two auth modes for Anthropic and Codex (both shown simultaneously on the card, one bears an `[active]` badge): subscription OAuth (preferred) OR API-Key (fallback).
- One auth mode for Gemini and Grok: API-Key only.
- A "Test" button per card that fires a real mini sub-agent mission ("write `ok.txt` with content `OK`"), streams status into the card, and reports ✓/✗ with a 60-second cooldown.
- Worker-dispatch routing through a single switch in `kontrollierer/dispatch.py` keyed off `cfg.subagent.active_provider`.
- A new `GrokWorker` analogous to the existing `GeminiWorker`.
- Frontier-model constants pinned in **one** file (`jarvis/missions/workers/_models.py`).

### 2.2 Out of Scope (YAGNI)

- Multi-spawn / parallel A/B-bench of all four providers on the same prompt.
- Brain-driven auto-routing (code→Codex, web→Gemini, reasoning→Claude).
- Model-picker dropdowns per card. Frontier per provider is hard-wired.
- Automatic health-check at boot. Only on-demand via the Test button.
- API-Key + OAuth used **together** on the same request. Priority is OAuth > API-Key.

## 3. Architecture

### 3.1 Layered Overview

```
L7 UI         ApiKeysView.tsx · SubagentProviderCard.tsx        (frontend)
L6 API        /api/providers/subagent/{id}/activate             (FastAPI)
              /api/subagent/test/{id}
L5 Config     SubagentConfig in jarvis/core/config.py            (Pydantic)
              [subagent].active_provider in jarvis.toml
L4 Dispatch   _make_worker() switch in kontrollierer/dispatch.py
L3 Workers    ClaudeDirectWorker | CodexDirectWorker             (existing)
              GeminiWorker | GrokWorker (new)
L2 Auth       OAuth env: ANTHROPIC_OAUTH_TOKEN, OPENAI_CODEX_*
              API env:   ANTHROPIC_API_KEY, CODEX_OPENAI_API_KEY,
                         GEMINI_API_KEY, GROK_API_KEY
L1 Secrets    Credential Manager (existing wizard SECRETS)
```

### 3.2 Frontier-Model Pin

A single file owns the four model strings:

```python
# jarvis/missions/workers/_models.py
CLAUDE_SUBAGENT_MODEL = "claude-opus-4-7"
CODEX_SUBAGENT_MODEL  = "gpt-5.5"
GEMINI_SUBAGENT_MODEL = "gemini-3.1-pro-preview"
GROK_SUBAGENT_MODEL   = "grok-4.3"
```

All four workers import from here. Frontier bumps in the future touch exactly one file.

### 3.3 Config Schema

```toml
# jarvis.toml
[subagent]
active_provider = "claude-subagent"   # one of: claude-subagent, codex-subagent,
                                      #         gemini-subagent, grok-subagent
test_cooldown_seconds = 60             # per-provider cooldown for Test button
```

`SubagentConfig` in `jarvis/core/config.py` validates the four provider IDs as a `Literal`. Drift-guard pins `active_provider` in `scripts/config-soll.json`.

### 3.4 Worker Dispatch Switch

`jarvis/missions/kontrollierer/dispatch.py::_make_worker()` is the single point of selection. It takes the mission to honour `worker_override`, falling back to `cfg.subagent.active_provider`:

```python
def _make_worker(mission: Mission, cfg: JarvisConfig, bus: EventBus) -> Worker:
    provider = mission.worker_override or cfg.subagent.active_provider
    match provider:
        case "claude-subagent": return ClaudeDirectWorker(bus=bus, model=CLAUDE_SUBAGENT_MODEL)
        case "codex-subagent":  return CodexDirectWorker(bus=bus, model=CODEX_SUBAGENT_MODEL)
        case "gemini-subagent": return GeminiWorker(bus=bus, model=GEMINI_SUBAGENT_MODEL)
        case "grok-subagent":   return GrokWorker(bus=bus, model=GROK_SUBAGENT_MODEL)
        case _: raise ValueError(f"unknown subagent provider: {provider!r}")
```

### 3.5 Auth Resolution (Anthropic + Codex)

Workers inherit env via `_env_builder()` in `jarvis/missions/init.py`. New logic for Anthropic and Codex:

```
if OAuth token present AND not expired:
    set ANTHROPIC_OAUTH_TOKEN (resp. OPENAI_*-OAuth equivalent)
    unset ANTHROPIC_API_KEY  (resp. CODEX_OPENAI_API_KEY)
    audit: "subagent auth=oauth"
elif API-Key present:
    set ANTHROPIC_API_KEY (resp. CODEX_OPENAI_API_KEY)
    unset ANTHROPIC_OAUTH_TOKEN
    audit: "subagent auth=api_key"
else:
    raise WorkerLaunchError("no Anthropic credentials configured")
```

**Why unset the alternative env?** Today's BUG (see `memory/project_anthropic_api_key_env_pollution.md`): when both vars contain something, `claude --print` prefers `ANTHROPIC_API_KEY`, treats an OAuth token as an API key, and gets a 401. Explicit unset is the only robust fix.

OAuth expiry is detected by reading `~/.claude/.credentials.json` (Anthropic) or the equivalent codex credential file. Treat "no expiry field" as "valid" (matches current Max subscription behaviour).

### 3.6 Test Endpoint

`POST /api/subagent/test/{provider_id}` →
1. Server-side cooldown check (60s per provider, in-memory dict). If hot → HTTP 429 with `Retry-After` header.
2. Validates that the requested provider is configured (OAuth present OR API-Key present). If neither → HTTP 412 "provider not configured".
3. Dispatches a mission via `MissionManager.dispatch` with:
   - `prompt = "Schreibe eine Datei `ok.txt` mit Inhalt `OK`."` (German runtime literal — "Write a file `ok.txt` with content `OK`.")
   - `source_actor = "subagent_test"`
   - `worker_override = provider_id` (new field, lets the test bypass the active-provider switch)
   - `max_critic_loops = 1` (telemetry/budget reduction)
4. Returns the mission ID. The UI subscribes to mission events over the existing WebSocket and watches for that ID.
5. Success criterion: mission ends `APPROVED` AND `artifacts/files/ok.txt` exists AND its content is "OK" (trimmed).

The `worker_override` field is plumbed through `Mission` and read inside `_make_worker()` *before* falling back to `cfg.subagent.active_provider`.

### 3.7 Frontend

**`ApiKeysView.tsx`**:
- `TIER_META` gains `subagent: { label: t("apikeys_view.tier_subagent"), icon: <Users/> }`.
- Tier render order: brain, tts, stt, **subagent** (last — sub-agents are operationally a sibling of brain, not above it).

**`SubagentProviderCard.tsx`** (new):
- Inherits the visual shell of the existing `ProviderCard` (header, "Set active" radio).
- For Anthropic and Codex, renders **two** stacked auth blocks (`<AbonementBlock>` + `<ApiKeyBlock>`), each with its own status badge: `[active]` on the one currently used by the worker, `[open]` on the unconfigured one, `[ready]` on a configured-but-not-active one.
- For Gemini and Grok, renders only `<ApiKeyBlock>`.
- Footer row: `[ Test ▷ ]` button + last-test result chip ("2 min ago ✓ ok.txt" or "12 min ago ✗ 401 Unauthorized"). Chip is clickable, opens a small inline panel with the full test log (worker stdout/stderr, last 50 lines).

**`AbonementBlock`** sub-component:
- Status: "Logged in as {user@…}, token valid until {date}" OR "Not logged in".
- Button: "Log in" (kicks off `claude /login` or `startCodexLogin()`) when logged out; "Logout" when logged in.
- The "Logged-in as" string comes from the credentials file (read via a small backend endpoint `GET /api/providers/subagent/{id}/auth-status`).

**State management**: `useProviders.ts` extends `ProviderTier` to `"brain" | "tts" | "stt" | "subagent"`. The hook already handles the active-provider switch via `switchTtsProvider` etc.; we add a parallel `switchSubagentProvider`.

### 3.8 Cooldown Implementation

Two cooldowns, distinct:

1. **Test-button cooldown** (60s per provider, server-side, in-memory). Lives in `jarvis/ui/web/subagent_routes.py`. Resets on process restart — that's acceptable because the test is cheap and a restart implies the user wants fresh state.
2. **Spawn cooldown** (existing 30s, `spawn_openclaw.py`, see commit `eab15ec`). Unchanged. Tests inherit it? **No** — test missions use `source_actor="subagent_test"` and bypass the spawn-tool entirely (they go through `MissionManager.dispatch` directly), so the spawn cooldown does not apply.

## 4. Data Flow

### 4.1 User activates a different provider

```
User clicks "Set active" on Codex card
  → PUT /api/providers/subagent/codex-subagent/activate
  → cfg.subagent.active_provider := "codex-subagent" (via config_writer with WRITE_LOCK)
  → ConfigReloaded event on EventBus
  → ApiKeysView.refetch()
  → Codex card now shows the [Set active] radio filled
```

### 4.2 User tests Gemini

```
User clicks "Test" on Gemini card
  → POST /api/subagent/test/gemini-subagent
  → cooldown check, auth check
  → MissionManager.dispatch(prompt=fixed, worker_override="gemini-subagent", …)
  → returns mission_id
  → UI subscribes to mission-events over /ws/missions
  → mission_started, mission_iteration_complete, mission_finished events stream in
  → on APPROVED: read artifacts/files/ok.txt, verify "OK", flip chip to ✓
  → on FAILED / ERROR / TIMEOUT: flip chip to ✗ with the first error line
```

### 4.3 User logs into Claude subscription

```
User clicks "Log in" on Claude card (subscription block)
  → POST /api/providers/subagent/claude-subagent/oauth-login
  → backend launches `claude /login` subprocess with NO_WINDOW_CREATIONFLAGS
  → backend streams login URL into the response (claude prints it to stdout)
  → frontend opens URL in browser
  → user completes OAuth in browser
  → claude CLI writes ~/.claude/.credentials.json
  → backend polls credentials.json for change (max 120s)
  → on success: refetch auth-status → card now shows "Logged in as …"
  → on timeout: error toast, no state change
```

## 5. Error Handling

| Case | Behaviour |
|---|---|
| Test button clicked while cooldown active | HTTP 429 with `Retry-After: <seconds>`, UI shows "wait another {n}s" |
| Test against provider with no auth | HTTP 412, UI shows "Log in first or set an API key" |
| Mission times out (>120s for the mini-prompt) | UI chip ✗ with "Timeout", logs accessible |
| `artifacts/files/ok.txt` missing after APPROVED | UI chip ✗ "Mission APPROVED but file missing — Critic gap" (rare; surfaces the recurring `_archive_task_artifacts` bug class) |
| OAuth token expired mid-test | `_env_builder()` already chose API-Key fallback; if no API-Key, mission fails fast with WorkerLaunchError, UI shows "Token expired" |
| User switches active provider mid-mission | Existing mission continues with its original worker; new spawns pick up the new active provider |

## 6. Testing

### 6.1 Unit Tests (new)

- `tests/unit/missions/test_subagent_worker_routing.py` — `_make_worker()` returns the correct worker class for each of the four provider IDs; raises on unknown. Uses fake config + bus.
- `tests/unit/missions/test_worker_override.py` — `_make_worker()` honours `mission.worker_override` over `cfg.subagent.active_provider`.
- `tests/unit/missions/test_env_builder_auth_priority.py` — OAuth-present → `ANTHROPIC_OAUTH_TOKEN` set, `ANTHROPIC_API_KEY` unset; OAuth-missing → reverse; both missing → raises.
- `tests/unit/web/test_subagent_test_endpoint.py` — happy path (200 + mission_id), cooldown (429), missing auth (412), unknown provider (404).
- `tests/unit/web/test_subagent_routes_activate.py` — activate switch writes via `config_writer`, emits `ConfigReloaded`.
- `tests/unit/workers/test_grok_worker.py` — analogous to existing `test_gemini_worker.py`: prompt construction, env handling, stream parsing.

### 6.2 Frontend Tests

- `ApiKeysView.test.tsx` — renders four cards in the new tier; "Set active" radio fires `switchSubagentProvider`.
- `SubagentProviderCard.test.tsx` — Anthropic card shows two auth blocks; Gemini card shows only API-Key block; Test button disabled while cooldown active.

### 6.3 Integration (manual smoke)

After implementation:
1. Boot Jarvis, open ApiKeysView, see the four cards in the Subagent tier.
2. Switch active to Gemini, click Test — mission spawns, ok.txt appears, chip turns ✓.
3. Switch back to Claude, voice-spawn a real sub-agent — uses `ClaudeDirectWorker`.

## 7. Files Touched

### Backend

| File | Change |
|---|---|
| `jarvis/missions/workers/_models.py` | **new** — frontier model constants |
| `jarvis/missions/workers/grok_worker.py` | **new** — analogous to `gemini_worker.py` |
| `jarvis/missions/workers/__init__.py` | export `GrokWorker` |
| `jarvis/missions/kontrollierer/dispatch.py` | `_make_worker()` switch, honour `worker_override` |
| `jarvis/missions/init.py` | `_env_builder()` auth-priority logic for Anthropic + Codex |
| `jarvis/missions/types.py` | add `worker_override: str \| None = None` to `Mission` |
| `jarvis/core/config.py` | new `SubagentConfig` Pydantic class |
| `jarvis/providers/spec.py` | `ProviderTier` += `"subagent"`, four provider specs |
| `jarvis/ui/web/subagent_routes.py` | **new** — activate, test, auth-status, oauth-login endpoints |
| `jarvis/ui/web/api.py` | mount the new router |
| `jarvis.toml` (default) | `[subagent] active_provider = "claude-subagent"` |
| `scripts/config-soll.json` | drift-guard pin for `active_provider` |

### Frontend

| File | Change |
|---|---|
| `jarvis/ui/web/frontend/src/components/SubagentProviderCard.tsx` | **new** |
| `jarvis/ui/web/frontend/src/components/AbonementBlock.tsx` | **new** |
| `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx` | render fourth tier |
| `jarvis/ui/web/frontend/src/hooks/useProviders.ts` | `ProviderTier` += `"subagent"`, `switchSubagentProvider` |
| `jarvis/ui/web/frontend/src/i18n/de.json` | new strings |
| `jarvis/ui/web/frontend/src/i18n/en.json` | new strings |

### Tests

| File | Change |
|---|---|
| `tests/unit/missions/test_subagent_worker_routing.py` | **new** |
| `tests/unit/missions/test_worker_override.py` | **new** |
| `tests/unit/missions/test_env_builder_auth_priority.py` | **new** |
| `tests/unit/web/test_subagent_test_endpoint.py` | **new** |
| `tests/unit/web/test_subagent_routes_activate.py` | **new** |
| `tests/unit/workers/test_grok_worker.py` | **new** |
| `jarvis/ui/web/frontend/src/views/ApiKeysView.test.tsx` | extend with subagent-tier cases |
| `jarvis/ui/web/frontend/src/components/SubagentProviderCard.test.tsx` | **new** |

## 8. Phases

Three sequential phases, each independently shippable, each with green tests at the end. No phase can move forward while the prior phase has red tests.

### Phase 1 — Backend Worker Routing (no UI)

1. `_models.py` with the four constants.
2. `GrokWorker`.
3. `SubagentConfig`, `[subagent]` in `jarvis.toml`, drift-guard pin.
4. `_make_worker()` switch.
5. `_env_builder()` auth priority.
6. `worker_override` field on `Mission`.
7. Tests 6.1.

**Shippable state**: changing `active_provider` in `jarvis.toml` and restarting Jarvis swaps the worker. Default value preserves today's behaviour.

### Phase 2 — Backend API

1. `subagent_routes.py` with four endpoints.
2. Mount router in `api.py`.
3. Cooldown dict, in-memory.
4. Tests for endpoints (happy + 429 + 412 + 404).

**Shippable state**: `curl POST /api/subagent/test/gemini-subagent` triggers a real mission; cooldown enforced.

### Phase 3 — Frontend Subagent Tier

1. `useProviders.ts` Tier-type extension + `switchSubagentProvider`.
2. `AbonementBlock` component.
3. `SubagentProviderCard` component (two variants by provider).
4. `ApiKeysView` renders the fourth tier.
5. i18n strings.
6. Frontend tests.

**Shippable state**: the user can switch providers, log in via UI, and run mini-tests entirely from the web UI.

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| OAuth token in wrong env-slot regression (the BUG that prompted this work) | Explicit `unset` of the alternative env in `_env_builder()`. Test `test_env_builder_auth_priority.py` is the regression guard. |
| New worker (Grok) doesn't pass artifact-archive correctly (`_archive_task_artifacts` ate three files yesterday) | Wave A diff-extraction fix from earlier today already in main; new Grok worker piggybacks on the same path. |
| Drift-guard rolls `active_provider` back to old value | Drift-guard pin must list `subagent.active_provider` in `config-soll.json` *before* Phase 1 ships, otherwise a parallel session can roll the value. |
| 60s test cooldown too short during dev | Configurable via `[subagent].test_cooldown_seconds`. Drift-guard pinned to 60. |
| `claude /login` subprocess on Windows blocks the FastAPI event loop | Run with `asyncio.create_subprocess_exec`, stream-read stdout off-loop; `NO_WINDOW_CREATIONFLAGS` mandatory (AP-1). |
| User switches active provider during an in-flight mission | Active mission retains its original worker (constructed at dispatch time). New spawns pick up the new active provider. No mid-mission swap. |

## 10. Non-Goals

- This design does **not** change anything about the brain-tier router. The brain still runs on whatever `[brain].primary` says (today: Gemini). Subagent is its own tier.
- It does **not** introduce a new harness; the four workers are all existing-pattern subprocess workers (or in the case of Grok, the standard streaming HTTP pattern from `gemini_worker.py`).
- It does **not** change the existing 30s spawn-cooldown in `spawn_openclaw.py`. That is a separate gate for the voice path.

---

**End of design.**

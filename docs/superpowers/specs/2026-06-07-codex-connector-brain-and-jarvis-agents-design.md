# Codex Connector — Brain-Provider Card + Jarvis-Agent Activation

**Date:** 2026-06-07
**Status:** Implemented + reviewed (APPROVE). See Amendment A1.
**Author:** assistant (with maintainer)

---

## Amendment A1 (2026-06-07, post-approval) — full Brain×Jarvis-Agent matrix

After the design was approved, the maintainer extended the requirement: Brain
and Jarvis-Agent must be **independently selectable in all combinations**, including
**Codex as a Brain provider** (e.g. brain=Codex + subagent=Gemini). This
**reverses decision D2** ("upper card = connection only"): Codex is now a
**first-class brain provider**, not connection-only.

Discovery during implementation: `jarvis/plugins/brain/codex.py` (`CodexBrain`)
was *also* a lost module — `pyproject.toml` registers the `codex` brain
entry-point but the module never existed in git. So restoring Codex-as-brain
restores the original intent.

Auth split (validated live against the maintainer's real Codex):
- **Codex-as-Brain** uses an **OpenAI API key** (`codex_openai_api_key`). A chat
  brain cannot use the ChatGPT OAuth. `/api/brain/switch` requires the key for
  codex (clear 409 otherwise) — no silent first-turn failure.
- **Codex-as-Jarvis-Agent** uses the **ChatGPT subscription (OAuth)** OR an API key.

New work beyond the original spec:
- `jarvis/plugins/brain/codex.py` rebuilt (OpenAI chat brain on the codex key);
  the `!= "codex"` bypass in `brain_switch` removed (codex is a real plugin now).
- Codex brain card keeps its "Activate" radio (it IS a brain) — the planned
  radio-removal in §4.2 is **dropped**.
- Single source of truth for the codex Jarvis-Agent slugs:
  `provider_map.CODEX_SUBAGENT_SLUGS` / `CODEX_SUBAGENT_CANONICAL`, imported by
  all four sites (switch endpoint, app_control, worker selector, env builder)
  + an object-identity parity test (BUG-008 hardening).

Verification: 949 backend tests + 128 vitest green; ruff clean on new code; live
API smoke confirms the brain card, Jarvis-Agent row, and Jarvis-Agent switch all work
against the maintainer's real `codex login` (mode=chatgpt). Remaining hands-on:
restart the app, activate Codex as Jarvis-Agent in the UI, run one real mission.

---

## 1. Problem

In the **API Keys & Providers** screen a user cannot connect Codex (OpenAI's
ChatGPT/Codex agent CLI). Two distinct symptoms, two distinct root causes:

1. **Brain-provider card (upper list).** The "Connect with ChatGPT" button is
   dead. The card permanently shows *"Loading Codex status"* and clicking
   *Activate* raises a never-ending toast: *"OpenAI Codex: first connect Codex or
   save API key, then activate."*
2. **Jarvis-Agents list (lower list).** Codex does not appear at all — it cannot be
   selected as the heavy-task worker.

The maintainer's goal: connect Codex **both** ways (login **and** API key), with
the upper card used only to *connect*, and Codex doing real work **as the
Jarvis-Agent**. The result must be **verified live**, not just in tests.

## 2. Root-cause analysis (verified in code)

### 2.1 The Codex auth module is a stub

`jarvis/codex_auth.py` is a placeholder. Git history (`git log -- jarvis/codex_auth.py`)
shows it was **first committed already as a stub** (`0bba80e7` "Lost Modules") —
the real `CodexAuthService` was never in version control and cannot be restored.

Consequences of the stub:
- `status()` returns `connected=False` **always**; it only probes
  `codex --version` for `installed`.
- `status().to_dict()` returns `{installed, connected, binary_path, user_email,
  error}` — it does **not** return the fields the frontend reads
  (`message`, `version`, `mode`). The frontend
  (`ApiKeysView.tsx::CodexAuthWidget`) renders `status?.message ?? "status_loading"`,
  so the card is stuck on *"Loading Codex status"*.
- `start_login()` **always raises** `FileNotFoundError`.
- `logout_blocking()` always fails.

Because `jarvis/brain/app_control.py::is_credential_present` resolves the
`auth_mode == "codex"` case as *"any saved `codex_openai_api_key` **OR**
`CodexAuthService.status().connected`"*, and the OAuth half is permanently
`False`, the card reports `configured == False` whenever the user relies on the
login flow — which is exactly the repeated activation toast.

### 2.2 Codex is absent from the Jarvis-Agents surface

The Jarvis-Agents section (`components/SubagentSection.tsx`) is rendered from
`GET /api/jarvis-agent/status` → `mapping_rows`, which is built by iterating
`jarvis/missions/worker_runtime/provider_map.py::MAPPINGS`. `MAPPINGS` contains
only `gemini, claude-api, openai, openrouter, grok`. **Codex has no row**, so no
card is shown. `POST /api/subagent/switch` likewise validates against the
Jarvis-Agent provider allow-list (derived from `MAPPINGS`) and rejects Codex with HTTP 404.

This is *correct* in spirit: Codex is **not** a Jarvis-Agents-routed provider — it has
no Jarvis-Agents provider slug and no shared ENV-var mapping. It is a **direct
worker**.

### 2.3 The Codex worker already exists and is OAuth-ready

`jarvis/missions/workers/codex_direct_worker.py::CodexDirectWorker` ("Welle 6",
2026-05-18) drives `codex exec --json` directly via ChatGPT-OAuth. The routing
function `jarvis/missions/init.py::_select_subagent_worker_kind` already maps
`sub_jarvis.provider in ("chatgpt", "openai-codex")` → `codex_direct`.

So the **execution path is complete**; only the **selection path** (UI + switch
endpoint key-check + persist) is missing. The worker currently **always strips**
`OPENAI_API_KEY` and `CODEX_HOME`, forcing the OAuth path — which means it does
**not yet** honor an API-key-only setup.

## 3. Decisions (from brainstorming)

- **D1 — Both auth models.** Support the ChatGPT-subscription login (`codex
  login` → `~/.codex/auth.json`, no per-call billing) **and** the OpenAI API key
  (`codex_openai_api_key`, OpenAI Platform billing). Primary/most-wanted path is
  the subscription login.
- **D2 — Upper card = connection only.** Codex is **not** a conversational router
  brain (no backend exists and it is the wrong tool — it is a coding agent). The
  upper card manages login + key + an honest status. Its misleading "Activate as
  router brain" radio is removed and replaced by a hint pointing to the Jarvis-Agents
  section.
- **D3 — Codex as Jarvis-Agent.** Codex becomes a selectable heavy-task worker in the
  Jarvis-Agents list, reusing the connection/key from the upper card (no second key
  input), with the same "Activate" radio as the other providers.
- **D4 — Codex is a special case, not a `MAPPINGS` row** (rejected approach B).
  Adding Codex to `MAPPINGS` would break the Jarvis-Agents bridge contract
  (`to_provider_slug`, `validate_configured_providers`, env-var logic). Codex is
  surfaced as an explicit, additive special case in the status + switch
  endpoints.
- **D5 — Live verification is part of "done".** A real `codex login` + a real
  mini-mission executed by the Codex Jarvis-Agent must be observed.

### Non-goals

- Codex as the lightweight router brain (`brain.primary`). Out of scope per D2.
- A general "direct-worker provider registry" refactor (approach C). Future
  cleanup, not this change.
- Retro-translating existing German UI strings beyond the strings this change
  touches.

## 4. Detailed design (approach A)

### 4.1 Rebuild `jarvis/codex_auth.py` (`CodexAuthService`)

Replace the stub with a real, cross-platform, IO-light service. **No new hard
dependency** — pure stdlib (`subprocess`, `pathlib`, `json`). It must degrade to
a clean "not installed" no-op when the `codex` binary is absent (CLOUD.md Rule #1
+ AP-1 subprocess hygiene).

**Binary detection.** Reuse the cross-platform resolver pattern from
`codex_direct_worker._resolve_codex_binary()`: `shutil.which` over
`("codex", "codex.cmd", "codex.exe")`, honoring an explicit `binary_path`
override (from `[codex].binary_path`).

**Auth-state detection.** Read the Codex auth file:
`Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "auth.json"`.
Parse defensively (treat as opaque JSON; never raise on shape drift):
- OAuth tokens present (e.g. a `tokens` object with an access/refresh token) →
  `mode="chatgpt"`, `connected=True`. Optionally decode the id-token JWT payload
  for the account email (best-effort; never required; never logged).
- A non-empty `OPENAI_API_KEY` field present → `mode="api_key"`,
  `connected=True`.
- Neither → `mode="unknown"`, `connected=False`.

  > The exact `auth.json` schema will be confirmed against the live file during
  > implementation; the parser is written tolerantly so a schema change degrades
  > to `unknown` rather than crashing.

**Status contract (the JSON the frontend consumes).** `to_dict()` must return:

```json
{
  "installed": true,
  "connected": true,
  "mode": "chatgpt",            // "chatgpt" | "api_key" | "unknown"
  "version": "codex 0.x.y",     // or null
  "message": "Connected via ChatGPT (you@example.com)",
  "user_email": "you@example.com",  // or null
  "binary_path": "codex",
  "error": null
}
```

`message` is a short human-readable summary the card shows directly (the frontend
falls back to a "loading" string only when `message` is missing — which must no
longer happen). The badge logic in `ApiKeysView.tsx` (`installed?`,
`configured?`) is unchanged.

**Login / logout.**
- `start_login()` spawns `codex login` as a detached subprocess and returns it
  (the route returns `proc.pid`). `codex login` opens the browser for OAuth and
  runs a local callback; on Windows under `pythonw.exe` it is spawned with a new
  console so a printed device URL is visible as a fallback. Subprocess hygiene:
  `creationflags` from `jarvis/core/process_utils.py`, `shell=False` (AP-1). On a
  headless host without a browser the flow may not complete — that is inherent to
  OAuth and documented; Codex remains a power-user extra and the base install
  never requires it.
- `logout_blocking()` runs `codex logout` (fallback: remove `auth.json`) and
  returns `(ok, error)`.

The REST routes in `provider_routes.py` (`/api/codex/status`, `/api/codex/login`,
`/api/codex/logout`, `/api/codex/binary-path`) already exist and call this
service — they need **no signature change**, only the real service behind them.

### 4.2 Upper Codex card → connection-only (`ApiKeysView.tsx`)

- For `auth_mode === "codex"`, **do not render** the brain-tier "Activate" radio
  (`ActiveControl`). Replace with a one-line hint (new i18n key):
  *"Codex runs as a Jarvis-Agent — activate it below."* / DE: *"Codex arbeitet als
  Jarvis-Agent — unten aktivieren."*
- The card keeps: status line (now populated by the real `message`/`version`/
  `mode`), the install hint + copy button (when not installed), the
  "Connect with ChatGPT" / "Disconnect" buttons, and the `codex_openai_api_key`
  form. This is the single home for the Codex credential.
- Card-click activation (`handleCardActivate`) must be a no-op for codex (it
  currently would call the brain switch). Guard on `auth_mode === "codex"`.

This removes the dead "activate as router brain" path entirely, so the
never-ending toast cannot recur.

### 4.3 Surface Codex in the Jarvis-Agents list

**Canonical slug.** Store/display Codex as `"openai-codex"` (the value
`_select_subagent_worker_kind` already accepts, alongside the alias `"chatgpt"`).

**`GET /api/jarvis-agent/status` (server.py).** After the `MAPPINGS` loop, append one
synthetic Codex row:

```python
{
  "jarvis": "openai-codex",
  "openclaw": "codex-cli (direct)",      # cosmetic; codex has no Jarvis-Agent slug
  "env_var": "ChatGPT-OAuth",
  "env_fallback": "OPENAI_API_KEY",
  "key_set": <codex connected OR codex_openai_api_key saved>,
  "is_active_brain": <primary == "openai-codex">,
}
```

`primary` is already `canonical_subagent_provider(sub_raw) or router_primary`;
`canonical_subagent_provider("openai-codex") == "openai-codex"` (passthrough), so
the active highlight works without further change. `to_provider_slug` is
correctly left to fail-soft to `None` for codex (it has no slug).

**`POST /api/subagent/switch` (provider_routes.py).** Add a codex branch
**before** the Jarvis-Agent provider allow-list membership check:

```python
CODEX_SUBAGENT_SLUGS = frozenset({"openai-codex", "chatgpt"})
if provider in CODEX_SUBAGENT_SLUGS:
    if not (CodexAuthService(_codex_binary_path(request)).status().connected
            or cfg_mod.get_secret("codex_openai_api_key")):
        raise HTTPException(409, "Codex is not connected — log in or save an API key first.")
    # 3-layer persist to the canonical "openai-codex"
    set_sub_jarvis_provider("openai-codex")
    ... return {ok, active: "openai-codex", persisted, restart_required: True}
```

Mirror the same acceptance in `jarvis/brain/app_control.py::_switch_subagent`
(the brain-tool path) so the two switch sites do not drift (BUG-008 anti-drift
discipline). The key-check there uses the same Codex-connected-or-key rule
instead of `get_provider_secret`.

**`SubagentSection.tsx`.** Add `"openai-codex": "OpenAI Codex"` to
`PROVIDER_LABELS` so both the provider card and the bridge card's "Active worker"
line render a friendly name. No structural change — the component already maps
over `bridge.mapping`.

### 4.4 Honor both auth models through to the worker

`CodexDirectWorker` currently strips `OPENAI_API_KEY` unconditionally to force
OAuth. Make the strip **conditional on OAuth being available**:

- If `~/.codex/auth.json` shows OAuth tokens → strip `OPENAI_API_KEY` (use the
  subscription, as today). Always strip `CODEX_HOME` (the per-mission dir breaks
  the global OAuth home — existing, correct behavior).
- Else, if a Codex API key is configured → **keep** `OPENAI_API_KEY` so `codex
  exec` runs in API mode.

The worker env (`jarvis/missions/init.py::_env_builder` →
`build_worker_env`) must carry the Codex key for the API-key path: source it from
`codex_openai_api_key` with `openai_api_key` as a fallback, surfaced as
`OPENAI_API_KEY`.

> Primary verification target is the **OAuth/subscription** path (the maintainer's
> "my Codex"). The API-key Jarvis-Agent path is supported but secondary.

## 5. Data & config touch-points (no new wire-format enum)

- Secret slot: `codex_openai_api_key` (already in `wizard.SECRETS` / provider
  spec). Unchanged.
- Config: `[codex].binary_path` (read by `_codex_binary_path`), and
  `[brain.sub_jarvis].provider = "openai-codex"` for the Jarvis-Agent selection.
  Both already exist; this change writes a new *value* (`"openai-codex"`), not a
  new key — so the five-layer enum parity scaffolding is **not** required (the
  set of accepted values lives in `_select_subagent_worker_kind` +
  `CODEX_SUBAGENT_SLUGS`; a small parity test pins them together).

## 6. Cross-platform / doctrine compliance

- `CodexAuthService` is stdlib-only, uses `pathlib` + `CODEX_HOME`, never
  hardcodes `C:\Users\...`, and returns a clean "not installed" status on any OS
  where `codex` is absent. Base `python:3.11-slim` boot is unaffected — Codex is
  a power-user extra, never required.
- All subprocess spawns use `creationflags` from `process_utils` and
  `shell=False` (AP-1).
- No secret is ever logged in full; `start_login`/`status` log only the binary
  name and connection booleans.

## 7. Testing strategy

**Unit (TDD, write tests first):**
1. `CodexAuthService.status()` — installed/not-installed; auth.json variants
   (OAuth tokens → `chatgpt`; `OPENAI_API_KEY` → `api_key`; empty/missing →
   `unknown`); `to_dict()` contains `message`/`version`/`mode`. Use a temp
   `CODEX_HOME` + a fake binary; never call the real CLI.
2. `is_credential_present` for codex — true when key saved, true when OAuth
   connected (mocked), false when neither.
3. `/api/subagent/switch` — accepts `openai-codex`/`chatgpt` when connected,
   409 when not connected, persists `"openai-codex"`; still 404 for genuinely
   unknown providers.
4. `/api/jarvis-agent/status` — includes the synthetic codex row; `is_active_brain`
   true when `sub_jarvis.provider == "openai-codex"`.
5. `CodexDirectWorker` env — strips `OPENAI_API_KEY` when OAuth present, keeps it
   when only the API key is configured (parametrized).
6. Frontend (vitest) — codex brain card renders **no** brain "Activate" radio +
   shows the Jarvis-Agents hint; the Jarvis-Agents list renders the Codex card.
7. Parity test pinning `CODEX_SUBAGENT_SLUGS` ↔ the slugs
   `_select_subagent_worker_kind` routes to `codex_direct`.

**Live verification (the maintainer, after restart + frontend rebuild):**
- Open API Keys: Codex brain card shows real status (not "Loading…").
- Click "Connect with ChatGPT" → complete `codex login` → card flips to
  "connected / ChatGPT".
- Jarvis-Agents list shows a Codex card → click "Activate" → success toast.
- Restart voice/app, run a small mission ("create a file X with content Y") and
  confirm the Codex Jarvis-Agent executes it (mission completes, file written).
- Record the outcome (this is required for "done" per D5).

## 8. Anti-patterns explicitly respected

- **AP-1** subprocess hygiene (`creationflags`, `shell=False`) in login/logout.
- **AP-2 / AP-12** no secret accepted by voice/chat; keys stay UI-only; nothing
  logs a full key.
- **AP-7** config writes go through `config_writer` (`set_sub_jarvis_provider`,
  `set_codex_binary_path`).
- **BUG-008 class** the two Jarvis-Agent-switch sites (`provider_routes` +
  `app_control`) gain the codex branch together + a parity test.
- **CLOUD.md Rule #1** cross-platform, graceful no-op when codex absent.

## 9. Risks / open questions

- **`auth.json` schema** is assumed (tokens vs `OPENAI_API_KEY`); confirmed live
  during implementation. Tolerant parsing means a wrong assumption degrades to
  `unknown`, never a crash.
- **`codex login` under `pythonw.exe`** (no console): mitigated by spawning a new
  console; final spawn flags settled during the live test.
- **API-key Jarvis-Agent path** depends on `codex exec` honoring `OPENAI_API_KEY`
  when no OAuth is present — verified during the live test; the OAuth path is the
  primary target.
- **Codex binary not installed** on the test machine → blocks live verification;
  the maintainer installs `npm i -g @openai/codex` and runs `codex login` first.

## 10. Files touched (summary)

- `jarvis/codex_auth.py` — rebuild service (core).
- `jarvis/ui/web/provider_routes.py` — codex branch in `/api/subagent/switch`.
- `jarvis/ui/web/server.py` — synthetic codex row in `/api/jarvis-agent/status`.
- `jarvis/brain/app_control.py` — mirror codex acceptance in `_switch_subagent`.
- `jarvis/missions/init.py` + `workers/codex_direct_worker.py` — conditional
  key handling for the API-key path.
- `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx` — connection-only codex card.
- `jarvis/ui/web/frontend/src/components/SubagentSection.tsx` — codex label.
- `jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json` — new hint key.
- Tests across `tests/unit/...` + a vitest spec.

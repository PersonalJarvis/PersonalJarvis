# Antigravity / Gemini-CLI over Google subscription — Brain + Jarvis-Agent provider

**Date:** 2026-06-20
**Status:** Design — awaiting maintainer review before implementation
**Author:** Jarvis dev session (goal: bill Antigravity over the Google subscription, in the API-Keys section, for Brain Provider and Jarvis-Agents)

---

## 1. Problem

The user wants Jarvis to run its conversational **Brain** and its **Jarvis-Agents** against their **Google subscription** (the "Sign in with Google" / Google AI Pro/Ultra path), **without a paid Gemini API key** — and to select that path from the API-Keys section, the same way they pick any other provider.

Today the only Google brain provider is `gemini`, which is **API-key only** (`GeminiBrain` raises if no `gemini_api_key` is set). There is no way to use the Google-account OAuth login that Antigravity / the Gemini CLI already use.

## 2. Key precedent (this is not new ground)

`CodexBrain` (`jarvis/plugins/brain/codex.py`) already implements exactly the pattern we need: **one brain provider, two backends** —

* **API key** → normal chat-completions stream.
* **Subscription OAuth** → drive an external CLI (`codex exec`) as a subprocess over the login token stored on disk; no per-call API billing.

`CodexAuthService` (`jarvis/codex_auth.py`) reports installed/connected/mode/email by reading the CLI's on-disk auth file. `provider_spec.py` carries `auth_mode="codex"` + `login_cli=("codex","login")`, and the UI renders a dedicated `CodexAuthWidget`. Backend routes `/api/codex/{status,login,logout,binary-path}` drive the flow.

**We replicate this pattern for Google.** A new provider `antigravity` mirrors Codex end-to-end.

## 3. Findings that shape the design (verified on this machine + web research, 2026-06-20)

1. **The user is already logged in via Google OAuth.** `~/.gemini/oauth_creds.json` holds a valid, refresh-capable token (`access_token`, `refresh_token`, `expiry_date`, scope `…/auth/cloud-platform`), `~/.gemini/settings.json` has `selectedType: "oauth-personal"` and `model: "gemini-3.1-pro-preview"`, active account `maintainer@example.com`. **No API key is involved.**
2. **The Gemini CLI is installed** (`@google/gemini-cli@0.47.0`) with a real headless mode: `-p/--prompt`, `-m/--model`, `--approval-mode {default,auto_edit,yolo,plan}`, `-o/--output-format {text,json,stream-json}`. (Its PATH shims are currently broken npm temp files — `.gemini.cmd-XXedit` etc. — so it must be invoked via `node <bundle>/gemini.js` or after a shim repair.)
3. **Antigravity is installed as an IDE** (Electron/VS Code fork under `%APPDATA%\Antigravity`) and shares the same `~/.gemini/` store, but the IDE itself has **no scriptable headless CLI**.
4. **The landscape changed on 2026-06-18 (two days ago).** Google sunset the Gemini CLI's *consumer* OAuth path for individual Pro/Ultra/free accounts and replaced it with the **Antigravity CLI `agy`** (a Go binary, `agy -p "…"`, "Sign in with Google", honors Pro/Ultra quota, credentials in the OS keyring). The installed Gemini CLI may still serve this account but is on a shrinking runway. `agy` is **not installed** on this machine yet.
5. **Hard ToS constraint.** Driving the *official* binary (`agy` / `gemini`) as a subprocess is normal CLI use and is allowed. **Scraping the stored OAuth token into our own HTTP client violates Google's ToS and risks account bans** (multiple reported). The design therefore **only ever shells out to the official binary** — it never reads the token to make its own API calls.

## 4. Decisions (maintainer, 2026-06-20)

* **D1 — CLI target:** binary-agnostic resolver that **prefers `agy`** (the official successor) and **falls back to the installed Gemini CLI**. Works today (via gemini) and survives the sunset (via agy). One provider, one auth service.
* **D2 — Verification:** **no auto-billed CLI call.** Live verification is the user-triggered "Test" button in the UI. (A subscription-tier check in the logged-in browser was requested but the claude-in-chrome extension is offline; deferred — see §10.)
* **D3 — UI surface:** a **separate provider entry "Antigravity (Google-Abo)"**, not an auth toggle on the existing Gemini API-key card. Clean separation, like Codex is separate from OpenAI.

## 5. Architecture

New provider id: **`antigravity`** (brain tier). It never has an API key; it is OAuth-only. Everything below is additive and mirrors the Codex provider.

### 5.1 Binary resolver — `jarvis/google_cli/resolver.py` (new)

Single source of truth for "which official Google CLI do we drive, and how".

```
resolve_google_cli() -> GoogleCli | None
```

`GoogleCli` = frozen dataclass `{kind: "agy"|"gemini", argv_prefix: list[str], version: str|None}`.

Resolution order (capability-probed, cross-platform via `shutil.which` + known install paths):
1. `agy` / `agy.exe` on PATH → `kind="agy"`, `argv_prefix=["agy"]`.
2. `gemini` / `gemini.cmd` on PATH → `kind="gemini"`, `argv_prefix=["gemini"]`.
3. Fallback: the npm-global Gemini bundle (`<npm root -g>/@google/gemini-cli/bundle/gemini.js`) → `argv_prefix=["node", "<bundle>"]`. (Covers the broken-shim case on this machine.)
4. None → provider is "installed=false".

### 5.2 Auth service — `jarvis/google_cli/auth_service.py` (new, mirror of `CodexAuthService`)

```
class GoogleCliAuthStatus:  # frozen
    installed: bool          # a CLI binary was resolved
    connected: bool          # an OAuth login is present
    mode: str                # "oauth-personal" | "api_key" | "unknown"
    cli_kind: str | None     # "agy" | "gemini"
    version: str | None
    user_email: str | None   # from google_accounts.json active / id_token

class GoogleCliAuthService:
    def status() -> GoogleCliAuthStatus
    def start_login() -> None        # spawn `agy login` / `gemini` interactive in a visible console
    def logout_blocking() -> ...     # `agy logout` / clear creds
    def set_binary_path(path) -> ... # optional manual override
```

`connected` detection (best-effort, no token value ever read into business logic):
* Gemini CLI: `~/.gemini/oauth_creds.json` exists **and** `~/.gemini/settings.json security.auth.selectedType == "oauth-personal"`.
* agy: probe the agy keyring/status command (`agy whoami` / equivalent) — exact command confirmed during implementation; degrade to `connected=false` if the probe fails.

The email is read from `~/.gemini/google_accounts.json.active` (already confirmed to be a plain string) or the `id_token` JWT payload — display only.

### 5.3 Brain plugin — `jarvis/plugins/brain/antigravity.py` (new, mirror of `CodexBrain`)

`AntigravityBrain` implements the `Brain` protocol. **OAuth-only — no API-key path.**

* `complete(req)`:
  1. Resolve the CLI (`resolve_google_cli`). None → `RuntimeError` with a clear English install/login hint.
  2. Build a light conversational prompt from the last ~6 turns (reuse the `_build_cli_prompt` shape from Codex; the heavy router system prompt is dropped — it would confuse and slow the agent CLI).
  3. Spawn `<argv_prefix> -p "<prompt>" -m <model> --approval-mode plan -o json` in a throwaway temp dir, `NO_WINDOW_CREATIONFLAGS`, dropped `GEMINI_API_KEY`/`GOOGLE_API_KEY` env (so the subscription login wins, never an accidental key).
  4. Stream a no-text progress tick every few seconds (same anti-stall trick as Codex — the agent CLI is slow), enforce `_CLI_TIMEOUT_S`, parse the JSON result frame, yield the answer text + `finish_reason="stop"`.
* `--approval-mode plan` = read-only: the conversational brain cannot write files or run commands.
* Default model: `gemini-3.1-pro-preview` (matches the user's settings; overridable via `[brain.providers.antigravity].model`).

### 5.4 Jarvis-Agent (heavy worker) backend

The Jarvis-Agent path wants a CLI that *can act* (write files, run commands), so it uses **`--approval-mode yolo`** (or `auto_edit`) instead of `plan`, inside the existing Phase-6 worktree + Job-Object isolation (no change to isolation invariants). Wiring:
* Add an `antigravity` row to the Jarvis-Agent provider mapping (`/api/openclaw/status` → `SubagentMappingRow`), `key_set` driven by `GoogleCliAuthService.status().connected` (not by an API-key slot).
* A `GoogleCliWorker` (mirror of `CodexWorker`, `jarvis/missions/workers/`) drives `<argv_prefix> -p … --approval-mode yolo -o stream-json` and streams `WorkerProgress`.
* `PROVIDER_LABELS` in `SubagentSection.tsx` gets `"antigravity" → "Antigravity (Google-Abo)"`.

### 5.5 Provider spec + config

* `provider_spec.py`: extend `AuthMode` to `Literal["api_key", "codex", "antigravity", "none"]`; add a `ProviderSpec(id="antigravity", label="Antigravity (Google-Abo)", tier="brain", auth_mode="antigravity", secret_keys=(), dashboard_url="https://antigravity.google", login_cli=("agy","login"), install_hint="curl -fsSL https://antigravity.google/cli/install.sh | bash  (or: already logged in via Gemini CLI)", credential_path_hint="~/.gemini/oauth_creds.json")`.
* `pyproject.toml` `[project.entry-points."jarvis.brain"]`: `antigravity = "jarvis.plugins.brain.antigravity:AntigravityBrain"`. (Run `pip install -e . --no-deps` after.)
* `PROVIDER_SECRET_CANDIDATES`: **no key slot for `antigravity`** (OAuth-only). `get_provider_secret("antigravity")` returns `None` by design.
* `[brain.providers.antigravity]` in `jarvis.toml`: `auth_mode="oauth"`, `model="gemini-3.1-pro-preview"`.

### 5.6 Catalog / Frontier (no live `/v1/models`)

Like Codex, `antigravity` is **excluded from `CATALOG_PROVIDERS`** (`model_catalog.py`) and from `frontier_resolver.SUPPORTED_PROVIDERS` — there is no API-key `/v1/models` endpoint over OAuth. The model picker shows a **curated list** (`gemini-3.1-pro-preview`, `gemini-3-flash`/`gemini-3.5-flash`, plus a note that the available set is plan-gated) with `source="curated"`. No code change is needed for graceful fallback — the catalog already degrades when a provider has no key.

### 5.7 Backend routes — `provider_routes.py` (mirror `/api/codex/*`)

* `GET  /api/antigravity/status` → `GoogleCliAuthService.status()`.
* `POST /api/antigravity/login` → `start_login()` (spawns the official CLI login in a visible console).
* `POST /api/antigravity/logout` → `logout_blocking()`.
* `POST /api/antigravity/binary-path` → optional manual path override.
* The existing brain/Jarvis-Agent switch routes (`/api/brain/switch`, `/api/subagent/switch`) work unchanged once `antigravity` is a registered provider.

### 5.8 Frontend (mirror `CodexAuthWidget` + reuse the CLI-connect coach)

* `AntigravityAuthWidget.tsx` (new, modeled on `CodexAuthWidget`): shows connected/disconnected, the login command, a "Connect with Google" button → `POST /api/antigravity/login`, disconnect button, account email.
* Reuse `CliConnectCoach` + `CliConnectPoller` for the "run this, then we detect it" UX (poll `GET /api/antigravity/status` until `connected`).
* `ApiKeysView` renders the new provider card by `auth_mode="antigravity"`; `BrainModelSelector` shows the curated list; the "Test" button uses the existing `POST /api/providers/antigravity/test` path (D2: user-triggered).

## 6. Data flow

**Brain turn:** voice/chat → `BrainManager` (active provider `antigravity`) → `AntigravityBrain.complete` → resolve CLI → `agy/gemini -p … --approval-mode plan -o json` subprocess → parse JSON → `BrainDelta` stream → TTS/chat. Billed against the Google subscription; no API key.

**Jarvis-Agent turn:** mission dispatch → `GoogleCliWorker` → `agy/gemini -p … --approval-mode yolo` inside the worktree → streamed `WorkerProgress` → Critic/Kontrollierer → deliverable.

## 7. Error handling & honesty

* **Not connected / no CLI:** clear English message ("Run the Antigravity/Gemini login, or install `agy`"), provider reports `installed/connected=false`, the brain falls back to the next provider in the chain — no silent failure.
* **Sunset runway:** if the Gemini CLI starts refusing the consumer OAuth (post-2026-06-18), the resolver's `agy`-first order means a present `agy` is used automatically; the status surface should show which `cli_kind` is active so the user can see the transition.
* **Tier-agnostic:** the feature works on whatever the account has (free Code Assist *or* Pro/Ultra). The tier only affects quota/rate-limits, not function. The UI notes that quota/limits depend on the user's Google plan.
* **ToS:** the design only shells out to the official binary. It must **never** read the OAuth token to issue its own HTTP requests. This is an explicit anti-pattern for reviewers.

## 8. Testing

* `tests/unit/google_cli/test_resolver.py` — resolver order (agy > gemini > bundle > none), cross-platform path probing (fakes, no real binary).
* `tests/unit/google_cli/test_auth_service.py` — status derivation from fake `~/.gemini/` fixtures (connected/disconnected/api_key/unknown), email extraction.
* `tests/unit/plugins/brain/test_antigravity_brain.py` — contract suite + a fake-subprocess that emits a JSON answer frame, timeout path, no-CLI error path, env-scrub (no `GEMINI_API_KEY` leaks to the child).
* `tests/unit/ui/test_provider_spec_antigravity.py` — spec entry + `AuthMode` literal parity.
* Frontend: `AntigravityAuthWidget.test.tsx` (connected/disconnected render, login click).
* Must not regress: full `pytest tests/unit/` + `npm run test` + `ruff` + `tsc`.

## 9. Units & interfaces (isolation)

| Unit | Purpose | Depends on |
|---|---|---|
| `google_cli/resolver.py` | pick the official binary + argv prefix | `shutil`, filesystem |
| `google_cli/auth_service.py` | report login status, start/stop login | resolver, `~/.gemini/` files |
| `plugins/brain/antigravity.py` | OAuth-only brain via CLI subprocess | resolver, `Brain` protocol |
| `missions/workers/google_cli_worker.py` | heavy worker via CLI subprocess | resolver, worker base |
| `provider_routes.py` (+spec) | HTTP surface for status/login/logout/test | auth_service |
| `AntigravityAuthWidget.tsx` (+coach reuse) | connect UI | status/login routes |

Each unit is independently testable with fakes; the brain and worker share the resolver but nothing else.

## 10. Out of scope / open points

* **Live billed verification (deferred, D2):** one real `agy/gemini -p` call to prove the OAuth path answers — done by the user via the "Test" button, or by the dev only with explicit go.
* **Browser subscription-tier check (deferred):** claude-in-chrome extension is offline; the Pro/Ultra confirmation is pending. Does not block the build (feature is tier-agnostic).
* **`agy` on Windows:** the documented installer is a macOS/Linux `curl | bash`; the Windows install path for `agy` is unconfirmed. Until `agy` is installed here, the resolver uses the installed Gemini CLI — which is exactly the D1 fallback.
* **PATH-shim repair for the Gemini CLI** (the broken `.gemini.cmd-XXedit` temp files) is a nice-to-have; the `node <bundle>` fallback makes it non-blocking.

## 11. Implementation status (2026-06-20)

Branch `feat/antigravity-google-subscription`. TDD throughout; 114 tests green.

**Committed (fully owned, clean files):**
* `jarvis/google_cli/resolver.py` — binary resolver (agy > gemini > npm bundle). **Fixed a real Windows bug live:** `npm root -g` fails (npm is a `.cmd`), so it now probes `%APPDATA%/npm/node_modules` directly.
* `jarvis/google_cli/auth_service.py` — `GoogleCliAuthService` (status/login/logout from `~/.gemini`, never reads the token).
* `jarvis/plugins/brain/antigravity.py` + `pyproject.toml` entry point — OAuth-only brain via the CLI.
* `jarvis/ui/web/provider_spec.py` — `antigravity` provider entry (auth_mode, no secret).
* `jarvis/ui/web/antigravity_routes.py` — `/api/antigravity/{status,login,logout}` (own router module).
* `jarvis/missions/worker_runtime/provider_map.py` — `ANTIGRAVITY_SUBAGENT_SLUGS` SSoT.
* `jarvis/missions/init.py` — selector hard-lock + factory branch + env force-OAuth.
* `jarvis/missions/workers/gemini_worker.py` — hardened binary resolution (same Windows fix).

**Live-verified (read-only, no billed call):** `resolve_google_cli()` → `node <bundle>`; `status()` → installed=True, connected=True, mode=oauth-personal, account `maintainer@example.com`. The brain registers in the plugin registry; the Jarvis-Agent worker resolves the CLI.

**Uncommitted riders** (functional live via the editable install; they live in files a parallel session is actively rewriting — the per-provider model picker — so committing them would sweep that foreign work):
* `server.py` — mount of the antigravity router.
* `provider_routes.py` — `/api/providers` antigravity status, `/api/brain/switch` OAuth gate, `/api/subagent/switch` antigravity acceptance.

**Remaining follow-up (rides with the sibling per-provider-picker work):**
* Frontend `AntigravityAuthWidget.tsx` + the `auth_mode === "antigravity"` render branch in `ApiKeysView.tsx` + `useProviders` login/logout fns + the `SubagentSection` label.
* The curated model-catalog entry for `antigravity` (`model_catalog._build_provider_catalog`).

**Verification still open (user-gated):** browser subscription-tier check (claude-in-chrome extension was offline); one real billed `-p` call to confirm the OAuth path answers end-to-end (user opted out of auto-billing).

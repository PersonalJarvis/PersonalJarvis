# Jarvis Control API

A local, authenticated HTTP API that lets Jarvis — and any local agent (Codex
CLI, Claude Code, a test harness) — do **everything the user can do in the
desktop app**: read and change settings, switch brain/STT/TTS providers, switch
the reply language, rotate provider keys, read the providers snapshot. It is the
real, deterministic alternative to Computer-Use for self-configuration.

Mounted at **`/api/control/*`** on the same FastAPI server the desktop UI uses.
Implemented as a thin authenticated facade (`jarvis/ui/web/control_routes.py`)
over the production-ready layers — `AtomicConfigWriter` (atomic backup +
validate + rollback + audit), `jarvis.brain.app_control` (live provider switch),
and the keyring secret store. It reimplements none of them.

## The per-user API key

Every install generates its own key on first boot (prefix `jctl_`, 256-bit). It
is **not** baked into the package — this is an open-source project, so each user
gets a unique key.

- **Storage (cross-platform):** OS keyring first (Windows Credential Manager /
  macOS Keychain / Linux Secret Service, service `personal-jarvis`, slot
  `jarvis_control_api_key`). On a headless Linux VPS without a Secret Service
  daemon the keyring silently fails, so the key falls back to a `0600` file at
  `data/.control_api_key` (or `$JARVIS_DATA_DIR`). Read order:
  keyring → file → `JARVIS_CONTROL_API_KEY` env seed.
- **Never exported into `os.environ`** during normal operation (a spawned worker
  would inherit it and leak it via `/proc/<pid>/environ`). Read on demand.
- **Where the user finds it:** desktop app → **API Keys → Access &
  Integrations → Control Key** (masked by default, Show/Hide, one-click Copy,
  Regenerate behind a confirm dialog, or replace it with a user-chosen key —
  min 12 chars, `[A-Za-z0-9._~-]`). The browser lock screen (AuthGate) points
  to this section. Headless: `GET /api/control/api-key` from the loopback
  interface, or read the key file.

## Auth

`Authorization: Bearer <key>` on every `/api/control/*` route, constant-time
compared. The key — **not** the localhost binding — is the security boundary
(cloud-first): desktop binds `127.0.0.1`; a VPS may bind `0.0.0.0` only when a
key exists (`assert_bind_safe`, fail-closed). The existing same-origin UI routes
(`/api/settings/*`, `/api/provider/*`) are unchanged and stay key-free. The
key-reveal/rotate endpoints additionally accept a loopback request so the
Settings panel can bootstrap the key before the user has it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/control/auth/probe` | 200 iff the key is valid |
| GET | `/api/control/allowlist` | machine-readable list of mutable settings |
| GET | `/api/control/config?path=…` | read a value (403 for protected paths) |
| PUT | `/api/control/config` | write `{path,value,reason}` — SAFE applies, ASK returns `pending_id` |
| POST | `/api/control/config/confirm` | apply an ASK-tier `pending_id` |
| POST | `/api/control/config/reject` | cancel a pending mutation |
| PUT | `/api/control/language` | convenience: set `reply_language` (auto/de/en/es) |
| GET | `/api/control/providers` | provider + settings snapshot |
| PUT | `/api/control/providers/{tier}` | switch brain/tts/stt/subagent |
| GET | `/api/control/secrets` | list secret slots (masked) |
| PUT/DELETE | `/api/control/secrets/{key}` | set / delete a provider key |
| GET | `/api/control/api-key` | reveal the key (loopback or Bearer) |
| POST | `/api/control/api-key/rotate` | regenerate randomly (`{confirm:true}`) |
| PUT | `/api/control/api-key` | set a user-chosen key (`{value, confirm:true}`; 422 on weak/invalid values, response is masked-only) |

### Two languages (interface vs reply)

Jarvis has **two** language settings, both now backend-backed and in the
allowlist:

- **`ui.language`** (en/de/es) — the INTERFACE the user sees: every label,
  button and message. Formerly frontend-only localStorage; now a config key so
  voice/the Control API can change it. SAFE. When it changes, a
  `UiLanguageChanged` event (and the `ConfigReloaded` from the writer) is
  forwarded over `/ws`, so the open React UI **switches language live** — no
  reload, every OS. The frontend hydrates it on mount and pushes UI clicks to
  `GET/PUT /api/settings/ui-language`.
- **`brain.reply_language`** (auto/de/en/es) — what Jarvis SPEAKS/writes back;
  `auto` mirrors the user's input language. SAFE, hot-reloads into the next turn.

The `PUT /api/control/language` verb (and the voice command) set **both** when
given a concrete code, so "switch the language to German" changes the whole
experience; `auto` only affects replies.

### SAFE vs ASK

`brain.reply_language`, `tts.speed`, `ui.theme` are **SAFE** — they apply
immediately. Language hot-reloads into the next turn (a `ConfigReloaded`
subscriber calls `BrainManager.set_reply_language`) with **no restart**.
`brain.primary`, `tts.provider`, `stt.*` are **ASK** — `PUT /config` returns
`needs_confirmation=true` + a `pending_id`; confirm to apply. `requires_restart`
is reported honestly (e.g. STT re-init).

## Voice path

"Jarvis, switch your language to English" works end-to-end without this HTTP
API: the router brain already has the `set_config_value` tool (router tier), the
strict force-spawn guard does not intercept a plain language request, and
`brain.reply_language` is SAFE → applied → hot-reloaded. The Control API is the
**agent** path; the voice tool is the **spoken** path. Both go through the same
allowlist, the same atomic writer, and the same audit log.

## The default skill

A built-in skill `control-api` (`jarvis/skills/builtin/control-api/SKILL.md`,
`category: meta`, shipped VALIDATED, no voice triggers) is copied into every
install's skills dir on first boot. It is the recipe a local coding agent reads
when asked "can you change something on my Jarvis?" — base URL, key location,
auth header, and `curl` examples for the allowlist/config/language/providers/
secrets endpoints. It has no voice triggers on purpose: a trigger matching
"switch language" would make the router run the skill body (which cannot make
HTTP calls) instead of calling the `set_config_value` tool.

## Cross-platform / cloud-first

Pure FastAPI + stdlib `secrets` + keyring — all in the base install, so the
headless `python:3.11-slim` container boots it unchanged with no GUI extra. The
config path honours `JARVIS_CONFIG` (a writable file on a VPS where
`PROJECT_ROOT` may be read-only). Desktop-only settings (overlay/bar/ducking)
report `requires_ui` honestly rather than crashing on a server.

## Known follow-up

Voice confirmation for **ASK-tier** mutations (the spoken "…confirm?" → "yes"
loop, `SelfModFlowController`) is not yet wired into the speech pipeline. SAFE
settings (the headline language switch) work on voice today, and ASK-tier
settings are fully usable via this Control API's confirm endpoint and via the
desktop UI — so this only affects *spoken* changes to ASK-tier settings.

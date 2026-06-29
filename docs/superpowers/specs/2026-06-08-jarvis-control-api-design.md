# Jarvis Control API — Design Spec

**Date:** 2026-06-08
**Status:** Approved-by-directive (maintainer `/goal`: "deep-dive, plan, just build it; cross-platform").
**Author:** Jarvis-Agents (deep-dive synthesis of 8 parallel explorers).

---

## 1. Problem

The maintainer wants Jarvis to edit **its own** configuration / settings / secrets through a **real local HTTP API** — not Computer-Use, not UI-clicking. Concretely:

1. A **Jarvis Control API** exposing everything the user can do in the desktop app: read/change settings, switch brain/STT/TTS providers, switch reply **language**, rotate API keys/secrets, read missions/outputs/contacts/telephony.
2. Authenticated by a **per-user API key** (open-source package → every install generates its own). Copyable, shown in a new **Settings** panel.
3. Other agents (Codex CLI, Claude Code, "Jarvis test web") can drive the API **locally** to change things.
4. A **default built-in skill** shipped with every install that teaches both the voice path and the agent/API path — e.g. *"Jarvis, switch your language from German to English."*
5. **Cross-platform** (Linux/macOS/Windows + headless VPS). Cloud-first: `jarvis/` must boot on `python:3.11-slim`.

### Why the existing self-mod "absolutely does not work" (root cause, code-verified)

Three independently fatal breaks for the headline "switch language" voice command:

1. **Wrong key in the allowlist.** Reply language is pinned by `brain.reply_language` (`manager.py:1018-1043` `_reply_language_directive`). The self-mod allowlist (`registry.py:37-124`) contains `profile.language` — a field nothing reads at runtime (`app_control.py:278` exposes it read-only). `set_config_value("brain.reply_language", …)` raises `AllowlistViolationError`.
2. **The pending-confirmation loop is never wired.** `build_self_mod_tools()` (`factory.py:347`) constructs an orphan `PendingMutationStore`; `SelfModFlowController` (`jarvis/voice/self_mod_flow.py`) is referenced nowhere in `jarvis/speech/`. An `ask`-tier mutation returns JSON the pipeline cannot action → dies silently.
3. **Force-spawn interception.** "wechsel auf / switch to" is an action verb → `_should_force_openclaw` may route the utterance to a contextless Jarvis-Agent worker instead of the tool → silent no-op.

---

## 2. Architecture — a thin authenticated facade

Build **one** Bearer-authenticated router mounted at `/api/control/*` that **delegates** to the already-production-ready layers; it reimplements nothing.

```
/api/control/*  (NEW, thin)  ── require_control_key dependency + uniform envelope + audit
   ├── config read/write ──▶ AtomicConfigWriter.mutate()  (11-step atomic pipeline)
   ├── providers         ──▶ jarvis.brain.app_control.apply_provider_switch()  (live + 3-layer persist)
   ├── secrets / keys    ──▶ cfg.set_secret/get_secret/delete_secret + ALLOWED_SECRET_KEYS guard
   ├── language verb     ──▶ writer (persist) + BrainManager.set_reply_language() (live)
   └── read-through      ──▶ existing missions/outputs/contacts/telephony routes (same Bearer guard)
```

"Everything the user can do" is achieved by **composition** — a generic `GET/PUT /api/control/config` over the allowlist + thin verbs for providers/secrets/language — not a giant new endpoint set.

### Three gap-closers shipped alongside the facade

- **(A) Allowlist fix.** Add `brain.reply_language` (`risk_tier=safe`, `needs_restart=False`), `stt.language`, `tts.language_code` to `SelfModRegistry.ALLOWED` with a five-layer parity test. SAFE → auto-applies through the writer with no confirmation round-trip (trivially reversible; the user explicitly wants instant switch). **Deviation from the original plan:** `profile.language` is left in place as a **legacy no-op** (it changes nothing at runtime) rather than aliased into `brain.reply_language` — the alias would have broken the existing `self_mod_flow` / `echo_confirmation` tests that use `profile.language` as their sample spec, for no real gain (the router/skill target the canonical `brain.reply_language`, and the old `profile.language` path was ASK-tier-broken anyway). `brain.reply_language` is canonical.

  **Also (completing B in practice):** `build_self_mod_tools()` in `factory.py` is now called with `writer_kwargs={"bus": bus}` so the VOICE path's writer dispatches `ConfigReloaded` too — without this the voice "switch to English" wrote to disk but stayed dormant until restart (the exact reported symptom).
- **(B) Hot-reload subscriber.** A new `ConfigReloaded` subscriber on the live `BrainManager` calls `set_reply_language()` when `brain.reply_language` changed → next turn is in the new language, **no restart**. This is the missing hot-reload subscriber the codebase lacks.
- **(C) Voice confirmation loop** (lower priority, only for remaining ASK-tier settings). Share **one** `PendingMutationStore` between `build_self_mod_tools(pending_store=…)` and a `SelfModFlowController` instantiated in the speech pipeline; a `PendingMutation` tool-result enters CONFIRMING and the next "ja/nein" turn drives `confirm/reject`. With language demoted to SAFE, the headline command works **before** (C) lands.

---

## 3. Control API surface

- `GET  /api/control/auth/probe` — 200 if Bearer valid, 401 otherwise (never returns the key).
- `GET  /api/control/allowlist` — machine-readable `SelfModRegistry.list_all()` (path, risk_tier, needs_restart, description) for agent discovery.
- `GET  /api/control/config?path=…` — `{path, value, in_allowlist, risk_tier, needs_restart}`; forbidden paths → 403 + redacted.
- `PUT  /api/control/config {path, value, reason}` — through `AtomicConfigWriter.mutate()`. SAFE → `applied:true`; ASK → `pending_id` + proposal text. Envelope `{ok, applied, persisted, requires_restart, backup_path, audit_id}`.
- `POST /api/control/config/confirm {pending_id}` / `POST /api/control/config/reject {pending_id}` — drives the shared `PendingMutationStore`; 410 if expired.
- `PUT  /api/control/language {reply_language, sync_tts?, sync_stt?}` — convenience verb; sets live + persists; optional TTS/STT sync to close the 3-source language drift.
- `GET  /api/control/providers` + `PUT /api/control/providers/{tier} {provider}` — delegate to `app_control`.
- `GET  /api/control/secrets` (masked previews) + `PUT/DELETE /api/control/secrets/{key}` — `ALLOWED_SECRET_KEYS` guard.
- `GET  /api/control/api-key` (loopback-or-valid-Bearer) + `POST /api/control/api-key/rotate {confirm}` — the only endpoints that may return the key in clear.

---

## 4. Auth

A single FastAPI dependency `require_control_key(request)` on the `/api/control/*` router (**not** global middleware — that would break the same-origin desktop UI and the missions/auth chicken-and-egg).

- Existing `/api/settings/*`, `/api/provider/*` stay open under the loopback-trust model they already assume → **zero UI regression**.
- `/api/control/*` requires `Authorization: Bearer <key>`. Validation = `secrets.compare_digest`. Never log the header (mask to last 4). On mismatch → 401, generic body.
- **Cloud-first:** the key is the boundary, not the bind address. Desktop stays `127.0.0.1`; VPS may bind `0.0.0.0` **only when a key exists** (fail-closed boot assertion). CORS unchanged (Bearer header bypasses the credentialed-cookie concern).
- Whitelist (key-free): `/api/health`, `/api/missions/auth/token`, static frontend. `GET /api/control/api-key` is guarded but additionally permits a same-origin loopback request so the Settings panel renders before the user has the key.
- **Subprocess safety:** never export the key into `os.environ` (it would leak via `/proc/<pid>/environ` to spawned workers); read from keyring/file on demand.

---

## 5. API key lifecycle

- **Generation:** `"jctl_" + secrets.token_urlsafe(32)` (256-bit, greppable prefix à la `ghp_`).
- **Storage (cross-platform, mandatory headless fallback):** primary `cfg.set_secret("jarvis_control_api_key", key)` (Credential Manager / Keychain / Secret Service under `KEYRING_SERVICE="personal-jarvis"`). **Check the return value** — `set_secret` silently returns False on headless Linux. Fallback: `data/.control_api_key`, `0600` on POSIX / per-user NTFS ACL on Windows. Read order: keyring → file → `JARVIS_CONTROL_API_KEY` env. Never assume keyring persisted. Never in `jarvis.toml` / `config-soll.json` / committed `.env` (AP-12).
- **Bootstrap:** generate-once **before** the FastAPI app is created. Idempotent — reuse an existing key, never silently regenerate (would lock out cached agents). Wizard shows "stored ✓" (mirrors `jarvis_admin_hmac`, `wizard.py:187-194`). Headless boot prints once to stdout.
- **Copy/display:** `GET /api/control/api-key` → full key to the same-origin panel; `robustCopy()` (`clipboard.ts:24`, WebView2-safe). Logs/lists show only `jctl_…last4`.
- **Rotation:** `POST /api/control/api-key/rotate {confirm:true}` requires the current Bearer (or same-origin loopback + optional admin_password_hash). New → persist → old invalidated by overwrite (single-key model). Emits `ControlApiKeyRotated`. No TTL in v1.

---

## 6. Settings panel

`JarvisApiGroup.tsx` in `frontend/src/views/settings/` (AppSettingsGroup 21-line pattern), registered in `SettingsView.tsx` after `AppSettingsGroup`. `Key` lucide icon; masked field `jctl_••••…last4` with Show/Hide; Copy button → `robustCopy` + success toast; Regenerate button → confirm dialog → rotate endpoint (disabled while saving). Caption explains local-agent usage with a copy-paste header example. New `useJarvisApi.ts` hook (cloned from `useAutostart.ts`) listens for a `jarvis:control-key-rotated` window event. i18n keys in `en/de/es` (English source). `JarvisApiGroup.test.tsx` (vitest) mocks fetch + clipboard; never asserts the literal key in a snapshot.

---

## 7. Default skill

One new built-in skill `control-api` (`jarvis/skills/builtin/control-api/SKILL.md`), added to `BUILTIN_SKILL_NAMES` (`builtin/__init__.py`).

- `state: validated` — **AP-15 critical**: NOT `active` (would bypass review) and NOT left `draft` (would never trigger). Loader maps None→VALIDATED, which `list_active()` includes.
- `intent_verbs: [switch, change, set, wechsel, stell, ändere]`, `intent_objects: [language, sprache, provider, brain, voice, stimme, theme, setting, einstellung]` — domain-agnostic verbs + config nouns, disjoint from the plugin-paired domain nouns (no capability collision).
- Triggers: **anchored narrow** regex, `language: [de, en]`. Never broad `^(change|switch)`.
- `requires_tools: []` — skill bodies are Supervisor markdown, cannot make HTTP calls. The skill is **documentation**: it teaches the router to call `set_config_value` (countering force-spawn via `run_skill` preference, `router.py:112-118`) **and** gives Codex CLI / Claude Code a copy-paste `curl` recipe + a pointer to `GET /api/control/allowlist`.
- Tests: parity (in `BUILTIN_SKILL_NAMES`), bootstrap-drift (no drift on second boot), AP-15 (loads VALIDATED not ACTIVE).

---

## 8. Cross-platform notes

- **THE cloud-first blocker:** config path is hardcoded `PROJECT_ROOT / "jarvis.toml"` (`config.py:46`) with no env override. Add a `JARVIS_CONFIG` env var honored by `load_config` + `AtomicConfigWriter`.
- Keyring fails silently on headless Linux → file fallback wired from day one.
- 3-layer persist's ENV layer is `winreg` (Windows-only) → no-op elsewhere; add `brain.reply_language` to the drift-soll layer so the Windows drift-guard does not revert a switch within 5 min (BUG-010).
- Desktop-only settings (overlay/bar/ducking) return `{applied:false, requires_ui:true}` on a VPS (reuse the 503/graceful pattern), never crash.
- Control API is pure FastAPI + stdlib `secrets` + keyring (all in base) → boots on the slim Linux image unchanged. No new GUI dep.

---

## 9. Build sequence (TDD)

1. Cloud-first unblock: `JARVIS_CONFIG` env override in `load_config` + `AtomicConfigWriter`.
2. Allowlist + five-layer parity: add `brain.reply_language` (SAFE), `stt.language`, `tts.language_code`.
3. Hot-reload subscriber: `ConfigReloaded` → `BrainManager.set_reply_language` (next turn, no restart).
4. Control-API key lifecycle: gen + keyring/file fallback + idempotent bootstrap + wizard slot.
5. Auth dependency: `require_control_key` + fail-closed non-loopback bind assertion.
6. `control_routes.py` thin facade: allowlist/config/confirm/reject/language/providers/secrets/api-key.
7. Settings UI: `useJarvisApi.ts` + `JarvisApiGroup.tsx` + register + i18n + test.
8. Voice confirmation loop (ASK-tier): shared `PendingMutationStore` + `SelfModFlowController` in pipeline.
9. Default skill `control-api` + `BUILTIN_SKILL_NAMES` + parity/AP-15 tests.
10. Docs + headless smoke proof (Bearer-only on `launcher --headless`).

### Reuse map (do not reinvent)

`AtomicConfigWriter.mutate`, `SelfModRegistry.ALLOWED/get_spec/require_spec/is_forbidden`, `PendingMutationStore`, `SelfModAudit`, `app_control.{apply_provider_switch,build_settings_snapshot,is_credential_present,_mask_secret}`, `config_writer.set_*`, `BrainManager.set_reply_language`, `cfg.set_secret/get_secret/delete_secret` + `KEYRING_SERVICE` + `ALLOWED_SECRET_KEYS`, `missions_auth` token primitive, `robustCopy`, FastAPI `Depends` + `app.state` + graceful-503, cursor pagination, `BUILTIN_SKILL_NAMES` + `ensure_user_skills_dir` bootstrap.

### Anti-patterns to respect

AP-2/AP-12 (never accept/commit secrets), AP-3 (only `ToolExecutor.execute`), AP-7/AP-13/AP-14 (atomic config writes, sync reload-test, backup dir outside watchdog), AP-15 (skills draft-only / shipped-validated), AP-16 (`ConfigDict(extra="allow")`), ROUTER_TOOLS stays a frozenset, five-layer enum anti-drift for any new wire vocab.

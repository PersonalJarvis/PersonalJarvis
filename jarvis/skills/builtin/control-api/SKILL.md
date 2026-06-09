---
schema_version: "1"
name: control-api
version: "1.0.0"
description: |
  Change Jarvis's own configuration through the local Control API instead of
  clicking the desktop UI or driving Computer-Use. Lets a local coding agent
  (Codex CLI, Claude Code) read and change settings, switch brain/STT/TTS
  providers, switch the reply language, and rotate API keys — all over HTTP on
  the loopback interface.
category: meta
tags: [self-config, control-api, agent, settings]
author: builtin
license: MIT
requires_tools: []
risk_policy:
  default_tier: ask
---

# Jarvis Control API

Jarvis exposes a local HTTP API at **`/api/control/*`** so an agent can do
anything the user can do in the desktop app — without Computer-Use. This skill
is the recipe a coding agent (Codex CLI, Claude Code) follows when the user
asks it to change a Jarvis setting on this machine.

## 1. Get the key and base URL

Every install generates its own per-user key (prefix `jctl_`).

- **Base URL:** `http://127.0.0.1:<port>` where `<port>` is `[ui].admin_api_port`
  in `jarvis.toml` (the same port the desktop app/browser UI uses).
- **Key:** copy it from the desktop app → **Settings → Jarvis API**, or read it
  on the loopback interface:

```bash
curl -s http://127.0.0.1:<port>/api/control/api-key
# => {"key":"jctl_...","masked":"jctl_…1234"}
```

Send the key as a Bearer header on every other call:

```
Authorization: Bearer jctl_...
```

Verify it works:

```bash
curl -s -H "Authorization: Bearer $JARVIS_KEY" \
  http://127.0.0.1:<port>/api/control/auth/probe        # => {"ok":true}
```

## 2. Discover what you can change

```bash
curl -s -H "Authorization: Bearer $JARVIS_KEY" \
  http://127.0.0.1:<port>/api/control/allowlist
```

Returns every mutable setting with its `path`, `risk_tier` (`safe`/`ask`),
`needs_restart`, and a description. Validate a `path` against this before you
write — a path that is not listed (or is a protected secret) is refused.

## 3. Read and write config

```bash
# read
curl -s -H "Authorization: Bearer $JARVIS_KEY" \
  "http://127.0.0.1:<port>/api/control/config?path=brain.reply_language"

# write
curl -s -X PUT -H "Authorization: Bearer $JARVIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"path":"brain.reply_language","value":"en","reason":"user asked"}' \
  http://127.0.0.1:<port>/api/control/config
```

The response envelope: `{ok, applied, needs_confirmation, pending_id,
requires_restart, backup_path, risk_tier, ...}`.

- **`safe`** settings (e.g. `brain.reply_language`, `tts.speed`, `ui.theme`)
  apply immediately: `applied=true`. Language hot-reloads into the next turn —
  no restart.
- **`ask`** settings (e.g. `brain.primary`, `tts.provider`) return
  `needs_confirmation=true` and a `pending_id`. Confirm it:

```bash
curl -s -X POST -H "Authorization: Bearer $JARVIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"pending_id":"<id>"}' \
  http://127.0.0.1:<port>/api/control/config/confirm
# or .../config/reject to cancel
```

`requires_restart=true` means the change is persisted but only takes effect
after the user restarts Jarvis (e.g. STT provider) — say so honestly.

## 4. Convenience: switch language

There are TWO language settings: `ui.language` (the INTERFACE the user sees —
every label/button) and `brain.reply_language` (what Jarvis SPEAKS/writes;
`auto` mirrors the user's input). When the user says "change the/your language
to X", switch BOTH — the `/language` verb does that for a concrete code:

```bash
curl -s -X PUT -H "Authorization: Bearer $JARVIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reply_language":"en"}' \
  http://127.0.0.1:<port>/api/control/language        # auto | de | en | es
# concrete code (de/en/es) -> sets reply_language AND ui.language; the open UI
# switches live. "auto" -> reply only.
```

Or set just one explicitly via /config (`ui.language` = en/de/es,
`brain.reply_language` = auto/de/en/es).

## 5. Providers and secrets

```bash
# snapshot of all providers + settings
curl -s -H "Authorization: Bearer $JARVIS_KEY" \
  http://127.0.0.1:<port>/api/control/providers

# switch a tier (brain | tts | stt | subagent)
curl -s -X PUT -H "Authorization: Bearer $JARVIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"gemini"}' \
  http://127.0.0.1:<port>/api/control/providers/brain

# list secret slots (masked) / set / delete a provider key
curl -s -H "Authorization: Bearer $JARVIS_KEY" \
  http://127.0.0.1:<port>/api/control/secrets
curl -s -X PUT -H "Authorization: Bearer $JARVIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"value":"sk-..."}' \
  http://127.0.0.1:<port>/api/control/secrets/openai_api_key
```

## Rules

- **Local only.** The API binds loopback on desktop; the Bearer key is the
  boundary. Never paste the key into chat, logs, commits, or a remote service.
- Only paths in `/api/control/allowlist` are writable. Secrets are write/rotate
  only — they are never returned in clear by the config endpoints.
- Prefer this API over Computer-Use or editing `jarvis.toml` by hand: it is
  atomic (backup + validate + rollback) and audited.

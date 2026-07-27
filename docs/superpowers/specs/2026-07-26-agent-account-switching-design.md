# Agent accounts — switching between several coding-CLI subscriptions

**Date:** 2026-07-26
**Status:** accepted, implementing
**Surface:** Settings (API-Keys view) + Agentic IDE

---

## 1. The problem

A user can hold more than one subscription for the same coding CLI — two Claude
Max seats, two ChatGPT/Codex plans. Today only one of them is reachable at a
time: the CLI keeps exactly one login, and reaching the other one means a full
`logout` / `login` round trip. That is slow, it throws away the previous
login, and it makes running both plans **at once** impossible — which is the
whole reason someone holds two.

## 2. The mechanism this rests on

Both CLIs resolve their entire identity — credentials, account file, and
conversation history — from one directory, and both expose the official
environment override that moves it:

| CLI | Override | Default | Contents |
|---|---|---|---|
| Claude Code | `CLAUDE_CONFIG_DIR` | `~/.claude` | `.credentials.json`, `.claude.json`, `projects/` |
| Codex | `CODEX_HOME` | `~/.codex` | `auth.json`, `config.toml`, `sessions/` |

So **one account is one directory**, and switching is "which directory does the
next spawn point at". Both logins stay valid side by side; nothing is copied,
nothing is logged out.

Copying tokens between directories is explicitly **not** the design. Claude Code
refreshes its OAuth access token in place, so a copy goes stale on its own — the
2026-07-06 and 2026-07-10 incidents (`jarvis/claude_credentials.py`) are exactly
that failure. Separate directories each refresh themselves.

## 3. What already exists

- `jarvis/terminal/backend.py` — all three PTY backends already accept `env=`.
- `jarvis/missions/isolation/env.py` — already pins `CODEX_HOME` +
  `CLAUDE_CONFIG_DIR` per mission worker.
- `jarvis/claude_credentials.py` — already scans several config dirs and lets
  the freshest live login win.
- `jarvis/workspace/trust.py` — already honours `CODEX_HOME`.

## 4. What is missing

1. `PtyManager.spawn()` takes no `env` — the chain from the backend seam up to
   the Agentic IDE is broken at exactly one link.
2. Nothing owns the list of accounts, or which one is active.
3. Resume discovery (`jarvis/agentic_ide/agent_sessions.py`) reads the config
   dir from `os.environ`, so a pane running account B would look for its
   transcript in account A's directory and silently start fresh.
4. `launch_interactive_terminal()` cannot carry an environment overlay, so a
   sign-in cannot be pointed at a specific account directory.

## 5. Design

### 5.1 `jarvis/agent_accounts.py` — the one owner

A small, stdlib-only module (the `recents.py` / `resume_store.py` pattern:
one JSON file under the per-user data dir, atomic writes, defensive reads).

```
AgentAccount(id, platform, label, config_dir, builtin)
```

- **`platform`** is `"claude"` or `"codex"`.
- **The built-in account is synthetic and always present**: it represents the
  CLI's own platform default (`~/.claude` / `~/.codex`), is never written to the
  store, and can neither be renamed away nor deleted. It is what a fresh install
  has, so the feature costs an untouched user nothing.
- **Added accounts** get a directory under
  `<user data>/agent-accounts/<platform>/<slug>/`.
- Store: `<user data>/agent_accounts.json`, holding the added accounts plus the
  active id per platform. An unreadable / newer-version file degrades to "only
  the built-in account exists" rather than breaking the view.

Public surface:

| Function | Purpose |
|---|---|
| `list_accounts(platform)` | built-in first, then added, stable order |
| `active_account(platform)` | falls back to built-in when the pinned id is gone |
| `set_active(platform, account_id)` | the switch |
| `create_account(platform, label)` | mint id + directory |
| `delete_account(account_id, *, remove_files)` | forget; optionally erase |
| `resolve(account_id)` | id → account, `None` when unknown |
| `spawn_env(platform, account_id)` | full child environment for a spawn |
| `describe(account)` | display-safe snapshot: connected, email, tier |

**`spawn_env` semantics (load-bearing).** It returns `os.environ` **plus** the
override, never a replacement — a replaced environment loses `PATH` and the
agent binary stops resolving. For the **built-in** account it *removes* any
inherited `CLAUDE_CONFIG_DIR` / `CODEX_HOME` instead of setting one, so the CLI
uses its true platform default — which on macOS is the Keychain, not a file.

**`describe` reuses the existing parsers** rather than growing a third copy:
`jarvis.claude_credentials` for the Claude bearer + `.claude.json` identity,
`jarvis.codex_auth` helpers for `auth.json`. It never returns a token.

### 5.2 Wiring the spawn

- `PtyManager.spawn(..., env: Mapping[str, str] | None = None)` → passed to
  `backend.spawn`. Default `None` keeps every existing caller identical.
- `Terminal` gains `account: str | None`. It is resolved to a concrete id **when
  the pane is created**, not when it spawns: flipping the global switch must not
  silently re-point a pane that is already on screen.
- `Session.attach()` builds `spawn_env(term.agent, term.account)`.
- `resume_store` persists `account` so a resumed workspace comes back on the
  same accounts.

### 5.3 Resume discovery follows the account

`has_conversation()` and `discover()` take an optional `home: Path | None`.
`None` keeps today's behaviour (`os.environ` → default). `session.py` passes the
pane's account directory. Without this the "continue where you left off" path is
wrong for every non-default account.

### 5.4 REST — `jarvis/ui/web/agent_accounts_routes.py`

Per the CLI-first contract (CLAUDE.md §5), which makes each of these a
`jarvis api agent-accounts <op>` command automatically:

| Route | Purpose |
|---|---|
| `GET /api/agent-accounts` | every account of both platforms + status |
| `POST /api/agent-accounts` | add one (mints the directory) |
| `PUT /api/agent-accounts/active` | switch the default for a platform |
| `POST /api/agent-accounts/{id}/login` | sign in *inside that account's dir* |
| `PATCH /api/agent-accounts/{id}` | rename |
| `DELETE /api/agent-accounts/{id}` | forget (`x-jarvis-dangerous`) |

`launch_interactive_terminal()` grows an `env` overlay parameter so the sign-in
lands in the right directory on all three OSes (Windows/Linux via `Popen(env=)`,
macOS via a `VAR=value` prefix in the AppleScript command, because Terminal.app
runs a shell string).

### 5.5 UI

- **Settings → API Keys**, inside `JarvisAgentSection`: an accounts block per
  platform under the existing connection card — the accounts, which is active,
  a radio-style switch, "Add account", rename, remove.
- **Agentic IDE**: the pane / batch creation form gets an account picker per
  agent, defaulting to the active one. This is what makes both subscriptions
  run *at the same time*.
- Strings go through i18n (`de` / `en` / `es`), English source.

### 5.6 Honesty rules

- A directory with no readable login shows **"not signed in"** with the sign-in
  button — never a green state it cannot back up.
- `describe()` never returns a token, only booleans + the display email/tier.
- **macOS caveat:** Claude Code stores its credentials in the Keychain there.
  Whether a second config directory yields a second Keychain entry is unproven
  on this hardware, so after a sign-in the account is **verified**: if the
  directory produced no readable login, the UI says so plainly instead of
  routing panes to an account that is silently the first one. Tracked in
  `docs/os-parity.md`.

## 6. Out of scope (deliberately)

- Automatic failover when a plan hits its rate limit. Real appeal, but it needs
  reliable limit detection from CLI output — a separate feature, and guessing
  wrong would move work onto the wrong plan silently.
- Mission workers (`build_worker_env`). They own a security-relevant isolated
  environment (ADR-0009 §3); folding accounts in there is its own change.
- Any account concept for API-key providers. This is about **subscription CLIs**.

## 7. Testing

- `tests/unit/test_agent_accounts.py` — store round-trip, built-in synthesis,
  active fallback when a pinned id vanishes, `spawn_env` overlay + built-in
  *removal*, corrupt/newer-version file degradation, no token in `describe`.
- `tests/unit/agentic_ide/test_account_spawn.py` — a pane spawns with its
  account's directory; the built-in pane spawns without an override; switching
  the global default does not re-point an existing pane.
- `tests/unit/test_agent_accounts_routes.py` — route contract + danger metadata.
- Frontend: the accounts panel renders, switches, and shows an unsigned account
  honestly.

---

## 8. Follow-up 2026-07-27 — the redirect cost the user their whole CLI

**Reported symptom.** An Agentic-IDE pane "is not a real Claude Code session":
in a normal terminal the maintainer sees 93 skills, thirteen plugins and their
global instructions; in a pane, only the CLI's built-in skills and the project's
own `.claude/`. Same machine, same binary, same folder.

**Cause.** §2's mechanism is sound about the login and wrong about everything
else in that directory. `CLAUDE_CONFIG_DIR` / `CODEX_HOME` do not move a
credential — they move the CLI's **entire user level**: `skills/`, `agents/`,
`commands/`, `hooks/`, `output-styles/`, `plugins/` (marketplaces = connectors),
the user-level `CLAUDE.md` / `AGENTS.md`, and `settings.json` /
`config.toml`. An account directory holds none of that, so every pane on an
added subscription ran a quieter, emptier version of the CLI the user installed.
`inherit_default_mode` had already patched the narrowest instance of this (the
operating mode) without recognising the class.

The Agentic IDE is the ONLY spawn path that passes `env=` to the PTY manager,
which is exactly why the same CLI behaves correctly in every other terminal
Jarvis opens.

**Resolution — `jarvis/agent_config_parity.py`.** Before every pane spawn, a
redirected config dir is given the user's own setup:

- **Directories are linked** — symlink, or a Windows **junction** where symlink
  creation needs a privilege the app does not have (measured: `WinError 1314` on
  the maintainer's non-elevated install), or a copy where neither works. A link
  means a skill installed today is in every account tomorrow.
- **Files are mirrored, then merged** — a file the account does not have is
  copied and tracked by digest, so a later change to the user's file follows
  through. A file the account has written to itself (a pane that changed its
  theme) is never overwritten: the keys it is **missing** are filled in
  recursively instead. Without that last step the fix was half a fix — the
  plugin trees arrived while `enabledPlugins` stayed absent, so nothing loaded.
- **`plugins/` is shared whole or not at all.** Which plugins are active lives
  in state files beside the marketplaces they name; the account's half-written
  copy is moved to `plugins.jarvis-superseded` and replaced by the user's.
- **Identity is untouched by construction** — an explicit allowlist, so
  `.credentials.json`, `auth.json`, `.claude.json`, `projects/`, `sessions/` and
  `history.jsonl` are never shared. Claude Code's user-scope MCP servers are
  merged out of `.claude.json` one key at a time, because that document holds
  the login too.
- **Never raises.** Every failure degrades to one unshared entry plus a log
  line, and the report names it — a pane must open either way.

**Second defect found on the way.** `jarvis/workspace/trust.py` seeded folder
trust only into the machine's default config, so a pane on an added account met
the "do you trust this directory?" dialog — which voice and the prompt bar
cannot answer. `ensure_trusted` now takes `config_dirs`, and the registry
pre-trusts the directory each pane will actually run from (once per folder and
account).

**Scope.** Agentic-IDE panes only. Mission workers keep their deliberately
isolated, hook-free environment (§6), and a pane on the built-in login still
spawns with a plain inherited environment — byte for byte what it always was.

**Tests.** `tests/unit/test_agent_config_parity.py` (the setup arrives and keeps
arriving; identity is never shared; an account's own value survives while its
missing keys are filled; symlink host, junction host, copy-only host, and a host
that can do none of it), plus `tests/unit/agentic_ide/test_account_spawn.py`
(the pane spawn provisions and pre-trusts) and `tests/unit/workspace/
test_trust.py` (per-account trust, de-duplicated).

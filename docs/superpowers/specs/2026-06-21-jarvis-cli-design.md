# Jarvis CLI — Design Spec

**Status:** Draft for review
**Date:** 2026-06-21
**Author:** brainstorming session (Opus 4.8)
**Supersedes / consolidates:** the partial `jarvis/cli_ctl/` package (`jarvisctl` / `jctl`)

---

## 1. Summary

Build a first-class **Jarvis CLI**: a command-line interface that drives Jarvis
itself, exposing every action a user can perform in the WebUI as a CLI command.
It is usable by the maintainer, by Jarvis's own Jarvis-Agents, and — the primary
driver — by **external coding agents (Claude Code, Codex)** that need to control
a running Jarvis programmatically. This is the same relationship `gcloud` / `aws`
/ `az` have with their cloud platforms: a CLI that surfaces the full capability
plane of a service.

The CLI is a **thin HTTP client over the existing REST API**, with two layers:

1. **Dynamic layer (completeness):** every REST endpoint is auto-surfaced from
   the server's OpenAPI specification. New endpoints become commands with no
   extra work.
2. **Curated layer (ergonomics):** hand-written, well-shaped commands for the
   highest-value domains, built wave by wave.

Because the CLI only ever calls the same routes the WebUI calls, it **inherits
all of Jarvis's safety machinery** (risk-tier evaluation, the atomic
config-write pipeline, event-bus emission, audit) for free — it cannot bypass a
guardrail it never re-implements. This is the lowest-risk possible construction
and is the central reason for choosing it.

A **project skill plus a hard CI/hook coverage gate** guarantee that every new
feature a coding agent ships is reachable from the CLI: the OpenAPI layer makes
the command appear automatically; the gate fails the push if a newly added route
lacks the metadata needed for a clean command; the skill scaffolds a curated
command + test + docs entry for high-value routes at "definition of done".

---

## 2. Goals & non-goals

### Goals

- One CLI, branded `jarvis`, that mirrors the WebUI action surface (~20 domains,
  ~350 endpoints).
- Drive a **running** Jarvis instance (live state), not a fresh boot per command.
- Zero-config for a local user/agent (auto-discover the running server's port +
  control key); explicit, documented setup for remote/VPS.
- Safe by default for consequential actions (mutations require confirmation or an
  explicit `--yes`; secrets never via argv).
- Self-documenting (`--help` everywhere) and backed by a complete, drift-free
  docs section.
- A mechanism so that "coding agent ships a feature → a CLI command exists for it"
  is true by construction and enforced.
- Cross-platform: boots and runs on a headless `python:3.11-slim` Linux VPS, on
  macOS, and on Windows (cloud-first doctrine).
- Nothing that exists today breaks — especially the `jarvis` launcher and
  `run.bat`.

### Non-goals (this iteration)

- Re-implementing action logic client-side. The CLI is a transport over REST; it
  never talks to `BrainManager` / `MissionManager` / `AtomicConfigWriter`
  directly. (One narrow exception: a possible `jarvis ask` in-process one-shot,
  deferred — see §11.)
- A new server-side auth model. We use the existing control-key (`jctl_…`
  Bearer) scheme. Extending the gate to currently same-origin routes for remote
  driving is a documented future option, not part of v1 (§7.3).
- Streaming-first UX for every WebSocket. We start with request/response REST;
  selected live streams (mission progress) are a later wave.
- Alternative output formats beyond JSON + human/table (`--csv`/`--yaml` are
  YAGNI for now).

---

## 3. Current state (evidence-based)

The `jarvis/cli_ctl/` package already exists and is substantially complete. This
spec **consolidates and completes** it rather than building from scratch.

**Already working:**

- Console scripts `jarvisctl` and `jctl` (`pyproject.toml [project.scripts]`).
- Cross-platform config/cache dirs via `platformdirs` (`paths.py`).
- Profile resolution: env → `~/.jarvisctl/config.json` → local control-key
  fallback via `jarvis.core.control_key.get_control_key()` (`config.py`). Result:
  a user on the same machine as a running Jarvis gets auth for free.
- `httpx`-based `JarvisClient` with Bearer auth + structured `ApiError`
  (`client.py`).
- Typer root with a global `--json` option; `version` and `refresh` commands
  (`__main__.py`).
- Curated command groups: `auth` (login/status/logout), `system`
  (restart/status), `tasks` (list/get/create/cancel/delete) under `commands/`.
- Dynamic layer: one Click group per OpenAPI tag, one command per operation,
  grafted under an `api` root group; path params → URL substitution, query
  params → typed `--options`, request body → `--json-body` (string or stdin)
  (`dynamic.py`).
- OpenAPI cache with 24h TTL, offline stale-fallback, completion-safe read
  (`openapi_cache.py`).
- Rich table / pretty-JSON rendering (`render.py`).
- A self-control catalog entry with risk tiers.
- A comprehensive test suite under `tests/unit/cli_ctl/` (13 files, all using
  fakes / `httpx.MockTransport`, no live server required).

**Gaps relative to this spec's goals:**

1. **Not integrated into the `jarvis` command.** It is a separate `jarvisctl`
   binary; the maintainer chose to extend `jarvis`.
2. **No session auto-discovery.** `config.py` does not read the single-instance
   session file (`%LOCALAPPDATA%\Jarvis\session.json` / platform equivalent) for
   the live port + token. Desktop works via the local control-key fallback;
   discovery would make the port (non-default) and the external-agent story
   robust.
3. **No non-interactive safety model.** Mutating commands (`tasks delete`,
   `system restart`, future `contacts call`, `config set`, `missions dispatch`)
   fire blindly — no `--yes`, no `--dry-run`, no confirmation.
4. **Curated coverage is 3 of ~20 domains.** The dynamic layer covers the rest
   functionally, but the high-value domains deserve ergonomic curated commands.
5. **Request bodies are an opaque `--json-body` blob.** No field hints / enum
   choices / `--help` detail for body fields on complex endpoints.
6. **No auto-generation skill and no coverage gate.** The "new feature → CLI
   command" guarantee is not yet enforced.
7. **Docs are minimal** (`docs/jarvisctl.md`) and not generated/drift-checked.
8. **Remote/VPS:** only `/api/control/*` and `/api/tasks/*` are Bearer-gated;
   other routes are loopback/same-origin, so full remote driving needs an SSH
   tunnel (acceptable, documented) until/unless server-side gating is extended.

---

## 4. Architecture

```
                    external Claude Code / Codex session
                    maintainer shell · Jarvis sub-agents
                                  │
                                  ▼
        ┌──────────────────────────────────────────────┐
        │  jarvis  (Typer app, dispatched from          │
        │          jarvis/__main__.py)                  │
        │                                               │
        │  curated commands        dynamic `api` group  │
        │  (missions, brain,       (every OpenAPI       │
        │   config, wiki, …)        operation)          │
        │                                               │
        │  safety gate (confirm / --yes / --dry-run)    │
        │  output renderer (--json / table)             │
        └───────────────┬───────────────────────────────┘
                        │  httpx + Bearer jctl_…
                        ▼
        ┌──────────────────────────────────────────────┐
        │  Running Jarvis FastAPI server                │
        │  127.0.0.1:47821 (configurable)               │
        │  → REST routes → ToolExecutor / AtomicConfig  │
        │    Writer / MissionManager / EventBus / audit │
        └──────────────────────────────────────────────┘
```

Discovery + auth resolution order (highest first):

1. Explicit flags `--url` / `--key`.
2. Env `JARVISCTL_BASE_URL` / `JARVISCTL_CONTROL_KEY`.
3. Saved profile `~/.jarvisctl/config.json` (from `jarvis auth login`).
4. **Single-instance session file** (new): live `port` + `token` of the running
   instance.
5. Local control-key fallback (`jarvis.core.control_key`) + default
   `127.0.0.1:47821`.

The CLI builds its command tree as: static curated commands (always present) +
the dynamic `api` group from the cached/fetched OpenAPI spec. Help and shell
completion read the cached spec, so they work offline once the CLI has been run
against a live server at least once.

### Module map (target)

The package stays at `jarvis/cli_ctl/` (already imported by the entry points and
tests). New/changed pieces:

| Module | Change |
|---|---|
| `jarvis/__main__.py` | Thin dispatch: bare/launcher invocations unchanged; control subcommands forwarded to the Typer app (§6). |
| `cli_ctl/config.py` | Add session-file auto-discovery to the resolution chain. |
| `cli_ctl/discovery.py` (new) | Cross-platform read of the single-instance session file (port/token/pid), with staleness check (PID alive). |
| `cli_ctl/safety.py` (new) | Mutation classification + confirm/`--yes`/`--dry-run` + secret-input helpers. |
| `cli_ctl/dynamic.py` | Apply the safety gate to mutating operations; optional body-field hinting from the schema. |
| `cli_ctl/commands/*.py` | New curated command modules per wave (missions, brain, config, wiki, sessions, …). |
| `cli_ctl/docgen.py` (new) | Generate the CLI reference doc from the Typer app + OpenAPI spec. |
| `scripts/ci/check_cli_coverage.py` (new) | The hard coverage gate. |
| `.claude/skills/generate-cli-command/` (new) | The auto-generation project skill. |

---

## 5. Command taxonomy

Curated groups (built wave by wave), mirroring the real routers. The dynamic
`api <tag> <op>` group always covers everything else and any future route.

```
jarvis
  ├─ auth        login | status | logout                 (exists)
  ├─ system      status | restart [--force] | health     (exists, extend)
  ├─ tasks       list | get | create | cancel | delete    (exists)
  ├─ missions    list | show | dispatch | cancel | rerun | kill | logs | watch
  ├─ brain       list | status | switch | models | test | deep-model
  ├─ config      get | set | audit | backup | restore | language | restart
  ├─ wiki        recall | page | tree | graph | backlinks | ingest
  ├─ sessions    list | show | export | resume | speak | delete
  ├─ chats       list | create | show | resume | delete | export
  ├─ skills      list | show | create | enable | disable | test | catalog | reload
  ├─ outputs     list | show | files | download | open
  ├─ board       summary | heatmap | records | achievements | bio
  ├─ workflows   list | show | create | edit | delete | run
  ├─ conductor   jobs | runs | webhooks
  ├─ contacts    list | show | add | edit | delete | call        (call = guarded)
  ├─ friends     list | add | show | edit | delete | message
  ├─ telephony   status | calls | outbound                       (outbound = guarded)
  ├─ clis        list | show | check | install | connect | usage
  ├─ mcps        list | enable | disable | check | import | delete
  ├─ marketplace plugins | search | connect
  ├─ docs        list | search | show | open
  ├─ frontier    pending | ack
  └─ api         <tag> <operation> …                      (dynamic, full coverage)
```

Curation is mechanical aliasing: a curated command resolves to a specific
endpoint with friendlier arguments, defaults, and output shaping. The
authoritative behavior always lives server-side.

---

## 6. Consolidation into `jarvis` (without breaking the launcher)

The root `jarvis` command today (argparse, `jarvis/__main__.py`) launches the
tray app (bare invocation), or handles `--wizard`, `serve`, `--check`,
`--plugins`, `--debug`, `--phase5-doctor`, `--orb-doctor`,
`--install-admin-helper`, `--reset-onboarding`. `run.bat` depends on it.

**Approach — thin dispatch, lowest risk.** In `jarvis/__main__.py:main()`, peek
at the first argument:

- If it is a **reserved control group/command** (the names in §5, plus `api`,
  `auth`, `version`, `refresh`) → forward `sys.argv[1:]` to the Typer app
  (`jarvis.cli_ctl.__main__:app`) and return its exit code.
- Otherwise → run the existing argparse launcher **unchanged** (bare `jarvis`,
  `--wizard`, `serve`, all current flags behave exactly as today).

A frozen `RESERVED_CONTROL_NAMES` set is the single source of truth; a unit test
asserts no launcher flag/command ever collides with a control name. `jarvisctl`
and `jctl` remain registered as aliases (back-compat; some scripts/tests use
them). This gives the maintainer one brand (`jarvis missions list`) while
preserving every existing entry path and `run.bat`.

Rejected alternative: rewriting the launcher as a Typer app and folding the
legacy flags in. Cleaner end-state, but it rewrites the boot dispatch — real risk
to the maintainer's daily launch flow for no functional gain. The dispatch
approach reaches the same UX with a fraction of the blast radius.

---

## 7. Auth, discovery & remote driving

### 7.1 Local (default, zero-config)

A coding agent or shell on the same machine as the running Jarvis resolves the
base URL + control key automatically (resolution chain in §4). New piece:
`discovery.py` reads the single-instance session file, validates the PID is alive,
and yields `{base_url, token}`. This makes a non-default `admin_api_port` and the
external-agent flow work without manual `auth login`.

### 7.2 Secrets handling (binding)

- The control key and any API keys are **never** accepted as positional argv or
  `--key value` in a way that lands in shell history when avoidable; `auth login`
  reads the key from a prompt or stdin (`--key -`) and stores it via the keyring
  (`paths`/profile), 0600 on POSIX.
- Commands that set provider secrets (`config`/provider key writes) read the
  secret from stdin/prompt and write to the Credential Manager — never to
  `jarvis.toml`, never echoed, never logged. This extends AP-2 / AP-12 to the
  CLI. (The CLI is a *safer* secret channel than voice — no STT transcript — but
  still defends against shell-history leaks.)

### 7.3 Remote / VPS

For driving a Jarvis on another host, the supported v1 path is an **SSH tunnel**
(`ssh -L 47821:127.0.0.1:47821 vps`), after which the remote instance is reached
as loopback and the full surface works with the control key. This is documented,
not a code change. A future option (out of scope here) is extending the
control-key gate to the currently same-origin mutating routes so the surface can
be driven directly over a bound, key-protected port — that touches the server and
gets its own spec.

---

## 8. Non-interactive safety model

The CLI adds defense-in-depth on top of the server-side guardrails, because an
external agent issuing mutations non-interactively is a new risk surface.

Classification (`safety.py`):

- **Read (GET):** safe, never prompts.
- **Mutating (POST/PUT/PATCH/DELETE):** require confirmation.
- **Dangerous (explicit denylist):** always require an explicit `--yes`; a plain
  interactive `[y/N]` prompt is not sufficient — e.g. `contacts call`,
  `telephony outbound`, `system restart`, `config set`, `missions dispatch`, any
  `DELETE`.

Behavior:

- Interactive TTY: mutating commands prompt `Proceed? [y/N]`; dangerous commands
  additionally print the exact request (method, path, body) and require `--yes`
  (the prompt alone does not authorize them).
- Non-interactive (piped / agent): mutating commands **fail closed** unless
  `--yes` / `-y` (or `JARVIS_CLI_ASSUME_YES=1`) is set. This forces an agent to
  be explicit about consequential actions.
- `--dry-run` on any command prints the resolved request (method, URL, body,
  whether auth is attached) and exits without sending. This is the agent's safe
  introspection path.

The model is method-based + a small explicit denylist — simple, predictable, no
dependency on per-route risk metadata (which REST routes don't carry; risk tiers
live at the brain-tool layer the routes sit behind).

---

## 9. Auto-generation: "new feature → a CLI command" (skill + hard gate)

Two mechanisms, layered:

### 9.1 Automatic by construction (the OpenAPI layer)

Every WebUI feature ships a REST route (or it isn't reachable from the UI). The
moment that route exists, `jarvis api <tag> <op>` exists. No per-feature codegen
is required for functional coverage. This is the foundation that makes
"complete" achievable across ~350 endpoints.

### 9.2 Hard coverage gate (the guarantee)

`scripts/ci/check_cli_coverage.py`, wired into `.githooks/pre-push` and a CI job
(mirroring the existing `language-policy` gate pattern). On a diff that adds REST
routes, it fails the push if a new route lacks the metadata needed for a clean
command:

- missing `operationId`, or
- missing a `summary`/`description`, or
- missing a router `tag`.

Optionally, a `must-curate` allowlist (high-traffic domains) can require a curated
command + test to exist for the route. An `i18n-allow`-style inline marker /
allowlist file provides escape hatches, matching repo convention. This is what
turns "the agents must somehow detect it themselves" into an enforced, hands-free
guarantee.

### 9.3 Scaffolding skill (the ergonomics)

`.claude/skills/generate-cli-command/SKILL.md` — a project skill a coding agent
runs at "definition of done", modeled on the public-release privacy gate:

1. Diff routes since `main` (`git diff main...HEAD` over `*_routes.py`).
2. For each new route, verify OpenAPI metadata (so the gate will pass).
3. For high-value routes, scaffold a curated command in the right
   `commands/<domain>.py`, a test in `tests/unit/cli_ctl/`, and a docs entry —
   from a template that encodes the §8 safety conventions.
4. Regenerate the CLI reference doc (§10).
5. Print next steps (review, run tests, commit).

The skill is the "how"; the gate is the "must". Together they satisfy the
maintainer's chosen "skill + hard gate" option.

---

## 10. Documentation

- `docs/jarvis-cli.md` — hand-written guide: concepts, the gcloud/aws analogy,
  install, auth & discovery, the safety model, scripting/JSON output, and a
  dedicated **"Driving Jarvis from a Claude Code / Codex session"** section
  (PATH, token auto-discovery, `--json` for machine parsing, `--dry-run`,
  `--yes`).
- `docs/jarvis-cli-reference.md` — **generated** from the Typer app + OpenAPI
  spec via `docgen.py`; lists every curated command and the dynamic surface. The
  coverage gate / skill regenerate it so it never drifts.
- A pointer in `CLAUDE.md` / `AGENTS.md` and optionally a
  `.claude/skills/drive-jarvis-cli/` skill so coding agents discover the command
  surface and the conventions.

---

## 11. Cross-platform & dependencies

- The CLI is pure-Python: `typer`, `httpx`, `platformdirs`, `rich`. These must be
  **base install** deps (not `[desktop]`), so the CLI boots on
  `python:3.11-slim`. Verify and, if needed, move them into base in
  `pyproject.toml`. (Action tracked in the plan.)
- UTF-8 stdout reconfigure on Windows; `pathlib` only; no Windows-only imports on
  the CLI import path; `NO_WINDOW_CREATIONFLAGS` on any subprocess (none expected
  in the thin client).
- Session-file discovery uses platform-correct locations (`platformdirs` /
  documented per-OS paths) and degrades to the control-key fallback when absent.
- Deferred in-process `jarvis ask` one-shot (no running server): only if a real
  need appears; would reuse `build_default_brain`. Kept as a non-goal to avoid a
  second heavy boot path (YAGNI).

---

## 12. Testing

- Extend the existing `tests/unit/cli_ctl/` suite (fakes + `httpx.MockTransport`,
  no live server) for every new module and curated command.
- New parity/guard tests: `RESERVED_CONTROL_NAMES` vs launcher flags (no
  collision); dispatch routes control subcommands to Typer and launcher flags to
  argparse; session-discovery staleness; safety gate (read passes, mutation
  fails-closed non-interactively, `--yes` proceeds, `--dry-run` sends nothing,
  dangerous denylist enforced); secret never in argv/logs.
- A `check_cli_coverage.py` self-test with a fixture OpenAPI spec (route with
  good metadata passes; route missing operationId/summary/tag fails).
- Existing `jarvisctl`/`jctl` behavior stays green throughout (back-compat
  guard).

---

## 13. Wave-by-wave rollout (the loop)

The maintainer chose "in waves, until complete." Each wave is TDD'd, verified by
the test-runner, and must not break existing behavior. The dynamic layer
guarantees nothing is *unreachable* between waves; curation adds ergonomics.

- **Wave 0 — Foundation & consolidation.** Dispatch `jarvis` → Typer
  (§6); session auto-discovery (§7.1); safety model (§8); confirm base-deps
  cross-platform (§11); docgen skeleton. Back-compat guards green.
- **Wave 1 — Highest-value curated domains + the coverage gate.** missions,
  brain, config, wiki, sessions. Land `check_cli_coverage.py` + pre-push/CI
  wiring early so all subsequent routes are enforced.
- **Wave 2 — Next domains.** skills, outputs, board, workflows, conductor,
  chats; harden `tasks`. Contacts/telephony with the guarded `call`/`outbound`.
- **Wave 3 — Remaining domains.** marketplace/plugins, mcps/tools,
  friends/socials, docs, frontier, preview, setup/onboarding, channels,
  federation, runs/review.
- **Wave 4 — Auto-generation skill + complete docs.** `generate-cli-command`
  skill (templates now that curated patterns are stable); `docs/jarvis-cli.md` +
  generated reference; CLAUDE.md/AGENTS.md pointer; `drive-jarvis-cli` skill.
- **Wave 5 — External-agent enablement & remote.** Claude Code "drive Jarvis"
  end-to-end doc + verification; remote/VPS SSH-tunnel path; optional live
  streams (`missions watch`).

### Definition of done (loop exit criteria)

1. `jarvis <group> <command>` works for every domain in §5; everything else is
   reachable via `jarvis api …`.
2. Coverage gate is wired and green; a new route without clean metadata fails the
   push.
3. The auto-generation skill scaffolds a working command + test + docs entry.
4. `docs/jarvis-cli.md` + generated reference are complete and in sync; a Claude
   Code session can drive a running Jarvis following the doc.
5. The full test suite (existing + new) is green; `jarvisctl`/`jctl` and the
   `jarvis` launcher + `run.bat` are unaffected.
6. Cross-platform: imports and `--help` succeed on a `python:3.11-slim` container.

---

## 14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Breaking the `jarvis` launcher / `run.bat` | Thin dispatch preserves all existing paths; reserved-name parity test; bare/`--wizard`/`serve` untouched. |
| Agent fires a destructive mutation non-interactively | Fail-closed mutations + dangerous denylist + `--dry-run` (§8). |
| Ugly/unusable dynamic commands from poor OpenAPI metadata | Coverage gate enforces operationId/summary/tag on new routes. |
| Help broken when server is down | OpenAPI cache + offline fallback; curated commands are static and always present. |
| Remote driving expectations | Documented SSH-tunnel path; server-side gating explicitly deferred. |
| Dependency creep into base install | Audit `typer`/`httpx`/`platformdirs`/`rich` placement; keep CLI import path desktop-free. |
| Shared working tree / parallel sessions | Hunk-isolated commits; touch only CLI-owned files; check active missions before any app restart. |

---

## 15. Open questions for review

- Curated domain priority within Wave 1 — is missions/brain/config/wiki/sessions
  the right "first five" for an external coding agent, or should provider/config
  lead?
- Should the dangerous-denylist (§8) be hardcoded or config-driven from the
  start? (Proposed: hardcoded constant first; config later if needed.)
- Live streams (`missions watch` over WebSocket): Wave 5 as proposed, or pulled
  earlier?

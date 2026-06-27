---
name: generate-cli-command
description: >-
  Use after building a feature that adds or changes a REST route in
  jarvis/ui/web/*_routes.py, as the "definition of done" before committing.
  Ensures the new endpoint is reachable from the Jarvis CLI: confirms the router
  is mounted, checks its OpenAPI metadata is clean, and (for high-value routes)
  scaffolds a curated `jarvis <group> <command>` plus a test and a docs entry.
  Trigger phrases: "generate a CLI command", "/generate-cli-command", "wire this
  feature into the CLI", "make this endpoint CLI-reachable".
---

# Generate a CLI Command for a New Feature

The Jarvis CLI (`jarvis/cli_ctl/`, the `jarvis`/`jarvisctl`/`jctl` binaries) is a
thin HTTP client over the REST API. Its dynamic `jarvis api <tag> <op>` layer turns
**every mounted REST route into a command automatically** — so a new feature is
CLI-reachable the moment its route is mounted with clean OpenAPI metadata. This
skill is the checklist that guarantees that, and adds an ergonomic curated command
for high-traffic routes.

Run this when you have finished a feature whose work added or changed a route in
`jarvis/ui/web/*_routes.py` (or `conductor/api`), before your final commit.

## Steps

### 1. Find the new/changed routes

```bash
git diff main...HEAD --name-only -- 'jarvis/ui/web/*_routes.py' 'conductor/api/*.py'
```

For each changed file, list the added route decorators:

```bash
git diff main...HEAD -- jarvis/ui/web/<file>_routes.py | grep -E '^\+\s*@router\.(get|post|put|patch|delete)'
```

Note each new `(METHOD, path)` and whether it is destructive (DELETE, or it
deletes / charges money / places a call / spawns work / writes arbitrary config).

### 2. Confirm the router is MOUNTED (the hard gate)

A route that exists in a file but is never `app.include_router(...)`'d is
unreachable from both the WebUI and the CLI (this has happened — see the frontier
/ antigravity / self-mod fixes). Run the coverage gate:

```bash
python scripts/ci/check_cli_coverage.py
```

If it reports your new module as unmounted, add the mount in
`jarvis/ui/web/server.py` next to the other `app.include_router(...)` calls (and
the matching `from .<module> import router as <name>` import). Re-run the gate
until it prints OK.

### 3. Check the OpenAPI metadata is clean

For the dynamic command to read well, each new route needs:

- a router **tag** (`APIRouter(prefix=..., tags=["<group>"])`) — becomes the
  `jarvis api <tag>` group;
- a sensible function name — FastAPI derives the `operationId` (→ command name)
  from it, so name it like the command you'd want (`list_widgets`, not `handler`);
- a one-line **summary or docstring** — becomes the command's `--help`.

Fix the route if any are missing. (You can eyeball the generated command after
booting the server: `jarvis api <tag> --help`.)

### 4. (High-value routes) Scaffold a curated command

If the route belongs to a domain users will drive often, add an ergonomic curated
command. Copy the pattern from an existing module (e.g.
`jarvis/cli_ctl/commands/missions.py`):

- Put the command in `jarvis/cli_ctl/commands/<domain>.py` (create the module +
  register it in `jarvis/cli_ctl/__main__.py` with `app.add_typer(...)`, and add
  `<domain>` to `RESERVED_CONTROL_NAMES` in `jarvis/cli_ctl/reserved.py` if new).
- Call `invoke.run(method, path, ...)`. For a **destructive** route pass
  `dangerous=True` so it requires `--yes`; reversible mutations proceed without it.
  Reads (GET) need nothing. Reuse `options.yes_opt()` / `options.dry_opt()` /
  `options.persist_opt()`.
- **Never** accept a secret as an inline argument — read it from a hidden prompt
  or stdin (`prompt=..., hide_input=True`), as `auth login` / `marketplace
  connect-pat` do (AP-12 / spec §7.2).

Add a test in `tests/unit/cli_ctl/test_commands_<domain>.py` using the
`capture_api` fixture: assert the command hits the right `(method, path, body)`
and that a destructive command refuses without `--yes`.

### 5. Regenerate the reference doc + run the checks

```bash
python scripts/ci/gen_cli_reference.py          # refresh docs/jarvis-cli-reference.md
python -m pytest tests/unit/cli_ctl/ -q         # all green
ruff check jarvis/cli_ctl/ scripts/ci/
python scripts/ci/check_cli_coverage.py         # OK
```

### 6. Commit

Commit the route, the mount (if added), the curated command + test, and the
regenerated reference together. Suggested message:

```
feat(<area>): <feature> + CLI command `jarvis <group> <command>`
```

## Hard rules

- The dynamic layer is the guarantee; a curated command is sugar. Never ship a
  curated command whose path is not a real, mounted route (the coverage gate and
  an adversarial path-vs-route check catch this).
- English only for all artifacts (code, help text, docs) — the `language-policy`
  CI gate blocks newly-added German.
- Destructive commands require `--yes`; secrets never go through argv.

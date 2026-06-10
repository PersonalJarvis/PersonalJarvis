---
title: "Reference: Jarvis-CLI"
slug: reference-jarvis-cli
diataxis: reference
status: active
owner: sam
last_reviewed: 2026-04-29
phase: "-"
audience: developer
tags: [cli, jarvis-ask, jarvis-skills, cli-tools]
---

# Reference: Jarvis-CLI

Complete reference of all command-line tools shipped with Personal
Jarvis — the main CLI ``jarvis``, the voice bridge
``jarvis-ask``, the skill runner ``jarvis-skills``, the review-pipeline
tools ``jarvis-review-eval`` / ``jarvis-review-gc``, plus the catalog of the
~20 external CLI tools that the Brain-Manager can dispatch as tools
(``gcloud``, ``aws``, ``gh``, ``stripe``, ``docker``, ``kubectl`` etc.).

## Table of Contents

- [Main CLI: ``jarvis``](#haupt-cli-jarvis)
- [Voice bridge: ``jarvis-ask``](#voice-bridge-jarvis-ask)
- [Skill runner: ``jarvis-skills``](#skill-runner-jarvis-skills)
- [Review pipeline: ``jarvis-review-eval`` / ``jarvis-review-gc``](#review-pipeline)
- [CLI tool registry (external tools)](#cli-tool-registry-externe-tools)
- [Launcher scripts (``run.bat``)](#launcher-skripte-runbat)
- [Module invocations (``python -m ...``)](#modul-aufrufe-python-m)

---

## Main CLI: ``jarvis``

The main entry point — installed via ``pip install -e .`` from the
repo root. Presents itself primarily as a tray/setup wrapper; the
running desktop app is started via ``run.bat``.

**Synopsis:**

```bash
jarvis [-h] [--version] [--wizard] [--check] [--plugins] [--debug]
       [--phase5-doctor] [--install-admin-helper]
```

| Flag | Effect |
|---|---|
| ``--version`` | Shows the package version and exits. |
| ``--wizard`` | Starts the setup wizard for API keys (Windows Credential Manager). |
| ``--check`` | Hardware analysis (CUDA, RAM, mic devices) + Whisper recommendation. |
| ``--plugins`` | Lists all plugin slots from the plugin registry (Wakeword, STT, TTS, Brain, Harness, Tool, Channel). |
| ``--debug`` | Console logging + verbose config dump at startup. |
| ``--phase5-doctor`` | Checks Phase-5 prerequisites: admin helper installed, vision deps available, kill hotkey bound, cost config set. |
| ``--install-admin-helper`` | Generates HMAC secret and registers the admin-helper shortcut (Phase-5 privileged-action handler). |

**Example:**

```bash
# Initial setup
jarvis --wizard

# Hardware check
jarvis --check

# Plugin inventory
jarvis --plugins
```

**Source:** ``jarvis/__main__.py:main``.

---

## Voice bridge: ``jarvis-ask``

A thin CLI/HTTP bridge over which OpenClaw (or another
CLI agent) can ask the user a question **by voice**. Jarvis reads
the question out via TTS, captures the spoken answer, and returns it on
stdout.

**Synopsis:**

```bash
jarvis-ask "<Frage>"                         # open question, free text
jarvis-ask --yes-no "<Frage>"                # yes/no variant
jarvis-ask --yes-no --json "<Frage>"         # JSON output for scripting
```

**Default timeout:** 30 seconds.

**Exit codes:**

| Code | Meaning |
|---|---|
| ``0`` | Answer received — stdout contains the raw answer text (or full JSON with ``--json``). |
| ``2`` | Voice pipeline offline (e.g. ``JARVIS_VOICE=0`` or headless mode). |
| ``3`` | Pipeline busy (another voice turn active). |
| ``4`` | Timeout — user did not answer within 30 s. |
| ``5`` | Network error talking to the local backend. |

**Pre-check (optional, ~50 ms):**

```bash
curl -s http://127.0.0.1:47821/api/voice-bridge/health
# -> {"available": true, "state": "LISTENING"}  -> jarvis-ask
# -> {"available": false, "state": null}        -> AskUserQuestion-Fallback
```

**Never query over the voice bridge:**

- API keys, passwords, auth tokens (STT data leak into the audio log).
- Security-critical "Are you sure?" confirmations (yes/no ambiguous in
  STT).
- Payment/cost confirmations (same reason).

**Source:** ``jarvis/clis/jarvis_ask.py:main``, endpoint
``POST /api/voice-bridge/ask`` in ``jarvis/ui/web/voice_bridge_routes.py``.

---

## Skill runner: ``jarvis-skills``

CLI frontend to the ``SkillRegistry`` — lists, inspects, runs skills,
imports external OpenClaw skills, and promotes sub-Jarvis-authored
drafts to ``state=active``.

**Synopsis:**

```bash
jarvis-skills (--list | --info NAME | --run NAME |
               --import-claude-skills PATH | --list-drafts | --promote SLUG)
```

| Flag | Effect |
|---|---|
| ``--list`` | Table of all skills (name, version, state, triggers). |
| ``--info NAME`` | Full detail for a skill: frontmatter, triggers, tools, resources, risk tier. |
| ``--run NAME`` | Run a skill (requires an MCP connection context). |
| ``--import-claude-skills PATH`` | Imports Anthropic-OpenClaw skills from a directory into the user skill repo. |
| ``--list-drafts`` | List sub-Jarvis-authored drafts (``state=draft``). |
| ``--promote SLUG`` | Promote a draft skill to ``state=active`` — user-explicit activation with a security lint of the skill body. |

**Examples:**

```bash
# Inventory
jarvis-skills --list

# Skill detail
jarvis-skills --info memory-save

# View sub-Jarvis drafts
jarvis-skills --list-drafts

# Promote a draft (Plan §7.5: user-explicit activation)
jarvis-skills --promote my-new-skill
```

**Promote lifecycle (Plan §AD-8):** draft → lint (security) → frontmatter
``state: active`` → SkillRegistry reload → audit entry ``skill_promoted``.
Throws ``UnsafeSkillError`` if the lint finds forbidden calls
(``eval``/``exec``/``system`` etc.).

**Source:** ``jarvis/skills/cli.py``.

---

## Review pipeline: ``jarvis-review-eval`` / ``jarvis-review-gc``

Phase-8.6 tools for the review pipeline (``dispatch_with_review``).
Use eval buckets from ``data/review/eval/``.

### ``jarvis-review-eval``

**Synopsis:**

```bash
jarvis-review-eval [--quick] [--mock] [--bucket BUCKET]
                   [--report PATH]
```

| Flag | Effect |
|---|---|
| ``--quick`` | Subset of the eval suite (deterministic cases, ~seconds). |
| ``--mock`` | Mock worker (no API call, free, < 1 s). For CI / smoke. |
| ``--bucket NAME`` | Filter to a single bucket (``code_gen_trivial``, ``research``, ``skill_authoring`` etc.). |
| ``--report PATH`` | Writes a JSON report with pass rate, latency, token usage. |

**Examples:**

```bash
# Mock mode (deterministic, free, < 1 s)
jarvis-review-eval --quick --mock

# Full suite (real, ~$2-5, ~10 min)
jarvis-review-eval --report data/last-eval.json

# Bucket filter
jarvis-review-eval --bucket code_gen_trivial
```

### ``jarvis-review-gc``

**Synopsis:**

```bash
jarvis-review-gc --older-than <duration>
```

Cleans up run artifacts under ``data/review/runs/<run_id>/`` when they
are older than ``<duration>``. The ``data/review.log`` JSON-Lines file
remains untouched (gapless audit trail).

**Example:**

```bash
jarvis-review-gc --older-than 30d
```

**Sources:** ``jarvis/cli/review_eval.py``, ``jarvis/cli/review_gc.py``.

---

## CLI tool registry (external tools)

A central registry indexes ~20 external CLI tools that the Brain-Manager
(main Jarvis and sub-Jarvis) can dispatch as tools. Each
entry carries a spec, auth profiles, a risk-tier default, and a probe status.

**Inventory (as of 2026-04-23, ``jarvis/clis/catalog/seed_catalog.json``):**

| Name | Display | Description |
|---|---|---|
| ``gcloud`` | Google Cloud CLI | Compute, Storage, IAM, GKE, Run |
| ``az`` | Azure CLI | Resources, VMs, AKS, Functions |
| ``aws`` | AWS CLI v2 | S3, EC2, Lambda, IAM, RDS |
| ``wrangler`` | Cloudflare Wrangler | Workers, Pages, R2, D1 |
| ``vercel`` | Vercel CLI | Deployments, Projects, env variables |
| ``netlify`` | Netlify CLI | Sites, Deployments, Functions, Env |
| ``heroku`` | Heroku CLI | Apps, Dynos, Addons, Releases |
| ``railway`` | Railway CLI | Projects, Services, Deployments |
| ``flyctl`` | Fly.io CLI | Apps, Machines, Volumes, Secrets |
| ``render`` | Render CLI | Services, Deploys, Logs |
| ``supabase`` | Supabase CLI | Projects, DB, Edge Functions, Migrations |
| ``firebase`` | Firebase CLI | Projects, Hosting, Functions, Firestore |
| ``pscale`` | PlanetScale CLI | MySQL branches, deploy requests |
| ``neonctl`` | Neon CLI | Serverless Postgres, Branches |
| ``gh`` | GitHub CLI | Repos, PRs, Issues, Actions, Releases |
| ``glab`` | GitLab CLI | Repos, MRs, Issues, CI/CD, Snippets |
| ``stripe`` | Stripe CLI | Webhooks, Events, Products, Subscriptions |
| ``twilio`` | Twilio CLI | Numbers, Messages, Voice, Verify, Flex |
| ``docker`` | Docker CLI | Containers, Images, Volumes, Networks |
| ``kubectl`` | Kubernetes CLI | Cluster management: pods, deployments etc. |
| ``gam`` | Google Workspace CLI (GAM) | Calendar events, Gmail messages, Drive, Users |

### Architecture components

- ``CliCatalog`` (``jarvis/clis/catalog/__init__.py``) — loads
  ``seed_catalog.json`` + allows user-custom specs in
  ``user_clis_dir()/custom.json``.
- ``CliStatusProber`` (``prober.py``) — async probe of whether a CLI is installed
  and login-capable; caches status per CLI.
- ``CliAuthManager`` (``auth.py``) — manages auth profiles (OAuth CLI,
  ENV token, manual login); injects ENV variables per call.
- ``CliInstaller`` (``installer.py``) — winget/scoop/npm/pip installation
  via a manifest from the catalog.
- ``CliTool`` (``tool.py``) — one instance per **connected** CLI;
  implements the tool protocol for the Brain-Manager. One tool per
  CLI, **not** per subcommand.
- ``UsageLog`` (``usage_log.py``) — SQLite DB under ``user_data_dir()/
  cli_usage.db``; every call is logged with latency + exit code.
- ``CliToolRegistry`` (``registry.py``) — aggregator. Set up by
  ``WebServer._setup_cli_registry()`` at startup; bootstrap
  runs asynchronously in the background.

### Risk-tier defaults

From ``seed_catalog.json``:

- **payments / prod-cluster** (``stripe``, prod branches in ``kubectl``,
  ``aws`` with ``--profile=prod``): default tier ``ask`` — user confirmation
  per call.
- **all others**: default tier ``monitor`` with a blacklist (e.g.
  ``gcloud projects delete``, ``aws s3 rm --recursive``).

### Binary guard

Every ``CliTool`` call validates before execution that the ``command`` arg
begins with the ``binary_name`` from the catalog. A brain that
attempts ``run-shell {"command": "rm -rf /"}`` would already have failed at
the risk-tier executor; the binary guard is the second
line of defense.

### ENV injection

``CliAuthManager.env_for(spec)`` returns a dict with the auth ENVs
(e.g. ``CLOUDSDK_AUTH_ACCESS_TOKEN`` for ``gcloud``,
``GH_TOKEN`` for ``gh``, ``STRIPE_API_KEY`` for ``stripe``). The
``CliTool`` merges that into ``os.environ`` for the subprocess.

### Output truncation

stdout: max **4000** characters, stderr: max **2000** characters. Prevents
token bloat with verbose CLIs (``kubectl describe pod`` etc.). The full
output lands in the UsageLog when the user looks it up.

---

## Launcher scripts (``run.bat``)

Windows launcher in the repo root. Sets the working directory, activates
``.venv\Scripts\activate.bat`` if present, and starts the launcher
via ``python`` or ``pythonw``.

**Variants:**

| Invocation | Effect |
|---|---|
| ``run.bat`` | pywebview window via ``pythonw`` (no console window), voice pipeline on. |
| ``run.bat --debug`` | Console window visible, ``JARVIS_DEBUG=1``, verbose logging. |
| ``run.bat --headless`` | Backend-only on ``127.0.0.1:47821`` (no window, **no** voice pipeline). |
| ``run.bat --dev`` | Frontend from the Vite dev server (port 5173), ``JARVIS_DEV=1`` — hot reload. |

**Disable voice:** ``set JARVIS_VOICE=0`` before the invocation.

---

## Module invocations (``python -m ...``)

Direct modules that can be invoked without an entry-point wrapper:

| Invocation | Purpose |
|---|---|
| ``python -m jarvis`` | Identical to ``jarvis`` (tray + setup). |
| ``python -m jarvis.ui.web.launcher`` | Desktop-app launcher directly; accepts ``--headless`` / ``--dev``. |
| ``python -m jarvis.skills.cli`` | Identical to ``jarvis-skills``. |
| ``python -m jarvis.hardware.detection`` | Standalone hardware analysis (RAM, CUDA, mic, Whisper recommendation). |

**Example:**

```bash
# Desktop app as backend only (for browser frontend)
python -m jarvis.ui.web.launcher --headless

# Hardware analysis as standalone
python -m jarvis.hardware.detection
```

---

## Related docs

- ADR-0011: Pure Dispatcher (4 tools) — why only ``run-shell`` and not
  a ``run-cli`` tool.
- ADR-0008: Computer-Use in-process — Computer-Use is a special case,
  does not run over the subprocess pattern of the CLI tools.

## Sources in the code

- Entry points: ``pyproject.toml [project.scripts]``.
- ``jarvis/__main__.py`` — main CLI.
- ``jarvis/clis/`` — voice bridge + CLI tool registry.
- ``jarvis/skills/cli.py`` — skill CLI.
- ``jarvis/cli/review_eval.py``, ``review_gc.py`` — review-pipeline tools.
- ``run.bat`` — Windows launcher.
- ``jarvis/ui/web/voice_bridge_routes.py`` — REST endpoint behind
  ``jarvis-ask``.

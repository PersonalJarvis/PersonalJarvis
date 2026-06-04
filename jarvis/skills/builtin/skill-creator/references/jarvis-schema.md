# Jarvis Skill Frontmatter Schema

Authoritative reference for the frontmatter block in every ``SKILL.md``.
Source: ``jarvis.skills.schema.SkillFrontmatter`` (Pydantic, ``extra="forbid"``).

## Required fields

| Field | Type | Example |
|------|-----|----------|
| `schema_version` | `"1"` (literal) | `"1"` |
| `name` | kebab-case string | `morning-routine` |

## Optional fields with defaults

| Field | Type | Default | Note |
|------|-----|---------|---------|
| `version` | string | `"0.1.0"` | Semver-compliant, not mandatory |
| `description` | string | `""` | read by the router as a trigger hint — keep it concrete |
| `category` | string | `"general"` | grouping in the UI; convention: `productivity`, `system`, `dev`, `meta`, `memory`, `general` |
| `tags` | `list[str]` | `[]` | free-form tags for search |
| `author` | string | `""` | e.g. `builtin`, a username, or `anthropic (adapted)` |
| `license` | string | `"MIT"` | SPDX ID or free-form |
| `triggers` | `list[SkillTrigger]` | `[]` | See below. `[]` = no auto-trigger (meta-skill) |
| `requires_tools` | `list[str]` | `[]` | tool names that must be present, otherwise DRAFT state |
| `risk_policy` | `SkillRiskPolicy` | `{default_tier: monitor}` | See below |
| `config` | `dict[str, Any]` | `{}` | skill-specific parameters (e.g. timer duration) |
| `token_budget_estimate` | int, 1..100_000 | `2000` | rough hint for budget tracking |

## SkillTrigger

A trigger is one of `voice`, `hotkey` or `schedule`. For each trigger the
matching field must be set:

```yaml
triggers:
  - type: voice
    pattern: "^(guten morgen|good morning)$"   # Regex, re.IGNORECASE wird beim Match gesetzt
    language: [de, en]                         # optional, Default [de, en]

  - type: hotkey
    combo: "ctrl+right_alt+j"                  # global-hotkeys-Syntax

  - type: schedule
    cron: "0 7 * * *"                          # croniter-Syntax (5 Felder)
```

**Voice patterns:**
- Matched case-insensitively against the transcription.
- Capture groups are allowed, e.g. `^merk dir:?\s+(.+)$` — the runner makes the
  groups available as `{{capture[0]}}` etc.
- Overly broad patterns (e.g. `.*`) are dangerous: they match every utterance.

**Hotkey combos:**
- Safe defaults: `ctrl+right_alt+<letter>`. Avoid Alt+F4, Ctrl+C, Win+*.
- Cross-platform note irrelevant: Jarvis is Windows-only.

**Schedule crons:**
- Standard 5-field cron: `minute hour day-of-month month day-of-week`.
- The scheduler runs in local time (not UTC) — the user timezone is the Jarvis timezone.

## SkillRiskPolicy

```yaml
risk_policy:
  default_tier: monitor           # safe | monitor | ask | block
  per_tool_overrides:             # pro-Tool anderer Tier
    gmail-mcp/send_mail: ask
    fetch-mcp/fetch_weather: safe
  require_confirmation:           # Tool-Aufrufe die immer Bestaetigung kriegen
    - send_money
```

**Tier meaning:**
- `safe` — runs without interaction, no logging highlight.
- `monitor` — runs, but the UI flags the call.
- `ask` — the executor pauses and asks the user via a toast.
- `block` — the tool is not executed; error in the runner.

**Whitelist override:** ``[safety.whitelist.commands]`` in ``jarvis.toml`` can
let tier-`ask` tools run without asking, per fnmatch pattern.

## Body conventions (after the frontmatter)

- **Imperative:** "Ask the calendar MCP", not "One could query the calendar".
- **TOOL marker:** Each tool call gets its own line:
  ```
  TOOL: gmail-mcp/list_unread {"limit": 5}
  ```
- **Make fallbacks explicit:** What does Jarvis say when a tool fails?
- **No walls of text:** Voice output is spoken, not read — short sentences.

## Bundle resources (sibling folders)

Analogous to Anthropic's structure, a skill folder may have sibling directories:

```
<skill-name>/
├── SKILL.md          (Pflicht)
├── references/       (Markdown-Docs, on-demand geladen)
├── scripts/          (Python/Shell, vom Runner aufrufbar)
├── assets/           (Templates, Configs, Icons)
└── agents/           (Sub-Agent-Personas als Markdown)
```

The Jarvis loader lists these files automatically in the skill detail panel. Access
from the runner: ``<skill_root>/scripts/my_helper.py`` — the runtime knows
``<skill_root>`` as the ``parent`` of the SKILL.md.

## DRAFT state

When the loader finds an error (invalid YAML, unknown field, trigger
without payload), the skill lands in the `DRAFT` state with the `error` field set. The
UI shows this in red — **no crash**, just diagnostics. Fix the skill, save again,
and hot-reload picks up the version.

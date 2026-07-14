---
name: "source-command-oc-status"
description: "OpenClaw bridge implementation status — which wave (1 Spike, 2 Mock, 3 Live, 4 Hardening) is done, which files are committed, which spike findings exist, what is next."
---

# source-command-oc-status

Use this skill when the user asks to run the migrated source command `oc-status`.

## Command Template

Produce an OpenClaw bridge status report. Proceed in this order:

1. Read the `docs/openclaw-bridge.md` migration-path table (§8) — the 10 phases.
2. Glob over `jarvis/plugins/harness/openclaw.py` — does it exist? How many LOC?
3. Read `docs/spike-results-openclaw.md` — the status header and the first SP section. If everything is `_(noch offen)_` → the spike has not been run yet. If findings are present → the spike is complete.
4. Glob over `tests/contract/test_harness_protocol.py` — does it contain an `openclaw` entry? (`grep openclaw`)
5. Glob over `tests/unit/harness/test_openclaw*.py` and `tests/integration/test_openclaw*.py`.
6. Git log for the most recent bridge commits: `git log --oneline -20 --all -- 'jarvis/plugins/harness/openclaw*' 'docs/openclaw-bridge.md' 'docs/spike-results-openclaw.md' 'scripts/spikes/openclaw*'`.
7. Read `[harness.openclaw]` from `jarvis.toml` — is the block already there? Which model value?
8. Check `pyproject.toml` to see whether the OpenClaw harness is registered as an entry_point.

Deliver a compact Markdown table:

```
## OpenClaw bridge status

### Wave progress
| Wave | Phase from §8 | Status | Evidence |
|---|---|---|---|
| 1 Spike | Phase 1 | DONE/OPEN | docs/spike-results-openclaw.md status header |
| 2 Mock bridge | Phases 2-3-4 | DONE/PARTIAL/OPEN | <file list> |
| 3 Live bridge | Phases 5-6 | DONE/PARTIAL/OPEN | <file list> |
| 4 Hardening | Phases 7-9-10 | DONE/PARTIAL/OPEN | <file list> |

### Spike findings
| SP | Question | Status | Implication |
|----|-------|--------|-------------|
| SP-1 | Native Windows | OPEN/PASS/FAIL | <assessment> |
| ...

### Code state
- `jarvis/plugins/harness/openclaw.py`: <EXISTS/MISSING + LOC>
- entry_point registered: <yes/no>
- `[harness.openclaw]` in jarvis.toml: <yes + model value / no>
- Tests: <N files, M tests green>

### Latest commits
<git log output, max 10 lines>

### Recommended next step
<one sentence>
```

Maximum 400 words.

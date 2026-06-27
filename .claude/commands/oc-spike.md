---
description: Trigger the OpenClaw bridge spike or read in spike output. If box access is available — show instructions. If a spike report is present — fold the findings into docs/spike-results-openclaw.md.
allowed-tools: Read, Edit, Bash(git status:*), Bash(git log:*)
argument-hint: [report-output] or empty for instructions
---

Behavior is based on `$ARGUMENTS`:

**When the argument is empty (instruction mode):**

Read the preparation section of `docs/spike-results-openclaw.md` and output a compact set of instructions:

1. PC prerequisites (Node 24, OpenClaw installed via npm or pnpm repo, ANTHROPIC_API_KEY set).
2. Spike invocation: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/spikes/openclaw_probe.ps1`.
3. Remind about the cheap-model default (Haiku 4.5 cost ~$0.10–$1).
4. Output path and how the content gets into the chat: `Get-Content '<report>' | Set-Clipboard`.
5. Note: after the spike, call `/oc-spike <pasted-output>` so the findings get recorded.

**When the argument contains a spike-report Markdown (read-in mode):**

1. Read `docs/spike-results-openclaw.md` and identify the eight SP sections (SP-1..SP-8).
2. Parse the `$ARGUMENTS` block — extract the finding line(s) for each SP item.
3. Edit `docs/spike-results-openclaw.md` for each SP section: replace `_(noch offen)_` with the empirical finding, plus a short assessment of what it means for the bridge architecture (e.g. "stdout = JSON → bridge parser uses `json.loads`", or "no --workdir flag → bridge must set `cwd` instead of a flag").
4. Update the "Summary & next steps" section at the end with the recommendation (architecture viable / plan B needed / minor adjustments required).
5. At the end, briefly list which assumptions from `docs/openclaw-bridge.md` were falsified by the spike — this list is input for a possible bridge-doc update.

NEVER write code. Only edit the spike-result doc.

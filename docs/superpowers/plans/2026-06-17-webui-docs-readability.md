# webui Docs Readability Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 12 user-facing docs in the separate `personal-jarvis-webui` repo readable for non-developers — gentle Claude-Code-style jargon glossing — without losing any technical depth.

**Architecture:** Clone the separate `personal-jarvis-webui` repo into an isolated working dir, work on a dedicated branch, rewrite only the **body prose** of each `src/content/docs/*.md` per the five-rule glossing canon, keep `npm run build` green (frontmatter/MDX intact), then bring the push back to the maintainer for sign-off. No code, no CSS, no frontmatter, no new files.

**Tech Stack:** Astro content collections (Markdown + YAML frontmatter), Node 18+, npm. Source spec: `docs/superpowers/specs/2026-06-17-webui-docs-readability-design.md`.

---

## The glossing canon (apply to EVERY doc)

1. **Plain-language hook** — open with one everyday sentence (what this is / why it matters) before the first jargon term.
2. **Gloss jargon on first use** — half-sentence inline, e.g. *"a **harness** — the interchangeable engine that actually runs the work"*.
3. **Catch presupposed tooling** — short parenthetical for `venv`, `npx`, "a PR", "headless", etc.
4. **Zero substance loss** — tables, code blocks, flags, deep sections stay complete.
5. **Consistent terms, second person, active voice** — one gloss per term per doc.

**Hard constraint:** never touch the YAML frontmatter block (`title`/`description`/`category`/`order`) — byte-identical before/after, or the Astro build breaks.

---

### Task 1: Setup — clone, branch, baseline build

**Files:**
- Clone target: `personal-jarvis-webui` (separate repo) into an isolated dir outside this working tree.

- [ ] **Step 1: Clone the separate repo into a temp working dir**

```bash
cd "$TMPDIR" 2>/dev/null || cd /tmp
gh repo clone octocat/personal-jarvis-webui webui-docs-work
cd webui-docs-work
```

- [ ] **Step 2: Create a dedicated branch**

```bash
git checkout -b docs/readability-pass-20260617
```

- [ ] **Step 3: Install deps and run the baseline build (must be green BEFORE any edit)**

```bash
npm install
npm run build
```
Expected: build succeeds. Record that it is green — this is the reference. If it is already red, STOP and report (we do not want to attribute a pre-existing break to our edits).

- [ ] **Step 4: List the 12 target docs to confirm scope**

```bash
ls src/content/docs/
```
Expected: `architecture.md brain-providers.md cli.md computer-use.md configuration.md first-run.md harness-dispatch.md installation.md introduction.md missions.md troubleshooting.md voice-pipeline.md`

---

### Task 2: Gloss `introduction.md` (the style anchor — fully worked example)

**Files:**
- Modify (body only): `src/content/docs/introduction.md`

- [ ] **Step 1: Read the whole file** so you keep the frontmatter and all sections intact.

- [ ] **Step 2: Apply the canon.** Concrete edits for this doc:
  - Gloss `meta-orchestrator` on first use, e.g.: *"a voice-driven **meta-orchestrator** — it doesn't answer you itself; it works out **which** tool should do the job and hands the work off, then reports back."*
  - Gloss `Supervisor-Agent` → *"a supervisor that classifies every spoken command and routes it"*.
  - Gloss `harness` on first use → *"an interchangeable **harness** (the engine that actually does the work — Jarvis-Agents, the Codex CLI, an MCP server, …)"*.
  - Gloss `Mission` → *"runs as a **Mission**: an isolated, self-checking background job"*; `Critic` → *"a **Critic** (a second pass that reviews the result and retries before answering you)"*; `git worktree` → *"an isolated **git worktree** (a throwaway copy of the project, so your real files are never touched)"*.
  - Gloss `MCP` on first use → *"MCP servers (a standard way to plug external tools into an AI)"*.
  - Keep the "three things that make it different" list, the blockquote, and "Who it is for" — only add the inline glosses.

- [ ] **Step 3: Verify frontmatter is unchanged**

```bash
git diff src/content/docs/introduction.md | grep -E '^[+-]' | grep -E 'title:|description:|category:|order:|^[+-]---'
```
Expected: NO output (no `+`/`-` lines inside the frontmatter block).

- [ ] **Step 4: Commit**

```bash
git add src/content/docs/introduction.md
git commit -m "docs: gloss jargon in introduction for non-developer readers"
```

---

### Task 3: Gloss `installation.md`

**Files:** Modify (body only): `src/content/docs/installation.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected presupposed-tooling to catch: `venv` (*"a virtual environment — an isolated Python sandbox for this app's dependencies"*), `npx` (*"npx runs an npm package without installing it globally"*), `headless` (*"headless = no desktop window, API only — for servers"*), `python:3.11-slim` container. Keep every command block, the flags table, and the requirements list byte-for-byte; add only the inline glosses and a one-sentence plain hook at the top.
- [ ] **Step 3: Verify frontmatter unchanged** (same grep as Task 2, swap the filename). Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss tooling assumptions in installation"`

---

### Task 4: Gloss `first-run.md`

**Files:** Modify (body only): `src/content/docs/first-run.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `wizard`, `credential manager` (*"your OS's secure password store"*), `BYOK / bring your own keys`, `API key` (*"a secret token that lets the app use a provider on your behalf"*), provider names. Lead with a plain hook ("On first launch, Jarvis asks for the keys it needs — here's what happens and why nothing leaves your machine."). Preserve all steps/screenshots/commands.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss first-run key wizard for newcomers"`

---

### Task 5: Gloss `configuration.md`

**Files:** Modify (body only): `src/content/docs/configuration.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `jarvis.toml` (*"the single text config file"*), `TOML`, `provider`, `risk tier` (*"how cautious Jarvis is before doing something — safe/monitor/ask/block"*), `ENV override`. Keep all config keys, tables, and example snippets intact.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss configuration concepts"`

---

### Task 6: Gloss `brain-providers.md`

**Files:** Modify (body only): `src/content/docs/brain-providers.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `provider` (*"the LLM service behind the assistant — Claude, Gemini, OpenAI, …"*), `BrainManager`, `fallback chain` (*"if one provider fails, it automatically tries the next"*), `router tier`, `Ack brain` (*"a tiny fast model that says 'on it' in under a second while the real answer is prepared"*). Keep provider tables/config.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss brain-providers for non-developers"`

---

### Task 7: Gloss `voice-pipeline.md`

**Files:** Modify (body only): `src/content/docs/voice-pipeline.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `wake word` (*"the phrase that starts listening, like 'Hey Jarvis'"*), `VAD` (*"voice-activity detection — it notices when you stop talking"*), `STT` (*"speech-to-text"*), `TTS` (*"text-to-speech"*), `Silero`, `barge-in` (*"interrupting the assistant mid-sentence"*), `endpointing`. Keep the pipeline diagram/stages.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss voice-pipeline terms"`

---

### Task 8: Gloss `missions.md`

**Files:** Modify (body only): `src/content/docs/missions.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `Mission` (*"a complex task that runs in the background and checks its own work"*), `worker`, `Critic`, `Kontrollierer/controller`, `worktree`, `self-healing` (*"retries on its own before giving up"*), `idempotency`. Keep state-machine/limits detail.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss missions concepts"`

---

### Task 9: Gloss `computer-use.md`

**Files:** Modify (body only): `src/content/docs/computer-use.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `Computer-Use` (*"letting the assistant click and type on your screen like a person"*), `UIA / accessibility tree` (*"the OS's machine-readable map of on-screen buttons and fields"*), `screenshot loop`, `harness`. Keep safety/guardrail detail in full (it matters here).
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss computer-use for newcomers"`

---

### Task 10: Gloss `cli.md`

**Files:** Modify (body only): `src/content/docs/cli.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `CLI` (*"command-line tool"*), `CLI catalog`, `PTY` (*"pseudo-terminal — what lets the app run an interactive terminal program"*), `cli_<name> tools`. Keep command/usage tables.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss cli doc"`

---

### Task 11: Gloss `harness-dispatch.md`

**Files:** Modify (body only): `src/content/docs/harness-dispatch.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Expected terms: `harness` (gloss again here — it's per-doc), `dispatch` (*"deciding which engine handles a request and handing it over"*), `OpenClaw`, `Codex`, `spawn`, `router` (*"the cheap fast brain that only decides where work goes"*), `force-spawn`. Keep the dispatch-decision detail.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss harness-dispatch"`

---

### Task 12: Gloss `architecture.md`

**Files:** Modify (body only): `src/content/docs/architecture.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon — lightest touch here, this is the deepest doc.** Add a one-sentence plain hook ("Here's how Jarvis is built and why the pieces stay swappable."). Gloss `protocol` (*"a fixed contract a layer talks through, without knowing the concrete implementation"*), `event bus` (*"an internal message channel layers use to talk sideways"*), `frozen dataclass` (*"an immutable record"*), `trace_id`, `structural not nominal` (*"a plugin matches by shape, not by name"*). Keep the 8-layer table, the dependency rule, and the plugin-group list complete.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: light gloss on architecture, depth preserved"`

---

### Task 13: Gloss `troubleshooting.md`

**Files:** Modify (body only): `src/content/docs/troubleshooting.md`

- [ ] **Step 1: Read the whole file.**
- [ ] **Step 2: Apply the canon.** Troubleshooting is already task-oriented; mainly add a plain hook and gloss any leftover jargon in symptom/cause descriptions. Keep every symptom→fix pair intact.
- [ ] **Step 3: Verify frontmatter unchanged.** Expected: no output.
- [ ] **Step 4: Commit** — `git commit -m "docs: gloss troubleshooting"`

---

### Task 14: Final verification — build green + frontmatter diff audit

**Files:** none (verification only)

- [ ] **Step 1: Full build must still be green**

```bash
npm run build
```
Expected: succeeds, same as the Task 1 baseline.

- [ ] **Step 2: Prove NO frontmatter line changed across all 12 docs**

```bash
git diff main...HEAD -- src/content/docs/ | grep -E '^[+-](title:|description:|category:|order:|---)' || echo "CLEAN: no frontmatter changes"
```
Expected: `CLEAN: no frontmatter changes`.

- [ ] **Step 3: Sanity-skim the rendered output** — start the dev server (`npm run dev`), open a couple of docs in the browser, confirm the hooks read naturally and code/tables still render. (Optional but recommended; if no browser is available, rely on the build + diff.)

---

### Task 15: Push sign-off (outward-facing — STOP and ask the maintainer)

**Files:** none

- [ ] **Step 1: Summarize the branch for the maintainer** — list the 12 commits and show `git diff --stat main...HEAD`.
- [ ] **Step 2: Ask the maintainer how to publish** — push the branch and open a PR, or push the branch directly, or hold. Do NOT push without an explicit answer.
- [ ] **Step 3: On approval, push the chosen way** (e.g. `git push -u origin docs/readability-pass-20260617`), then report the result and the PR/branch URL.

---

## Self-review notes

- **Spec coverage:** all 5 canon rules → encoded in the per-doc steps; all 12 docs → Tasks 2–13; frontmatter-untouched + build-green → Tasks 2–14; outward-facing push sign-off → Task 15. No gaps.
- **No placeholders:** each doc task names concrete expected terms + glosses; the exact final wording is produced when the doc is read (a writing task, not a code stub). The introduction (Task 2) is fully worked as the style anchor.
- **Consistency:** `harness`, `Mission`, `Critic`, `worktree`, `MCP` are glossed per-doc on first use (rule 5), so the repeated glosses in Tasks 8/9/11 are intentional, not duplication.

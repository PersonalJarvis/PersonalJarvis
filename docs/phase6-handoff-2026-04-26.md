# Phase 6 Handoff — 2026-04-26

## Branch
`router-permanent-vision` — **WARNING: not `phase6-self-healing`**.
The Phase 6 implementation currently lives on the same branch as other parallel WIP (Self-Mod Phase 7.x). Before a production deploy: cherry-pick the Phase 6 files into a dedicated `phase6-self-healing` branch.

## Current phase
**Phase 6 — Self-Healing Worker-Critic — COMPLETE (all 5 sub-phases live)**

| # | Sub-phase | Status | Tests |
|---|---|---|---|
| 1 | Foundation | ✓ | 80 |
| 2 | Worker layer | ✓ | 69 |
| 3 | Critic loop + Kontrollierer | ✓ | 156 |
| 4 | UI/API (backend + frontend) | ✓ | 21 + tsc-clean |
| 5 | Safety + Voice + Cleanup + Bootstrap | ✓ | 132 |
| | **Total** | | **458** |

## Last 5 commits (committed state)
```
cfae4227 feat(self-mod): phase 7.3 — brain tools for mutable settings
88899ddc feat(self-mod): phase 7.2 — atomic writer with backup + rollback
6931d2f6 feat(self-mod): phase 7.1 — registry + audit foundation
9e18652b docs(self-mod): bootstrap context for phase 7
1f1bebbb feat(voice+phase6): Window-Visibility-Gate + Mic-Auto-Resolve + Mission-Foundation
```

**Important:** the last commit `cfae4227` is Phase 7.3 (Self-Mod) — NOT Phase 6 code. All Phase 6 sub-phases 1-5 are UNCOMMITTED on the current branch.

## Uncommitted (working tree)
40 modified files, 1682 insertions / 151 deletions. Phase-6-relevant:
- **`CLAUDE.md`** — Phase 6 section set to "Live" + sub-phase table
- **`jarvis.toml`** — new `[phase6.safety]`, `[phase6.voice]`, `[phase6.cleanup]` sections
- **`docs/phase6-prompt-chain.md`** — status update
- **`jarvis/missions/`** — complete subsystem (29 files):
  - `__init__.py`, `events.py`, `event_bus.py`, `event_store.py`, `state_machine.py`, `manager.py`, `recovery.py`, `ids.py`, `missions_schema.sql`, `budget.py`, `cleanup.py`, `init.py`
  - `workers/` (5 files), `isolation/` (3 files), `critic/` (7 files), `kontrollierer/` (3 files), `safety/` (4 files), `voice/` (3 files)
- **`jarvis/ui/web/missions_*`** — REST/WS/PTY/Auth routes
- **`jarvis/ui/web/server.py`** — `_init_mission_stack()` with `bootstrap_missions()` integration
- **`jarvis/ui/web/frontend/`** — Mission-Control UI (11 new + 3 modified files), 4 npm packages
- **`tests/missions/`** — 458 tests (5 sub-phases)
- **`scripts/smoke_phase6_p1.py` + `_p2.py` + `_p3.py` + `_p3_real.py`** — smoke scripts

## What's next (3 concrete steps)

The prompt chain is **complete** — all 5 prompts implemented. The next steps are PRODUCTION FOLLOW-UPS:

1. **Branch refactor:** `git checkout -b phase6-self-healing` and cherry-pick the Phase 6 files (out of the Phase 7 branch). Clear commit order by sub-phase.

2. **Master E2E run (S2 manual):** Real voice path with `python -m jarvis` → wake → "Schreib eine Primzahl-Funktion" → Mission-Tree UI → approval + DE voice output. Expected: <90s, <$0.40, 1-2 critic iterations. Document in `docs/phase6-e2e-run-<date>.md`.

3. **Activate TTS wiring + brain caller in bootstrap:** currently `bootstrap_missions(tts_speak_fn=None, brain_caller=None)` runs — the voice listener is disabled and the decomposer operates in heuristic-only mode. Production wiring of DesktopApp.SpeechPipeline._tts.synthesize → bootstrap_missions is the prerequisite for voice readback and multi-step mission decomposition to work.

## Known open questions

**From ADR-0009 §"Open":**
- ✓ **Reflection memory layout:** decided — Markdown in the mission root (not JSON in SQLite). Implemented in `jarvis/missions/critic/reflections.py`.
- 🔄 **Cross-model critic trigger:** still deferred — opt-in possible via a config flag in `escalation.py`, but no default path. Phase 7 decision.
- 🔄 **Worktree cleanup policy on MissionFailed:** currently a uniform 14-day prune. Alternative: immediate `git worktree remove --force` on Failed (disk savings). Phase 7 decision.
- ✓ **Voice readback at iteration 2:** decided — default OFF, opt-in via `[phase6.voice].announce_critic_loop=true`. Implemented in `jarvis/missions/voice/listener.py`.

**Phase 5 CAVEAT (not in ADR-0009, but newly discovered):**
- ✓ **Critic auth path:** `openclaw agent --bare` opt-in via `ANTHROPIC_API_KEY` detection (otherwise the OAuth/Keychain path). Implemented in `jarvis/missions/critic/runner.py:148`.

**TODO/FIXME markers:**
- `jarvis/ui/web/missions_pty_routes.py` — Phase-4-MVP stub marked as `# TODO: wire to actual log-tail in Phase 5`. The Phase 5 cleanup did NOT touch the stub file — a full PTY tail remains a Phase 7 task.

## Repair before the next prompt

**Branch + commit strategy:**
- `git checkout -b phase6-self-healing` from HEAD or from `1f1bebbb` (before Self-Mod Phase 7.x).
- Split the Phase 6 files into 5 commits (sub-phase 1-5 separately) so that the git history reflects the architecture.
- All 458 tests green via `pytest tests/missions/` before each commit.

**Optional but recommended:**
- Cross-link `docs/phase6-test-report.md` (freshly created) in CLAUDE.md under the Phase 6 section.
- Finalize ADR-0009 §"Open" via `/skill phase6-adr-update` with the 4 decisions above.

## Cross-refs
- ADR: `docs/adr/0009-self-healing-worker-critic.md`
- Prompt chain: `docs/phase6-prompt-chain.md`
- Research: `SubAgentenSt/Unbenanntes Dokument (4).md` (== `docs/research/self-healing-architecture.md` final)
- Test report: `docs/phase6-test-report.md`
- Plan files:
  - `C:\Users\Administrator\.claude\plans\glistening-wobbling-owl.md` (Phase 6 tooling bootstrap)
  - `C:\Users\Administrator\.claude\plans\phase6-critic-loop-plan.md` (Prompt 3)
  - `C:\Users\Administrator\.claude\plans\phase6-safety-voice-cleanup-plan.md` (Prompt 5)
- Master plan: `C:\Users\Administrator\.claude\plans\also-er-muss-auch-lexical-pond.md`

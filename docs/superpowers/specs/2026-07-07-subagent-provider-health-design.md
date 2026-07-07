# Subagent Provider Health & Failure Surfacing — Design

**Date:** 2026-07-07
**Status:** Approved (maintainer, 2026-07-07)
**Context:** The 2026-07-06 incident (missions `019f36e5` + `019f38b1`): the
Claude Max OAuth token expired in place and every subagent mission died with
"401 Invalid authentication credentials" — while the desktop app showed the
subagent provider as connected, the Sub-Agents view showed only the raw
`task_error` token, and the voice line said the generic "The worker aborted."
The cross-family rescue (commit `6cfdd88a` and the stop-hook auto-saves around
it) fixed the *mission outcome*; this design fixes the *visibility*: provider
problems must be seen in the app — proactively (before a mission dies) and
reactively (an honest failure message when one does).

Main-provider errors already have this UX (section-health tab dots + provider
cards). Subagents are the gap. This design extends the existing pattern; it
introduces no new UI concept.

## Goals

1. Every worker-terminal failure carries a machine-readable **error class**,
   the **provider that failed**, and a short **human-readable detail** — end
   to end (event → store → REST/WS → UI → voice).
2. The Sub-Agents section health is **live-honest**: expired OAuth, a
   provider proven dead this session, and quota cooldowns are reflected
   *before* any mission fails.
3. The Sub-Agents view shows a **warning banner** (degraded/dead) and a
   **readable failure message** per failed mission instead of the raw
   `task_error` token.
4. The **voice announcement** names the real cause class instead of the
   generic "The worker aborted."

## Non-Goals

- No repair buttons in the banner (repairs live in the API-Keys view).
- No WebSocket push for section health (the existing 15 s polling + 45 s
  backend cache stays).
- No LLM-generated failure phrasing (failure announcements are deterministic
  today; we extend the existing phrase table).

## 1. Data model — error classification (five-layer discipline, AP-4)

New closed token set `MISSION_ERROR_CLASSES` (single source of truth in
`jarvis/missions/events.py`):

| token | meaning | classified from |
|---|---|---|
| `provider_auth` | credential dead/invalid (401, not logged in, expired token) | `_worker_error_is_auth` (orchestrator) |
| `provider_quota` | usage/session/rate limit or billing/credits exhausted | billing branch + quota/rate markers |
| `provider_unreachable` | transient availability (5xx, overloaded, unreachable) | remaining `_worker_error_is_transient` matches |
| `worker_timeout` | wall-clock / first-output timeout | structured `timed_out` flag |

`MissionFailed` (`jarvis/missions/events.py:122`) gains two OPTIONAL fields
(backward compatible — old stored events validate with defaults):

- `error_detail: str | None` — the upstream error text, truncated (~200 chars).
- `failed_provider: str | None` — provider slug of the worker that failed
  (`getattr(worker, "provider", None) or worker.cli`).

`error_class` (existing, today always `None` on live paths) is populated from
the table above. The recovery sweep's legacy values
(`MissionInterrupted` / `OrchestratorCrash`) remain valid; the field stays
`str | None` (no breaking literal). `WorkerKilled` gains the same two optional
fields so the Sub-Agents registry can label the node without waiting for
`MissionFailed`.

**Parity guard:** a new test (pattern: `test_hangup_reason_parity.py`) asserts
the Python token set == the TS map keys (`frontend/src/types/missions.ts` /
the view's message map) == the voice phrase-table keys. This is the BUG-008
defense.

## 2. Orchestrator — populate the classification

In `jarvis/missions/kontrollierer/orchestrator.py`, the existing
`spawn_result.worker_error` handling already computes `is_timeout`,
`is_transient`, `is_auth`, and the billing branch. A small pure helper
`_classify_worker_error(err, timed_out) -> str | None` maps those to the
token table (unit-tested in isolation; `None` when nothing matches, so the UI
falls back to `reason`). `_fail_mission` and `_publish_worker_killed` pass
`error_class`, `error_detail` (the verbatim `worker_error`, truncated), and
`failed_provider` through.

## 3. Section health — live-honest subagent status

`_jarvis_agent_section_health` (`jarvis/ui/web/provider_routes.py:676`) today
only checks credential presence and can never report `error`. It becomes:

- **`ok`** — the selected worker provider is usable right now: auth-service
  connected AND not flagged dead this session
  (`claude_auth_dead()` / `codex_needs_reauth()`), no quota cooldown
  (`claude_in_quota_cooldown()`), and for Claude a non-expired live OAuth
  status (`live_claude_oauth_status()`) or a classic API key
  (`_claude_cli_auth_viable()`).
- **`needs_setup`** (amber = degraded) — the SELECTED provider is dead or
  cooling down, but a cross-family fallback is reachable (codex OAuth live,
  or any API-key family per `get_provider_secret`). `detail` names both
  sides, e.g. `"Claude subscription login expired — missions run on codex
  until you run 'claude /login'."`
- **`error`** (red) — no provider family is reachable at all: the next
  mission WILL fail. `detail` says what to configure.

No new status literal is introduced — the existing
`{ok, needs_setup, error, unknown}` vocabulary and the existing tab-dot UI
consume this unchanged. The check stays cheap and offline (file reads +
process-local flags; no network probes) so the 45 s cache and 15 s polling
budgets hold.

## 4. Sub-Agents view (frontend)

`jarvis/ui/web/frontend/src/views/JarvisAgentsView.tsx` /
`views/sub-agents/DepartureBoard.tsx`:

1. **Health banner** at the top of the view, driven by the EXISTING
   `useSectionHealth()` hook (`subagents` section): hidden when `ok`/`unknown`;
   amber banner when `needs_setup`; red banner when `error`. Shows the
   i18n'd status label plus the backend `detail` text. No new endpoint.
2. **Readable failure text**: `SubAgentNode` gains `errorClass` (TS mirror of
   `error_class`). `resultLabel()` / the drilldown map `errorClass` → an
   i18n message ("Provider sign-in expired — reconnect in the API-Keys
   view."), falling back to `node.error` (which now carries `error_detail`
   instead of the raw reason token — see §5), then to the reason token.
3. i18n: new keys with **English source** strings + `de` translations in the
   existing locale files (repo language rule).

## 5. Sub-Agents registry (backend node mapping)

`jarvis/agents/registry.py`: on `WorkerKilled` / `MissionFailed`, set
`node.error = payload.error_detail or <existing reason text>` and
`node.error_class = payload.error_class`. `/api/sub-agents/tree` serializes
both; the TS store mirrors them.

## 6. Voice announcement

`jarvis/missions/voice/announcer.py` (+ `readback.py`'s shared table): when
`payload.error_class` matches a key in `FAILURE_REASON_PHRASES`, use that
phrase; otherwise keep the existing `FAILURE_REASON_PHRASES[reason]` path.
The entries live in the EXISTING `FAILURE_REASON_PHRASES` table — keyed by
the voice readback system's `Lang` literal, which is `de`/`en` — while the
UI i18n locales carry `en`/`de`/`es`; extending the voice system itself to
`es` is tracked as separate backlog. Example (`provider_auth`, en):
"The mission failed: the AI provider sign-in is invalid or expired."

## 7. Prevention recap

- Proactive: §3 turns the API-Keys **Subagents tab dot** amber/red and the
  §4 banner on *before* any mission is dispatched — the 2026-07-06 incident
  would have shown amber from 02:53 on.
- Reactive: §§1-2 + 4-6 make any future provider failure legible in one
  glance (UI) and one sentence (voice).
- The underlying resilience (expiry-aware token reader, `claude_auth_dead`
  flag, cross-family rescue) already landed on 2026-07-06/07.

## 8. Testing

- **Orchestrator:** `_classify_worker_error` unit matrix (the live 401 text,
  quota texts, 5xx, timeout, unclassifiable) + an integration test asserting
  `MissionFailed.error_class/error_detail/failed_provider` are populated.
- **Section health:** three-scenario test (ok / degraded-with-fallback /
  all-dead) with the same monkeypatch seams as
  `tests/missions/test_worker_cross_family_fallback.py`.
- **Registry:** node carries `error_class` + readable `error`.
- **Frontend:** banner renders per status; failed row shows the mapped
  message (existing vitest patterns in `DepartureBoard`/view tests).
- **Voice:** `provider_auth` phrase resolves in all supported languages;
  unknown class falls back to the reason phrase.
- **Parity:** Python tokens ↔ TS map ↔ phrase table (AP-4 guard).

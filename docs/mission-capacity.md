# Mission capacity and paid API fallback

How a mission behaves when the provider it runs on has no capacity left (a
spent usage window, an expired login, a provider that cannot run), and when it
may spend money on an API key. Code: `jarvis/missions/capacity.py`; worker
choice: `jarvis/missions/init.py`; orchestration:
`jarvis/missions/kontrollierer/orchestrator.py`; critic:
`jarvis/missions/critic/runner.py`.

## Who is affected

A **subscription install** is one that connected a subscription recently (the
sticky signal of `jarvis/brain/background_policy.py`) or has a usable
subscription login right now (the `claude` CLI login, codex over the ChatGPT
login). A CLI that is merely installed does not count.

An **API-key-only install** is everything else. Its keys are its primary
provider: the cross-family worker chain is unchanged, the Anthropic key is not
stripped from the CLI environment, and missions never park for capacity. The
paid-fallback setting has no effect there.

## Order of choice (subscription install)

Every decision — dispatch, retry, resume, critic — goes through one rule,
`decide_route`:

1. The configured subscription, else **another connected subscription**
   (claude ↔ codex). Moving between subscriptions costs nothing extra and needs
   no approval, for the worker and the critic.
2. An **active manual approval** of this mission run (the offer shown in the
   app). It covers worker and critic calls and grants up to `$2` more than the
   mission already paid.
3. The **paid-fallback setting** `[missions] paid_api_fallback` (default
   `false`). When on, the mission continues on the user's own API key — in
   process, never through a CLI on a classic key — within the caps below.
4. Otherwise the mission **parks** in `WAITING_CAPACITY` with a checkpoint.

Antigravity and grok build run when they are the configured worker, but they
are never a silent fallback target: nothing offline can tell whether they have
capacity.

## Caps (hard, fail closed)

| Cap | Value | Scope |
| --- | --- | --- |
| Per mission | `$2` | Cumulative over every paid call of the mission — parallel steps, retries, resumes, worker and critic. Automatic use never resets; each manual approval grants `$2` more. |
| Per day | `$10` | Rolling 24 h over all automatic (setting-based) paid use. Manual approvals are recorded there for reporting but never blocked by it. |

Every paid call reserves its maximum possible cost first (request bytes as an
upper bound on input tokens, priced as cache writes, plus the full output
budget), then books the actual cost from the usage report. A call without a
usage report is charged its whole reservation. Cache reads and writes are
counted. Parallel steps share one ledger, so they cannot overshoot together.
The daily ledger lives in the user data directory
(`data/mission_paid_ledger.json`), written atomically; an unreadable ledger
blocks automatic paid use.

## Consent per call

The paid worker and the paid critic call a gate before **each** model call.
It re-reads the setting (no cache, no restart needed) and checks for an
active approval. Switching the setting off stops automatic paid use at the
next call; the mission parks with reason `paid_consent_revoked` and keeps its
checkpoint.

## Parking and resuming

Reasons (`CapacityWaitReason`): `provider_quota`, `provider_auth`,
`provider_unavailable`, `paid_cap_reached`, `paid_daily_cap_reached`,
`paid_consent_revoked`.

A background loop (every 5 minutes plus jitter, never on the boot path)
resumes a parked mission when a subscription can run its open step, when a
critic has capacity for a step whose review is pending, or — setting on and
caps left — on the paid key. A mission that parks again without finishing
anything new backs off: 5 minutes, doubling to an hour, jittered. A failed
claim puts the mission back into `WAITING_CAPACITY`; an app shutdown parks a
resumed mission again instead of cancelling it. The checkpoint is deleted when
the mission reaches a terminal state.

## HTTP

- `GET /api/mission-billing` — the switch, `subscription_mode`, both caps,
  `spent_last_24h_usd` (automatic use) and the key that would be used first
  (`paid_provider`, or `null`). Reads keys, config, login files and the local
  ledger only; never calls a provider and never returns a secret.
- `PUT /api/mission-billing` with `{"paid_api_fallback": true|false}` (strict
  boolean) — persisted through `config_writer.set_missions_paid_api_fallback`,
  returns the `GET` shape. Marked `x-jarvis-dangerous`, so the CLI asks for
  `--yes`.
- `GET /api/missions/{id}/paid-offer` — the per-mission offer, including
  `spent_usd` (what the mission already paid) and `covers_critic: true`.
- `POST /api/missions/{id}/capacity-decision` — `wait`, `approve_paid` (must
  echo the shown provider and model) or `cancel`.

Both billing routes sit behind the global `SurfaceSecurity` boundary like every
`/api` route: a credential is required, and a browser write needs a trusted
`Origin`. The setting can never be changed by voice, chat or an agent: the
config path is in `jarvis/core/self_mod/forbidden.py`, and both the billing
routes and the capacity decision are excluded from the brain's app-action
catalog (`jarvis/app_actions/catalog.py`).

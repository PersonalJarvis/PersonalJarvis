---
title: "ADR-0006: Cost-Budget im Brain"
slug: adr-0006-cost-budget-hook
diataxis: adr
status: active
owner: harald
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0006 — Cost-Budget Hook in the BrainManager

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Control

## Context

Mandate requirement: €2 per task, €30 per day, 60-minute cooldown on overrun. The mandate warns: "Streaming costs accumulate asynchronously. A cost check before the call is worthless if the stream blows up in the middle."

## Decision

**Single-point hook in `BrainManager.complete()`** that tracks cost per `trace_id` and, on overrun, fires the `CancelToken` — rather than skipping the next iteration.

### Data flow
```
BrainManager.complete(req, ctx)
    ↓
CostMeter.start(ctx.trace_id, ctx.task_id, provider, model)
    ↓
async for delta in provider.complete(req):
    if delta.usage:
        CostMeter.add(ctx.trace_id, delta.usage, provider, model)
        if CostMeter.over_task_budget(ctx.trace_id):
            ctx.cancel_token.cancel("budget_task_exceeded")
            break
        if CostMeter.over_daily_budget():
            ctx.cancel_token.cancel("budget_daily_exceeded")
            RateLimitTracker.set_cooldown_until(time.time() + 3600)
            break
    yield delta
CostMeter.close(ctx.trace_id)
```

### Cost calculation
Via the `Brain.estimate_cost` protocol method (already present) and the `usage.input_tokens` / `output_tokens` / `cache_hit_tokens` from `BrainDelta`. Price table per provider/model in `jarvis.toml:[cost.prices]` (input/output USD per 1M tokens).

### Persistence
The daily total is accumulated in the `data/jarvis.db` table `cost_ledger (day, provider, model, tokens_in, tokens_out, cost_usd)`. At app start, today's daily total is loaded. The cooldown-end timestamp lives in `data/cost_cooldown.json` (so a restart does not forget the cooldown).

### Exchange rate
The mandate is in euros, API prices are in USD. Fixed rate in `jarvis.toml:[cost] eur_per_usd = 0.92` (maintain manually once a year). Non-critical (the budget cap is a safety net, not accounting).

## Consequences

+ One hook, one truth. All 9 brain providers are covered, because they all run through the BrainManager.
+ Cancel via CancelToken means: the stream stops immediately, and the subprocess-kill logic from ADR-0004 applies here too.
+ Separate task and daily budgets are representable with a single data model.
+ Reuse of the `RateLimitTracker` for cooldown behavior.
- The pre-call estimate is **no longer** a gate — the stream may briefly exceed the limit until a `usage` delta arrives. Worst case: 1–2 chunks over the limit, tolerated.
- For providers without `usage` in intermediate steps (e.g. early delta chunks without usage), the budget is only hit at the end of the stream. Mitigation: fallback estimate based on `estimate_cost(req)` × elapsed_fraction.

## Alternatives Considered

- **Instrumentation in every brain plugin:** 9 sites, drift apart. Rejected.
- **OTel metrics hook:** async lag, non-blocking → no abort possible. Rejected.
- **Gate only before the call:** the mandate warning makes clear this fails. Rejected.
- **CreditManager pattern (pre-booking):** over-engineering for the use case. Rejected.

## Open

- Config schema:
  ```toml
  [cost]
  enabled = false
  per_task_eur = 2.00
  per_day_eur = 30.00
  cooldown_minutes = 60
  eur_per_usd = 0.92

  [cost.prices.claude-haiku-4-5-20251001]
  usd_per_1m_input = 1.00
  usd_per_1m_output = 5.00
  # ...
  ```

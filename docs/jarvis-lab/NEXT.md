# JARVIS-LAB next remote frontier

Verified on 2026-10-05: the lead-chat event-routine creation and connection-path readback contract is present at `094a2d4`. Its CI #213 passed static gates, contracts, frontend and the completed portable shards while newer branch activity superseded two Windows shards. PR #1 remains open and draft.

M4 room scheduling is no longer the next unblocked task. The branch now carries bounded live-room scheduling through the existing `SocietyScheduler`, restart recovery, voice completion/status, curator review/taint invariants, and blocking exit contracts. M5 hardening guards are also present for legacy Agents routing, Jarvis-only production seeding, capability-driven teammate proposals, the canonical Society cost surface, locale/enum parity, boot budget, and Ledger accessibility.

M6 already includes learned-skill review, signed GitHub merged-PR triggers, isolated screen leases and validated shareable figure recipes. Lead-chat event routine schemas and agent-chat save mandates are present. Italian schedule/event intent now uses the same routing and save guards, with focused tests excluding habits, how-to questions and skill authoring. These are implemented slices, not complete native qualification.

Current qualification: `9d29df9` adds the full signed-delivery contract, from lead-chat routine creation through the existing credential route, merge filtering, replay deduplication, owned execution and result readback without credential disclosure. CI #220 (`37284242791`) passed static gates; portable test shards were still running at this check. The branch also carries durable routine readback (`b4a4885`), client-zone updates (`a68ff94`), REST opener provenance (`3650b5c`) and cancellation/cost recovery through the existing scheduler (`99d4ee4`). These changes remain subject to the latest complete CI result.

Bounded room-watcher recovery now cancels the exact owned turn after three event-read failures, reads available terminal cost, fails the durable claim and releases the scheduler slot. Dispatch cancellation and lost-claim subscription cleanup also have deterministic regressions. Runner qualification remains pending for these additions.

Signed routine receipt contracts now cover active and paused ownership, owner-seat failure, temporary admission deferral, queued database reopen and claimed-delivery interruption. They use the real TaskStore, TaskRunner and TaskScheduler through authenticated GitHub ingress and verify no generic model fallback, retained pending delivery and no replay of potentially executed work (4414438, 9332744). CI #222 exposed one new test error: a timezone-bearing one-shot update was incorrectly expected to pass the recurring-only routine contract. f81a719 preserves the 409 rejection and checks that the durable spec and timezone context remain unchanged. Baselines were not expanded.

The owner runner now fails closed when the saved task or its model seat cannot be read, instead of silently substituting the owner's live seat. Focused contracts require zero new run chats and no main-chat mutation on missing or unreadable storage. Runner qualification is pending for this change and the signed-receipt additions.

CI #226 (37288552623) is now complete: every portable, Windows, frontend, contracts, static, installer, updater and browser job passed; the three native Mac lanes remain the documented skips. The owner seat and signed-delivery changes are therefore qualified by the full portable run. The SocietyScheduler now logs a deferred busy-recipient receipt while retaining the durable queue retry (35a416c).

The next remote-safe step is to continue the continuity and handoff audit from the integration matrix, starting with durable queued-delivery observability and exact retry receipts. Any new behavior must use the existing scheduler, approval, policy and memory boundaries. Do not introduce parallel infrastructure or claim an external provider is connected merely because a local credential was saved.

Native MacAgentBench qualification remains explicitly deferred to a physical Mac with user-granted permissions and must not be represented as remotely complete.

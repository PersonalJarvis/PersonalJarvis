# JARVIS-LAB next remote frontier

Verified on 2026-10-05: commit `388cb24` has a successful GitHub Actions run (`37267330253`, CI #189). The subsequent M5 qualification note is at `61e3e2a`; its CI #190 was still running at this check. PR #1 remains open and draft.

M4 room scheduling is no longer the next unblocked task. The branch now carries bounded live-room scheduling through the existing `SocietyScheduler`, restart recovery, voice completion/status, curator review/taint invariants, and blocking exit contracts. M5 hardening guards are also present for legacy Agents routing, Jarvis-only production seeding, capability-driven teammate proposals, the canonical Society cost surface, locale/enum parity, boot budget, and Ledger accessibility.

M6 already includes learned-skill review, signed GitHub merged-PR triggers, isolated screen leases and validated shareable figure recipes. Lead-chat event routine schemas and agent-chat save mandates are present. Italian schedule/event intent now uses the same routing and save guards, with focused tests excluding habits, how-to questions and skill authoring. These are implemented slices, not complete native qualification.

The next remote-safe frontier is an end-to-end contract for creating an event routine through the lead-chat app command and reading back its connection requirement. Preserve existing approval, routine, scheduler, policy and memory boundaries. Do not introduce parallel infrastructure or claim a webhook is connected merely because a routine was saved.

Native MacAgentBench qualification remains explicitly deferred to a physical Mac with user-granted permissions and must not be represented as remotely complete.

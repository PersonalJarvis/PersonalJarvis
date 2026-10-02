# JARVIS-LAB Autopilot

This branch is advanced through two execution modes:

1. Interactive work while a ChatGPT session is actively running.
2. Scheduled autonomous review and continuation once per hour.

The hourly pass must verify that `jarvis-lab` is progressing. If progress has stalled, it should resume the highest-priority unblocked JARVIS-LAB task, preserve the existing PersonalJarvis architecture, add focused tests where appropriate, and commit only to `jarvis-lab`.

Safety constraints remain unchanged:

- never merge automatically into `main`;
- never use destructive history rewrites;
- never publish releases automatically;
- never modify credentials or introduce paid services without explicit approval;
- defer native macOS permission prompts and physical-Mac qualification until a user-driven test pass;
- keep one orchestrator, one safety boundary, and one authoritative memory system.

Current workstream priority:

1. MacAgentBench stale-target-refusal coverage after the takeover and semantic-target contracts;
2. upstream alignment and regression checks;
3. next highest-priority architecture gap that is not blocked on physical macOS testing.

Completed remotely in the current benchmark phase: physical-user-takeover and
semantic-target-hit receipt contracts. Native qualification remains deferred
until an explicit real-Mac pass can grant the required permissions and capture
live receipts.

The scheduled pass is intentionally limited to the platform-supported maximum cadence of once per hour; it is not a continuously resident daemon.

import { describe, expect, it } from "vitest";

import type { AnyPayload, EventEnvelope } from "@/types/missions";
import {
  buildStory,
  classifyNote,
  deriveOutcome,
  groupStory,
  missionIdFromTraceId,
  splitReason,
} from "./outcome";

let seq = 0;
function env(
  payload: AnyPayload,
  over: Partial<Omit<EventEnvelope, "payload">> = {},
): EventEnvelope {
  seq += 1;
  return {
    event_id: `e${seq}`,
    seq,
    mission_id: "m1",
    parent_event_id: null,
    worker_id: null,
    source_actor: "kontrollierer",
    ts_ms: 1_000 + seq * 1_000,
    schema_version: 1,
    ...over,
    payload,
  };
}

/** The event stream of a real quota failure (2026-08-10, mission 019fecaa). */
function quotaFailure(): EventEnvelope[] {
  const wid = "019fecaa-5a92::019fecaa-5aa5::iter0";
  return [
    env({ event_type: "MissionDispatched", prompt: "Build the page", parent_mission_id: null, priority: 0, language: "en" }),
    env({ event_type: "MissionStateChanged", from_state: "PENDING", to_state: "RUNNING", reason: "kontrollierer-start" }),
    env({ event_type: "MissionPlanReady", plan: [{}], n_workers: 1, expected_output: "Single-step plan" }),
    env(
      { event_type: "WorkerSpawned", worker_id: wid, step: {}, pid: 0, cli: "claude", model: "", worktree: "C:/wt", session_id: "s1" },
      { worker_id: wid },
    ),
    env(
      { event_type: "WorkerProgress", worker_id: wid, pct: null, note: "You've reached your limit.", stalled: false, tokens_so_far: 0, cost_so_far: 0 },
      { worker_id: wid, source_actor: "worker" },
    ),
    env(
      { event_type: "WorkerKilled", worker_id: wid, reason: "budget", error_class: "provider_quota", error_detail: "You've reached your limit." },
      { worker_id: wid },
    ),
    env({
      event_type: "MissionFailed",
      reason: "task_error",
      error_class: "provider_quota",
      last_state: "CRITIQUING",
      partial_artifacts: [],
      error_detail: "You've reached your limit.",
      failed_provider: "claude",
    }),
  ];
}

describe("deriveOutcome", () => {
  it("reads a provider-quota failure into reason, class, detail and the kill", () => {
    const out = deriveOutcome(quotaFailure(), "en");
    expect(out.terminal).toBe("failed");
    expect(out.reason).toBe("task_error");
    expect(out.error_class).toBe("provider_quota");
    expect(out.error_detail).toBe("You've reached your limit.");
    expect(out.failed_provider).toBe("claude");
    expect(out.last_state).toBe("CRITIQUING");
    expect(out.kills).toHaveLength(1);
    expect(out.kills[0].reason).toBe("budget");
    expect(out.workers).toHaveLength(1);
    expect(out.workers[0].cli).toBe("claude");
    expect(out.workers[0].ended_reason).toBe("budget");
    expect(out.planWorkers).toBe(1);
  });

  it("keeps the reviewer's last verdict and correction on a review failure", () => {
    const wid = "m::t::iter1";
    const events = [
      env({ event_type: "MissionDispatched", prompt: "x", parent_mission_id: null, priority: 0, language: "de" }),
      env(
        {
          event_type: "CriticVerdictReady",
          worker_id: wid,
          verdict: "revise",
          summary: "The review document is missing.",
          confidence: 0.8,
          axes: { correctness: { status: "fail", evidence: ["log:no file"] } },
          iteration: 1,
        },
        { source_actor: "critic", worker_id: wid },
      ),
      env(
        { event_type: "WorkerCorrectionRequired", worker_id: wid, correction_instruction: "Write the document.", iteration: 1, next_model: "opus" },
        { source_actor: "critic", worker_id: wid },
      ),
      env({
        event_type: "MissionFailed",
        reason: "review_time_budget_exhausted",
        error_class: null,
        last_state: "CRITIQUING",
        partial_artifacts: ["C:/out/diff.iter1.patch"],
      }),
    ];
    const out = deriveOutcome(events, "de");
    expect(out.reason).toBe("review_time_budget_exhausted");
    expect(out.lastVerdict?.verdict).toBe("revise");
    expect(out.lastCorrection?.next_model).toBe("opus");
    expect(out.revisions).toBe(1);
    expect(out.partial_artifacts).toEqual(["C:/out/diff.iter1.patch"]);
  });

  it("picks the summary in the mission's language on approval", () => {
    const events = [
      env({
        event_type: "MissionApproved",
        result_uri: "file:///out",
        tokens_used: 10,
        cost_usd: 0.02,
        wall_ms: 5000,
        summary_de: "Fertig.", // i18n-allow: German runtime-output fixture
        summary_en: "Done.",
      }),
    ];
    expect(deriveOutcome(events, "de").summary).toBe("Fertig."); // i18n-allow
    expect(deriveOutcome(events, "en").summary).toBe("Done.");
    expect(deriveOutcome(events, "en").cost_usd).toBe(0.02);
  });

  it("has no terminal for a run still in flight", () => {
    const out = deriveOutcome(quotaFailure().slice(0, 4), "en");
    expect(out.terminal).toBeNull();
    expect(out.workers[0].ended_ms).toBeNull();
  });
});

describe("buildStory", () => {
  it("drops state-machine chatter and keeps the narrative in order", () => {
    const story = buildStory(quotaFailure());
    expect(story.map((s) => s.kind)).toEqual([
      "dispatched",
      "plan",
      "spawn",
      "narration",
      "killed",
      "failed",
    ]);
    const killed = story.find((s) => s.kind === "killed")!;
    expect(killed.meta.reason).toBe("budget");
    expect(killed.tone).toBe("error");
    expect(story.at(-1)!.meta.provider).toBe("claude");
  });

  it("tells a tool note apart from the worker's own commentary", () => {
    const wid = "m::t::iter0";
    const note = (text: string) =>
      env(
        { event_type: "WorkerProgress", worker_id: wid, pct: null, note: text, stalled: false, tokens_so_far: 0, cost_so_far: 0 },
        { worker_id: wid, source_actor: "worker" },
      );
    const story = buildStory([
      note("Grep: jarvis/ui/web/frontend/src"),
      note("Ich schaue mir zuerst den Arbeitsbereich an."), // i18n-allow: worker narration fixture
      note("PowerShell: git diff AGENTS.md"),
    ]);
    expect(story.map((s) => [s.kind, s.tool])).toEqual([
      ["tool", "Grep"],
      ["narration", null],
      ["tool", "PowerShell"],
    ]);
    expect(story[0].text).toBe("jarvis/ui/web/frontend/src");
    expect(story[0].iteration).toBe(0);
  });
});

describe("groupStory", () => {
  it("folds a run of tool calls into one block and drops repeated kill text", () => {
    const wid = "m::t::iter0";
    const note = (text: string) =>
      env(
        { event_type: "WorkerProgress", worker_id: wid, pct: null, note: text, stalled: false, tokens_so_far: 0, cost_so_far: 0 },
        { worker_id: wid, source_actor: "worker" },
      );
    const blocks = groupStory(
      buildStory([
        note("Grep: a"),
        note("Read: b"),
        note("Grep: c"),
        note("Limit reached."),
        env({ event_type: "WorkerKilled", worker_id: wid, reason: "budget", error_detail: "Limit reached." }, { worker_id: wid }),
        env({ event_type: "MissionFailed", reason: "task_error", error_class: null, last_state: "RUNNING", partial_artifacts: [], error_detail: "Limit reached." }),
      ]),
    );
    expect(blocks.map((b) => (b.kind === "actions" ? `actions:${b.entries.length}` : b.entry.kind))).toEqual([
      "actions:3",
      "narration",
      "killed",
      "failed",
    ]);
    const actions = blocks[0];
    if (actions.kind !== "actions") throw new Error("expected an actions block");
    expect(actions.counts).toEqual([
      { tool: "Grep", n: 2 },
      { tool: "Read", n: 1 },
    ]);
    // The kill and the terminal event repeat the note verbatim — the fold
    // blanks their text so the same sentence is not printed three times.
    const killed = blocks[2];
    const failed = blocks[3];
    expect(killed.kind === "entry" && killed.entry.text).toBe("");
    expect(failed.kind === "entry" && failed.entry.text).toBe("");
  });
});

describe("classifyNote", () => {
  it("does not mistake a sentence with a colon for a tool", () => {
    expect(classifyNote("Note: the build passed").kind).toBe("narration");
    expect(classifyNote("Read: C:/x.py").kind).toBe("tool");
    expect(classifyNote("WebFetch: https://example.org").tool).toBe("WebFetch");
  });
});

describe("splitReason / missionIdFromTraceId", () => {
  it("splits a reason with a detail tail", () => {
    expect(splitReason("decompose_failed: no brain")).toEqual({ head: "decompose_failed", tail: "no brain" });
    expect(splitReason("task_error")).toEqual({ head: "task_error", tail: null });
    expect(splitReason(null)).toEqual({ head: null, tail: null });
  });

  it("re-dashes a stripped uuid and leaves anything else alone", () => {
    expect(missionIdFromTraceId("019fecaa5a927360a784829281639cf6")).toBe(
      "019fecaa-5a92-7360-a784-829281639cf6",
    );
    expect(missionIdFromTraceId("019fecaa-5a92-7360-a784-829281639cf6")).toBe(
      "019fecaa-5a92-7360-a784-829281639cf6",
    );
    expect(missionIdFromTraceId("abc")).toBe("abc");
  });
});

import { describe, expect, it } from "vitest";
import {
  FAILED_TTL_MS,
  MAX_COMMAND_ENTRIES,
  pruneCommandActivity,
  reduceCommandActivity,
  RUNNING_TTL_MS,
  SETTLED_TTL_MS,
  type CommandActivityEntry,
} from "./commandActivity";

const T0 = 1_000_000;

function proposed(
  command: string,
  impact?: { level?: string; commands?: string },
) {
  return {
    tool_name: "run_shell",
    args: { command },
    risk_tier: "monitor",
    ...(impact ? { impact } : {}),
  };
}

function feed(
  events: Array<[string, string, unknown, number?]>,
): CommandActivityEntry[] {
  let entries: CommandActivityEntry[] = [];
  for (const [name, traceId, payload, ts] of events) {
    entries =
      reduceCommandActivity(entries, name, traceId, payload, ts ?? T0) ??
      entries;
  }
  return entries;
}

describe("reduceCommandActivity", () => {
  it("surfaces a proposed run_shell call with its impact badge", () => {
    const entries = feed([
      [
        "ActionProposed",
        "t1",
        proposed("rm -rf build", {
          level: "destructive",
          commands: "rm",
        }),
      ],
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      id: "t1",
      command: "rm -rf build",
      level: "destructive",
      words: "rm",
      status: "running",
    });
  });

  it("ignores every other tool", () => {
    const result = reduceCommandActivity(
      [],
      "ActionProposed",
      "t1",
      { tool_name: "open_app", args: { app: "notepad" } },
      T0,
    );
    expect(result).toBeNull();
  });

  it("pairs Executed with its Proposed via trace id", () => {
    const entries = feed([
      ["ActionProposed", "t1", proposed("ls", { level: "read" })],
      [
        "ActionExecuted",
        "t1",
        { tool_name: "run_shell", success: true, duration_ms: 42 },
        T0 + 500,
      ],
    ]);
    expect(entries[0]).toMatchObject({
      status: "done",
      durationMs: 42,
      settledTs: T0 + 500,
    });
  });

  it("marks a failed execution with its error detail", () => {
    const entries = feed([
      ["ActionProposed", "t1", proposed("definitely-missing")],
      [
        "ActionExecuted",
        "t1",
        { tool_name: "run_shell", success: false, error: "exit 127" },
        T0 + 300,
      ],
    ]);
    expect(entries[0]).toMatchObject({ status: "failed", detail: "exit 127" });
  });

  it("shows a blacklist denial even without a Proposed", () => {
    // A blacklist match raises BEFORE ActionProposed — the denial is the
    // only event of that run and must still become a visible card.
    const entries = feed([
      [
        "ActionDenied",
        "t9",
        { tool_name: "run_shell", reason: "blacklist: rm -rf /*" },
      ],
    ]);
    expect(entries[0]).toMatchObject({
      status: "blocked",
      detail: "blacklist: rm -rf /*",
      level: "destructive",
    });
  });

  it("missing impact classifies as unknown, never crashes", () => {
    const entries = feed([
      ["ActionProposed", "t1", proposed("some-binary --x")],
    ]);
    expect(entries[0].level).toBe("unknown");
  });

  it("dedupes a replayed Proposed for the same trace", () => {
    const p = proposed("ls", { level: "read" });
    const entries = feed([
      ["ActionProposed", "t1", p],
      ["ActionProposed", "t1", p],
    ]);
    expect(entries).toHaveLength(1);
  });

  it("caps the list and keeps running entries alive", () => {
    const events: Array<[string, string, unknown, number?]> = [];
    for (let i = 0; i < MAX_COMMAND_ENTRIES + 5; i++) {
      events.push(["ActionProposed", `t${i}`, proposed("ls")]);
      // Settle every entry except the very first one.
      if (i > 0) {
        events.push([
          "ActionExecuted",
          `t${i}`,
          { tool_name: "run_shell", success: true },
        ]);
      }
    }
    const entries = feed(events);
    expect(entries.length).toBeLessThanOrEqual(MAX_COMMAND_ENTRIES);
    expect(entries.some((e) => e.id === "t0")).toBe(true); // still running
  });
});

describe("pruneCommandActivity", () => {
  it("expires done entries after their TTL, failures later", () => {
    const entries = feed([
      ["ActionProposed", "ok", proposed("ls")],
      ["ActionExecuted", "ok", { tool_name: "run_shell", success: true }, T0],
      ["ActionProposed", "bad", proposed("rm x")],
      [
        "ActionExecuted",
        "bad",
        { tool_name: "run_shell", success: false, error: "exit 1" },
        T0,
      ],
    ]);
    const afterDoneTtl = pruneCommandActivity(entries, T0 + SETTLED_TTL_MS + 1);
    expect(afterDoneTtl?.map((e) => e.id)).toEqual(["bad"]);
    const afterFailTtl = pruneCommandActivity(
      afterDoneTtl ?? [],
      T0 + FAILED_TTL_MS + 1,
    );
    expect(afterFailTtl).toEqual([]);
  });

  it("expires a running entry whose Executed never arrived", () => {
    const entries = feed([["ActionProposed", "t1", proposed("ls")]]);
    expect(pruneCommandActivity(entries, T0 + RUNNING_TTL_MS - 1)).toBeNull();
    expect(pruneCommandActivity(entries, T0 + RUNNING_TTL_MS + 1)).toEqual([]);
  });

  it("returns null when nothing changed", () => {
    const entries = feed([["ActionProposed", "t1", proposed("ls")]]);
    expect(pruneCommandActivity(entries, T0 + 10)).toBeNull();
  });
});

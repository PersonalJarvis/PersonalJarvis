import { describe, expect, it } from "vitest";
import type { TerminalRecap } from "@/lib/agenticIdeApi";
import { sameRows } from "./paneRows";

function recap(name: string, activity = "waiting"): TerminalRecap {
  return {
    key: name.toLowerCase(),
    name,
    status: "live",
    recap: "Waiting for work",
    recap_detail: "The terminal is ready.",
    activity: activity as TerminalRecap["activity"],
    activity_since: 10,
    worked: true,
  };
}

describe("recap poll equality", () => {
  it("recognizes a repeated response", () => {
    const current = { T1: recap("T1") };
    expect(sameRows(current, { T1: { ...current.T1 } })).toBe(true);
  });

  it("detects an activity transition", () => {
    expect(sameRows({ T1: recap("T1") }, { T1: recap("T1", "working") })).toBe(false);
  });

  it("detects a changed optional field even when both values are undefined", () => {
    const before: Record<string, TerminalRecap> = { T1: { ...recap("T1"), note: undefined } };
    const after: Record<string, TerminalRecap> = { T1: { ...recap("T1"), writer: undefined } };
    expect(sameRows(before, after)).toBe(false);
  });
});

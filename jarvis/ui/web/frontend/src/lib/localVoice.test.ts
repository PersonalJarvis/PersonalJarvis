import { describe, expect, it } from "vitest";

import {
  latencyFigure,
  localVoiceIsBusy,
  percentOf,
  stageKey,
  type LocalVoiceStatus,
} from "@/lib/localVoice";

describe("localVoice helpers", () => {
  it("formats a latency range with the UI language's decimal mark", () => {
    const measured = { low_s: 0.75, high_s: 0.9, basis: "measured" as const };
    expect(latencyFigure(measured, "en")).toBe("0.75–0.9");
    expect(latencyFigure(measured, "de")).toBe("0,75–0,9");
    expect(latencyFigure({ low_s: 2, high_s: 2, basis: "estimate" }, "en")).toBe("2");
  });

  it("has no figure when only the self-test can tell", () => {
    expect(latencyFigure({ low_s: null, high_s: null, basis: "selftest" }, "en")).toBeNull();
  });

  it("folds the worker's load stages into what a person recognises", () => {
    expect(stageKey("packages")).toBe("packages");
    expect(stageKey("tts:de")).toBe("voice");
    expect(stageKey("stt")).toBe("speech");
    expect(stageKey("process")).toBe("process");
    expect(stageKey("something-new")).toBe("");
  });

  it("clamps progress", () => {
    expect(percentOf(0.423)).toBe(42);
    expect(percentOf(2)).toBe(100);
    expect(percentOf(Number.NaN)).toBe(0);
  });

  it("polls only while something moves on its own", () => {
    const base = {
      phase: "stopped",
      setup: { running: false },
      selftest_running: false,
    } as unknown as LocalVoiceStatus;
    expect(localVoiceIsBusy(null)).toBe(false);
    expect(localVoiceIsBusy(base)).toBe(false);
    expect(localVoiceIsBusy({ ...base, phase: "starting" })).toBe(true);
    expect(localVoiceIsBusy({ ...base, selftest_running: true })).toBe(true);
  });
});

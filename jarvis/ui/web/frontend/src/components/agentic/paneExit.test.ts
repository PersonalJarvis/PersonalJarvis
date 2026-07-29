import { describe, expect, it } from "vitest";
import { describeExit, explainExit } from "./paneExit";

describe("paneExit", () => {
  it("never shows the raw unsigned Windows code the bug report carried", () => {
    // The backend re-signs it before it gets here; this pins that the pane has
    // no path back to the ten-digit form the user was shown.
    expect(describeExit("Codex", -1)).not.toContain("4294967295");
    expect(describeExit("Codex", -1)).toContain("stopped unexpectedly");
    expect(describeExit("Codex", -1)).toContain("Restart");
  });

  it("calls a clean stop a clean stop", () => {
    expect(describeExit("Claude Code", 0)).toBe("[Claude Code stopped]");
    expect(explainExit(0)).toBe("stopped");
  });

  it("names the Windows statuses whose meaning is unambiguous", () => {
    expect(explainExit(-1073741510)).toContain("Ctrl-C");
    expect(explainExit(-1073741510)).toContain("0xC000013A");
    expect(explainExit(-1073741819)).toContain("access violation");
  });

  it("keeps an unrecognised code visible instead of guessing at it", () => {
    // The number is the part worth searching for, so it stays — what must not
    // happen is a confident explanation that is wrong.
    expect(explainExit(137)).toContain("137");
    expect(explainExit(137)).toContain("stopped unexpectedly");
  });

  it("wraps the explanation in the pane's own agent name", () => {
    expect(describeExit("Codex", 3)).toBe(
      "[Codex stopped unexpectedly (exit code 3) — use Restart to bring it back]",
    );
  });
});

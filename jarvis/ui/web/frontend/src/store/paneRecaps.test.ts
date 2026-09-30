import { describe, expect, it } from "vitest";
import { paneTitleFrom } from "./paneRecaps";

const row = { recap: "", last_prompt: "", display_name: "Claude Code", name: "T1" };

describe("paneTitleFrom", () => {
  it("prefers a title the model or the user wrote", () => {
    expect(paneTitleFrom({ recap: "Release pipeline — green CI", source: "model" }, row)).toBe("Release pipeline — green CI");
    expect(paneTitleFrom({ recap: "Demo branch", source: "user" }, row)).toBe("Demo branch");
  });

  it("ignores the screen-derived line and falls back to the pane's topic", () => {
    const asked = { ...row, last_prompt: "Fix the failing login test" };
    expect(paneTitleFrom({ recap: "Claude Code — running since 17:22", source: "heuristic" }, asked)).toBe(
      "Fix the failing login test",
    );
  });

  it("answers empty for a pane nobody asked anything, so the call-sign shows", () => {
    expect(paneTitleFrom(undefined, row)).toBe("");
    expect(paneTitleFrom(undefined, undefined)).toBe("");
  });
});

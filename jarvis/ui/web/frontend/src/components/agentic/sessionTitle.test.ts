/**
 * A session row's label says what the conversation is for — the pane's title
 * first, the prompt's opening next, and the CLI's name only when the pane has
 * been asked nothing at all.
 */
import { describe, expect, it } from "vitest";

import { promptOpening, sessionTitle } from "@/components/agentic/sessionTitle";

const BARE = { recap: "", last_prompt: "", display_name: "Claude Code", name: "T1" };

describe("sessionTitle", () => {
  it("is the pane's recap when the header has one", () => {
    expect(sessionTitle({ ...BARE, recap: "Fixing the login test", last_prompt: "Fix it" })).toBe(
      "Fixing the login test",
    );
  });

  it("falls back to the opening line of the last prompt", () => {
    const brief = "Refactor the parser so it streams.\n\nStart with jarvis/core/config.py.";
    expect(sessionTitle({ ...BARE, last_prompt: brief })).toBe("Refactor the parser so it streams.");
  });

  it("names the CLI only for a pane that was asked nothing", () => {
    expect(sessionTitle(BARE)).toBe("Claude Code");
    expect(sessionTitle({ ...BARE, recap: "   " })).toBe("Claude Code");
    expect(sessionTitle({ ...BARE, display_name: "" })).toBe("T1");
  });
});

describe("promptOpening", () => {
  it("skips blank leading lines and collapses whitespace", () => {
    expect(promptOpening("\r\n  \r\n  Write   the\ttests first  \r\nthen ship")).toBe(
      "Write the tests first",
    );
  });

  it("is empty for an empty prompt", () => {
    expect(promptOpening("")).toBe("");
    expect(promptOpening(" \n ")).toBe("");
  });

  it("skips the section heading a composed brief opens with", () => {
    expect(promptOpening("## Task\nFix the failing login test\n\n## Context\nmore")).toBe(
      "Fix the failing login test",
    );
    expect(promptOpening("**Context**\n\nGoal:\nShip the parser")).toBe("Ship the parser");
    expect(promptOpening("Task: fix the   login test")).toBe("fix the login test");
    // A brief that is nothing but labels still answers with its first one.
    expect(promptOpening("## Task\n## Context")).toBe("Task");
  });
});

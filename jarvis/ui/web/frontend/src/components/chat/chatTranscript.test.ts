/**
 * Reading a terminal screen as a conversation.
 *
 * The properties pinned here are the ones that decide whether a reader can
 * trust what they see: nothing is invented, nothing recognisable is dropped,
 * and — the important one — an unrecognised line still reaches the screen.
 */
import { describe, expect, test } from "vitest";

import { readTranscript, subagentsOf } from "@/components/chat/chatTranscript";

describe("readTranscript", () => {
  test("drops the CLI's own banner", () => {
    // Every conversation would otherwise open on somebody else's logo.
    const events = readTranscript([
      "Claude Code v2.1.222",
      "Opus 5 (1M context) with high effort · Claude Max",
      // Written with forward slashes on purpose: a Windows path in a TS
      // string literal needs doubled backslashes, and a fixture that
      // silently loses them tests the wrong input.
      "~/Desktop/Personal Jarvis",
      "2 MCP servers need authentication · run /mcp",
      "",
      "Hello there.",
    ]);

    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("assistant");
    expect(events[0].text).toBe("Hello there.");
  });

  test("separates what the user said from what the agent answered", () => {
    const events = readTranscript(["> fix the wake path", "", "Looking at it now."]);

    expect(events.map((e) => e.kind)).toEqual(["user", "assistant"]);
    expect(events[0].text).toBe("fix the wake path");
  });

  test("an empty prompt caret is the input box, not something said", () => {
    expect(readTranscript(["> "])).toEqual([]);
  });

  test("keeps a line it does not recognise", () => {
    // The fail-safe direction. A classifier that dropped the unfamiliar would
    // hide exactly the output that matters the day a CLI changes something.
    const events = readTranscript(["!!! something entirely new !!!"]);

    expect(events).toHaveLength(1);
    expect(events[0].text).toContain("something entirely new");
  });

  test("folds a wrapped paragraph into one block but keeps turns apart", () => {
    const events = readTranscript([
      "The wake word never fired because",
      "the energy gate rejected it.",
      "",
      "> thanks",
    ]);

    expect(events).toHaveLength(2);
    expect(events[0].text).toBe(
      "The wake word never fired because\nthe energy gate rejected it.",
    );
    expect(events[1].kind).toBe("user");
  });

  test("recognises steps and names them for a collapsed row", () => {
    const events = readTranscript(["● Read jarvis/core/config.py (420 lines)"]);

    expect(events[0].kind).toBe("step");
    expect(events[0].label).toBe("Read jarvis/core/config.py");
  });

  test("tells a sub-agent apart from an ordinary step", () => {
    const events = readTranscript([
      "● Read config.py",
      "● Spawning agent: audit the wake path",
    ]);

    expect(events.map((e) => e.kind)).toEqual(["step", "subagent"]);
    expect(subagentsOf(events)).toHaveLength(1);
  });

  test("never reorders and never invents a block", () => {
    const lines = ["> one", "", "two", "", "● three"];
    const events = readTranscript(lines);

    expect(events.map((e) => e.text)).toEqual(["one", "two", "three"]);
  });
});

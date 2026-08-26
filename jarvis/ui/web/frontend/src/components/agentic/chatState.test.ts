import { describe, expect, it } from "vitest";
import type { TerminalRecap, TerminalState } from "@/lib/agenticIdeApi";
import {
  chatTerminalIdentity,
  initialChatOrder,
  orderChatTerminals,
  reconcileChatOrder,
  sameRows,
  swapChatOrder,
} from "./chatState";

function terminal(
  key: string,
  column: number,
  historyId = `${key}-lifetime`,
  startedAt = column + 1,
): TerminalState {
  return {
    key,
    name: key.toUpperCase(),
    agent: "codex",
    display_name: "Codex",
    index: column,
    column,
    slot: 0,
    status: "live",
    exit_code: null,
    error: "",
    started_at: startedAt,
    history_id: historyId,
    last_output_at: null,
    idle_seconds: null,
    prompts_sent: 0,
    last_prompt: "",
    lines_captured: 0,
  };
}

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

describe("chat session ordering", () => {
  it("appends a split pane even when the grid inserts it in the middle", () => {
    const previous = ["t1-lifetime", "t2-lifetime", "t3-lifetime"];
    const gridOrderAfterSplit = [
      terminal("t1", 0),
      terminal("t4", 1),
      terminal("t2", 2),
      terminal("t3", 3),
    ];

    expect(reconcileChatOrder(previous, gridOrderAfterSplit)).toEqual([
      "t1-lifetime",
      "t2-lifetime",
      "t3-lifetime",
      "t4-lifetime",
    ]);
  });

  it("keeps surviving panes stable while removing closed panes", () => {
    expect(
      reconcileChatOrder(
        ["t1-lifetime", "t2-lifetime", "t3-lifetime"],
        [terminal("t3", 0), terminal("t1", 1)],
      ),
    ).toEqual(["t1-lifetime", "t3-lifetime"]);
  });

  it("orders terminal objects independently of their grid coordinates", () => {
    const terminals = [terminal("t1", 0), terminal("t3", 1), terminal("t2", 2)];
    expect(
      orderChatTerminals(terminals, ["t1-lifetime", "t2-lifetime", "t3-lifetime"]).map(
        (item) => item.key,
      ),
    ).toEqual(["t1", "t2", "t3"]);
  });

  it("sorts an unseen workspace by pane start time instead of grid position", () => {
    const terminals = [
      terminal("t3", 0, "third", 30),
      terminal("t1", 1, "first", 10),
      terminal("t2", 2, "second", 20),
    ];

    expect(initialChatOrder(terminals)).toEqual(["first", "second", "third"]);
  });

  it("treats a reused call-sign as a new pane lifetime", () => {
    const replacement = terminal("t1", 0, "new-t1", 50);

    expect(reconcileChatOrder(["old-t1", "t2-lifetime"], [replacement, terminal("t2", 1)])).toEqual(
      ["t2-lifetime", "new-t1"],
    );
    expect(chatTerminalIdentity(replacement)).toBe("new-t1");
  });

  it("exchanges exactly the two sessions that were dragged together", () => {
    expect(swapChatOrder(["first", "second", "third", "fourth"], "first", "third"))
      .toEqual(["third", "second", "first", "fourth"]);
  });

  it("keeps the existing order object when either dragged session is gone", () => {
    const order = ["first", "second"];
    expect(swapChatOrder(order, "first", "gone")).toBe(order);
    expect(swapChatOrder(order, "first", "first")).toBe(order);
  });
});

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

import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/en.json";
import type { ToolBlock, TurnBlock, TurnStatus } from "@/components/agentchat/reduce";
import { buildThreadRows, liveItem, thoughtPreview, type ThreadRow, type WorkGroup } from "./threadWork";

type Dict = Record<string, unknown>;
const t = (key: string): string => {
  let cur: unknown = en as Dict;
  for (const part of key.split(".")) cur = cur && typeof cur === "object" ? (cur as Dict)[part] : undefined;
  return typeof cur === "string" ? cur : key;
};

const tool = (id: string, over: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool", callId: id, name: "Bash", input: { command: "ls" }, output: "a.ts", isError: false,
  durationMs: 300, approval: null, startedMs: 0, ...over,
});
const read = (id: string, path: string) => tool(id, { name: "Read", input: { file_path: path }, output: "x" });
const text = (id: string, body: string): TurnBlock => ({ kind: "text", id, text: body });
const thought = (id: string, body: string, live = false): TurnBlock => ({ kind: "reasoning", id, text: body, durationMs: live ? null : 1200, live, startedMs: 0 });

const rows = (blocks: TurnBlock[], status: TurnStatus = "done"): ThreadRow[] => buildThreadRows(blocks, { t, lang: "en", status });
const groups = (list: ThreadRow[]) => list.filter((row): row is WorkGroup => row.kind === "work");

describe("thread work groups", () => {
  it("folds thinking and calls up to the next prose into one group", () => {
    const list = rows([
      thought("r1", "Looking at the repo"),
      tool("c1"),
      thought("r2", "Now the tests"),
      tool("c2", { input: { command: "npm test" } }),
      read("f1", "src/a.ts"),
      text("n1", "Tests pass."),
    ]);
    expect(list.map((row) => row.kind)).toEqual(["work", "text"]);
    const [group] = groups(list);
    expect(group.items.map((item) => item.kind)).toEqual(["thought", "call", "thought", "call", "call"]);
    expect(group.summary).toBe("Ran 2 commands and read 1 file");
    expect(group.live).toBe(false);
  });

  it("counts what it leaves out instead of dropping it", () => {
    const [group] = groups(rows([
      tool("c1"),
      read("f1", "a.ts"),
      tool("l1", { name: "LS", input: { path: "src" }, output: "x" }),
      tool("e1", { name: "Edit", input: { file_path: "b.ts", old_string: "a", new_string: "b" }, output: "ok" }),
    ]));
    expect(group.summary).toBe("Ran 1 command, edited 1 file, and performed 2 other actions");
  });

  it("names a lone call by itself and a lone thought as a thought", () => {
    expect(groups(rows([tool("c1", { input: { command: "npm run build" } })]))[0].summary).toBe("npm run build");
    const [solo] = groups(rows([thought("r1", "Plan the change")]));
    expect(solo.summary).toBe("Thought");
    expect(solo.sole).toBe("thought");
  });

  it("keeps the running turn's newest group live and points at its running call", () => {
    const list = rows([text("n1", "Starting."), tool("c1"), tool("c2", { input: { command: "npm test" }, output: null, durationMs: null })], "running");
    const [group] = groups(list);
    expect(group.live).toBe(true);
    const item = liveItem(group);
    expect(item.kind === "call" && item.call.text).toBe("npm test");
  });

  it("is not live once prose follows the group", () => {
    const [group] = groups(rows([tool("c1"), text("n1", "Done looking.")], "running"));
    expect(group.live).toBe(false);
  });

  it("drops wordless thinking unless it is the turn's live thought", () => {
    expect(rows([thought("r1", "")])).toEqual([]);
    const [group] = groups(rows([thought("r1", "", true)], "running"));
    expect(group.items).toHaveLength(1);
  });

  it("breaks a group at a card that waits for the person", () => {
    const list = rows([
      tool("c1"),
      tool("q1", { name: "AskUserQuestion", input: { questions: [] }, output: null, question: { closed: false } as ToolBlock["question"] }),
      tool("c2"),
    ]);
    expect(list.map((row) => row.kind)).toEqual(["work", "pending", "work"]);
  });
});

describe("thought preview", () => {
  it("reads a Markdown thought as one plain line", () => {
    expect(thoughtPreview("**Inspecting the repo**\n\nI need to read `src/app.ts` and [the docs](https://x.y).\n- one\n- two"))
      .toBe("Inspecting the repo I need to read src/app.ts and the docs. one two");
  });
});

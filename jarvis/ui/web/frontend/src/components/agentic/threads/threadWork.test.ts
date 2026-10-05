import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/en.json";
import type { ToolBlock, TurnBlock, TurnStatus } from "@/components/agentchat/reduce";
import { buildThreadRows, type ThreadRow, type WorkGroup } from "./threadWork";

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
  it("reads every worded thought as a paragraph that splits the work around it", () => {
    const list = rows([
      thought("r1", "Looking at the repo"),
      tool("c1"),
      tool("c2", { input: { command: "npm test" } }),
      thought("r2", "Now the source"),
      read("f1", "src/a.ts"),
      tool("c3", { input: { command: "npm run build" } }),
      text("n1", "Tests pass."),
    ]);
    expect(list.map((row) => row.kind)).toEqual(["thought", "work", "thought", "work", "text"]);
    const [commands, mixed] = groups(list);
    expect(commands.items.map((item) => item.kind)).toEqual(["call", "call"]);
    expect(commands.summary).toBe("Ran commands");
    expect(mixed.items).toHaveLength(2);
    expect(mixed.sole).toBeNull();
    expect(commands.live).toBe(false);
  });

  it("keeps every kind of call between two paragraphs in one group", () => {
    const list = groups(rows([tool("c1"), read("f1", "a.ts"), tool("c2")]));
    expect(list).toHaveLength(1);
    expect(list[0].items).toHaveLength(3);
  });

  it("names a lone call by itself", () => {
    expect(groups(rows([tool("c1", { input: { command: "npm run build" } })]))[0].summary).toBe("npm run build");
  });

  it("keeps the running turn's newest group live", () => {
    const list = rows([text("n1", "Starting."), tool("c1"), tool("c2", { input: { command: "npm test" }, output: null, durationMs: null })], "running");
    const [group] = groups(list);
    expect(group.live).toBe(true);
  });

  it("is not live once prose follows the group", () => {
    const [group] = groups(rows([tool("c1"), text("n1", "Done looking.")], "running"));
    expect(group.live).toBe(false);
  });

  it("marks the thought the running turn is still writing as live", () => {
    const list = rows([tool("c1"), thought("r1", "Weighing it", true)], "running");
    expect(list.map((row) => row.kind)).toEqual(["work", "thought"]);
    expect(list[1].kind === "thought" && list[1].live).toBe(true);
  });

  it("drops wordless thinking unless it is the turn's live thought", () => {
    expect(rows([thought("r1", "")])).toEqual([]);
    expect(rows([tool("c1"), thought("r1", ""), tool("c2")]).map((row) => row.kind)).toEqual(["work"]);
    const [group] = groups(rows([thought("r1", "", true)], "running"));
    expect(group.sole).toBe("thought");
    expect(group.summary).toBe("Thinking");
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

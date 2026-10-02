import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import { reduceThinkingSteps, type ThinkingStep } from "@/lib/thinkingSteps";
import type { ToolBlock, TurnBlock } from "./reduce";
import { buildTimeline, headerLabel, previewOutput, shortPath, timelineMarkdown, traceDuration, type TimelineOptions } from "./traceEntries";
import { stepsToBlocks } from "./VoiceWorkTrace";

type Dict = Record<string, unknown>;
function translator(dict: Dict) {
  return (key: string): string => {
    let cur: unknown = dict;
    for (const part of key.split(".")) cur = cur && typeof cur === "object" ? (cur as Dict)[part] : undefined;
    return typeof cur === "string" ? cur : key;
  };
}
const t = translator(en as Dict);
const opts = (over: Partial<TimelineOptions> = {}): TimelineOptions => ({ t, lang: "en", status: "done", live: false, ...over });

const tool = (id: string, over: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool", callId: id, name: "Bash", input: { command: "ls" }, output: "a.ts", isError: false,
  durationMs: 300, approval: null, startedMs: 0, ...over,
});
const text = (id: string, body: string): TurnBlock => ({ kind: "text", id, text: body });
const thinking = (id: string, body: string, durationMs = 1000): TurnBlock => ({ kind: "reasoning", id, text: body, durationMs, live: false, startedMs: 0 });

function replay(events: Array<[string, Record<string, unknown>]>): ThinkingStep[] {
  let steps: ThinkingStep[] = [];
  events.forEach(([name, payload], i) => {
    steps = reduceThinkingSteps(steps, name, payload, 1000 + i * 10) ?? steps;
  });
  return steps.map((s) => (s.status === "active" ? { ...s, status: "done" } : s));
}

describe("trace timeline", () => {
  it("merges reads, listings and searches into one Codex-style Explored entry", () => {
    const line = buildTimeline([
      tool("r1", { name: "Read", input: { file_path: "C:/repo/src/app/main.ts" }, output: "1→a" }),
      tool("r2", { name: "Read", input: { file_path: "README.md" }, output: "x" }),
      tool("l", { name: "LS", input: { path: "src" }, output: "a\nb" }),
      tool("g", { name: "Grep", input: { pattern: "TODO", path: "src/lib" }, output: "src/lib/a.ts:3" }),
      tool("r3", { name: "Read", input: { file_path: "package.json" }, output: "{}" }),
    ], opts());
    expect(line.entries).toHaveLength(1);
    const explore = line.entries[0];
    expect(explore.kind).toBe("explore");
    if (explore.kind !== "explore") return;
    expect(explore.lines).toEqual([
      { verb: "read", targets: ["…/src/app/main.ts", "README.md"] },
      { verb: "list", targets: ["src"] },
      { verb: "search", targets: ["TODO in src/lib"] },
      { verb: "read", targets: ["package.json"] },
    ]);
    expect(explore.durationMs).toBe(1500);
    expect(line.summary).toBe("Read 3 files, listed a folder, and ran a search");
  });

  it("shows a command with the first lines it printed", () => {
    const line = buildTimeline([
      tool("c", { input: { command: "npm test\n--watch=false" }, output: "PASS a\nPASS b\n\nPASS c\nPASS d\nPASS e" }),
    ], opts());
    expect(line.entries[0]).toMatchObject({
      kind: "command", command: "npm test", status: "done",
      output: { lines: ["PASS a", "PASS b", "PASS c"], more: 2 },
    });
    expect(previewOutput("")).toBeNull();
  });

  it("tells a wrapped command by what it printed", () => {
    const line = buildTimeline([
      tool("j", { name: "mcp__jarvis__cli_jarvisctl", input: { command: "jarvisctl outputs list" },
        output: JSON.stringify({ exit_code: 0, stdout: "a.md\nb.md\n", stderr: "", duration_ms: 12 }) }),
    ], opts());
    expect(line.entries[0]).toMatchObject({ kind: "tool", result: "a.md" });
  });

  it("sizes an edit and names a created file", () => {
    const line = buildTimeline([
      tool("e", { name: "Edit", input: { file_path: "src/app.ts", old_string: "a\nb", new_string: "c" }, output: "ok" }),
      tool("w", { name: "Write", input: { file_path: "src/new.ts", content: "x\ny" }, output: "ok" }),
    ], opts());
    expect(line.entries[0]).toMatchObject({ kind: "edit", verb: "edit", path: "src/app.ts", added: 1, removed: 2 });
    expect(line.entries[1]).toMatchObject({ kind: "edit", verb: "write", path: "src/new.ts" });
    expect(line.summary).toBe("Edited a file and created a file");
  });

  it("keeps the model's words as prose and drops wordless thinking and plumbing", () => {
    const line = buildTimeline([
      thinking("r1", "", 820),
      text("n", "The repo has 85 stars. I'll fetch the timestamps."),
      tool("s", { name: "ToolSearch", input: { query: "select:mcp__jarvis__create_artifact" }, output: "[]" }),
      tool("a", { name: "mcp__jarvis__create_artifact", input: { title: "GitHub Stars" }, output: "The artifact is being built." }),
      thinking("r2", "**Checking** the result."),
    ], opts());
    expect(line.entries.map((e) => e.kind)).toEqual(["thought", "tool", "thought"]);
    expect(line.entries[1]).toMatchObject({ label: "Created an artifact", detail: "GitHub Stars", result: "The artifact is being built." });
    expect(line.thinkingMs).toBe(820);
    expect(line.actionCount).toBe(1);
  });

  it("names refusals, declines, failures and interruptions in plain words", () => {
    const line = buildTimeline([
      tool("b", { output: "blacklist: <tool-declared-block>", isError: true }),
      tool("d", { output: null, approval: { approvalId: "x", summary: "", decision: "deny" } }),
      tool("f", { output: "Error: 2 tests failed\n  at run", isError: true }),
      tool("i", { output: null }),
    ], opts({ status: "cancelled" }));
    expect(line.entries.map((e) => e.kind === "command" ? [e.status, e.reason] : null)).toEqual([
      ["blocked", "a safety rule blocks this action"],
      ["declined", ""],
      ["failed", "Error: 2 tests failed"],
      ["interrupted", ""],
    ]);
    expect(line.problemCount).toBe(4);
  });

  it("leaves running calls, approvals and replies to the live rows while a turn runs", () => {
    const blocks: TurnBlock[] = [
      tool("done"),
      tool("running", { output: null }),
      tool("approve", { output: null, approval: { approvalId: "a", summary: "Delete?", decision: null } }),
      text("reply", "Halfway there."),
      { kind: "reasoning", id: "live", text: "thinking…", durationMs: null, live: true, startedMs: 0 },
    ];
    const line = buildTimeline(blocks, opts({ status: "running", live: true }));
    expect(line.entries.map((e) => e.kind)).toEqual(["command", "live", "live", "live", "live"]);
  });

  it("tells a stored Jarvis voice turn with registry names, a cut result and a refusal", () => {
    const steps = replay([
      ["ActionProposed", { tool_name: "find-app-action", args: { query: "computers" } }],
      ["ActionExecuted", { tool_name: "find-app-action", success: true, duration_ms: 4, output_preview: '{"actions": [{"action_id": "add_computer"' }],
      ["AnnouncementRequested", { kind: "preamble", text: "I'll check the computers you set up." }],
      ["ActionDenied", { tool_name: "run-app-action", reason: "blacklist: <tool-declared-block>" }],
      ["BrainTurnStarted", { provider: "grok", model: "grok-4.3" }],
      ["BrainTurnCompleted", { provider: "grok", model: "grok-4.3" }],
    ]);
    const line = buildTimeline(stepsToBlocks(steps, false, t), opts());
    expect(line.entries.map((e) => e.kind)).toEqual(["tool", "thought", "tool"]);
    expect(line.entries[0]).toMatchObject({ label: "Find App Action", detail: "computers" });
    expect(line.entries[1]).toMatchObject({ text: "I'll check the computers you set up." });
    expect(line.entries[2]).toMatchObject({ label: "Run App Action", status: "blocked", reason: "a safety rule blocks this action" });
    expect(headerLabel(line, 2400, t)).toBe("Worked for 2.4s");
  });

  it("says Thought for … when the turn only thought", () => {
    const line = buildTimeline([thinking("r", "Plan the answer.")], opts());
    expect(headerLabel(line, 3000, t)).toBe("Thought for 3.0s");
    expect(headerLabel(line, null, t)).toBe("Thought");
  });

  it("copies as Markdown in the timeline's own order", () => {
    const line = buildTimeline([
      text("n", "First the port."),
      tool("c", { durationMs: 1200, output: "8080" }),
      tool("r", { name: "Read", input: { file_path: "a.ts" }, output: "x" }),
      tool("f", { name: "Edit", input: { file_path: "b.ts", old_string: "a", new_string: "b" }, output: "boom", isError: true }),
    ], opts());
    expect(timelineMarkdown(line, headerLabel(line, 4200, t), t)).toBe([
      "**Worked for 4.2s** — Read a file, ran a command, and edited a file",
      "",
      "First the port.",
      "",
      "- Ran `ls` (1.2s)",
      "  ```",
      "  8080",
      "  ```",
      "",
      "- Explored",
      "  - Read a.ts",
      "",
      "- Edited `b.ts` (+1 −1) (0.3s)",
      "  Failed: boom",
    ].join("\n"));
  });

  it("formats durations and paths the way a trace line shows them", () => {
    expect(traceDuration(49)).toBe("49ms");
    expect(traceDuration(4200)).toBe("4.2s");
    expect(traceDuration(125_000)).toBe("2m 05s");
    expect(shortPath("C:\\Users\\me\\repo\\src\\a.ts")).toBe("…/repo/src/a.ts");
  });

  it.each([["de", de], ["es", es]] as const)("fills every %s line", (lang, dict) => {
    const tl = translator(dict as Dict);
    const line = buildTimeline([
      text("n", "Why."),
      tool("a", { name: "search_web", input: { query: "Wetter" }, output: '{"results": []}' }),
      tool("b", { isError: true, output: "boom" }),
      tool("c", { name: "Grep", input: { pattern: "x", path: "src" }, output: "y" }),
      tool("d", { output: "1\n2\n3\n4\n5" }),
    ], { t: tl, lang, status: "done", live: false });
    const all = [line.summary, headerLabel(line, 9000, tl), timelineMarkdown(line, headerLabel(line, 9000, tl), tl)].join("\n");
    expect(all).not.toMatch(/\{\w+\}|trace_report\./);
  });
});

describe("trace_report locale parity", () => {
  function keys(obj: Dict, prefix = ""): string[] {
    return Object.entries(obj).flatMap(([k, v]) => (v && typeof v === "object" ? keys(v as Dict, `${prefix}${k}.`) : [`${prefix}${k}`]));
  }
  const base = keys((en as Dict).trace_report as Dict).sort();
  it.each([["de", de], ["es", es]] as const)("%s carries exactly the English keys", (_lang, dict) => {
    expect(keys((dict as Dict).trace_report as Dict).sort()).toEqual(base);
  });
  it("never uses {name}, which the i18n layer fills with the assistant's name", () => {
    expect(JSON.stringify((en as Dict).trace_report)).not.toContain("{name}");
  });
});

import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import pt from "@/i18n/locales/pt.json";
import { reduceThinkingSteps, type ThinkingStep } from "@/lib/thinkingSteps";
import type { ToolBlock, TurnBlock } from "./reduce";
import {
  buildTimeline, commandBinary, shortPath, traceDuration, withoutCd,
  type ActivityEntry, type Call, type TimelineOptions,
} from "./traceEntries";
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

const activity = (blocks: TurnBlock[], over: Partial<TimelineOptions> = {}): ActivityEntry => {
  const entry = buildTimeline(blocks, opts(over)).entries.find((e) => e.kind === "activity");
  if (!entry || entry.kind !== "activity") throw new Error("no activity");
  return entry;
};
const only = (block: ToolBlock, over: Partial<TimelineOptions> = {}): Call => activity([block], over).calls[0];

function replay(events: Array<[string, Record<string, unknown>]>): ThinkingStep[] {
  let steps: ThinkingStep[] = [];
  events.forEach(([name, payload], i) => {
    steps = reduceThinkingSteps(steps, name, payload, 1000 + i * 10) ?? steps;
  });
  return steps.map((s) => (s.status === "active" ? { ...s, status: "done" } : s));
}

describe("trace timeline", () => {
  it("alternates the model's words with one line per stretch of calls", () => {
    const line = buildTimeline([
      text("n1", "I'll look at the blog first."),
      tool("c1"),
      tool("w1", { name: "search_web", input: { query: "openai docs" }, output: '{"results": [1,2,3]}' }),
      text("n2", "The blog lives in its own repo."),
      tool("e1", { name: "Edit", input: { file_path: "post.md", old_string: "a", new_string: "b" }, output: "ok" }),
      tool("c2", { input: { command: "npm run build" }, output: "built" }),
    ], opts());
    expect(line.entries.map((e) => e.kind)).toEqual(["prose", "activity", "prose", "activity"]);
    const [, first, , second] = line.entries as [unknown, ActivityEntry, unknown, ActivityEntry];
    expect(first.summary).toBe("Ran a command, searched the web");
    expect(second.summary).toBe("Edited a file, ran a command");
    expect(line.callCount).toBe(4);
  });

  it("names each call in its own words", () => {
    expect(only(tool("r", { name: "Read", input: { file_path: "C:/repo/src/app/main.ts" }, output: "x" })).text).toBe("Read …/src/app/main.ts");
    expect(only(tool("i", { name: "Read", input: { file_path: "shots/hero.png" }, output: "" }))).toMatchObject({ kind: "image", text: "Viewed an image", detail: "shots/hero.png" });
    expect(only(tool("g", { name: "Grep", input: { pattern: "TODO", path: "src/lib" }, output: "" })).text).toBe("Searched for TODO in src/lib");
    expect(only(tool("l", { name: "LS", input: { path: "src" }, output: "" })).text).toBe("Listed src");
    expect(only(tool("w", { name: "Write", input: { file_path: "new.ts", content: "a\nb" }, output: "ok" }))).toMatchObject({ kind: "write", text: "Created new.ts" });
    expect(only(tool("e", { name: "Edit", input: { file_path: "app.ts", old_string: "a\nb", new_string: "c" }, output: "ok" }))).toMatchObject({ text: "Edited app.ts", added: 1, removed: 2 });
  });

  it("shows what a command ran, not where, and wears the CLI vendor's logo", () => {
    const call = only(tool("c", { input: { command: 'cd "C:/Users/me/repo" && gh api repos/x --jq .stars' }, output: "85" }));
    expect(call).toMatchObject({ kind: "command", text: "gh api repos/x --jq .stars", brand: "gh" });
    expect(only(tool("p", { input: { command: "npm test" }, output: "" })).brand).toBeNull();
    expect(withoutCd("Set-Location C:\\x; git status")).toBe("git status");
    expect(commandBinary("FOO=1 npx vercel deploy")).toBe("vercel");
  });

  it("tells a plugin by its brand, however the call reached it", () => {
    const viaJarvis = only(tool("g", { name: "mcp__jarvis__gmail", input: { query: "is:unread" }, output: "[]" }));
    expect(viaJarvis).toMatchObject({ kind: "service", text: "Gmail" });
    expect(viaJarvis.brand).toBeTruthy();
    const viaClaude = only(tool("h", { name: "mcp__claude_ai_Gmail__search_threads", input: { query: "newer_than:2d" }, output: "[]" }));
    expect(viaClaude.kind).toBe("service");
    expect(viaClaude.text).not.toMatch(/claude/i);
    const own = only(tool("r", { name: "mcp__jarvis__society_routines", input: {}, output: "{}" }));
    expect(own).toMatchObject({ kind: "tool", text: "Society routines" });
    const cli = only(tool("j", { name: "mcp__jarvis__cli_jarvisctl", input: { command: "jarvisctl outputs list" }, output: "" }));
    expect(cli).toMatchObject({ kind: "command", text: "jarvisctl outputs list" });
  });

  it("keeps Jarvis's own tools in their words and a terse result", () => {
    const call = only(tool("a", { name: "mcp__jarvis__create_artifact", input: { title: "GitHub Stars" }, output: "The artifact is being built." }));
    expect(call).toMatchObject({ kind: "family", text: "Created an artifact", detail: "GitHub Stars", result: "The artifact is being built." });
    const web = only(tool("w", { name: "search_web", input: { query: "x" }, output: '{"results": [1,2]}' }));
    expect(web).toMatchObject({ kind: "web", result: "2 results" });
  });

  it("drops wordless thinking and plumbing; keeps reasoning text as quiet prose", () => {
    const line = buildTimeline([
      thinking("r1", "", 820),
      tool("s", { name: "ToolSearch", input: { query: "select:mcp__jarvis__create_artifact" }, output: "[]" }),
      thinking("r2", "**Checking** the result."),
    ], opts());
    expect(line.entries).toEqual([expect.objectContaining({ kind: "prose", tone: "reasoning", text: "**Checking** the result." })]);
    expect(line.callCount).toBe(0);
  });

  it("names refusals, declines, failures and cut-offs in plain words", () => {
    const calls = activity([
      tool("b", { output: "blacklist: <tool-declared-block>", isError: true }),
      tool("d", { output: null, approval: { approvalId: "x", summary: "", decision: "deny" } }),
      tool("f", { output: "Error: 2 tests failed\n  at run", isError: true }),
      tool("i", { output: null }),
    ], { status: "cancelled" }).calls;
    expect(calls.map((c) => [c.status, c.reason])).toEqual([
      ["blocked", "a safety rule blocks this action"],
      ["declined", ""],
      ["failed", "Error: 2 tests failed"],
      ["interrupted", ""],
    ]);
  });

  it("keeps a running call in its stretch and hands approvals, questions and replies to the live rows", () => {
    const line = buildTimeline([
      tool("done"),
      tool("running", { output: null }),
      tool("approve", { output: null, approval: { approvalId: "a", summary: "Delete?", decision: null } }),
      text("reply", "Halfway there."),
      { kind: "reasoning", id: "live", text: "thinking…", durationMs: null, live: true, startedMs: 0 },
    ], opts({ status: "running", live: true }));
    expect(line.entries.map((e) => e.kind)).toEqual(["activity", "live", "live", "live"]);
    expect((line.entries[0] as ActivityEntry).calls.map((c) => c.status)).toEqual(["done", "running"]);
  });

  it("tells a stored Jarvis voice turn: registry names, a preamble, a refusal", () => {
    const steps = replay([
      ["ActionProposed", { tool_name: "find-app-action", args: { query: "computers" } }],
      ["ActionExecuted", { tool_name: "find-app-action", success: true, duration_ms: 4, output_preview: '{"actions": [{"action_id": "add_computer"' }],
      ["AnnouncementRequested", { kind: "preamble", text: "I'll check the computers you set up." }],
      ["ActionDenied", { tool_name: "run-app-action", reason: "blacklist: <tool-declared-block>" }],
      ["BrainTurnStarted", { provider: "grok", model: "grok-4.3" }],
      ["BrainTurnCompleted", { provider: "grok", model: "grok-4.3" }],
    ]);
    const line = buildTimeline(stepsToBlocks(steps, false, t), opts());
    expect(line.entries.map((e) => e.kind)).toEqual(["activity", "prose", "activity"]);
    expect((line.entries[0] as ActivityEntry).calls[0]).toMatchObject({ text: "Find app action", detail: "computers" });
    expect((line.entries[2] as ActivityEntry).calls[0]).toMatchObject({ text: "Run app action", status: "blocked" });
    expect(line.problemCount).toBe(1);
  });

  it("formats durations and paths the way a line shows them", () => {
    expect(traceDuration(49)).toBe("49ms");
    expect(traceDuration(125_000)).toBe("2m 05s");
    expect(shortPath("C:\\Users\\me\\repo\\src\\a.ts")).toBe("…/repo/src/a.ts");
  });

  it.each([["de", de], ["es", es], ["pt", pt]] as const)("fills every %s line", (lang, dict) => {
    const tl = translator(dict as Dict);
    const line = buildTimeline([
      text("n", "Why."),
      tool("a", { name: "search_web", input: { query: "Wetter" }, output: '{"results": []}' }),
      tool("b", { isError: true, output: "boom" }),
      tool("c", { name: "Grep", input: { pattern: "x", path: "src" }, output: "y" }),
      tool("d", { name: "Read", input: { file_path: "a.png" }, output: "" }),
      tool("e", { name: "mcp__linear__list_issues", input: {}, output: "[]" }),
    ], { t: tl, lang, status: "done", live: false });
    const all = line.entries.flatMap((e) => e.kind === "activity"
      ? [e.summary, ...e.calls.map((c) => `${c.text} ${c.detail} ${c.result} ${c.reason}`)] : []).join(" | ");
    expect(all).not.toMatch(/\{\w+\}|trace_report\./);
  });
});

describe("trace_report locale parity", () => {
  function keys(obj: Dict, prefix = ""): string[] {
    return Object.entries(obj).flatMap(([k, v]) => (v && typeof v === "object" ? keys(v as Dict, `${prefix}${k}.`) : [`${prefix}${k}`]));
  }
  const base = keys((en as Dict).trace_report as Dict).sort();
  it.each([["de", de], ["es", es], ["pt", pt]] as const)("%s carries exactly the English keys", (_lang, dict) => {
    expect(keys((dict as Dict).trace_report as Dict).sort()).toEqual(base);
  });
  it("never uses {name}, which the i18n layer fills with the assistant's name", () => {
    expect(JSON.stringify((en as Dict).trace_report)).not.toContain("{name}");
  });
});

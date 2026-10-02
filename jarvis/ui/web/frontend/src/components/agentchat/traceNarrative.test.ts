import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import { reduceThinkingSteps, type ThinkingStep } from "@/lib/thinkingSteps";
import type { ToolBlock, TurnBlock } from "./reduce";
import { narrateTurn, narrativeMarkdown, proseDuration, type NarrateOptions } from "./traceNarrative";
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
const opts = (over: Partial<NarrateOptions> = {}): NarrateOptions => ({ t, lang: "en", status: "done", durationMs: 4200, ...over });

const tool = (id: string, over: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool", callId: id, name: "Bash", input: { command: "ls" }, output: "a.ts", isError: false,
  durationMs: 300, approval: null, startedMs: 0, ...over,
});
const text = (id: string, body: string): TurnBlock => ({ kind: "text", id, text: body });
const thinking = (id: string, body: string, durationMs = 1000): TurnBlock => ({ kind: "reasoning", id, text: body, durationMs, live: false, startedMs: 0 });

/** The steps a stored trace replays into, the way history and voice build them. */
function replay(events: Array<[string, Record<string, unknown>]>): ThinkingStep[] {
  let steps: ThinkingStep[] = [];
  events.forEach(([name, payload], i) => {
    steps = reduceThinkingSteps(steps, name, payload, 1000 + i * 10) ?? steps;
  });
  return steps.map((s) => (s.status === "active" ? { ...s, status: "done" } : s));
}

describe("trace narrative", () => {
  it("tells a stored Jarvis turn — registry names, a cut JSON result, a refusal — as prose", () => {
    // The shape of a real stored turn (chats.db, 2026-10-02): no rationale,
    // a result cut inside its first object, a call refused by a rule before
    // it was ever proposed, and the brain call published after the tools.
    const steps = replay([
      ["ActionProposed", { tool_name: "find-app-action", args: { query: "computers", area: "computers" }, rationale: "" }],
      ["ActionExecuted", { tool_name: "find-app-action", success: true, duration_ms: 4, output_preview: '{"actions": [{"action_id": "add_computer_api_computers_post", "title": "Add Computer"' }],
      ["ActionDenied", { tool_name: "run-app-action", reason: "blacklist: <tool-declared-block>" }],
      ["BrainTurnStarted", { provider: "grok", model: "grok-4.3" }],
      ["BrainTurnCompleted", { provider: "grok", model: "grok-4.3" }],
    ]);
    const blocks = stepsToBlocks(steps, false, t);
    // The brain call is the turn, not a step — no empty "Thought" row.
    expect(blocks.map((b) => b.kind)).toEqual(["tool", "tool"]);
    const story = narrateTurn(blocks, opts({ model: "grok · grok-4.3" }));
    expect(story.steps.map((s) => s.sentence)).toEqual([
      "Used the tool Find App Action with “computers”.",
      "Used the tool Run App Action.",
    ]);
    expect(story.steps[0].result).toBe("It returned a list of actions.");
    expect(story.steps[1].result).toBe("It did not run: a safety rule blocks this action.");
    expect(story.steps[1].outcome).toBe("denied");
    expect(story.overview).toBe(
      "This answer took 4.2 s and 2 steps. Along the way it used Find App Action and Run App Action. "
      + "One step was not allowed to run. Model: grok · grok-4.3.",
    );
  });

  it("puts the model's own words before the step they explain and drops plumbing", () => {
    const blocks: TurnBlock[] = [
      thinking("r1", "", 820),
      tool("c1", { input: { command: "gh repo list --limit 50\nhead -50" }, output: "[]" }),
      text("n1", "The repo has 85 stars. I'll fetch the timestamps."),
      tool("c2", { input: { command: "gh api repos/x/stargazers" }, output: "85 /tmp/stars.txt\n2 2026-07-23\n1 2026-07-24" }),
      thinking("r2", "", 541),
      tool("c3", { name: "ToolSearch", input: { query: "select:mcp__jarvis__create_artifact" }, output: "[]" }),
      tool("c4", { name: "mcp__jarvis__create_artifact", input: { title: "GitHub Stars", request: "Build it" }, output: "The artifact is being built in the background." }),
    ];
    const story = narrateTurn(blocks, opts({ durationMs: 28_518 }));
    expect(story.actionCount).toBe(3);
    expect(story.steps.map((s) => s.sentence)).toEqual([
      "Ran the command `gh repo list --limit 50`.",
      "Ran the command `gh api repos/x/stargazers`.",
      "Created the artifact “GitHub Stars”.",
    ]);
    expect(story.steps[0].why).toEqual([]);
    expect(story.steps[1].why).toEqual(["The repo has 85 stars. I'll fetch the timestamps."]);
    expect(story.steps[0].result).toBe("It returned no results.");
    expect(story.steps[1].result).toBe("It returned 3 lines, starting with “85 /tmp/stars.txt”.");
    // A quote that ends its own sentence keeps one stop.
    expect(story.steps[2].result).toBe("It returned “The artifact is being built in the background.”");
    // Wordless thinking is time in the overview, never a step.
    expect(story.thinkingMs).toBe(1361);
    expect(story.overview).toContain("This answer took 29 s and 3 steps.");
    expect(story.overview).toContain("1.4 s of that went into thinking.");
    expect(story.overview).toContain("Along the way it ran 2 commands and used artifacts.");
    expect(story.overview).toContain("Every step succeeded.");
  });

  it("measures edits, reads, searches and failures in their own words", () => {
    const story = narrateTurn([
      tool("e", { name: "Edit", input: { file_path: "src/app.ts", old_string: "a\nb", new_string: "c" }, output: "ok" }),
      tool("r", { name: "Read", input: { file_path: "src/app.ts" }, output: "1→one\n2→two\n3→three" }),
      tool("g", { name: "Grep", input: { pattern: "TODO" }, output: "src/a.ts:3\nsrc/b.ts:9" }),
      tool("f", { input: { command: "npm test" }, isError: true, output: "Error: 2 tests failed\n  at run" }),
      tool("i", { output: null }),
    ], opts({ status: "cancelled" }));
    expect(story.steps.map((s) => [s.sentence, s.result])).toEqual([
      ["Edited `src/app.ts`.", "Added 1 lines and removed 2."],
      ["Read `src/app.ts`.", "It was 3 lines long."],
      ["Searched the files for `TODO`.", "It found 2 matches."],
      ["Ran the command `npm test`.", "It failed: Error: 2 tests failed."],
      ["Ran the command `ls`.", "It stopped before it returned a result."],
    ]);
    expect(story.problemCount).toBe(2);
    expect(story.overview).toContain("One step failed. One step stopped before it returned a result.");
    expect(story.overview).toContain("The turn was stopped before it finished.");
  });

  it("tells a wrapped command by what it printed", () => {
    const story = narrateTurn([
      tool("j", { name: "mcp__jarvis__cli_jarvisctl", input: { command: "jarvisctl outputs list" },
        output: JSON.stringify({ exit_code: 0, stdout: "a.md\nb.md\n", stderr: "", duration_ms: 12 }) }),
    ], opts());
    expect(story.steps[0].result).toBe("It returned 2 lines, starting with “a.md”.");
  });

  it("tells approvals, declines and questions honestly", () => {
    const story = narrateTurn([
      tool("p", { output: null, approval: { approvalId: "a", summary: "Delete?", decision: null } }),
      tool("d", { output: null, approval: { approvalId: "b", summary: "", decision: "deny" } }),
      tool("q", {
        name: "ask_user", input: {}, output: "", question: {
          questionId: "q", asker: "Scout", questions: [{ question: "Which repo?", options: [], recommendationReason: "" }],
          answers: [{ text: "PersonalJarvis", optionIndex: 0, source: "user" }], expiresMs: null, closed: true,
        } as unknown as ToolBlock["question"],
      }),
    ], opts({ status: "running", durationMs: null }));
    expect(story.steps.map((s) => s.result)).toEqual([
      "It is waiting for your approval.",
      "You declined it, so it did not run.",
      "You answered “PersonalJarvis”.",
    ]);
    expect(story.steps[2].sentence).toBe("Asked you: “Which repo?”");
    expect(story.steps[1].outcome).toBe("declined");
  });

  it("keeps a closing thought as a note and escapes runtime text", () => {
    const story = narrateTurn([
      tool("w", { name: "search_web", input: { query: "*bold* [link](x)" }, output: "<b>hi</b>" }),
      thinking("r", "**Check** the answer."),
    ], opts());
    expect(story.steps[0].sentence).toBe("Searched the web for “\\*bold\\* \\[link\\](x)”.");
    expect(story.steps[0].result).toBe("It returned “\\<b\\>hi\\</b\\>”.");
    expect(story.steps[1]).toMatchObject({ kind: "note", why: ["**Check** the answer."] });
    expect(story.actionCount).toBe(1);
    expect(story.overview).toBe("This answer took 4.2 s and one step. The step succeeded.");
  });

  it("says only how long a turn thought when it took no step", () => {
    expect(narrateTurn([thinking("r", "", 3000)], opts()).overview).toBe("This answer took 4.2 s of thinking.");
    expect(narrateTurn([], opts({ durationMs: null })).overview).toBe("");
  });

  it("copies as Markdown: overview, the model's words quoted, numbered steps", () => {
    const story = narrateTurn([text("n", "First the port."), tool("c", { durationMs: 1200 })], opts());
    expect(narrativeMarkdown(story, t)).toBe([
      "### How this answer came about",
      "",
      "This answer took 4.2 s and one step. The step succeeded.",
      "",
      "> First the port.",
      "",
      "1. Ran the command `ls`. (1.2 s) It returned “a.ts”.",
    ].join("\n"));
  });

  it("formats prose durations without rounding a short call to zero", () => {
    expect(proseDuration(49)).toBe("49 ms");
    expect(proseDuration(4200)).toBe("4.2 s");
    expect(proseDuration(28_518)).toBe("29 s");
    expect(proseDuration(125_000)).toBe("2 min 05 s");
  });

  it.each([["de", de], ["es", es]] as const)("writes whole %s sentences with every placeholder filled", (lang, dict) => {
    const tl = translator(dict as Dict);
    const story = narrateTurn([
      text("n", "Why."),
      tool("a", { name: "search_web", input: { query: "Wetter" }, output: '{"results": []}' }),
      tool("b", { isError: true, output: "boom" }),
      tool("c", { name: "mcp__github__create_issue", input: { title: "Bug" }, output: "#12" }),
    ], { t: tl, lang, status: "done", durationMs: 9000, model: "m" });
    const all = [story.overview, ...story.steps.flatMap((s) => [s.sentence, s.result]), narrativeMarkdown(story, tl)].join("\n");
    expect(all).not.toMatch(/\{\w+\}|trace_report\./);
    expect(story.overview).not.toContain("This answer");
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
});

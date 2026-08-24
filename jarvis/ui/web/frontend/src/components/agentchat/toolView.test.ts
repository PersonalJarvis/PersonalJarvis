import { describe, expect, it } from "vitest";

import {
  agentToolView,
  formatTokens,
  inputSummary,
  inputTokens,
  outputTokens,
} from "@/components/agentchat/toolView";

describe("agentToolView", () => {
  it("keeps the agent's own tool name — the row must match the log", () => {
    expect(agentToolView("PowerShell", { command: "Get-ChildItem" })).toMatchObject({
      label: "PowerShell",
      family: "shell",
      summary: "Get-ChildItem",
    });
    expect(agentToolView("ToolSearch", { query: "chrome tabs" })).toMatchObject({
      label: "ToolSearch",
      family: "tools",
      summary: "chrome tabs",
    });
    // A Grep shows WHAT was searched for, not the folder it looked in — the
    // path alone reads as the wrong call next to the agent's log.
    const grep = agentToolView("Grep", {
      path: "C:\\Users\\Administrator\\Desktop",
      pattern: "permissionMode",
    });
    expect(grep.label).toBe("Grep");
    expect(grep.summary).toBe("permissionMode");
    expect(agentToolView("Read", { file_path: "a.py", pattern: "x" }).summary).toBe("a.py");
    expect(agentToolView("Read", { file_path: "a.py" }).family).toBe("read");
    expect(agentToolView("MultiEdit", {}).family).toBe("edit");
    expect(agentToolView("Bash", { command: "ls -la\nsecond line" }).summary).toBe("ls -la");
    expect(agentToolView("TodoWrite", {}).family).toBe("plan");
    expect(agentToolView("Task", { prompt: "go" }).family).toBe("agent");
  });

  it("names an MCP call after its server and wears the vendor mark when there is one", () => {
    const gh = agentToolView("mcp__github__create_issue", { title: "Bug" });
    expect(gh.family).toBe("mcp");
    expect(gh.label).toBe("GitHub · Create Issue");
    expect(gh.logoUrl).toBeTruthy();
    const slash = agentToolView("blender/get_scene_info", {});
    expect(slash.family).toBe("mcp");
    expect(slash.label).toContain("Blender");
  });

  it("falls back to a wrench for a tool nobody knows, never to nothing", () => {
    const odd = agentToolView("frobnicate", { thing: "x" });
    expect(odd.label).toBe("frobnicate");
    expect(odd.family).toBe("other");
    expect(agentToolView("", {}).label).toBe("tool");
  });
});

describe("inputSummary", () => {
  it("prefers the human field and keeps it to one line", () => {
    expect(inputSummary({ command: "pytest -q" })).toBe("pytest -q");
    expect(inputSummary({ nothing: 3, note: "hello" })).toBe("hello");
    expect(inputSummary("plain string")).toBe("plain string");
    expect(inputSummary(null)).toBe("");
    expect(inputSummary({ command: "x".repeat(300) })).toHaveLength(200);
  });
});

describe("token formatting", () => {
  it("reads like the Claude CLI: 4.8k, 1.2M", () => {
    expect(formatTokens(219)).toBe("219");
    expect(formatTokens(4800)).toBe("4.8k");
    expect(formatTokens(12000)).toBe("12k");
    expect(formatTokens(1_200_000)).toBe("1.2M");
    expect(formatTokens(-5)).toBe("0");
  });

  it("reads either usage shape", () => {
    expect(outputTokens({ output_tokens: 5 })).toBe(5);
    expect(outputTokens({ output: 7 })).toBe(7);
    expect(inputTokens({ prompt_tokens: 9 })).toBe(9);
    expect(outputTokens(null)).toBeNull();
    expect(outputTokens({})).toBeNull();
  });

  it("counts the cache into the input side, and never twice", () => {
    // Anthropic keeps the cached part beside input_tokens; both are paid for.
    expect(
      inputTokens({
        input_tokens: 2,
        cache_creation_input_tokens: 30420,
        cache_read_input_tokens: 21355,
      }),
    ).toBe(51777);
    // The OpenAI shape counts cached tokens INSIDE prompt_tokens already.
    expect(inputTokens({ prompt_tokens: 900, cached_input_tokens: 800 })).toBe(900);
    expect(inputTokens({ cache_read_input_tokens: 40 })).toBe(40);
    expect(inputTokens({})).toBeNull();
  });
});

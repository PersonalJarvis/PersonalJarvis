import { describe, expect, it } from "vitest";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { chatLines, chatVersion, MAX_CHAT_LINES, toolLabel } from "./deskChat";

const ev = (seq: number, kind: string, payload: Record<string, unknown>): AgentChatEvent => ({ seq, ts_ms: seq, kind, payload });

describe("desk chat lines", () => {
  it("keeps who said what and which tool runs, dropping noise", () => {
    const lines = chatLines([
      ev(1, "user_message", { text: "Please   ship it" }),
      ev(2, "turn_started", {}),
      ev(3, "reasoning", { text: "" }),
      ev(4, "assistant_text", { text: "On it." }),
      ev(5, "tool_call", { name: "mcp__jarvis__society_shell" }),
      ev(6, "tool_result", { output: "…" }),
      ev(7, "error", { message: "boom" }),
    ]);
    expect(lines).toEqual([
      { kind: "user", text: "Please ship it" },
      { kind: "agent", text: "On it." },
      { kind: "tool", text: "society_shell" },
      { kind: "error", text: "boom" },
    ]);
  });
  it("keeps only the newest lines and truncates long text", () => {
    const many = Array.from({ length: 40 }, (_, i) => ev(i, "assistant_text", { text: `line ${i} ${"x".repeat(300)}` }));
    const lines = chatLines(many);
    expect(lines).toHaveLength(MAX_CHAT_LINES);
    expect(lines.at(-1)!.text.startsWith("line 39")).toBe(true);
    expect(lines[0].text.length).toBeLessThanOrEqual(160);
  });
  it("labels tools and versions events", () => {
    expect(toolLabel("Read")).toBe("Read");
    expect(toolLabel(undefined)).toBe("tool");
    expect(chatVersion([])).toBe("0");
    expect(chatVersion([ev(9, "x", {})])).toBe("9:1");
  });
});

import { describe, expect, it } from "vitest";
import {
  MAX_TERM_LINES,
  countWords,
  emptyDeckState,
  reduceDeck,
  splitTerminalData,
  type DeckState,
} from "@/lib/deckState";

/**
 * The deck shows numbers a person will act on, so each one has to be traced
 * to the payload it came from and nothing else. These tests pin that: cost
 * and tokens add up exactly, Computer-Use follows its events, a capture bumps
 * a sequence, terminal noise is stripped, and unrelated events change nothing.
 */

function fold(events: Array<[string, unknown]>, start: DeckState = emptyDeckState()): DeckState {
  let s = start;
  let ts = 1_000;
  for (const [name, payload] of events) {
    s = reduceDeck(s, name, payload, (ts += 100));
  }
  return s;
}

describe("reduceDeck", () => {
  it("returns the same object for events it does not read", () => {
    const start = emptyDeckState();
    expect(reduceDeck(start, "HotkeyPressed", {}, 1)).toBe(start);
    expect(reduceDeck(start, "SomethingNew", { x: 1 }, 1)).toBe(start);
  });

  it("adds brain cost and tokens per turn and per model", () => {
    const s = fold([
      ["BrainTurnCompleted", { tokens_in: 100, tokens_out: 20, cost_usd: 0.01, provider: "openrouter", model: "sonnet" }],
      ["BrainTurnCompleted", { tokens_in: 50, tokens_out: 10, cost_usd: 0.005, provider: "openrouter", model: "sonnet" }],
      ["BrainTurnCompleted", { tokens_in: 10, tokens_out: 5, cost_usd: 0.001, provider: "anthropic", model: "opus" }],
    ]);
    expect(s.usage.turns).toBe(3);
    expect(s.usage.tokensIn).toBe(160);
    expect(s.usage.tokensOut).toBe(35);
    expect(s.usage.costUsd).toBeCloseTo(0.016, 6);
    expect(s.usage.lastModel).toBe("opus");
    expect(s.usage.byModel.sonnet).toEqual({ turns: 2, tokensIn: 150, tokensOut: 30, costUsd: 0.015 });
    expect(s.usage.byModel.opus.turns).toBe(1);
  });

  it("tolerates a turn without usage figures", () => {
    const s = fold([["BrainTurnCompleted", { provider: "ollama" }]]);
    expect(s.usage.turns).toBe(1);
    expect(s.usage.tokensIn).toBe(0);
    expect(s.usage.costUsd).toBe(0);
    expect(s.usage.byModel.ollama.turns).toBe(1);
  });

  it("follows a computer-use mission from start to end", () => {
    let s = fold([["CUControlStarted", { mission_id: "m1" }]]);
    expect(s.cu.active).toBe(true);
    expect(s.cu.phase).toBe("observe");

    s = fold(
      [
        ["CUStepProfiled", { phase: "plan", step_idx: 3 }],
        ["ActionPlanned", { action_kind: "click", target_hint: "{role:Button,name:Save}" }],
        ["ObservationCaptured", { screenshot_hash: "ab".repeat(32), window_title: "Docker Desktop" }],
        ["ActionExecuted", { success: true }],
      ],
      s,
    );
    expect(s.cu.stepIdx).toBe(3);
    expect(s.cu.phase).toBe("act");
    expect(s.cu.lastActionKind).toBe("click");
    expect(s.cu.lastTargetHint).toBe("{role:Button,name:Save}");
    expect(s.cu.lastFrameSha).toBe("ab".repeat(32));
    expect(s.cu.windowTitle).toBe("Docker Desktop");
    expect(s.cu.frames).toBe(1);
    expect(s.cu.lastActionOk).toBe(true);

    s = fold([["CUControlEnded", { reason: "finished" }]], s);
    expect(s.cu.active).toBe(false);
    expect(s.cu.phase).toBe("idle");
    expect(s.cu.endedReason).toBe("finished");
    // The last frame stays: the card keeps showing what was done.
    expect(s.cu.lastFrameSha).toBe("ab".repeat(32));
  });

  it("ignores ordinary tool results while no computer-use mission runs", () => {
    const start = emptyDeckState();
    // ActionExecuted also fires for plain tool calls — those belong to the
    // thinking trace, not to the CU card.
    expect(reduceDeck(start, "ActionExecuted", { success: false }, 1)).toBe(start);
  });

  it("bumps the capture sequence on every completed screen capture", () => {
    let s = fold([["ScreenCaptureCompleted", { target_kind: "monitor", target_label: "Monitor 1", width: 1920, height: 1080, redaction_count: 2 }]]);
    expect(s.capture?.seq).toBe(1);
    expect(s.capture?.targetLabel).toBe("Monitor 1");
    expect(s.capture?.redactions).toBe(2);
    s = fold([["ScreenCaptureCompleted", { width: 800, height: 600 }]], s);
    expect(s.capture?.seq).toBe(2);
    expect(s.capture?.width).toBe(800);
  });

  it("collects terminal commands, output lines and CLI calls in order", () => {
    const s = fold([
      ["TerminalCommandExecuted", { terminal_id: "t1", command: "pytest -q" }],
      ["TerminalOutput", { terminal_id: "t1", data: "\x1b[32m3 passed\x1b[0m\r\n" }],
      ["CliInvoked", { cli_name: "gh", command_preview: "pr list" }],
      ["CliInvocationFinished", { cli_name: "gh", exit_code: 0, duration_ms: 420 }],
      ["CliInvocationFinished", { cli_name: "gh", exit_code: 1, duration_ms: 12 }],
    ]);
    expect(s.termLines.map((l) => [l.kind, l.text])).toEqual([
      ["cmd", "pytest -q"],
      ["out", "3 passed"],
      ["cli", "gh pr list"],
      ["note", "gh · exit 0 · 420 ms"],
      ["err", "gh · exit 1 · 12 ms"],
    ]);
  });

  it("caps the terminal buffer", () => {
    let s = emptyDeckState();
    for (let i = 0; i < MAX_TERM_LINES + 25; i++) {
      s = reduceDeck(s, "TerminalOutput", { terminal_id: "t", data: `line ${i}\n` }, i);
    }
    expect(s.termLines).toHaveLength(MAX_TERM_LINES);
    expect(s.termLines[0].text).toBe("line 25");
  });

  it("keeps the newest wiki change per page at the front", () => {
    const s = fold([
      ["WikiPageChanged", { slug: "a", kind: "created" }],
      ["WikiPageChanged", { slug: "b", kind: "created" }],
      ["WikiPageChanged", { slug: "a", kind: "updated" }],
    ]);
    expect(s.wikiChanges.map((c) => `${c.slug}:${c.kind}`)).toEqual(["a:updated", "b:created"]);
  });

  it("counts spoken words from final transcripts only", () => {
    const s = fold([
      ["TranscriptPartial", { transcript: { text: "this is not" } }],
      ["TranscriptFinal", { transcript: { text: "  Sortier   mir die Rechnungen " } }],
      ["TranscriptFinal", { transcript: { text: "" } }],
      ["TranscriptFinal", { transcript: { text: "Danke" } }],
    ]);
    expect(s.wordsSession).toBe(5);
    expect(s.wordsLast).toBe(1);
    expect(s.utterances).toBe(2);
  });
});

describe("countWords", () => {
  it("counts runs of non-space characters", () => {
    expect(countWords("")).toBe(0);
    expect(countWords("   ")).toBe(0);
    expect(countWords("ein")).toBe(1);
    expect(countWords("ein  zwei\tdrei\nvier")).toBe(4);
  });
});

describe("splitTerminalData", () => {
  it("strips ANSI colour and cursor sequences", () => {
    expect(splitTerminalData("\x1b[1;32mok\x1b[0m\n")).toEqual(["ok"]);
    expect(splitTerminalData("\x1b[2K\x1b[1Gprogress 50%\r")).toEqual(["progress 50%"]);
  });

  it("strips OSC window-title sequences terminated by BEL or ESC backslash", () => {
    expect(splitTerminalData("\x1b]0;my title\x07real\n")).toEqual(["real"]);
    expect(splitTerminalData("\x1b]0;my title\x1b\\real\n")).toEqual(["real"]);
  });

  it("keeps CR-only redraws as their own lines and drops empties", () => {
    expect(splitTerminalData("10%\r20%\r30%\r\n\n")).toEqual(["10%", "20%", "30%"]);
  });
});

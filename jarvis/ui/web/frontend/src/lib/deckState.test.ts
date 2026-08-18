import { describe, expect, it } from "vitest";
import {
  MAX_CAPTURES,
  MAX_JOURNAL_LINES,
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

// One clock for the whole file, so a state folded in two steps still sees
// time moving forward between them (durations are wall-clock deltas).
let clock = 1_000;

function fold(events: Array<[string, unknown]>, start: DeckState = emptyDeckState()): DeckState {
  let s = start;
  for (const [name, payload] of events) {
    s = reduceDeck(s, name, payload, (clock += 100));
  }
  return s;
}

describe("reduceDeck", () => {
  it("returns the same object for events it does not read", () => {
    const start = emptyDeckState();
    expect(reduceDeck(start, "TerminalSpawned", {}, 1)).toBe(start);
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

/**
 * The session log and the turn are what the front page shows INSTEAD of a
 * live screen feed (maintainer decision 2026-08-18): a terminal that always
 * has something to say, and an instrument that reads how the last answer came
 * about. Both are folded from the same events, so both are pinned here.
 */
describe("the session log", () => {
  it("writes one line per thing heard, thought, done and said — with durations", () => {
    const s = fold([
      ["WakeWordDetected", { keyword: "nova", confidence: 0.92 }],
      ["TranscriptFinal", { transcript: { text: "wie spät ist es" } }],
      ["BrainTurnStarted", { provider: "openrouter", model: "claude-sonnet-5" }],
      ["BrainTTFT", { cache_hit: true, model: "claude-sonnet-5" }],
      ["ActionProposed", { tool_name: "get_time" }],
      ["ActionExecuted", { tool_name: "get_time", success: true, duration_ms: 84 }],
      ["BrainTurnCompleted", { tokens_in: 1200, tokens_out: 88, cost_usd: 0.0031 }],
      ["SpeechSpoken", { text: "Es ist 14:47.", spoken_kind: "reply" }],
      ["LatencySpan", { phase: "turn_to_first_audio", duration_ms: 1900 }],
    ]);
    const lines = s.journal.map((l) => [l.kind, l.labelKey ?? "", l.text ?? "", l.ms ?? null, l.ok ?? null, l.open ?? false]);
    expect(lines).toEqual([
      ["wake", "deck.log_wake", "nova · 0.92", null, null, false],
      ["hear", "", "wie spät ist es", null, null, false],
      // The brain line was opened, then closed in place when the turn completed.
      ["think", "", "openrouter · claude-sonnet-5", 400, true, false],
      ["note", "deck.log_first_token", "cache", 100, null, false],
      ["tool", "", "get_time", 84, true, false],
      ["done", "deck.log_done", "", 400, null, false],
      ["say", "", "Es ist 14:47.", null, null, false],
      ["note", "deck.log_first_audio", "", 1900, null, false],
    ]);
    const done = s.journal.find((l) => l.kind === "done")!;
    expect(done.args).toEqual({ in: "1.20k", out: "88", cost: "$0.0031" });
  });

  it("does not write the same reply twice when it arrives spoken AND as a message", () => {
    const s = fold([
      ["SpeechSpoken", { text: "Erledigt.", spoken_kind: "reply" }],
      ["MessageSent", { role: "assistant", text: "Erledigt." }],
      ["MessageSent", { role: "assistant", text: "Something else." }],
    ]);
    expect(s.journal.map((l) => l.text)).toEqual(["Erledigt.", "Something else."]);
  });

  it("logs a typed message once, not once per channel, and never the voice transcript again", () => {
    const s = fold([
      ["TranscriptFinal", { transcript: { text: "mach das licht an" } }],
      ["MessageSent", { role: "user", text: "mach das licht an" }],
      ["MessageSent", { role: "user", text: "typed instead" }],
    ]);
    expect(s.journal.map((l) => [l.labelKey ?? "", l.text])).toEqual([
      ["", "mach das licht an"],
      ["deck.log_typed", "typed instead"],
    ]);
  });

  it("marks a denied tool and a failed worker as not ok", () => {
    const s = fold([
      ["ActionProposed", { tool_name: "run_shell" }],
      ["ActionDenied", { tool_name: "run_shell" }],
      ["JarvisAgentTaskStarted", { utterance: "sort my invoices" }],
      ["JarvisAgentTaskCompleted", { success: false, duration_s: 12.5 }],
    ]);
    const tool = s.journal.find((l) => l.kind === "tool")!;
    expect(tool.ok).toBe(false);
    expect(tool.labelKey).toBe("deck.log_denied");
    const worker = s.journal.find((l) => l.kind === "worker")!;
    expect(worker.ok).toBe(false);
    expect(worker.ms).toBe(12_500);
    expect(worker.open).toBe(false);
  });

  it("closes a brain attempt when the fallback chain starts the next one", () => {
    const s = fold([
      ["BrainTurnStarted", { provider: "anthropic", model: "opus" }],
      ["BrainTurnStarted", { provider: "openrouter", model: "sonnet" }],
      ["BrainTurnCompleted", { tokens_in: 1, tokens_out: 1 }],
    ]);
    const thinks = s.journal.filter((l) => l.kind === "think");
    // Both closed, both with a duration; only the DONE line says which one answered.
    expect(thinks.map((l) => [l.text, l.open, l.ms])).toEqual([
      ["anthropic · opus", false, 100],
      ["openrouter · sonnet", false, 100],
    ]);
    expect(s.journal.at(-1)?.kind).toBe("done");
  });

  it("notes captures, memory writes, control sessions and errors", () => {
    const s = fold([
      ["ScreenCaptureCompleted", { target_label: "Chrome", width: 1920, height: 1080, redaction_count: 2 }],
      ["WikiPageChanged", { slug: "projects/nova", kind: "updated" }],
      ["CUControlStarted", { mission_id: "m1" }],
      ["CUControlEnded", { reason: "finished" }],
      ["ErrorOccurred", { layer: "brain", message: "rate limited" }],
    ]);
    expect(s.journal.map((l) => [l.kind, l.labelKey ?? "", l.text ?? "", l.ok ?? null])).toEqual([
      ["look", "deck.log_look_redacted", "Chrome · 1920×1080", null],
      ["memory", "deck.log_memory", "projects/nova · updated", null],
      ["control", "deck.log_control", "finished", true],
      ["error", "", "brain: rate limited", false],
    ]);
    expect(s.journal[0].args).toEqual({ n: "2" });
  });

  it("says the voice is ready once, not on every heartbeat", () => {
    const s = fold([
      ["VoiceBootStatus", { ready: false }],
      ["VoiceBootStatus", { ready: true }],
      ["VoiceBootStatus", { ready: true }],
    ]);
    expect(s.journal).toHaveLength(1);
    expect(s.journal[0].labelKey).toBe("deck.log_voice_ready");
  });

  it("caps the log", () => {
    let s = emptyDeckState();
    for (let i = 0; i < MAX_JOURNAL_LINES + 30; i++) {
      s = reduceDeck(s, "SpeechSpoken", { text: `line ${i}` }, i);
    }
    expect(s.journal).toHaveLength(MAX_JOURNAL_LINES);
    expect(s.journal[0].text).toBe("line 30");
  });
});

describe("the turn", () => {
  it("follows a voice turn from wake word to the mic reopening, keeping every mark", () => {
    let s = fold([["WakeWordDetected", { keyword: "nova" }]]);
    expect(s.turn.index).toBe(1);
    expect(s.turn.phase).toBe("hear");
    expect(s.turn.voice).toBe(true);
    expect(s.turn.anchorTs).toBeNull();

    // LISTENING after the wake word is the hear phase, not the end.
    s = fold([["SystemStateChanged", { new_state: "LISTENING", previous: "IDLE" }]], s);
    expect(s.turn.phase).toBe("hear");
    expect(s.turn.index).toBe(1);

    s = fold(
      [
        ["TranscriptFinal", { transcript: { text: "wie spät ist es" } }],
        ["LatencySpan", { phase: "stt_finalize", duration_ms: 380 }],
        ["SystemStateChanged", { new_state: "THINKING", previous: "LISTENING" }],
        ["BrainTurnStarted", { provider: "openrouter", model: "sonnet" }],
        ["LatencySpan", { phase: "ack_first_audio", duration_ms: 900 }],
        ["BrainTTFT", { cache_hit: false }],
        ["LatencySpan", { phase: "brain_first_token", duration_ms: 1300 }],
        ["ActionProposed", { tool_name: "get_time" }],
      ],
      s,
    );
    expect(s.turn.index).toBe(1);
    expect(s.turn.anchorTs).not.toBeNull();
    expect(s.turn.words).toBe(4);
    expect(s.turn.sttMs).toBe(380);
    expect(s.turn.ackMs).toBe(900);
    expect(s.turn.ttftMs).toBe(1300);
    expect(s.turn.provider).toBe("openrouter");
    expect(s.turn.phase).toBe("act");
    expect(s.turn.tools).toBe(1);

    s = fold(
      [
        ["ActionExecuted", { tool_name: "get_time", success: true, duration_ms: 84 }],
        ["BrainTurnCompleted", { tokens_in: 1200, tokens_out: 88, cost_usd: 0.003, provider: "openrouter", model: "sonnet" }],
        ["SystemStateChanged", { new_state: "SPEAKING", previous: "THINKING" }],
        ["LatencySpan", { phase: "turn_to_first_audio", duration_ms: 1900 }],
      ],
      s,
    );
    expect(s.turn.phase).toBe("speak");
    expect(s.turn.tokensIn).toBe(1200);
    expect(s.turn.brainMs).toBeGreaterThan(0);
    expect(s.turn.firstAudioMs).toBe(1900);
    expect(s.turn.endedTs).toBeNull();

    s = fold([["SystemStateChanged", { new_state: "LISTENING", previous: "SPEAKING" }]], s);
    expect(s.turn.phase).toBe("idle");
    expect(s.turn.endedTs).not.toBeNull();
    // The figures stay for the card to show as the LAST turn.
    expect(s.turn.index).toBe(1);
    expect(s.turn.firstAudioMs).toBe(1900);
  });

  it("does not count a turn twice when the reply's message lands after the turn closed", () => {
    let s = fold([
      ["TranscriptFinal", { transcript: { text: "hallo" } }],
      ["BrainTurnStarted", { provider: "p", model: "m" }],
      ["BrainTurnCompleted", {}],
      ["SystemStateChanged", { new_state: "SPEAKING" }],
      ["SystemStateChanged", { new_state: "LISTENING" }],
    ]);
    expect(s.turn.index).toBe(1);
    expect(s.turn.phase).toBe("idle");
    // A late soft signal within the grace period re-opens the same turn…
    s = fold([["SystemStateChanged", { new_state: "SPEAKING" }]], s);
    expect(s.turn.index).toBe(1);
    expect(s.turn.phase).toBe("speak");
    // …and a follow-up transcript starts turn two.
    s = fold([["TranscriptFinal", { transcript: { text: "und jetzt" } }]], s);
    expect(s.turn.index).toBe(2);
    expect(s.turn.phase).toBe("think");
  });

  it("ends a typed turn when the reply arrives, and a background brain turn when the brain is done", () => {
    let s = fold([
      ["MessageSent", { role: "user", text: "what is the time" }],
      ["BrainTurnStarted", { provider: "p", model: "m" }],
    ]);
    expect(s.turn.voice).toBe(false);
    expect(s.turn.words).toBe(4);
    expect(s.turn.phase).toBe("think");
    s = fold([["BrainTurnCompleted", { tokens_in: 10 }]], s);
    expect(s.turn.phase).toBe("idle");
    expect(s.turn.tokensIn).toBe(10);

    // Nobody asked: the brain ran on its own (a summary, a wiki extraction).
    // It shows as a turn while it runs and is over when it completes.
    s = reduceDeck(s, "BrainTurnStarted", { provider: "vertex", model: "flash" }, 60_000);
    expect(s.turn.index).toBe(2);
    expect(s.turn.phase).toBe("think");
    s = reduceDeck(s, "BrainTurnCompleted", { tokens_in: 5 }, 61_000);
    expect(s.turn.phase).toBe("idle");
    expect(s.turn.brainMs).toBe(1000);
  });

  it("goes back to thinking when the last tool returns, unless computer use has the screen", () => {
    let s = fold([
      ["TranscriptFinal", { transcript: { text: "click save" } }],
      ["BrainTurnStarted", {}],
      ["ActionProposed", { tool_name: "a" }],
      ["ActionProposed", { tool_name: "b" }],
      ["ActionExecuted", { tool_name: "a", success: true }],
    ]);
    expect(s.turn.phase).toBe("act");
    s = fold([["ActionExecuted", { tool_name: "b", success: false }]], s);
    expect(s.turn.phase).toBe("think");
    expect(s.turn.toolsFailed).toBe(1);

    s = fold([["CUControlStarted", { mission_id: "m" }], ["ActionProposed", { tool_name: "click" }], ["ActionExecuted", { tool_name: "click", success: true }]], s);
    expect(s.turn.cu).toBe(true);
    expect(s.turn.phase).toBe("act");
    s = fold([["CUControlEnded", { reason: "finished" }]], s);
    expect(s.turn.phase).toBe("think");
  });

  it("fills missing marks from the end-of-turn snapshot and closes on a realtime completion", () => {
    let s = fold([
      ["VoiceTurnStarted", { turn_id: "t1" }],
      ["TranscriptFinal", { transcript: { text: "hey" } }],
      ["LatencyTurnComplete", { stages_ms: { stt_finalize: 210, brain_first_token: 800, turn_to_first_audio: 1500 } }],
    ]);
    expect(s.turn.sttMs).toBe(210);
    expect(s.turn.ttftMs).toBe(800);
    expect(s.turn.firstAudioMs).toBe(1500);
    s = fold([["VoiceTurnCompleted", { provider: "openai", model: "rt", tokens_in: 3, tokens_out: 4, cost_usd: 0.01, tool_calls: ["a", "b"] }]], s);
    expect(s.turn.phase).toBe("idle");
    expect(s.turn.provider).toBe("openai");
    expect(s.turn.tokensIn).toBe(3);
    expect(s.turn.tools).toBe(2);
  });

  it("ends the thinking when the brain fails", () => {
    const s = fold([
      ["TranscriptFinal", { transcript: { text: "hallo" } }],
      ["BrainTurnStarted", {}],
      ["ErrorOccurred", { layer: "brain", message: "boom" }],
    ]);
    expect(s.turn.phase).toBe("idle");
    expect(s.turn.errors).toBe(1);
  });

  it("keeps a ledger of this session's captures, newest first", () => {
    let s = emptyDeckState();
    for (let i = 0; i < MAX_CAPTURES + 2; i++) {
      s = reduceDeck(s, "ScreenCaptureCompleted", { target_label: `w${i}`, width: 10, height: 10 }, i);
    }
    expect(s.captures).toHaveLength(MAX_CAPTURES);
    expect(s.captures[0].targetLabel).toBe(`w${MAX_CAPTURES + 1}`);
    expect(s.capture?.seq).toBe(MAX_CAPTURES + 2);
  });
});

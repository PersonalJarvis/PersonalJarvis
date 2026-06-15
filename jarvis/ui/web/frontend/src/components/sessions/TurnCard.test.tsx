/**
 * The Transcription turn card must show the "Spoken output" track — every
 * phrase Jarvis VOICED that is not the normal reply (timeout / clarify /
 * announcement / …). User report 2026-06-15: those special phrases were
 * spoken but never appeared in the transcript.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TurnCard } from "./TurnCard";
import type { VoiceSpokenLine, VoiceTurnRow } from "./types";

afterEach(cleanup);

function turn(over: Partial<VoiceTurnRow> = {}): VoiceTurnRow {
  return {
    id: "turn-0",
    session_id: "sess-1",
    idx: 0,
    started_ms: 1_717_780_000_000,
    ended_ms: null,
    user_text: "Wie spät ist es?",
    user_lang: "de",
    jarvis_text: "",
    jarvis_lang: "de",
    tier: "",
    provider: "",
    model: "",
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    latency_total_ms: 0,
    think_ms: 0,
    speak_ms: 0,
    tool_calls: [],
    ...over,
  };
}

function spokenLine(over: Partial<VoiceSpokenLine> = {}): VoiceSpokenLine {
  return {
    turn_id: "turn-0",
    ts_ms: 1_717_780_001_000,
    text: "Das hat zu lange gedauert.",
    spoken_kind: "timeout",
    ...over,
  };
}

describe("TurnCard spoken track", () => {
  it("renders each voiced phrase with a human-readable kind label", () => {
    render(<TurnCard turn={turn()} spoken={[spokenLine()]} />);
    expect(screen.getByText("Spoken output")).toBeTruthy();
    expect(screen.getByText("Das hat zu lange gedauert.")).toBeTruthy();
    // The raw "timeout" kind is shown via its friendly label, not the raw key.
    expect(screen.getByText("Timeout notice")).toBeTruthy();
  });

  it("falls back to the raw kind when it has no label", () => {
    render(
      <TurnCard
        turn={turn()}
        spoken={[spokenLine({ spoken_kind: "weird_new_kind", text: "Hmm." })]}
      />,
    );
    expect(screen.getByText("weird_new_kind")).toBeTruthy();
  });

  it("shows no Spoken-output section when there is nothing extra", () => {
    render(<TurnCard turn={turn({ jarvis_text: "Es ist drei Uhr." })} spoken={[]} />);
    expect(screen.queryByText("Spoken output")).toBeNull();
  });
});

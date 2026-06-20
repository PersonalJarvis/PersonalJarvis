/**
 * The Transcription turn card must show the "Spoken output" track — every
 * phrase Jarvis VOICED that is not the normal reply (timeout / clarify /
 * announcement / …). User report 2026-06-15: those special phrases were
 * spoken but never appeared in the transcript.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TurnCard, formatTurnPlain } from "./TurnCard";
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
    awaiting_confirmation: false,
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

  it("renders the technical detail line under a failure readback", () => {
    // The voice is humanized, but the transcript surfaces the exit code + raw
    // harness reason for debugging (user request 2026-06-16).
    render(
      <TurnCard
        turn={turn()}
        spoken={[
          spokenLine({
            spoken_kind: "completion",
            text: "Das am Bildschirm hat nicht geklappt.", // i18n-allow: German voice fixture
            detail: "exit 5 · 5 guard-blocked actions this mission",
          }),
        ]}
      />,
    );
    expect(
      screen.getByText("exit 5 · 5 guard-blocked actions this mission"),
    ).toBeTruthy();
  });

  it("shows no detail line when a spoken phrase has no technical detail", () => {
    render(<TurnCard turn={turn()} spoken={[spokenLine()]} />);
    expect(screen.queryByText(/exit \d+/)).toBeNull();
  });

  it("renders a sub-agent readback with its own label and a distinct colour", () => {
    const { container } = render(
      <TurnCard
        turn={turn()}
        spoken={[
          spokenLine({
            spoken_kind: "subagent",
            text: "Erledigt. Der Sub-Agent hat fünf Themen gefunden.", // i18n-allow: German voice fixture
          }),
        ]}
      />,
    );
    // Attributed label, not the generic "Background result".
    expect(screen.getByText("Jarvis Sub-Agent / Output")).toBeTruthy();
    // The line block is tinted violet (agent) — visibly distinct from the sky
    // tint used by every other spoken kind.
    const line = container.querySelector('[data-spoken-kind="subagent"]');
    expect(line).not.toBeNull();
    expect(line?.className).toContain("violet");
    expect(line?.className).not.toContain("sky-400");
  });

  it("keeps a generic completion readback on the sky-tinted track", () => {
    const { container } = render(
      <TurnCard
        turn={turn()}
        spoken={[spokenLine({ spoken_kind: "completion", text: "Fertig." })]}
      />,
    );
    expect(screen.getByText("Background result")).toBeTruthy();
    const line = container.querySelector('[data-spoken-kind="completion"]');
    expect(line?.className).toContain("sky-400");
    expect(line?.className).not.toContain("violet");
  });

  it("labels a pending two-turn confirmation reply distinctly", () => {
    render(
      <TurnCard
        turn={turn({
          jarvis_text: "Soll ich die E-Mail wirklich senden? Sag ja oder nein.", // i18n-allow: German voice fixture
          awaiting_confirmation: true,
        })}
      />,
    );
    expect(screen.getByText("Awaiting confirmation")).toBeTruthy();
  });

  it("shows no awaiting label on a normal settled reply", () => {
    render(
      <TurnCard
        turn={turn({ jarvis_text: "Es ist drei Uhr.", awaiting_confirmation: false })} // i18n-allow: German voice fixture
      />,
    );
    expect(screen.queryByText("Awaiting confirmation")).toBeNull();
  });

  it("marks a pending confirmation in the copied plain text", () => {
    const copied = formatTurnPlain(
      turn({ jarvis_text: "Soll ich senden?", awaiting_confirmation: true }), // i18n-allow: German voice fixture
    );
    expect(copied).toContain("(awaiting confirmation)");
  });

  it("copies spoken preambles before the final Jarvis reply", () => {
    const copied = formatTurnPlain(
      turn({ ended_ms: 1_717_780_020_000, jarvis_text: "Final answer." }),
      [
        spokenLine({
          ts_ms: 1_717_780_001_000,
          spoken_kind: "preamble",
          text: "Preamble first.",
        }),
      ],
    );

    expect(copied.indexOf("[SPOKEN: PREAMBLE] Preamble first.")).toBeLessThan(
      copied.indexOf("[JARVIS] Final answer."),
    );
  });
});

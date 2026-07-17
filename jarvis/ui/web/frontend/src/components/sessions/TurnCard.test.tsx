/**
 * The Transcription turn card uses playback-confirmed replies as its primary
 * assistant text and keeps status phrases/readbacks on a supplemental track.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { useEventStore } from "@/store/events";
import { TurnCard, formatTurnPlain } from "./TurnCard";
import type { VoiceSpokenLine, VoiceTurnRow } from "./types";

// Arbitrary wake-word-derived name: the subagent label must brand itself with
// whatever assistant name is configured, never a fixed product name.
beforeEach(() => {
  useEventStore.setState({ assistantName: "Athena" });
});

afterEach(() => {
  cleanup();
  useEventStore.setState({ assistantName: "Assistant" });
});

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
    voice_name: "",
    voice_provider: "",
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
  it("uses the contiguous display number instead of a stale stored index", () => {
    render(<TurnCard turn={turn({ idx: 1 })} displayNumber={1} />);

    expect(screen.getByText("Turn 1")).toBeTruthy();
    expect(screen.queryByText("Turn 2")).toBeNull();
  });

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

  it("uses the playback-confirmed reply instead of unplayed model text", () => {
    render(
      <TurnCard
        turn={turn({ jarvis_text: "Generated text that never reached audio." })}
        spoken={[
          spokenLine({
            spoken_kind: "reply",
            text: "This sentence reached playback.",
          }),
        ]}
      />,
    );

    expect(screen.getByText("This sentence reached playback.")).toBeTruthy();
    expect(screen.queryByText("Generated text that never reached audio.")).toBeNull();
    expect(screen.queryByText("Spoken output")).toBeNull();
  });

  it("wraps very long transcript content without widening the turn card", () => {
    const longUserText = `user-${"x".repeat(400)}`;
    const longJarvisText = `assistant-${"y".repeat(400)}`;
    const { container } = render(
      <TurnCard
        turn={turn({ user_text: longUserText, jarvis_text: longJarvisText })}
      />,
    );

    expect(container.firstElementChild?.className).toContain("min-w-0");
    for (const text of [longUserText, longJarvisText]) {
      const block = screen.getByText(text);
      expect(block.className).toContain("whitespace-pre-wrap");
      expect(block.className).toContain("[overflow-wrap:anywhere]");
    }
  });

  it("does NOT surface the technical detail in the transcript", () => {
    // The technical diagnostic (exit code + raw harness reason) is still recorded
    // on the SpeechSpoken event so the Run Inspector can show it, but it must NOT
    // appear in the Transcription section — the transcript shows only what was
    // said/spoken (user request 2026-06-22, reversing the 2026-06-16 ask).
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
    // The spoken phrase is still shown…
    expect(screen.getByText("Das am Bildschirm hat nicht geklappt.")).toBeTruthy();
    // …but the technical detail (and its "detail" label) is not.
    expect(
      screen.queryByText("exit 5 · 5 guard-blocked actions this mission"),
    ).toBeNull();
    expect(screen.queryByText("detail")).toBeNull();
  });

  it("omits the technical detail from the copied transcript text", () => {
    // The copy-as-plain-text export is the transcript's textual form, so the
    // technical detail must not ride along there either — only in the Run Inspector.
    const copied = formatTurnPlain(turn({ ended_ms: 1_717_780_020_000 }), [
      spokenLine({
        ts_ms: 1_717_780_001_000,
        spoken_kind: "completion",
        text: "Das am Bildschirm hat nicht geklappt.", // i18n-allow: German voice fixture
        detail: "exit 5 · 5 guard-blocked actions this mission",
      }),
    ]);
    expect(copied).toContain("Das am Bildschirm hat nicht geklappt.");
    expect(copied).not.toContain("[DETAIL]");
    expect(copied).not.toContain("exit 5");
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
    // Attributed label, not the generic "Background result" — branded with the
    // configured assistant name.
    expect(screen.getByText("Athena-Agent / Output")).toBeTruthy();
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

  it("shows which voice actually spoke when the turn recorded one", () => {
    render(
      <TurnCard
        turn={turn({
          jarvis_text: "Servus!", // i18n-allow: German voice fixture
          voice_name: "Fenrir",
          voice_provider: "gemini-live",
        })}
      />,
    );
    expect(screen.getByText("Fenrir · gemini-live")).toBeTruthy();
  });

  it("shows no voice badge when the speaking voice is unknown", () => {
    render(<TurnCard turn={turn({ jarvis_text: "Hallo." })} />); // i18n-allow: German voice fixture
    expect(screen.queryByText(/gemini-live/)).toBeNull();
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

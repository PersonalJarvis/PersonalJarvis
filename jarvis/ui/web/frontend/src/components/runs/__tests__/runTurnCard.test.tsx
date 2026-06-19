import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));

import { RunTurnCard } from "../RunTurnCard";
import type { RunTurn } from "../types";

const turn: RunTurn = {
  idx: 0, trace_id: "t1", outcome: "partial",
  user_text: "Open Discord please", jarvis_text: "On it, Boss.",
  tier: "deep", provider: "gemini", model: "gemini-3.1-pro",
  tokens_in: 120, tokens_out: 40, cost_usd: 0.016, think_ms: 2800, speak_ms: 1200,
  transcript: [
    { role: "user", kind: "TranscriptFinal", text: "Open Discord please", offset_ms: 0, ts_ms: 0, spoken_kind: null },
    { role: "jarvis", kind: "ResponseGenerated", text: "On it, Boss.", offset_ms: 10, ts_ms: 10, spoken_kind: null },
    { role: "system", kind: "SystemStateChanged", text: "LISTENING -> THINKING", offset_ms: 5, ts_ms: 5, spoken_kind: null },
    { role: "system", kind: "SpeechSpoken", text: "exit 5 - harness reported failure", offset_ms: 50, ts_ms: 50, spoken_kind: null },
  ],
  timeline: [], latency: [], decision_path: [], tools: [], errors: [],
  extras: { interrupted: false, cache_hit: null, endpoint_reason: null, context_tokens: null },
  activity: { tools: ["search_web"], agents: ["computer_use"] },
};

describe("RunTurnCard", () => {
  it("shows the conversation, the triggered capabilities and the system trace inline", () => {
    const { container } = render(<RunTurnCard turn={turn} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Turn 1");
    expect(text).toContain("Open Discord please");   // user block
    expect(text).toContain("On it, Boss.");          // jarvis block
    expect(text).toContain("Computer-Use");          // triggered agent badge
    expect(text).toContain("search_web");            // triggered tool chip
    expect(text).toContain("exit 5");                // system output in "what happened"
    // raw state-machine churn is filtered out of the readable trace
    expect(text).not.toContain("LISTENING -> THINKING");
    // outcome dot
    expect(container.querySelector('[data-outcome="partial"]')).not.toBeNull();
  });
});

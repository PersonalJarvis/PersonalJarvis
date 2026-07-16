import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

vi.mock("@/hooks/useRuns", () => ({
  useRunDetail: () => ({
    isLoading: false,
    data: {
      session: { id: "s1", started_ms: 1, ended_ms: null, hangup_reason: "idle_timeout",
        turn_count: 1, total_cost_usd: 0.012, total_tokens_in: 0, total_tokens_out: 0,
        providers_used: [], language: "en", wake_keyword: "" },
      outcome: "success",
      turns: [{ idx: 0, trace_id: "t1", outcome: "success", user_text: "hi", jarvis_text: "yo",
        tier: "router", provider: "claude-api", model: "opus", tokens_in: 0, tokens_out: 0,
        cost_usd: 0.012, think_ms: 0, speak_ms: 0, transcript: [], timeline: [], latency: [],
        decision_path: [], tools: [], errors: [],
        extras: { interrupted: false, cache_hit: null, endpoint_reason: null, context_tokens: null },
        activity: { tools: [], agents: [] } }],
      missions: [],
      activity: { tools: [], agents: [] },
      analytics: { total_duration_s: 1.0, total_think_ms: 0, total_speak_ms: 0,
        total_tokens_in: 0, total_tokens_out: 0, cost_by_provider: {}, tool_counts: {},
        interruptions: 0, worst_slo_status: "ok" },
    },
  }),
}));
vi.mock("@/i18n", () => ({
  useT: () => (k: string) => k,
  useUiLanguage: () => "en",
}));

import { RunDetail } from "../RunDetail";

describe("RunDetail", () => {
  it("renders the outcome header and a fully-visible turn card", () => {
    const { container } = render(<RunDetail sessionId="s1" />);
    expect(container.querySelector('[data-testid="run-detail"]')).not.toBeNull();
    expect(container.querySelector('[data-outcome="success"]')).not.toBeNull();
    expect(container.textContent).toContain("Success");
    // turn content is visible without expanding anything
    expect(container.querySelector('[data-testid="run-turn-card"]')).not.toBeNull();
    expect(container.textContent).toContain("hi");
    expect(container.textContent).toContain("yo");
  });
});

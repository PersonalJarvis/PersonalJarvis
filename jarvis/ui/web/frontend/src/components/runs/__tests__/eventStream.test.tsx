import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";

vi.mock("@/i18n", () => ({
  useT: () => (k: string) => k,
  useUiLanguage: () => "en",
}));
vi.mock("@/store/events", () => ({
  useEventStore: (sel: (s: { pushToast: () => void }) => unknown) =>
    sel({ pushToast: () => undefined }),
}));

import { EventStream } from "../EventStream";
import type { RawEvent } from "../types";

const events: RawEvent[] = [
  { seq: 1, kind: "VoiceTurnStarted", category: "lifecycle", ts_ms: 1000, offset_ms: 0,
    summary: "turn 1 opened", payload: { turn_index: 0 } },
  { seq: 2, kind: "LatencySpan", category: "latency", ts_ms: 1041, offset_ms: 41,
    summary: "realtime_routing_decision · 41ms", payload: { phase: "realtime_routing_decision", duration_ms: 40.7 } },
  { seq: 3, kind: "BrainTurnCompleted", category: "brain", ts_ms: 8000, offset_ms: 7000,
    summary: "grok/grok-4.3 · 72729+142 tok · $0.0913", payload: { provider: "grok", tokens_in: 72729 } },
];

describe("EventStream", () => {
  it("renders every event with its offset, kind and summary", () => {
    const { container } = render(<EventStream events={events} />);
    const text = container.textContent ?? "";
    expect(text).toContain("VoiceTurnStarted");
    expect(text).toContain("+41ms");
    expect(text).toContain("realtime_routing_decision · 41ms");
    expect(text).toContain("$0.0913");
    expect(container.querySelectorAll("li")).toHaveLength(3);
  });

  it("filters by lane", () => {
    const { container, getByTestId } = render(<EventStream events={events} />);
    fireEvent.click(getByTestId("lane-brain"));
    expect(container.querySelectorAll("li")).toHaveLength(1);
    expect(container.textContent).toContain("BrainTurnCompleted");
    expect(container.textContent).not.toContain("VoiceTurnStarted");
  });

  it("filters by free text across kind, summary and payload", () => {
    const { container, getByTestId } = render(<EventStream events={events} />);
    fireEvent.change(getByTestId("event-filter"), { target: { value: "grok" } });
    expect(container.querySelectorAll("li")).toHaveLength(1);
    expect(container.textContent).toContain("BrainTurnCompleted");
  });

  it("reveals the verbatim payload on click — the ground truth behind a summary", () => {
    const { container, getByText } = render(<EventStream events={events} />);
    expect(container.querySelector("pre")).toBeNull();
    fireEvent.click(getByText("LatencySpan"));
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain("realtime_routing_decision");
    expect(pre?.textContent).toContain("40.7");
  });

  it("states truncation instead of silently cutting the stream", () => {
    const { container } = render(<EventStream events={events} truncated />);
    expect(container.textContent).toContain("run_inspector.stream.truncated");
  });

  it("degrades to an empty marker with no events", () => {
    const { container } = render(<EventStream events={[]} />);
    expect(container.textContent).toContain("run_inspector.stream.empty");
  });

  it("styles an unknown lane instead of failing", () => {
    const odd: RawEvent[] = [
      { seq: 9, kind: "FutureEvent", category: "quantum", ts_ms: 0, offset_ms: 0,
        summary: "something new", payload: {} },
    ];
    const { container } = render(<EventStream events={odd} />);
    expect(container.textContent).toContain("FutureEvent");
    expect(container.querySelector('[data-category="quantum"]')).not.toBeNull();
  });
});

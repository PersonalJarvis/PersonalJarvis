import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { LatencyWaterfall } from "../LatencyWaterfall";

describe("LatencyWaterfall", () => {
  it("colors each phase by slo_status and shows an empty note", () => {
    const { container, rerender } = render(<LatencyWaterfall entries={[]} />);
    expect(container.textContent).toContain("n/a");
    rerender(<LatencyWaterfall entries={[
      { phase: "intent_decision", duration_ms: 200, slo_status: "breach" },
      { phase: "stt_finalize", duration_ms: 40, slo_status: "ok" },
    ]} />);
    expect(container.textContent).toContain("intent_decision");
    const row = container.querySelector('[data-testid="lat-intent_decision"]');
    expect(row?.getAttribute("data-slo")).toBe("breach");
  });
});

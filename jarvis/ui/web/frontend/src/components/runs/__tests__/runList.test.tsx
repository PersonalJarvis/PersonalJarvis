import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { RunList } from "../RunList";
import type { RunListItem } from "../types";

const item: RunListItem = {
  session_id: "s1", started_ms: Date.now(), ended_ms: null, duration_s: 1.2,
  hangup_reason: "idle_timeout", wake_source: "voice", turn_count: 3,
  total_cost_usd: 0, error_count: 0, outcome: "partial", slo_status: "breach",
  feature_tags: ["computer_use", "cli_gcloud"], preview: "do a thing",
};

describe("RunList", () => {
  it("colors by outcome, shows feature badges + a slow chip, fires onSelect", () => {
    const onSelect = vi.fn();
    const { container } = render(<RunList items={[item]} selectedId={null} onSelect={onSelect} />);
    expect(container.textContent).toContain("do a thing");
    // outcome dot, not a SLO dot
    expect(container.querySelector('[data-outcome="partial"]')).not.toBeNull();
    // feature badges surface the agent + tool
    expect(container.textContent).toContain("Computer-Use");
    expect(container.textContent).toContain("cli_gcloud");
    // a slow run is flagged as latency, not failure
    expect(container.textContent?.toLowerCase()).toContain("slow");
    fireEvent.click(container.querySelector("button")!);
    expect(onSelect).toHaveBeenCalledWith("s1");
  });
});

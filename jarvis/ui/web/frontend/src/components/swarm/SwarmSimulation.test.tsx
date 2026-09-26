import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SwarmSimulation } from "./SwarmSimulation";
import { snapshotFixture } from "./testFixtures";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(cleanup);

describe("live team simulation", () => {
  it("shows persisted work and bounded events, opens evidence and clears them on team switch", () => {
    const snapshot = snapshotFixture();
    snapshot.agents[0].tool_activity = "swarm_execute";
    snapshot.activity = Array.from({ length: 12 }, (_, i) => ({ id: `event-${i}`, team_id: "alpha", seq: String(12 - i), kind: "message.delivered", summary: `Delivered result ${i}`, agent_id: "alpha-lead", task_id: null, trace_id: "trace", created_at: 1, data: {} }));
    const onAgent = vi.fn(); const onRecord = vi.fn();
    const { rerender } = render(<SwarmSimulation snapshot={snapshot} selected="" onAgent={onAgent} onRecord={onRecord} />);
    expect(screen.getByText("Tool: swarm_execute")).toBeTruthy();
    const activity = screen.getByRole("group", { name: "Recent team activity" });
    expect(within(activity).getAllByRole("button")).toHaveLength(6);
    expect(within(activity).queryByText("Delivered result 6")).toBeNull();
    fireEvent.click(within(activity).getByRole("button", { name: "Delivered result 0 #12" }));
    expect(onRecord).toHaveBeenCalledWith("events", "event-0");
    fireEvent.click(screen.getByRole("button", { name: /alpha-lead/ }));
    expect(onAgent).toHaveBeenCalledWith("alpha-lead", undefined);
    rerender(<SwarmSimulation snapshot={snapshotFixture("beta")} selected="" onAgent={onAgent} onRecord={onRecord} />);
    expect(screen.queryByText("Tool: swarm_execute")).toBeNull();
    expect(screen.queryByRole("group", { name: "Recent team activity" })).toBeNull();
    expect(screen.getByRole("button", { name: /beta-lead/ })).toBeTruthy();
    expect(document.querySelector("canvas")).toBeNull();
  });
});

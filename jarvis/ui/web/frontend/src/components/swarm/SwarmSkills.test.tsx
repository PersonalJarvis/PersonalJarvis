import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { measuredRateText, SwarmRecheck, SwarmSkills } from "./SwarmSkills";
import { validRate } from "./reputationTypes";

vi.mock("@/i18n", () => ({ useUiLanguage: () => "en" }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const profile = {
  team_id: "alpha", agent_id: "worker", level: 2, has_more: false,
  profiles: [{ domain: "math", level: 2, reliability: 0.8, uncertainty: 0.12, samples: "5", verified_tasks: "4", difficulty_counts: { "1": "1", "5": "3" }, credits: 4, next_level_credits: 8,
    acceptance: { numerator: "4", denominator: "5", rate: 0.8 },
    regression: { numerator: "0", denominator: "0", rate: null }, rollback: { numerator: null, denominator: null, rate: null } }],
  history: [{ id: "rating", task_id: "task", evidence_key: "proof", domain: "math", previous_level: 1, level: 2, credit_delta: 4, reason: "Independently accepted", created_at: 1000 }],
};
describe("measured Swarm skill progression", () => {
  it("renders explicit samples, measured denominators and linked level changes", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(profile)));
    const onTask = vi.fn();
    render(<SwarmSkills teamId="alpha" agentId="worker" awake onTask={onTask} />);
    expect(await screen.findByText("80.0% (4 / 5)")).toBeTruthy();
    expect(screen.getByText("± 12.0% · 5 evidence samples")).toBeTruthy();
    expect(screen.getByText("Not measured (0 / 0)")).toBeTruthy();
    expect(screen.getByText("Not measured")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("max")).toBe("8");
    fireEvent.click(screen.getByRole("button", { name: "math · Level 1 → 2" }));
    expect(onTask).toHaveBeenCalledWith("task");
  });
  it("rejects foreign profiles and never fetches while backgrounded", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(profile)));
    vi.stubGlobal("fetch", fetch);
    const view = render(<SwarmSkills teamId="beta" agentId="worker" awake={false} onTask={() => {}} />);
    expect(fetch).not.toHaveBeenCalled();
    view.rerender(<SwarmSkills teamId="beta" agentId="worker" awake onTask={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Mismatched");
    expect(screen.queryByText("80.0% (4 / 5)")).toBeNull();
  });
  it("keeps unknown metrics distinct from measured zero and preserves large denominators", () => {
    expect(validRate({ numerator: null, denominator: null, rate: 0 })).toBe(false);
    expect(validRate({ numerator: "0", denominator: "0", rate: null })).toBe(true);
    expect(measuredRateText({ numerator: "0", denominator: "100000000000000000000", rate: 0 }, "Unknown"))
      .toContain(BigInt("100000000000000000000").toLocaleString());
  });
  it("performs an owner recheck in StrictMode and reports unsupported review honestly", async () => {
    let path = "";
    vi.stubGlobal("fetch", async (url: string) => { path = url; return new Response(JSON.stringify({
      id: "check", team_id: "alpha", task_id: "task", state: "unsupported", reason: "New review budget required", evidence_ids: [], rating_id: null,
    })); });
    render(<StrictMode><SwarmRecheck teamId="alpha" taskId="task" onEvidence={() => {}} /></StrictMode>);
    fireEvent.click(screen.getByRole("button", { name: "Recheck original proof" }));
    expect((await screen.findByRole("status")).textContent).toContain("Independent review required: New review budget required");
    expect(path).toBe("/api/swarm/teams/alpha/tasks/task/recheck");
  });
});

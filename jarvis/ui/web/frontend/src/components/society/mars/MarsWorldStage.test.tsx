import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { MarsSceneProps } from "./MarsScene";
import { MarsWorldStage } from "./MarsWorldStage";
import { navigationSnapshotSchema } from "./navigationApi";
import { VIEW_KEY } from "./viewPreferences";
import { WORLD } from "./world";

const queries = vi.hoisted(() => ({ navigation: {} as Record<string, unknown>, roster: {} as Record<string, unknown>, rosterOptions: {} as Record<string, unknown> }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@tanstack/react-query", () => ({ useQuery: (options: Record<string, unknown>) => { queries.rosterOptions = options; return queries.roster; } }));
vi.mock("@react-three/fiber", () => ({ Canvas: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("framer-motion", () => ({ useReducedMotion: () => true }));
vi.mock("@/hooks/useCanvasAwake", () => ({ useCanvasAwake: () => true }));
vi.mock("@/hooks/useWebglSurface", () => ({ useWebglSurface: () => ({ generation: 0 }) }));
vi.mock("@/lib/graphDimension", () => ({ useWebglSupported: () => true }));
vi.mock("../companion/useCompanionPresentation", () => ({ useCompanionPresentation: () => "idle" }));
vi.mock("./MarsBackgroundControl", () => ({ MarsBackgroundControl: () => null }));
vi.mock("./useMarsNavigation", async () => {
  const { createContext } = await import("react");
  return { MarsNavigationContext: createContext(null), useMarsNavigation: () => queries.navigation };
});
vi.mock("./MarsScene", () => ({ MarsScene: (props: MarsSceneProps) => <div data-testid="scene" data-focus={props.gigiFocus} data-target={JSON.stringify(props.followTarget)}>
  <button onClick={props.onOrbit}>Manual orbit</button>
  <button onClick={() => props.onSelect("operations")}>Select building</button>
  <button onClick={() => props.onSelectAgent?.("one")}>Inspect agent</button>
  <button onClick={() => props.onSavePose({ position: [300, 82, 130], target: [290, 70, 110] })}>Save camera</button>
</div> }));

const record = {
  command_id: "first", request_id: "request", agent_id: "one", trace_id: "trace",
  world_id: "mars:ordinary", station_id: "communications-console", mode: "pedestrian",
  graph_version: 1, graph_signature: "a".repeat(64), state: "moving", presence: "placed",
  position: [268, 58, 68], current_node: "outpost-arrival", edge_id: "route-03",
  next_node: "console-approach", edge_progress: 0.3, reason: "",
};
function navigation(commands = [record]) {
  return navigationSnapshotSchema.parse({ world_id: WORLD.world_id, schema_version: 1, graph_version: 1,
    graph_signature: "a".repeat(64), seq: 1, commands, occupancies: [] });
}
function stage() { return document.querySelector("[data-mars-world]") as HTMLElement; }
function start() { fireEvent.change(screen.getByRole("combobox", { name: "society.mars.follow_agent" }), { target: { value: "one" } }); }
function saved() { return JSON.parse(localStorage.getItem(VIEW_KEY) ?? "null"); }
beforeEach(() => {
  localStorage.clear();
  queries.navigation = { data: navigation(), isSuccess: true, isFetchedAfterMount: true, isError: false, fetchStatus: "idle" };
  queries.roster = { data: [{ agent_id: "one", name: "Worker", state: "idle" }], isSuccess: true, isFetchedAfterMount: true, isError: false, fetchStatus: "idle" };
});
afterEach(() => { cleanup(); localStorage.clear(); });

describe("Mars stage follow arbitration", () => {
  it("keeps inspection independent, starts follow accessibly, and refreshes only the active roster", () => {
    const inspect = vi.fn(); render(<MarsWorldStage onOpenLedger={() => undefined} onSelectAgent={inspect} />);
    expect(queries.rosterOptions.refetchInterval).toBe(false);
    fireEvent.click(screen.getByText("Inspect agent")); expect(inspect).toHaveBeenCalledWith("one");
    expect(stage().dataset.marsMode).toBe("overview");
    start();
    expect(stage().dataset.marsMode).toBe("follow");
    expect(stage().dataset.marsFollowAgent).toBe("one");
    expect(document.activeElement).toBe(screen.getByRole("application"));
    expect(saved().followAgentId).toBe("one");
    const interval = queries.rosterOptions.refetchInterval as () => number;
    expect(interval()).toBeGreaterThanOrEqual(2500); expect(interval()).toBeLessThan(3000);
  });

  it.each(["Manual orbit", "society.mars.stop_follow", "society.mars.overview", "society.mars.outpost", "society.mars.walk", "Select building"])("%s takes ownership from follow and preserves the last saved pose", (action) => {
    render(<MarsWorldStage onOpenLedger={() => undefined} />); start();
    fireEvent.click(screen.getByText("Save camera")); const pose = saved().pose;
    fireEvent.click(screen.getByRole("button", { name: action }));
    expect(stage().dataset.marsMode).not.toBe("follow");
    expect(stage().dataset.marsFollowAgent).toBe("");
    expect(saved().pose).toEqual(pose); expect(saved().followAgentId).toBeNull();
    expect(queries.rosterOptions.refetchInterval).toBe(false);
  });

  it("cancels pending Gigi focus when following, and gives a new Gigi request camera ownership", () => {
    render(<MarsWorldStage onOpenLedger={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "society.mars.gigi_focus" }));
    expect(screen.getByTestId("scene").dataset.focus).toBe("1");
    start(); expect(screen.getByTestId("scene").dataset.focus).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "society.mars.gigi_focus" }));
    expect(stage().dataset.marsMode).toBe("orbit"); expect(stage().dataset.marsFollowAgent).toBe("");
    expect(screen.getByTestId("scene").dataset.focus).toBe("2");
  });

  it("holds confirmed placement offline, resumes after reconnect, and cancels confirmed deletion", () => {
    const view = render(<MarsWorldStage onOpenLedger={() => undefined} />); start();
    fireEvent.click(screen.getByText("Save camera"));
    const target = screen.getByTestId("scene").dataset.target;
    queries.navigation = { ...queries.navigation, data: undefined, isSuccess: false, isError: true };
    view.rerender(<MarsWorldStage onOpenLedger={() => undefined} />);
    expect(stage().dataset.marsMode).toBe("follow"); expect(stage().dataset.marsFollowState).toBe("offline");
    expect(screen.getByTestId("scene").dataset.target).toBe(target);
    expect(screen.getByText("society.mars.follow_offline")).toBeTruthy();
    queries.navigation = { ...queries.navigation, data: navigation([{ ...record, command_id: "replacement", position: [274, 58, 72] }]), isSuccess: true, isError: false };
    view.rerender(<MarsWorldStage onOpenLedger={() => undefined} />);
    expect(stage().dataset.marsFollowState).toBe("live");
    expect(JSON.parse(screen.getByTestId("scene").dataset.target ?? "null").position).toEqual([274, 58, 72]);
    queries.roster = { ...queries.roster, data: [] };
    view.rerender(<MarsWorldStage onOpenLedger={() => undefined} />);
    expect(stage().dataset.marsMode).toBe("orbit"); expect(stage().dataset.marsFollowAgent).toBe("");
    expect(screen.getByTestId("scene").dataset.target).toBe("null");
  });

  it.each([true, false])("waits on restoration and uses a safe fallback when the persisted agent is absent (pose=%s)", (hasPose) => {
    localStorage.setItem(VIEW_KEY, JSON.stringify({ world_id: WORLD.world_id, layout_version: WORLD.layout_version,
      mode: "follow", followAgentId: "one", viewpoint: "reference", neutral: false, shadows: true,
      pose: hasPose ? { position: [300, 82, 130], target: [290, 70, 110] } : null }));
    queries.navigation = { ...queries.navigation, data: undefined, isSuccess: false, isFetchedAfterMount: false };
    queries.roster = { ...queries.roster, isFetchedAfterMount: false };
    const view = render(<MarsWorldStage onOpenLedger={() => undefined} />);
    expect(stage().dataset.marsMode).toBe("follow"); expect(stage().dataset.marsFollowState).toBe("waiting");
    expect(screen.getByTestId("scene").dataset.target).toBe("null");
    queries.navigation = { ...queries.navigation, data: navigation([]), isSuccess: true, isFetchedAfterMount: true };
    view.rerender(<MarsWorldStage onOpenLedger={() => undefined} />);
    expect(stage().dataset.marsMode).toBe(hasPose ? "orbit" : "overview");
  });

  it("handles viewport Escape without taking Escape from a form or player mode", () => {
    const view = render(<MarsWorldStage onOpenLedger={() => undefined} />); start();
    const viewport = screen.getByRole("application");
    const input = document.createElement("input"); viewport.append(input); input.focus();
    fireEvent.keyDown(input, { key: "Escape" }); expect(stage().dataset.marsMode).toBe("follow");
    viewport.focus(); expect(fireEvent.keyDown(viewport, { key: "Escape" })).toBe(false);
    expect(stage().dataset.marsMode).toBe("orbit");
    fireEvent.click(screen.getByRole("button", { name: "society.mars.walk" }));
    expect(fireEvent.keyDown(viewport, { key: "Escape" })).toBe(true);
    expect(stage().dataset.marsMode).toBe("player");
    view.unmount();
  });
});

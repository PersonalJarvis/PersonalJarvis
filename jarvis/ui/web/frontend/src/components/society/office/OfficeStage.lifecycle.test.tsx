import { useContext, useEffect, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CanvasActivity } from "@/hooks/useCanvasAwake";
import { useOfficeStore } from "./officeStore";
import { useProgression } from "../progression/progressionStore";
import { OfficeStage } from "./OfficeStage";

const state = vi.hoisted(() => ({
  canvasMounts: 0, canvasUnmounts: 0, visits: 0, maps: 0,
  roster: { data: { agents: [], sample: false } },
  coding: { occupants: [], byAgentId: new Map(), loaded: true },
  rosterEnabled: vi.fn(), codingEnabled: vi.fn(), chatsEnabled: vi.fn(),
  client: { invalidateQueries: vi.fn(async () => undefined) },
}));

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => state.client }));
vi.mock("framer-motion", () => ({ useReducedMotion: () => false }));
vi.mock("@/hooks/useWebglSurface", () => ({ useWebglSurface: () => ({ generation: 0 }) }));
vi.mock("@/lib/graphDimension", () => ({ useWebglSupported: () => true }));
vi.mock("@/hooks/usePets", () => ({ useActivePet: () => null }));
vi.mock("../companion/companionPetStore", () => ({ useSyncCompanionPet: () => undefined }));
vi.mock("../data", () => ({
  useSocietyRoster: (enabled: boolean) => { state.rosterEnabled(enabled); return state.roster; },
  useQuickCreateAgent: () => async () => undefined,
}));
vi.mock("./codingFloor", () => ({
  useCodingFloorOccupants: (enabled: boolean) => { state.codingEnabled(enabled); return state.coding; },
}));
vi.mock("./useDeskChats", () => ({
  useDeskChats: (_sessions: unknown, enabled: boolean) => { state.chatsEnabled(enabled); return new Map(); },
}));
vi.mock("./useDprBudget", () => ({ useDprBudget: () => 1 }));
vi.mock("@react-three/fiber", () => ({
  advance: () => undefined,
  Canvas: ({ children, frameloop }: { children: ReactNode; frameloop: string }) => {
    const active = useContext(CanvasActivity);
    useEffect(() => {
      state.canvasMounts += 1;
      return () => { state.canvasUnmounts += 1; };
    }, []);
    return <div data-testid="canvas" data-loop={frameloop} data-active={active}>{children}</div>;
  },
}));
vi.mock("./OfficeScene", () => ({ OfficeScene: ({ awake }: { awake: boolean }) => <div data-testid="scene" data-awake={awake} /> }));
vi.mock("./OfficeCameraRig", () => ({ ZOOM_SECONDS: 1 }));
vi.mock("./OfficeFrameDriver", () => ({ OfficeFrameDriver: () => null }));
vi.mock("./OfficePlayer", () => ({ ownsKeyboard: () => false }));
vi.mock("./OfficePanels", () => ({ AgentPanel: () => null, CheckpointPanel: () => null }));
vi.mock("./PaneCommandPanel", () => ({ PaneCommandPanel: () => null }));
vi.mock("./ArcadeCabinet", () => ({ ArcadeCabinet: () => null }));
vi.mock("./ElevatorPanel", () => ({ ElevatorPanel: () => null }));
vi.mock("./ElevatorDoors", () => ({ ElevatorDoors: () => null }));
vi.mock("./OfficeCompass", () => ({ OfficeCompass: () => null }));
vi.mock("./OfficeMinimap", () => ({ OfficeMinimap: () => {
  useEffect(() => { state.maps += 1; return () => { state.maps -= 1; }; }, []);
  return null;
} }));
vi.mock("../progression/useProgressionSync", () => ({ useProgressionSync: () => {
  useEffect(() => { state.visits += 1; return () => { state.visits -= 1; }; }, []);
} }));
vi.mock("../progression/LevelHud", () => ({ LevelHud: () => null, LevelToasts: () => null }));
vi.mock("../progression/LevelUpBanner", () => ({ LevelUpBanner: () => null }));
vi.mock("../progression/hall/LevelHallScreen", () => ({ LevelHallScreen: () => null }));

afterEach(() => {
  cleanup();
  useOfficeStore.getState().select(null);
  useProgression.getState().openPanel(null);
  vi.useRealTimers();
  vi.clearAllMocks();
  state.canvasMounts = 0;
  state.canvasUnmounts = 0;
});

it("retains one canvas across chat visits while stopping input, visits, HUD and polling", () => {
  vi.useFakeTimers();
  const view = render(<OfficeStage />);
  const canvas = screen.getByTestId("canvas");
  expect(canvas.dataset.loop).toBe("always");
  expect(state.canvasMounts).toBe(1);
  expect(state.visits).toBe(1);
  expect(state.maps).toBe(1);
  fireEvent.keyDown(window, { code: "KeyH" });
  expect(useOfficeStore.getState().selection).toEqual({ kind: "checkpoint", id: "create" });

  view.rerender(<OfficeStage active={false} />);
  expect(screen.getByTestId("canvas")).toBe(canvas);
  expect(canvas.dataset.loop).toBe("never");
  expect(canvas.dataset.active).toBe("false");
  expect(screen.getByTestId("scene").dataset.awake).toBe("false");
  expect(state.visits).toBe(0);
  expect(state.maps).toBe(0);
  expect(state.rosterEnabled).toHaveBeenLastCalledWith(false);
  expect(state.codingEnabled).toHaveBeenLastCalledWith(false);
  expect(state.chatsEnabled).toHaveBeenLastCalledWith(false);
  expect(useOfficeStore.getState().selection).toBeNull();
  for (const code of ["KeyH", "KeyL", "KeyM"]) {
    const event = new KeyboardEvent("keydown", { code, bubbles: true, cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  }
  expect(useOfficeStore.getState().selection).toBeNull();
  expect(useProgression.getState().panel).toBeNull();
  act(() => vi.advanceTimersByTime(30_000));
  expect(state.client.invalidateQueries).not.toHaveBeenCalled();

  view.rerender(<OfficeStage />);
  expect(screen.getByTestId("canvas")).toBe(canvas);
  expect(canvas.dataset.loop).toBe("always");
  expect(state.canvasMounts).toBe(1);
  expect(state.canvasUnmounts).toBe(0);
  expect(state.visits).toBe(1);
  expect(state.maps).toBe(1);
  expect(state.rosterEnabled).toHaveBeenLastCalledWith(true);
  act(() => vi.advanceTimersByTime(7_000));
  expect(state.client.invalidateQueries).toHaveBeenCalledOnce();
  view.unmount();
  expect(state.canvasUnmounts).toBe(1);
  expect(state.visits).toBe(0);
  expect(state.maps).toBe(0);
});

it("releases the coding poll while a visited coding floor is hidden", () => {
  const view = render(<OfficeStage initialFloor="coding" />);
  expect(state.codingEnabled).toHaveBeenLastCalledWith(true);
  view.rerender(<OfficeStage initialFloor="coding" active={false} />);
  expect(state.codingEnabled).toHaveBeenLastCalledWith(false);
  view.rerender(<OfficeStage initialFloor="coding" />);
  expect(state.codingEnabled).toHaveBeenLastCalledWith(true);
});

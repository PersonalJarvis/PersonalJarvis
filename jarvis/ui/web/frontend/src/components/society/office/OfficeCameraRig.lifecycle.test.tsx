import { useEffect, useImperativeHandle } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PerspectiveCamera, Vector3 } from "three";
import { CanvasActivity } from "@/hooks/useCanvasAwake";
import { OfficeCameraRig } from "./OfficeCameraRig";
import { buildOfficeLayout } from "./officeLayout";
import { officeSession, useOfficeStore } from "./officeStore";

const state = vi.hoisted(() => ({
  frames: new Set<(_state: unknown, dt: number) => void>(),
  renderer: null as unknown as { camera: PerspectiveCamera; size: { width: number; height: number }; gl: { domElement: HTMLCanvasElement }; scene: object; events: { connected: undefined } },
  controls: null as unknown as { target: Vector3; enabled: boolean; update: () => void; mouseButtons: { LEFT: number } },
}));

vi.mock("@react-three/fiber", () => ({
  useThree: (select: (value: typeof state.renderer) => unknown) => select(state.renderer),
  useFrame: (frame: (_state: unknown, dt: number) => void) => {
    useEffect(() => { state.frames.add(frame); return () => { state.frames.delete(frame); }; }, [frame]);
  },
}));
vi.mock("@react-three/drei", async () => {
  const { forwardRef } = await import("react");
  return {
  OrbitControls: forwardRef(({ enabled }: { enabled: boolean }, ref) => {
    state.controls.enabled = enabled;
    useImperativeHandle(ref, () => state.controls);
    return null;
  }),
  };
});

beforeEach(() => {
  vi.useFakeTimers();
  state.renderer = {
    camera: new PerspectiveCamera(35, 1.6, 0.2, 800), size: { width: 1280, height: 800 },
    gl: { domElement: document.createElement("canvas") }, scene: {}, events: { connected: undefined },
  };
  state.controls = { target: new Vector3(), enabled: true, update: () => undefined, mouseButtons: { LEFT: 0 } };
  officeSession.camera = null;
  useOfficeStore.setState({ zoom: null, focus: null, follow: true });
});

afterEach(() => {
  cleanup();
  state.frames.clear();
  vi.useRealTimers();
});

it.each([2, 14])("returns from a hidden monitor dive after %i frames without replaying it", (frames) => {
  const layout = buildOfficeLayout([]);
  const tree = (active: boolean) => <CanvasActivity.Provider value={active}><OfficeCameraRig layout={layout} overview={0} /></CanvasActivity.Provider>;
  const view = render(tree(true));
  const initialPosition = state.renderer.camera.position.toArray();
  const initialTarget = state.controls.target.toArray();
  act(() => {
    useOfficeStore.getState().zoomInto([4, 1, 7], 0);
    for (let i = 0; i < frames; i += 1) for (const step of state.frames) step(null, 0.1);
  });
  expect(state.renderer.camera.position.toArray()).not.toEqual(initialPosition);
  expect(state.controls.enabled).toBe(false);

  view.rerender(tree(false));
  expect(state.renderer.camera.position.toArray()).toEqual(initialPosition);
  expect(state.controls.target.toArray()).toEqual(initialTarget);
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(3_000));
  expect(state.controls.enabled).toBe(false);

  view.rerender(tree(true));
  act(() => { for (const step of state.frames) step(null, 0.1); });
  expect(state.renderer.camera.position.toArray()).toEqual(initialPosition);
  expect(state.controls.target.toArray()).toEqual(initialTarget);
  expect(state.controls.enabled).toBe(true);
});

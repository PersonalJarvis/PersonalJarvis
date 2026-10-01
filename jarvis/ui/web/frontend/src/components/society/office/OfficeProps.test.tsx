import { afterEach, describe, expect, it } from "vitest";
import { act, createRoot, extend, type ReconcilerRoot } from "@react-three/fiber";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as THREE from "three";
import { Box3, type Object3D } from "three";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";
import { FurniturePiece, MeetingChairs, PROP_RENDERERS } from "./OfficeProps";

/** Just enough of a WebGLRenderer for R3F to mount a scene graph without a GPU. */
function fakeRenderer(canvas: HTMLCanvasElement) {
  const noop = () => undefined;
  return {
    domElement: canvas,
    render: noop, setSize: noop, setPixelRatio: noop, setAnimationLoop: noop, dispose: noop,
    getPixelRatio: () => 1,
    shadowMap: { enabled: false, type: 0, needsUpdate: false },
    xr: { enabled: false, isPresenting: false, addEventListener: noop, removeEventListener: noop, setAnimationLoop: noop },
    outputColorSpace: "srgb", toneMapping: 0, toneMappingExposure: 1,
    info: { render: {}, memory: {} },
  };
}

// <Canvas> registers the three namespace; a bare root must do it itself.
extend(THREE);

let root: ReconcilerRoot<HTMLCanvasElement> | null = null;

async function mount(element: JSX.Element): Promise<Object3D> {
  const canvas = document.createElement("canvas");
  const current = createRoot(canvas);
  root = current;
  current.configure({
    // Test double: R3F only needs the renderer's shape here, nothing is drawn.
    gl: fakeRenderer(canvas) as unknown as NonNullable<Parameters<typeof current.configure>[0]>["gl"],
    frameloop: "never",
    size: { width: 100, height: 100, top: 0, left: 0 },
  });
  let captured: Object3D | null = null;
  await act(async () => {
    // Some props read app data (the server room's wall reads Spend): give them a client that never retries.
    current.render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <group ref={(g) => { captured = g; }}>{element}</group>
      </QueryClientProvider>,
    );
  });
  if (!captured) throw new Error("the element did not mount");
  return captured;
}

afterEach(() => {
  root?.unmount();
  root = null;
});

const KINDS = Object.keys(FURNITURE_SIZE) as FurnitureKind[];

describe("office props", () => {
  it("has a renderer for every furniture kind", () => {
    expect(Object.keys(PROP_RENDERERS).sort()).toEqual([...KINDS].sort());
  });

  it.each(KINDS)("%s stays inside its footprint", async (kind) => {
    const size = kind === "rug" ? { w: 3, d: 2 } : undefined;
    const item: Furniture = { id: `test-${kind}`, kind, x: 0, z: 0, rotationY: 0, room: "floor", size };
    const group = await mount(<FurniturePiece item={item} />);
    group.updateMatrixWorld(true);
    const box = new Box3().setFromObject(group);
    const { w, d, h } = { ...FURNITURE_SIZE[kind], ...(size ?? {}) };
    const eps = 0.02;
    expect(box.min.x).toBeGreaterThanOrEqual(-w / 2 - eps);
    expect(box.max.x).toBeLessThanOrEqual(w / 2 + eps);
    expect(box.min.z).toBeGreaterThanOrEqual(-d / 2 - eps);
    expect(box.max.z).toBeLessThanOrEqual(d / 2 + eps);
    expect(box.min.y).toBeGreaterThanOrEqual(-eps);
    expect(box.max.y).toBeLessThanOrEqual(Math.max(h, 0.03) + 0.05);
  });

  it("puts the meeting chairs on both long sides, outside the table", async () => {
    const table: Furniture = { id: "t", kind: "meetingTable", x: 0, z: 0, rotationY: 0, room: "team" };
    const group = await mount(<MeetingChairs table={table} />);
    group.updateMatrixWorld(true);
    const box = new Box3().setFromObject(group);
    expect(box.min.z).toBeLessThan(-FURNITURE_SIZE.meetingTable.d / 2);
    expect(box.max.z).toBeGreaterThan(FURNITURE_SIZE.meetingTable.d / 2);
  });
});

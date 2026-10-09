import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createRoot, extend, type ReconcilerRoot, type RootState } from "@react-three/fiber";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OfficeAgents, type WalkerContext } from "./OfficeAgents";
import type { SocietyAgent } from "../data";
import { BUILTIN_COMPANIONS } from "../companion/petCompanions";
import { useCompanionPet } from "../companion/companionPetStore";
import { buildOfficeLayout } from "./officeLayout";
import { SpotBook } from "./officeBehavior";
import { player, useOfficeStore } from "./officeStore";
import { useEventStore } from "@/store/events";

let model: THREE.Group;
vi.mock("@react-three/drei", async (original) => ({
  ...await original<typeof import("@react-three/drei")>(),
  // Load the shipped GLB below; keep the real R3F tree and all frame callbacks.
  useGLTF: () => ({ scene: model }),
  Html: () => null,
}));

extend(THREE);
let root: ReconcilerRoot<HTMLCanvasElement> | null = null;
const originalPlayer = { ...player };
const originalPet = useCompanionPet.getState().pet;
const originalVoice = useEventStore.getState().voiceState;

beforeAll(async () => {
  const bytes = readFileSync(resolve(__dirname, "../../../assets/society/companions/pets/miso.glb"));
  const buffer = new Uint8Array(bytes).buffer;
  model = (await new GLTFLoader().parseAsync(buffer, "")).scene;
});

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  Object.assign(player, { x: 0, z: 0, heading: 0, path: [], moving: false });
  useOfficeStore.setState({ summons: {} });
  useCompanionPet.setState({ pet: { id: "miso", ...BUILTIN_COMPANIONS.miso } });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  Object.assign(player, originalPlayer);
  useOfficeStore.setState({ summons: {} });
  useCompanionPet.setState({ pet: originalPet });
  useEventStore.setState({ voiceState: originalVoice });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** R3F scene graph test double; no browser, GPU or inference calls. */
function fakeRenderer(canvas: HTMLCanvasElement) {
  const noop = () => undefined;
  return {
    domElement: canvas, render: noop, setSize: noop, setPixelRatio: noop, setAnimationLoop: noop, dispose: noop,
    getPixelRatio: () => 1, shadowMap: { enabled: false, type: 0, needsUpdate: false },
    xr: { enabled: false, isPresenting: false, addEventListener: noop, removeEventListener: noop, setAnimationLoop: noop },
    outputColorSpace: "srgb", toneMapping: 0, toneMappingExposure: 1,
    info: { render: {}, memory: {} },
  };
}

const lead = {
  agentId: "jarvis", name: "George", tier: "lead", state: "idle", figure: null,
  palette: { primary: "#f2a65a" }, provider: "", chatSessionId: null,
} as SocietyAgent;
const agents = new Map([[lead.agentId, lead]]);

async function mount(reduced: boolean) {
  const canvas = document.createElement("canvas");
  const current = createRoot(canvas);
  root = current;
  let state!: RootState;
  const scene = new THREE.Scene();
  // Check real geometry and animation without GPU reflection baking.
  scene.environment = new THREE.Texture();
  current.configure({
    scene,
    gl: fakeRenderer(canvas) as unknown as NonNullable<Parameters<typeof current.configure>[0]>["gl"],
    frameloop: "never", size: { width: 100, height: 100, top: 0, left: 0 },
    onCreated: (value) => { state = value; },
  });
  const ctx: WalkerContext = {
    layout: buildOfficeLayout([]), book: new SpotBook(), colleagues: () => [], spawn: { x: 0, z: 0 },
    // An open floor isolates presentation from obstacle routing.
    grid: { cell: 1, cols: 40, rows: 40, originX: -20, originZ: -20, blocked: new Uint8Array(1600) },
  };
  const render = async (awake = true, selectedId: string | null = null) => {
    await act(async () => {
      current.render(
        <group position={[7, 2, -4]} rotation={[0, 0.7, 0]} scale={1.6}>
          <OfficeAgents agents={agents} desks={[]} ctx={ctx} newcomers={new Set()} awake={awake}
            reduced={reduced} selectedId={selectedId} onSelect={() => undefined} />
        </group>,
      );
    });
  };
  await render();
  let time = 0;
  return {
    scene: state.scene, render,
    frame: async (dt: number) => { time += dt; await act(async () => state.advance(time)); state.scene.updateMatrixWorld(true); },
  };
}

function groundObjects(scene: THREE.Scene) {
  const cat = scene.getObjectByName("Pet_miso");
  let ring: THREE.Mesh | undefined;
  scene.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh && mesh.geometry.type === "RingGeometry") ring = mesh;
  });
  if (!cat || !ring) throw new Error("The lead's cat and floor ring must both mount");
  return { cat, ring };
}

function expectCentered(scene: THREE.Scene) {
  const { cat, ring } = groundObjects(scene);
  const torso = cat.getObjectByName("miso_Torso");
  if (!torso) throw new Error("The shipped cat must have its authored torso");
  const at = new THREE.Box3().setFromObject(torso).getCenter(new THREE.Vector3());
  const centre = ring.getWorldPosition(new THREE.Vector3());
  expect(Math.hypot(at.x - centre.x, at.z - centre.z), "cat torso projection versus ring centre").toBeLessThan(1e-6);
  // The parent floor is y=2 at scale 1.6; body hops and pulses cannot lift or resize the marker.
  expect(centre.y).toBeCloseTo(2 + 0.015 * 1.6, 6);
  expect(ring.getWorldScale(new THREE.Vector3()).x).toBeCloseTo(1.6, 6);
  expect(ring.visible, "a ground pet keeps its floor marker").toBe(true);
}

describe("the lead pet's floor anchor", () => {
  it("starts on the floor instead of descending from a flyer's hover height", async () => {
    const view = await mount(false);
    view.scene.updateMatrixWorld(true);
    const { cat } = groundObjects(view.scene);
    expect(cat.getWorldPosition(new THREE.Vector3()).y).toBeCloseTo(2, 6);
    await view.frame(1 / 60);
    expect(cat.getWorldPosition(new THREE.Vector3()).y).toBeCloseTo(2, 6);
  });

  it.each([1 / 144, 1 / 30, 0.25])("keeps the real cat centred through motion and turns at dt=%s", async (dt) => {
    const view = await mount(false);
    // Straight travel, a right turn, a reversal, then standing still.
    for (let i = 0; i < 100; i++) {
      if (i < 25) player.z += 1.2 * Math.min(dt, 0.1);
      else if (i < 50) { player.x += 1.2 * Math.min(dt, 0.1); player.heading = Math.PI / 2; }
      else if (i < 75) { player.x -= 1.2 * Math.min(dt, 0.1); player.heading = -Math.PI / 2; }
      await view.frame(dt);
      expectCentered(view.scene);
    }
    await view.render(true, lead.agentId);
    await act(async () => useEventStore.setState({ voiceState: "speaking" }));
    for (let i = 0; i < 20; i++) { await view.frame(dt); expectCentered(view.scene); }
    await view.render(false, lead.agentId);
    const before = groundObjects(view.scene).cat.getWorldPosition(new THREE.Vector3());
    player.x += 3;
    await view.frame(dt);
    expectCentered(view.scene);
    expect(groundObjects(view.scene).cat.getWorldPosition(new THREE.Vector3()).toArray()).toEqual(before.toArray());
  });

  it.each([false, true])("keeps the marker on the cat during summons and return to following (reduced=%s)", async (reduced) => {
    const view = await mount(reduced);
    view.scene.updateMatrixWorld(true);
    expectCentered(view.scene);
    await view.frame(1 / 60);
    expectCentered(view.scene);
    useOfficeStore.setState({ summons: { jarvis: { target: { x: 4, z: 3 }, untilMs: Date.now() + 60000 } } });
    for (let i = 0; i < 3; i++) { await view.frame(1 / 60); expectCentered(view.scene); }
    useOfficeStore.setState({ summons: {} });
    player.x = -6; player.z = -5; player.heading = Math.PI;
    for (let i = 0; i < 3; i++) { await view.frame(1 / 60); expectCentered(view.scene); }
  });
});

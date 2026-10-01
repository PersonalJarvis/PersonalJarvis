/**
 * The coding floor's server room: two rows of 42U racks facing each other
 * across a cold aisle, a NOC console in front of a status wall, a UPS with
 * its battery cabinet and a fire-suppression bank beside the door.
 *
 * Racks are drawn, not modelled: every row's server faces are one canvas
 * texture built from a deterministic rack plan, and the same plan places the
 * status LEDs (one instanced mesh per row) and the patch cables (another).
 * The LEDs twinkle from a single throttled useFrame per row that rewrites the
 * instance colours in place — no per-frame allocation. The screens redraw on
 * a slow timer and read the floor's live panes, so the cluster load follows
 * the coding agents at work. The status wall is the exception: it shows what
 * the Agentic IDE's agents really spent (the Spend section's read model), and
 * a click on it dives in and opens Spend.
 *
 * Pieces are built in local space centred on the origin, front facing +z, and
 * stay inside their FURNITURE_SIZE box.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import {
  CanvasTexture, Color, CylinderGeometry, InstancedMesh, MeshBasicMaterial, MeshStandardMaterial, Object3D,
  RepeatWrapping, SphereGeometry, SRGBColorSpace, TorusGeometry, type Texture,
} from "three";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { EMPTY_FILTERS, useCostSummary, type CostFilters, type CostSummary } from "@/hooks/useCosts";
import { cachedCanvasTexture, canvasMaterial } from "./canvasMaterials";
import { paneOccupants } from "./codingFloor";
import { useMonitorDive } from "./CommandOffice";
import { Box, GEO, MAT, matte, Rounded } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";

type Ctx = CanvasRenderingContext2D;

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

const SRV = {
  cabinet: matte("#1a1d22", { roughness: 0.5, metalness: 0.3 }),
  cabinetEdge: matte("#2b3038", { roughness: 0.4, metalness: 0.4 }),
  plinth: matte("#0e1013", { roughness: 0.7 }),
  handle: matte("#b9c0c9", { roughness: 0.25, metalness: 0.8 }),
  trayRail: matte("#17191c", { roughness: 0.5, metalness: 0.4 }),
  fibre: matte("#f5c518", { roughness: 0.55 }),
  cableBlue: matte("#2f6fd6", { roughness: 0.8 }),
  cableGrey: matte("#8d96a1", { roughness: 0.8 }),
  cableRed: matte("#d9453b", { roughness: 0.8 }),
  // Accent light strips: lit from within, so they read as light in any scene lighting.
  ice: new MeshStandardMaterial({ color: "#7fe9ff", emissive: "#5fdcff", emissiveIntensity: 1.4, toneMapped: false }),
  panel: new MeshStandardMaterial({ color: "#e8f6ff", emissive: "#cdeeff", emissiveIntensity: 1.2, toneMapped: false }),
  glass: new MeshStandardMaterial({
    color: "#a9dcff", transparent: true, opacity: 0.13, roughness: 0.05, metalness: 0.2, depthWrite: false,
  }),
  red: matte("#c8262b", { roughness: 0.35, metalness: 0.1 }),
  valve: matte("#aab2bc", { roughness: 0.3, metalness: 0.7 }),
  cream: matte("#ece8df", { roughness: 0.6 }),
  backplate: matte("#cfd4da", { roughness: 0.7 }),
  ledGreen: new MeshStandardMaterial({ color: "#46f08c", emissive: "#46f08c", emissiveIntensity: 1.2, toneMapped: false }),
  stoolSeat: matte("#2a3a44", { roughness: 0.8 }),
};

const SGEO = {
  cyl: new CylinderGeometry(1, 1, 1, 20),
  dome: new SphereGeometry(1, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2),
  // A half ring: a patch cable hanging in a loop below its port.
  loop: new TorusGeometry(1, 0.13, 5, 14, Math.PI),
};

/** A cached texture cloned once per repeat, so every rack door shares one drawing. */
const repeatedMaterials = new Map<string, MeshStandardMaterial>();
function repeatedMaterial(
  key: string, width: number, height: number, draw: (ctx: Ctx, w: number, h: number) => void,
  repeat: [number, number], fallback: string, roughness = 0.6,
): MeshStandardMaterial {
  const id = `${key}:${repeat.join("x")}`;
  let material = repeatedMaterials.get(id);
  if (!material) {
    const base = cachedCanvasTexture(key, width, height, draw);
    let map: Texture | null = null;
    if (base) {
      map = base.clone();
      map.wrapS = map.wrapT = RepeatWrapping;
      map.repeat.set(repeat[0], repeat[1]);
      map.needsUpdate = true;
    }
    material = new MeshStandardMaterial({ color: map ? "#ffffff" : fallback, map, roughness, metalness: 0.2 });
    repeatedMaterials.set(id, material);
  }
  return material;
}

function Panel({ size, position, material, rotationY = 0 }: {
  size: [number, number]; position: [number, number, number]; material: MeshStandardMaterial | MeshBasicMaterial; rotationY?: number;
}) {
  return (
    <mesh position={position} rotation={[0, rotationY, 0]} material={material}>
      <planeGeometry args={size} />
    </mesh>
  );
}

function Cyl({ radius, height, position, material, cast = true }: {
  radius: number; height: number; position: [number, number, number]; material: MeshStandardMaterial; cast?: boolean;
}) {
  return <mesh geometry={SGEO.cyl} material={material} position={position} scale={[radius, height, radius]} castShadow={cast} receiveShadow />;
}

/** Deterministic hash in [0, 1): the same seed always gives the same rack plan and twinkle. */
function hash01(n: number): number {
  const s = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return s - Math.floor(s);
}

function seedOf(id: string): number {
  let h = 7;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h;
}

// ---------------------------------------------------------------------------
// Rack plan: what sits in each U, where its LEDs and cables go
// ---------------------------------------------------------------------------

type UnitType = "patch" | "manager" | "switch" | "server1" | "server2" | "gpu" | "storage" | "blank" | "ups";
type RackRole = "network" | "compute" | "gpu" | "storage";

const UNIT_U: Record<UnitType, number> = { patch: 1, manager: 1, switch: 1, server1: 1, server2: 2, gpu: 4, storage: 4, blank: 1, ups: 2 };
const n = (type: UnitType, count: number): UnitType[] => Array.from({ length: count }, () => type);

/** Each role's units from the top down; a 2U UPS always sits at the bottom, the rest stays empty rails. */
const ROLE_UNITS: Record<RackRole, UnitType[]> = {
  network: ["patch", "manager", "patch", "manager", ...n("switch", 4), "blank", "patch", "manager", ...n("switch", 4), "blank", "blank", ...n("server2", 3), "blank", ...n("server1", 4)],
  compute: ["patch", "manager", "switch", "switch", "blank", ...n("server1", 10), "blank", ...n("server2", 5), "blank", ...n("server1", 6)],
  gpu: ["patch", "manager", "switch", "blank", ...n("gpu", 7), "blank", ...n("server1", 2)],
  storage: ["patch", "manager", "switch", ...n("server2", 2), ...n("storage", 6), "blank", ...n("server1", 2)],
};
const ROLE_ORDER: RackRole[] = ["network", "compute", "gpu", "gpu", "storage"];

export const RACKS_PER_ROW = 5;
const RACK_W = FURNITURE_SIZE.serverRack.w / RACKS_PER_ROW;
const ROW_W = FURNITURE_SIZE.serverRack.w;
const ROW_D = FURNITURE_SIZE.serverRack.d;
/** The 42U mounting area on the front, in metres above the floor. */
const FACE = { y0: 0.13, h: 1.9 };
const FACE_Z = ROW_D * 0.45 + 0.003;
const CANVAS = { rackPx: 192, h: 1024 };
const PX_U = CANVAS.h / 42;

type LedMode = 0 | 1 | 2; // steady, activity flicker, slow heartbeat
interface Led { px: number; py: number; colour: string; mode: LedMode; size: number }
interface CableLoop { px: number; py: number; radius: number; colour: string }
interface PlacedUnit { type: UnitType; rack: number; py: number; ph: number }
export interface RackPlan { units: PlacedUnit[]; leds: Led[]; loops: CableLoop[] }

const LED = { green: "#3dff8a", amber: "#ffb020", blue: "#38bdf8", cyan: "#5eead4", red: "#ff5a4f" };
const CABLES = ["#3b82f6", "#facc15", "#ef4444", "#22c55e", "#e5e7eb", "#f97316", "#a855f7"];

/** Device extent inside a rack column (19" between the rails). */
const DEV = { x: 24, w: 144 };

/** Lays out a row of racks for a seed: pure, so the canvas and the meshes always agree. */
export function rackPlan(seed: number): RackPlan {
  const units: PlacedUnit[] = [];
  const leds: Led[] = [];
  const loops: CableLoop[] = [];
  const shift = seed % ROLE_ORDER.length;
  let k = 0;
  const rnd = () => hash01(seed * 0.001 + (k += 1) * 1.618);
  for (let rack = 0; rack < RACKS_PER_ROW; rack += 1) {
    const role = ROLE_ORDER[(rack + shift) % ROLE_ORDER.length];
    const x0 = rack * CANVAS.rackPx + DEV.x;
    let top = 42;
    const place = (type: UnitType, u: number) => {
      const h = UNIT_U[type];
      const py = (42 - u) * PX_U;
      const ph = h * PX_U;
      units.push({ type, rack, py, ph });
      const cy = py + ph / 2;
      const right = x0 + DEV.w;
      switch (type) {
        case "server1":
          leds.push({ px: right - 20, py: cy, colour: LED.blue, mode: 0, size: 0.011 });
          leds.push({ px: right - 11, py: cy, colour: LED.green, mode: 1, size: 0.011 });
          break;
        case "server2":
          leds.push({ px: right - 12, py: py + 9, colour: LED.green, mode: 0, size: 0.012 });
          for (let i = 0; i < 4; i += 1) leds.push({ px: x0 + 18 + i * 20, py: py + ph - 8, colour: LED.green, mode: 1, size: 0.008 });
          if (rnd() < 0.12) leds.push({ px: right - 12, py: py + ph - 9, colour: LED.amber, mode: 2, size: 0.011 });
          break;
        case "gpu":
          leds.push({ px: right - 14, py: py + ph - 14, colour: LED.cyan, mode: 0, size: 0.013 });
          leds.push({ px: right - 14, py: py + 14, colour: LED.cyan, mode: 2, size: 0.012 });
          leds.push({ px: right - 14, py: cy, colour: LED.green, mode: 1, size: 0.011 });
          break;
        case "storage":
          for (let r = 0; r < 4; r += 1) for (let c = 0; c < 6; c += 2) {
            const bad = rnd() < 0.03;
            leds.push({ px: x0 + 12 + c * 19 + 14, py: py + 6 + r * 23 + 18, colour: bad ? LED.red : LED.green, mode: bad ? 2 : 1, size: 0.007 });
          }
          break;
        case "switch":
          for (let i = 0; i < 8; i += 1) leds.push({ px: x0 + 14 + i * 12, py: py + 5, colour: rnd() < 0.2 ? LED.amber : LED.green, mode: 1, size: 0.006 });
          leds.push({ px: right - 8, py: cy, colour: LED.green, mode: 0, size: 0.009 });
          break;
        case "patch":
          // Short patch cables drop from the panel into the cable manager below.
          for (let i = 0; i < 5; i += 1) {
            loops.push({ px: x0 + 16 + i * 26 + rnd() * 10, py: cy + 2, radius: 0.028 + rnd() * 0.03, colour: CABLES[Math.floor(rnd() * CABLES.length)] });
          }
          break;
        case "ups":
          leds.push({ px: right - 12, py: cy, colour: LED.green, mode: 0, size: 0.012 });
          break;
        default:
          break;
      }
    };
    for (const type of ROLE_UNITS[role]) {
      const h = UNIT_U[type];
      if (top - h + 1 <= 2) break;
      place(type, top);
      top -= h;
    }
    place("ups", 2);
  }
  return { units, leds, loops };
}

/** Canvas px → local metres on the row's front face. */
const toX = (px: number) => -ROW_W / 2 + (px / (CANVAS.rackPx * RACKS_PER_ROW)) * ROW_W;
const toY = (py: number) => FACE.y0 + (1 - py / CANVAS.h) * FACE.h;

function drawUnit(ctx: Ctx, unit: PlacedUnit, rand: () => number): void {
  const x = unit.rack * CANVAS.rackPx + DEV.x;
  const y = unit.py + 1;
  const w = DEV.w;
  const h = unit.ph - 2;
  const bezel = (fill: string) => {
    ctx.fillStyle = fill;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "rgba(255,255,255,0.07)";
    ctx.fillRect(x, y, w, 1.5);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(x, y + h - 1.5, w, 1.5);
  };
  const ears = () => {
    ctx.fillStyle = "#4a525d";
    ctx.fillRect(x, y, 5, h);
    ctx.fillRect(x + w - 5, y, 5, h);
  };
  switch (unit.type) {
    case "server1": {
      bezel("#2a2f37");
      ears();
      for (let i = 0; i < 8; i += 1) {
        ctx.fillStyle = "#15181d";
        ctx.fillRect(x + 9 + i * 12, y + 3, 10, h - 6);
        ctx.fillStyle = "#4b5461";
        ctx.fillRect(x + 9 + i * 12, y + 3, 10, 1.5);
      }
      ctx.fillStyle = "#1a1d22";
      for (let i = 0; i < 4; i += 1) ctx.fillRect(x + 108 + i * 5, y + 4, 2, h - 8);
      break;
    }
    case "server2": {
      bezel("#262b33");
      ears();
      for (let r = 0; r < 2; r += 1) for (let c = 0; c < 6; c += 1) {
        ctx.fillStyle = "#3a414b";
        ctx.fillRect(x + 9 + c * 19, y + 4 + r * 21, 17, 18);
        ctx.fillStyle = "#14171b";
        ctx.fillRect(x + 11 + c * 19, y + 7 + r * 21, 13, 10);
      }
      ctx.fillStyle = "#d8dde3";
      ctx.fillRect(x + 126, y + 18, 10, 6);
      break;
    }
    case "gpu": {
      bezel("#1b1f25");
      ears();
      // Honeycomb intake over most of the face, an accent stripe and a badge.
      ctx.fillStyle = "#0b0d10";
      for (let r = 0; r < 12; r += 1) for (let c = 0; c < 15; c += 1) {
        ctx.beginPath();
        ctx.arc(x + 12 + c * 7.4 + (r % 2) * 3.7, y + 8 + r * 7, 2.3, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = "#2dd4bf";
      ctx.fillRect(x + w - 26, y + 6, 3, h - 12);
      ctx.fillStyle = "#c9d1da";
      ctx.fillRect(x + w - 19, y + h / 2 + 8, 11, 5);
      break;
    }
    case "storage": {
      bezel("#22262d");
      ears();
      for (let r = 0; r < 4; r += 1) for (let c = 0; c < 6; c += 1) {
        ctx.fillStyle = rand() < 0.5 ? "#434b56" : "#3b424c";
        ctx.fillRect(x + 12 + c * 19, y + 5 + r * 23, 17, 21);
        ctx.fillStyle = "#15181c";
        ctx.fillRect(x + 14 + c * 19, y + 8 + r * 23, 13, 9);
      }
      break;
    }
    case "switch": {
      bezel("#1f2329");
      for (let r = 0; r < 2; r += 1) for (let c = 0; c < 12; c += 1) {
        ctx.fillStyle = "#6b7480";
        ctx.fillRect(x + 12 + c * 8, y + 8 + r * 7, 7, 6);
        ctx.fillStyle = "#07080a";
        ctx.fillRect(x + 13 + c * 8, y + 9 + r * 7, 5, 4);
      }
      ctx.fillStyle = "#aab2bc";
      for (let i = 0; i < 4; i += 1) ctx.fillRect(x + 112 + i * 7, y + 8, 6, 12);
      break;
    }
    case "patch": {
      bezel("#15181c");
      for (let r = 0; r < 2; r += 1) for (let c = 0; c < 12; c += 1) {
        ctx.fillStyle = "#050608";
        ctx.fillRect(x + 12 + c * 10.5, y + 5 + r * 8, 7, 6);
        if (rand() < 0.7) {
          ctx.fillStyle = CABLES[Math.floor(rand() * CABLES.length)];
          ctx.fillRect(x + 13 + c * 10.5, y + 6 + r * 8, 5, 4);
        }
      }
      break;
    }
    case "manager": {
      bezel("#0f1114");
      ctx.fillStyle = "#050607";
      ctx.fillRect(x + 8, y + h / 2 - 3, w - 16, 6);
      ctx.fillStyle = "#23272d";
      for (let i = 0; i < 8; i += 1) ctx.fillRect(x + 10 + i * 17, y + 3, 6, h - 6);
      break;
    }
    case "ups": {
      bezel("#1e2228");
      ears();
      ctx.fillStyle = "#6ee7b7";
      ctx.fillRect(x + 16, y + 14, 36, 16);
      ctx.fillStyle = "#0f3d2e";
      ctx.fillRect(x + 19, y + 18, 20, 3);
      ctx.fillStyle = "#171a1f";
      for (let i = 0; i < 10; i += 1) ctx.fillRect(x + 64 + i * 6, y + 8, 3, h - 16);
      break;
    }
    case "blank": {
      bezel("#121418");
      ctx.fillStyle = "#2a2f36";
      ctx.fillRect(x + 7, y + h / 2 - 1.5, 3, 3);
      ctx.fillRect(x + w - 10, y + h / 2 - 1.5, 3, 3);
      break;
    }
  }
}

function drawRackFaces(plan: RackPlan, seed: number, ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#07080a";
  ctx.fillRect(0, 0, w, h);
  for (let rack = 0; rack < RACKS_PER_ROW; rack += 1) {
    const x0 = rack * CANVAS.rackPx;
    // Empty rails: square cage-nut holes up the mounting posts.
    ctx.fillStyle = "#0c0e11";
    ctx.fillRect(x0 + DEV.x, 0, DEV.w, h);
    for (const rx of [x0 + DEV.x - 12, x0 + DEV.x + DEV.w + 2]) {
      ctx.fillStyle = "#3a4049";
      ctx.fillRect(rx, 0, 10, h);
      ctx.fillStyle = "#101216";
      for (let u = 0; u < 42; u += 1) for (let j = 0; j < 3; j += 1) ctx.fillRect(rx + 3, u * PX_U + 3 + j * 7, 4, 4);
    }
  }
  let k = 0;
  const rand = () => hash01(seed * 0.0007 + (k += 1) * 2.414);
  for (const unit of plan.units) drawUnit(ctx, unit, rand);
}

/** The rear door: perforated steel with a latch, one drawing repeated per rack. */
function drawPerforated(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#23272e";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#0a0b0d";
  for (let y = 18; y < h - 14; y += 7) for (let x = 12 + ((y / 7) % 2) * 3.5; x < w - 16; x += 7) {
    ctx.beginPath();
    ctx.arc(x, y, 2.2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = "#15181c";
  ctx.fillRect(0, 0, 5, h);
  ctx.fillRect(w - 5, 0, 5, h);
  ctx.fillStyle = "#9aa3ad";
  ctx.fillRect(w - 13, h * 0.45, 4, h * 0.1);
}

/** Row plate on the aisle end of each row: the row letter and its rack range. */
function drawRowPlate(letter: string, ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#1c2026";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#5eead4";
  ctx.fillRect(0, h - 10, w, 10);
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = "700 120px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText(letter, w / 2, h * 0.42);
  ctx.fillStyle = "#9fb0c2";
  ctx.font = "600 34px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText(`${letter}01–${letter}05`, w / 2, h * 0.78);
}

// ---------------------------------------------------------------------------
// Instanced LEDs and patch cables
// ---------------------------------------------------------------------------

const LED_GEO = GEO.box;
const TMP = new Object3D();
const TMP_COLOUR = new Color();
/** Twinkle rate: the LEDs re-roll their state this many times a second. */
const TWINKLE_HZ = 9;

function RackLeds({ plan }: { plan: RackPlan }) {
  const { mesh, base, modes } = useMemo(() => {
    const material = new MeshBasicMaterial({ color: "#ffffff", toneMapped: false });
    const made = new InstancedMesh(LED_GEO, material, plan.leds.length);
    const colours = new Float32Array(plan.leds.length * 3);
    const ledModes = new Uint8Array(plan.leds.length);
    plan.leds.forEach((led, i) => {
      TMP.position.set(toX(led.px), toY(led.py), FACE_Z + 0.004);
      TMP.scale.set(led.size, led.size, 0.006);
      TMP.updateMatrix();
      made.setMatrixAt(i, TMP.matrix);
      TMP_COLOUR.set(led.colour);
      made.setColorAt(i, TMP_COLOUR);
      TMP_COLOUR.toArray(colours, i * 3);
      ledModes[i] = led.mode;
    });
    made.instanceMatrix.needsUpdate = true;
    made.computeBoundingBox();
    made.computeBoundingSphere();
    return { mesh: made, base: colours, modes: ledModes };
  }, [plan]);
  useEffect(() => () => { mesh.dispose(); (mesh.material as MeshBasicMaterial).dispose(); }, [mesh]);
  const lastTick = useRef(-1);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const tick = Math.floor(t * TWINKLE_HZ);
    if (tick === lastTick.current || !mesh.instanceColor) return;
    lastTick.current = tick;
    const out = mesh.instanceColor.array as Float32Array;
    for (let i = 0; i < modes.length; i += 1) {
      const mode = modes[i];
      const level = mode === 0 ? 1
        : mode === 1 ? (hash01(i * 7.13 + tick * 0.37) > 0.42 ? 1 : 0.12)
          : 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(t * 2.2 + i * 0.9));
      out[i * 3] = base[i * 3] * level;
      out[i * 3 + 1] = base[i * 3 + 1] * level;
      out[i * 3 + 2] = base[i * 3 + 2] * level;
    }
    mesh.instanceColor.needsUpdate = true;
  });
  return <primitive object={mesh} />;
}

function PatchCables({ plan }: { plan: RackPlan }) {
  const mesh = useMemo(() => {
    const made = new InstancedMesh(SGEO.loop, matte("#ffffff", { roughness: 0.7 }), plan.loops.length);
    plan.loops.forEach((loop, i) => {
      // The half ring opens upwards: both ends plug into the panel, the loop sags below it.
      TMP.position.set(toX(loop.px), toY(loop.py), FACE_Z + 0.012);
      TMP.rotation.set(0, 0, Math.PI);
      TMP.scale.set(loop.radius, loop.radius, loop.radius);
      TMP.updateMatrix();
      made.setMatrixAt(i, TMP.matrix);
      made.setColorAt(i, TMP_COLOUR.set(loop.colour));
    });
    TMP.rotation.set(0, 0, 0);
    made.instanceMatrix.needsUpdate = true;
    made.computeBoundingBox();
    made.computeBoundingSphere();
    return made;
  }, [plan]);
  useEffect(() => () => { mesh.dispose(); (mesh.material as MeshStandardMaterial).dispose(); }, [mesh]);
  return <primitive object={mesh} />;
}

// ---------------------------------------------------------------------------
// A row of five 42U racks
// ---------------------------------------------------------------------------

const RACK_XS = Array.from({ length: RACKS_PER_ROW }, (_, i) => -ROW_W / 2 + RACK_W * (i + 0.5));
const POST_XS = Array.from({ length: RACKS_PER_ROW + 1 }, (_, i) => Math.max(-ROW_W / 2 + 0.015, Math.min(ROW_W / 2 - 0.015, -ROW_W / 2 + RACK_W * i)));
const RUNG_XS = Array.from({ length: 12 }, (_, i) => -ROW_W / 2 + 0.125 + i * 0.25);
const TRAY_Y = 2.21;

export function ServerRack({ item }: { item: Furniture }) {
  const seed = seedOf(item.id);
  const plan = useMemo(() => rackPlan(seed), [seed]);
  const face = canvasMaterial(`server:faces:${seed}`, CANVAS.rackPx * RACKS_PER_ROW, CANVAS.h,
    (ctx, w, h) => drawRackFaces(plan, seed, ctx, w, h), { glow: 0.22, fallback: "#15181d", roughness: 0.45 });
  const rear = repeatedMaterial("server:rear", 128, 512, drawPerforated, [RACKS_PER_ROW, 1], "#23272e", 0.55);
  const letter = item.id.endsWith("-e") ? "B" : "A";
  const plate = canvasMaterial(`server:plate:${letter}`, 256, 320, (ctx, w, h) => drawRowPlate(letter, ctx, w, h),
    { glow: 0.3, fallback: "#1c2026", roughness: 0.5 });
  const bodyH = FACE.h + 0.1;
  const halfD = ROW_D / 2;
  return (
    <group>
      {/* Plinth with a cool toe-kick glow on the aisle side, the cabinet body and its lid. */}
      <Box size={[ROW_W - 0.02, 0.1, ROW_D - 0.06]} position={[0, 0.05, 0]} material={SRV.plinth} />
      <Box size={[ROW_W - 0.04, 0.014, 0.01]} position={[0, 0.05, halfD - 0.03]} material={SRV.ice} cast={false} />
      <Box size={[ROW_W, bodyH, ROW_D * 0.9]} position={[0, 0.1 + bodyH / 2, 0]} material={SRV.cabinet} />
      <Box size={[ROW_W, 0.05, ROW_D]} position={[0, 0.1 + bodyH + 0.025, 0]} material={SRV.cabinetEdge} />
      <Box size={[ROW_W - 0.06, 0.016, 0.01]} position={[0, 0.1 + bodyH + 0.01, halfD - 0.004]} material={SRV.ice} cast={false} />
      {/* The server faces, their LEDs and patch cables, behind a smoked glass door per rack. */}
      <Panel size={[ROW_W, FACE.h]} position={[0, FACE.y0 + FACE.h / 2, FACE_Z]} material={face} />
      <RackLeds plan={plan} />
      <PatchCables plan={plan} />
      <Panel size={[ROW_W - 0.02, bodyH]} position={[0, 0.1 + bodyH / 2, halfD - 0.02]} material={SRV.glass} />
      {POST_XS.map((x) => <Box key={x} size={[0.03, bodyH, 0.07]} position={[x, 0.1 + bodyH / 2, halfD - 0.04]} material={SRV.cabinetEdge} />)}
      {RACK_XS.map((x) => <Box key={x} size={[0.016, 0.22, 0.02]} position={[x + RACK_W / 2 - 0.06, 1.12, halfD - 0.012]} material={SRV.handle} cast={false} />)}
      {/* Perforated rear doors facing the wall, and a row plate on the aisle end (local -x). */}
      <Panel size={[ROW_W, bodyH - 0.04]} position={[0, 0.1 + bodyH / 2, -ROW_D * 0.45 - 0.003]} material={rear} rotationY={Math.PI} />
      <Panel size={[0.5, 0.62]} position={[-ROW_W / 2 - 0.003, 1.62, 0.05]} material={plate} rotationY={-Math.PI / 2} />
      {/* The overhead ladder tray on short brackets: black rails, rungs, and blue, grey and yellow runs. */}
      {[-1.2, 0, 1.2].map((x) => <Box key={x} size={[0.03, TRAY_Y - 2.13, 0.03]} position={[x, (TRAY_Y + 2.13) / 2 - 0.02, 0]} material={SRV.trayRail} cast={false} />)}
      {[-0.19, 0.19].map((z) => <Box key={z} size={[ROW_W, 0.06, 0.015]} position={[0, TRAY_Y + 0.03, z]} material={SRV.trayRail} />)}
      {RUNG_XS.map((x) => <Box key={x} size={[0.025, 0.012, 0.37]} position={[x, TRAY_Y + 0.006, 0]} material={SRV.trayRail} cast={false} />)}
      <Box size={[ROW_W - 0.04, 0.04, 0.12]} position={[0, TRAY_Y + 0.032, -0.09]} material={SRV.cableBlue} />
      <Box size={[ROW_W - 0.04, 0.03, 0.06]} position={[0, TRAY_Y + 0.027, 0.03]} material={SRV.cableGrey} />
      <Box size={[ROW_W - 0.04, 0.03, 0.05]} position={[0, TRAY_Y + 0.027, 0.11]} material={SRV.cableRed} />
      {/* The yellow fibre raceway along the front, dropping a spout into every rack. */}
      <Box size={[ROW_W, 0.07, 0.1]} position={[0, 2.215, halfD - 0.06]} material={SRV.fibre} />
      {RACK_XS.map((x) => <Box key={x} size={[0.06, 0.05, 0.06]} position={[x, 2.16, halfD - 0.08]} material={SRV.fibre} cast={false} />)}
      {RACK_XS.map((x) => <Box key={x} size={[0.05, TRAY_Y - 2.13, 0.05]} position={[x - 0.1, (TRAY_Y + 2.13) / 2, -0.1]} material={SRV.cableBlue} cast={false} />)}
    </group>
  );
}

// ---------------------------------------------------------------------------
// The cold aisle: perforated floor tiles, overhead trays, a linear light
// ---------------------------------------------------------------------------

const AISLE = FURNITURE_SIZE.coldAisle;

function drawAisleFloor(ctx: Ctx, w: number, h: number): void {
  const cols = 4, rows = 5;
  const tw = w / cols, th = h / rows;
  for (let r = 0; r < rows; r += 1) for (let c = 0; c < cols; c += 1) {
    const perforated = c === 0 || c === cols - 1;
    const x = c * tw, y = r * th;
    ctx.fillStyle = perforated ? "#a9b0b9" : (r + c) % 2 === 0 ? "#c9ced5" : "#c3c8cf";
    ctx.fillRect(x, y, tw, th);
    if (perforated) {
      ctx.fillStyle = "#58606b";
      for (let py = y + 10; py < y + th - 6; py += 8) for (let px = x + 10; px < x + tw - 6; px += 8) {
        ctx.beginPath();
        ctx.arc(px, py, 2.3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.fillStyle = "rgba(90,98,110,0.6)";
    ctx.fillRect(x, y, tw, 2);
    ctx.fillRect(x, y, 2, th);
  }
}

const CROSS_ZS = [-AISLE.d / 2 + 0.35, AISLE.d / 2 - 0.35];

export function ColdAisle() {
  const floor = canvasMaterial("server:aisle", 256, 320, drawAisleFloor, { fallback: "#c3c8cf", roughness: 0.75 });
  const w = AISLE.w;
  return (
    <group>
      <mesh position={[0, 0.009, 0]} rotation={[-Math.PI / 2, 0, 0]} material={floor} receiveShadow>
        <planeGeometry args={[w, AISLE.d]} />
      </mesh>
      {/* Two ladder trays bridge the aisle between the rows. */}
      {CROSS_ZS.map((z) => (
        <group key={z} position={[0, 2.25, z]}>
          {[-0.12, 0.12].map((dz) => <Box key={dz} size={[w, 0.05, 0.012]} position={[0, 0, dz]} material={SRV.trayRail} cast={false} />)}
          {[-0.9, -0.45, 0, 0.45, 0.9].map((x) => <Box key={x} size={[0.02, 0.01, 0.24]} position={[x, -0.02, 0]} material={SRV.trayRail} cast={false} />)}
          <Box size={[w - 0.04, 0.03, 0.1]} position={[0, -0.005, -0.03]} material={SRV.cableBlue} cast={false} />
          <Box size={[w - 0.04, 0.025, 0.05]} position={[0, -0.007, 0.06]} material={SRV.fibre} cast={false} />
        </group>
      ))}
      {/* A slim linear luminaire hung between the trays, and the aisle's one cool light. */}
      <Box size={[0.07, 0.035, AISLE.d - 0.9]} position={[0, 2.21, 0]} material={SRV.trayRail} cast={false} />
      <Box size={[0.05, 0.006, AISLE.d - 0.95]} position={[0, 2.19, 0]} material={SRV.panel} cast={false} />
      <pointLight position={[0, 1.95, 0]} color="#8fe3ff" intensity={4} distance={5.5} decay={2} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Live screens: the NOC console and the status wall
// ---------------------------------------------------------------------------

interface ClusterData { load: number; working: number; total: number }

/** Cluster load follows the coding agents at work: an idle floor hums along, a busy one runs hot. */
function useClusterData(): ClusterData {
  const panes = useWorkspacePanesStore((s) => s.panes);
  return useMemo(() => {
    let working = 0, total = 0;
    for (const { agent } of paneOccupants(panes)) {
      total += 1;
      if (agent.state === "working") working += 1;
    }
    return { load: 0.28 + 0.62 * (total ? working / total : 0), working, total };
  }, [panes]);
}

/** A counter that ticks on a slow timer; the screens redraw on it, never per frame. */
function useTicker(ms: number): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return tick;
}

function useCanvasScreen(width: number, height: number, key: string, draw: (ctx: Ctx, w: number, h: number) => void): MeshBasicMaterial {
  const canvas = useMemo(() => (typeof document !== "undefined" ? document.createElement("canvas") : null), []);
  const material = useMemo(() => {
    if (!canvas) return new MeshBasicMaterial({ color: "#0b0d10" });
    canvas.width = width;
    canvas.height = height;
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.anisotropy = 8;
    return new MeshBasicMaterial({ map: texture, toneMapped: false });
  }, [canvas, width, height]);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    let ctx: Ctx | null = null;
    try { ctx = canvas?.getContext("2d") ?? null; } catch {
      // jsdom without the canvas package: the plain dark screen is the right fallback.
      ctx = null;
    }
    if (!ctx || !material.map) return;
    drawRef.current(ctx, width, height);
    material.map.needsUpdate = true;
  }, [key, canvas, material, width, height]);
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return material;
}

const SCREEN_BG = "#070b12";
const FONT = (px: number, weight = 600) => `${weight} ${px}px ui-monospace, 'Cascadia Mono', 'SF Mono', Menlo, monospace`;

/** A smooth pseudo-random series for charts: deterministic in (seed, i). */
function series(seed: number, i: number, base: number, swing: number): number {
  return base + swing * (0.55 * Math.sin(i * 0.35 + seed) + 0.3 * Math.sin(i * 0.9 + seed * 2.1) + 0.15 * (hash01(i + seed * 13) - 0.5) * 2);
}

function drawChart(ctx: Ctx, x: number, y: number, w: number, h: number, tick: number, seed: number, level: number, colour: string, fill: string): void {
  ctx.strokeStyle = "rgba(120,160,200,0.14)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    ctx.beginPath();
    ctx.moveTo(x, y + (h * i) / 4);
    ctx.lineTo(x + w, y + (h * i) / 4);
    ctx.stroke();
  }
  const points = 40;
  const value = (i: number) => Math.max(0.04, Math.min(0.97, series(seed, i + tick, level, 0.16)));
  ctx.beginPath();
  ctx.moveTo(x, y + h);
  for (let i = 0; i < points; i += 1) ctx.lineTo(x + (w * i) / (points - 1), y + h - value(i) * h);
  ctx.lineTo(x + w, y + h);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.beginPath();
  for (let i = 0; i < points; i += 1) {
    const px = x + (w * i) / (points - 1), py = y + h - value(i) * h;
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.strokeStyle = colour;
  ctx.lineWidth = 3;
  ctx.stroke();
}

function tempColour(t: number): string {
  if (t < 0.45) return "#1f9d8b";
  if (t < 0.65) return "#3fc58a";
  if (t < 0.8) return "#e8b23a";
  return "#e5553f";
}

/** What the status wall shows: the Agentic IDE's spend over the Spend section's default window. */
export interface IdeSpend {
  days: number;
  cost: number;
  billed: number;
  tokens: number;
  sessions: number;
  /** Cost per bucket (a day), oldest first. */
  series: { key: string; cost: number }[];
  /** The costliest models, most expensive first. */
  models: { name: string; cost: number }[];
}

/** The wall's numbers from a cost summary. Pure. */
export function ideSpendFrom(summary: CostSummary | undefined, days: number): IdeSpend | null {
  if (!summary) return null;
  const { totals } = summary;
  return {
    days,
    cost: totals.cost_usd,
    billed: Math.max(0, totals.cost_usd - totals.subscription_usd),
    tokens: totals.tokens_total,
    sessions: summary.refs_total,
    series: summary.series.map((b) => ({ key: b.key, cost: b.cost_usd })),
    models: [...summary.by_model].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 5).map((b) => ({ name: b.key, cost: b.cost_usd })),
  };
}

/** $1,234 · $12.30 · $0.042: whole dollars once it is large, cents below. Pure. */
export function formatUsd(v: number): string {
  if (v >= 1000) return `$${Math.round(v).toLocaleString("en-US")}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v > 0) return `$${v.toFixed(3)}`;
  return "$0";
}

/** 1.2B · 34.5M · 812K · 950. Pure. */
export function formatCount(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3)}K`;
  return String(Math.round(v));
}

function drawStatusWall(ctx: Ctx, w: number, h: number, spend: IdeSpend | null, failed: boolean): void {
  ctx.fillStyle = SCREEN_BG;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#5eead4";
  ctx.fillRect(0, 0, w, 6);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = "#e6f1ff";
  ctx.font = FONT(44, 700);
  ctx.fillText("AGENTIC IDE", 36, 50);
  const titleW = ctx.measureText("AGENTIC IDE").width;
  ctx.fillStyle = "#8fa3ba";
  ctx.font = FONT(30);
  ctx.fillText(`SPEND · ${spend ? `${spend.days}D` : "…"}`, 36 + titleW + 24, 52);
  ctx.textAlign = "right";
  ctx.font = FONT(34);
  ctx.fillText(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), w - 36, 50);
  // KPI tiles: what the IDE's agents cost, what of it an API key paid, tokens, sessions.
  const dash = failed ? "n/a" : "…";
  const tiles: [string, string, string][] = [
    ["COST", spend ? formatUsd(spend.cost) : dash, "#5eead4"],
    ["API BILLED", spend ? formatUsd(spend.billed) : dash, "#e8b23a"],
    ["TOKENS", spend ? formatCount(spend.tokens) : dash, "#7dd3fc"],
    ["SESSIONS", spend ? String(spend.sessions) : dash, "#c4b5fd"],
  ];
  const tw = (w - 72 - 3 * 20) / 4;
  tiles.forEach(([label, value, colour], i) => {
    const x = 36 + i * (tw + 20);
    ctx.fillStyle = "#0e1522";
    ctx.fillRect(x, 96, tw, 128);
    ctx.fillStyle = colour;
    ctx.fillRect(x, 96, 5, 128);
    ctx.textAlign = "left";
    ctx.fillStyle = "#8fa3ba";
    ctx.font = FONT(24);
    ctx.fillText(label, x + 22, 126);
    ctx.fillStyle = colour;
    ctx.font = FONT(value.length > 8 ? 46 : 58, 700);
    ctx.fillText(value, x + 22, 184);
  });
  // Cost per day as bars, and the costliest models beside it.
  const cx = 36, cy = 262, cw = w * 0.6, ch = h - cy - 44;
  ctx.strokeStyle = "rgba(120,160,200,0.14)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    ctx.beginPath(); ctx.moveTo(cx, cy + (ch * i) / 4); ctx.lineTo(cx + cw, cy + (ch * i) / 4); ctx.stroke();
  }
  const bars = spend?.series ?? [];
  const peak = Math.max(0, ...bars.map((b) => b.cost));
  if (peak > 0) {
    const bw = cw / bars.length;
    bars.forEach((b, i) => {
      const bh = (b.cost / peak) * ch;
      ctx.fillStyle = i === bars.length - 1 ? "#5eead4" : "rgba(94,234,212,0.55)";
      ctx.fillRect(cx + i * bw + bw * 0.15, cy + ch - bh, Math.max(2, bw * 0.7), bh);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = "#8fa3ba";
    ctx.font = FONT(20);
    ctx.fillText(`${formatUsd(peak)} / day peak`, cx, cy + ch + 24);
  } else {
    ctx.textAlign = "center";
    ctx.fillStyle = "#4b5d72";
    ctx.font = FONT(26);
    ctx.fillText(spend ? "no spend in this window" : failed ? "spend unavailable" : "loading…", cx + cw / 2, cy + ch / 2);
  }
  const mx = cx + cw + 40, mw = w - mx - 36;
  ctx.textAlign = "left";
  ctx.fillStyle = "#8fa3ba";
  ctx.font = FONT(22);
  ctx.fillText("TOP MODELS", mx, cy + 8);
  const models = spend?.models ?? [];
  const top = models[0]?.cost || 1;
  models.forEach((m, i) => {
    const y = cy + 48 + i * ((ch - 30) / 5);
    ctx.textAlign = "left";
    ctx.fillStyle = "#e6f1ff";
    ctx.font = FONT(22, 600);
    const cost = formatUsd(m.cost);
    const room = mw - ctx.measureText(cost).width - 20;
    let name = m.name;
    while (name.length > 3 && ctx.measureText(`${name}…`).width > room) name = name.slice(0, -1);
    ctx.fillText(name === m.name ? name : `${name}…`, mx, y);
    ctx.textAlign = "right";
    ctx.fillStyle = "#5eead4";
    ctx.fillText(cost, mx + mw, y);
    ctx.fillStyle = "#101a28";
    ctx.fillRect(mx, y + 18, mw, 6);
    ctx.fillStyle = "#1f9d8b";
    ctx.fillRect(mx, y + 18, Math.max(4, (mw * m.cost) / top), 6);
  });
}

function drawThroughput(ctx: Ctx, w: number, h: number, data: ClusterData, tick: number): void {
  ctx.fillStyle = SCREEN_BG;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#7dd3fc";
  ctx.font = FONT(26, 700);
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("NET  Gb/s", 22, 28);
  ctx.textAlign = "right";
  ctx.fillStyle = "#e6f1ff";
  ctx.fillText((40 + data.load * 60 + 3 * Math.sin(tick)).toFixed(1), w - 22, 28);
  drawChart(ctx, 18, 58, w - 36, h - 76, tick, 4.2, 0.3 + data.load * 0.5, "#7dd3fc", "rgba(125,211,252,0.14)");
  drawChart(ctx, 18, 58, w - 36, h - 76, tick, 9.1, 0.2 + data.load * 0.3, "#c4b5fd", "rgba(196,181,253,0.08)");
}

function drawThermal(ctx: Ctx, w: number, h: number, data: ClusterData, tick: number): void {
  ctx.fillStyle = SCREEN_BG;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#5eead4";
  ctx.font = FONT(26, 700);
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("GPU", 22, 28);
  // Utilisation bars, one per GPU node.
  const bars = 14;
  const bw = (w - 44) / bars;
  for (let i = 0; i < bars; i += 1) {
    const v = Math.max(0.06, Math.min(1, data.load + 0.25 * (hash01(i * 5.7 + tick * 0.5) - 0.5)));
    const bh = (h - 90) * v;
    ctx.fillStyle = "#101a28";
    ctx.fillRect(22 + i * bw + 3, 58, bw - 6, h - 90);
    ctx.fillStyle = tempColour(v);
    ctx.fillRect(22 + i * bw + 3, 58 + (h - 90) - bh, bw - 6, bh);
  }
  ctx.fillStyle = "#8fa3ba";
  ctx.font = FONT(20);
  ctx.textAlign = "left";
  ctx.fillText(`${Math.round(data.load * 100)}%`, 22, h - 16);
}

function drawLog(ctx: Ctx, w: number, h: number, _data: ClusterData, tick: number): void {
  ctx.fillStyle = SCREEN_BG;
  ctx.fillRect(0, 0, w, h);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  const rows = 9;
  for (let i = 0; i < rows; i += 1) {
    const entry = tick + rows - i;
    const y = 26 + i * ((h - 36) / rows);
    const warn = hash01(entry * 1.3) < 0.12;
    ctx.fillStyle = warn ? "#e8b23a" : "#3dff8a";
    ctx.beginPath();
    ctx.arc(26, y, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#4b5d72";
    ctx.font = FONT(20);
    ctx.fillText(`${String(10 + (entry % 50)).padStart(2, "0")}:${String((entry * 7) % 60).padStart(2, "0")}`, 44, y);
    ctx.fillStyle = warn ? "#f4d27a" : "#b7c7d9";
    ctx.fillRect(122, y - 4, 90 + hash01(entry * 2.7) * (w - 260), 8);
  }
}

function Screen({ w, h, material }: { w: number; h: number; material: MeshBasicMaterial }) {
  return (
    <group>
      <Rounded size={[w + 0.04, h + 0.04, 0.03]} radius={0.01} position={[0, 0, 0]} material={MAT.monitor} />
      <mesh position={[0, 0, 0.0155]} material={material}>
        <planeGeometry args={[w, h]} />
      </mesh>
    </group>
  );
}

const WALL = { w: 2.3, h: 0.94, y: 1.6 };

/** The Spend section's default window, narrowed to what the Agentic IDE's agents spent. */
const IDE_SPEND_FILTERS: CostFilters = { ...EMPTY_FILTERS, surfaces: ["agentic-ide"] };

/** A floor-standing status display: the Agentic IDE's spend for the whole room. A click dives into it and opens Spend. */
export function StatusWall() {
  const summary = useCostSummary(IDE_SPEND_FILTERS);
  const spend = ideSpendFrom(summary.data, IDE_SPEND_FILTERS.days);
  // The clock in the corner moves once a minute; the numbers when the summary answers.
  const tick = useTicker(30_000);
  const screen = useCanvasScreen(1280, Math.round((1280 * WALL.h) / WALL.w), `${tick}:${summary.dataUpdatedAt}:${summary.isError}`,
    (ctx, w, h) => drawStatusWall(ctx, w, h, spend, summary.isError && !spend));
  const dive = useMonitorDive("costs", [WALL.w, WALL.h], 0.0155);
  return (
    <group>
      <Box size={[1.3, 0.03, 0.28]} position={[0, 0.015, 0]} material={SRV.cabinet} />
      {[-0.5, 0.5].map((x) => <Box key={x} size={[0.07, WALL.y - 0.1, 0.06]} position={[x, (WALL.y - 0.1) / 2, -0.06]} material={SRV.cabinetEdge} />)}
      <group position={[0, WALL.y, -0.02]} {...dive}>
        <Screen w={WALL.w} h={WALL.h} material={screen} />
      </group>
      <Box size={[WALL.w - 0.1, 0.012, 0.012]} position={[0, WALL.y - WALL.h / 2 - 0.05, -0.02]} material={SRV.ice} cast={false} />
    </group>
  );
}

const MON = { w: 0.6, h: 0.34, y: 1.07 };
const MON_CANVAS = { w: 640, h: Math.round((640 * 0.34) / 0.6) };
const NOC_D = FURNITURE_SIZE.nocConsole.d;
const DESK = { d: 0.72, z: -NOC_D / 2 + 0.36, top: 0.74 };
const STOOL_Z = NOC_D / 2 - 0.26;
const MUG = new CylinderGeometry(0.04, 0.036, 0.1, 16);

/** The NOC console: a graphite desk with three monitors (network, GPUs, the event log) and a stool. */
export function NocConsole() {
  const data = useClusterData();
  const tick = useTicker(2000);
  const key = `${tick}:${data.load}`;
  const net = useCanvasScreen(MON_CANVAS.w, MON_CANVAS.h, key, (ctx, w, h) => drawThroughput(ctx, w, h, data, tick));
  const gpu = useCanvasScreen(MON_CANVAS.w, MON_CANVAS.h, key, (ctx, w, h) => drawThermal(ctx, w, h, data, tick));
  const log = useCanvasScreen(MON_CANVAS.w, MON_CANVAS.h, key, (ctx, w, h) => drawLog(ctx, w, h, data, tick));
  const monZ = DESK.z - DESK.d / 2 + 0.16;
  return (
    <group>
      {/* Desk: a dark top on two panel legs, a modesty panel and a cool strip under the lip. */}
      <Rounded size={[1.96, 0.04, DESK.d]} radius={0.012} position={[0, DESK.top, DESK.z]} material={SRV.cabinetEdge} />
      {[-0.93, 0.93].map((x) => <Box key={x} size={[0.04, DESK.top - 0.02, DESK.d - 0.08]} position={[x, (DESK.top - 0.02) / 2, DESK.z]} material={SRV.cabinet} />)}
      <Box size={[1.82, 0.42, 0.02]} position={[0, 0.48, DESK.z - DESK.d / 2 + 0.06]} material={SRV.cabinet} />
      <Box size={[1.9, 0.01, 0.01]} position={[0, DESK.top - 0.03, DESK.z + DESK.d / 2 - 0.02]} material={SRV.ice} cast={false} />
      {/* Three monitors on one bar: the side screens turned in towards the operator. */}
      <Box size={[1.5, 0.03, 0.03]} position={[0, MON.y - 0.08, monZ - 0.04]} material={MAT.monitorArm} cast={false} />
      <Box size={[0.05, MON.y - DESK.top - 0.08, 0.05]} position={[0, (MON.y + DESK.top - 0.08) / 2, monZ - 0.05]} material={MAT.monitorArm} />
      <Box size={[0.26, 0.012, 0.18]} position={[0, DESK.top + 0.026, monZ - 0.02]} material={MAT.monitorArm} cast={false} />
      <group position={[0, MON.y, monZ]}><Screen w={MON.w} h={MON.h} material={net} /></group>
      <group position={[-0.63, MON.y, monZ + 0.1]} rotation={[0, 0.38, 0]}><Screen w={MON.w} h={MON.h} material={gpu} /></group>
      <group position={[0.63, MON.y, monZ + 0.1]} rotation={[0, -0.38, 0]}><Screen w={MON.w} h={MON.h} material={log} /></group>
      {/* Keyboard, mouse, a mug and a headset on its stand. */}
      <Rounded size={[0.44, 0.02, 0.14]} radius={0.006} position={[0, DESK.top + 0.03, DESK.z + 0.15]} material={MAT.keyboard} cast={false} />
      <Rounded size={[0.06, 0.02, 0.1]} radius={0.01} position={[0.32, DESK.top + 0.03, DESK.z + 0.16]} material={MAT.keyboard} cast={false} />
      <mesh geometry={MUG} material={MAT.mug} position={[-0.52, DESK.top + 0.07, DESK.z + 0.12]} castShadow />
      <Cyl radius={0.05} height={0.012} position={[0.72, DESK.top + 0.026, DESK.z + 0.08]} material={SRV.cabinet} cast={false} />
      <Cyl radius={0.008} height={0.2} position={[0.72, DESK.top + 0.13, DESK.z + 0.08]} material={MAT.monitorArm} cast={false} />
      <mesh geometry={SGEO.loop} material={SRV.cabinet} position={[0.72, DESK.top + 0.2, DESK.z + 0.08]} rotation={[0, Math.PI / 2, 0]} scale={0.075} />
      {/* A drafting stool: five-star base, gas lift, round seat and foot ring. */}
      <group position={[0, 0, STOOL_Z]}>
        {[0, 1, 2, 3, 4].map((i) => (
          <group key={i} rotation={[0, (i * Math.PI * 2) / 5, 0]}>
            <Box size={[0.04, 0.03, 0.2]} position={[0, 0.05, 0.1]} material={MAT.chair} />
          </group>
        ))}
        <Cyl radius={0.025} height={0.56} position={[0, 0.34, 0]} material={MAT.monitorArm} />
        <mesh geometry={SGEO.loop} material={MAT.monitorArm} position={[0, 0.3, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[0.17, 0.17, 0.17]} />
        <mesh geometry={SGEO.loop} material={MAT.monitorArm} position={[0, 0.3, 0]} rotation={[-Math.PI / 2, 0, 0]} scale={[0.17, 0.17, 0.17]} />
        <Cyl radius={0.2} height={0.07} position={[0, 0.66, 0]} material={SRV.stoolSeat} />
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// UPS and fire suppression
// ---------------------------------------------------------------------------

function drawUpsDisplay(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#0b1a14";
  ctx.fillRect(0, 0, w, h);
  // Battery glyph, full, and three load bars.
  ctx.strokeStyle = "#6ee7b7";
  ctx.lineWidth = 5;
  ctx.strokeRect(18, 22, 70, 36);
  ctx.fillStyle = "#6ee7b7";
  ctx.fillRect(88, 32, 8, 16);
  ctx.fillRect(25, 29, 56, 22);
  ctx.font = "700 34px ui-monospace, Menlo, monospace";
  ctx.textBaseline = "middle";
  ctx.fillText("100%", 112, 40);
  for (let i = 0; i < 3; i += 1) {
    ctx.fillStyle = "#123427";
    ctx.fillRect(18, 80 + i * 18, w - 36, 10);
    ctx.fillStyle = "#6ee7b7";
    ctx.fillRect(18, 80 + i * 18, (w - 36) * [0.46, 0.52, 0.41][i], 10);
  }
}

function drawVents(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#23272e";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#0c0d10";
  for (let y = 10; y < h - 6; y += 12) ctx.fillRect(10, y, w - 20, 5);
}

function drawHazardStripes(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#f5c518";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#15171a";
  for (let x = -h; x < w + h; x += 40) {
    ctx.beginPath();
    ctx.moveTo(x, h);
    ctx.lineTo(x + 20, h);
    ctx.lineTo(x + 20 + h, 0);
    ctx.lineTo(x + h, 0);
    ctx.closePath();
    ctx.fill();
  }
}

/** A white-on-red pictogram sign: a flame over a gas bottle. */
function drawSuppressionSign(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#c8262b";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.moveTo(w * 0.5, h * 0.12);
  ctx.bezierCurveTo(w * 0.78, h * 0.35, w * 0.7, h * 0.6, w * 0.5, h * 0.62);
  ctx.bezierCurveTo(w * 0.3, h * 0.6, w * 0.24, h * 0.38, w * 0.42, h * 0.28);
  ctx.bezierCurveTo(w * 0.42, h * 0.4, w * 0.5, h * 0.42, w * 0.5, h * 0.12);
  ctx.fill();
  ctx.fillRect(w * 0.38, h * 0.7, w * 0.24, h * 0.2);
  ctx.fillRect(w * 0.46, h * 0.65, w * 0.08, h * 0.06);
}

const UPS_H = 1.86;

/** A UPS and its battery cabinet side by side, fronts facing +z. */
export function UpsCabinet() {
  const display = canvasMaterial("server:ups-display", 256, 150, drawUpsDisplay, { glow: 0.9, fallback: "#0b1a14", roughness: 0.3 });
  const vents = canvasMaterial("server:ups-vents", 128, 256, drawVents, { fallback: "#23272e", roughness: 0.6 });
  const perforated = repeatedMaterial("server:rear", 128, 512, drawPerforated, [1, 1], "#23272e", 0.55);
  const d = FURNITURE_SIZE.ups.d;
  const front = d / 2 - 0.02;
  return (
    <group>
      {[-0.3, 0.3].map((x) => (
        <group key={x} position={[x, 0, 0]}>
          <Box size={[0.58, 0.08, d - 0.06]} position={[0, 0.04, 0]} material={SRV.plinth} />
          <Box size={[0.58, UPS_H - 0.08, d - 0.04]} position={[0, 0.08 + (UPS_H - 0.08) / 2, 0]} material={SRV.cabinet} />
          <Box size={[0.58, 0.012, 0.01]} position={[0, UPS_H - 0.03, front + 0.006]} material={SRV.ledGreen} cast={false} />
        </group>
      ))}
      {/* UPS: a status display and LEDs over a vented door. */}
      <Box size={[0.3, 0.2, 0.012]} position={[-0.3, 1.45, front]} material={MAT.monitor} cast={false} />
      <Panel size={[0.26, 0.152]} position={[-0.3, 1.45, front + 0.0065]} material={display} />
      {[0, 1, 2].map((i) => <Box key={i} size={[0.018, 0.018, 0.008]} position={[-0.38 + i * 0.04, 1.28, front + 0.004]} material={SRV.ledGreen} cast={false} />)}
      <Panel size={[0.46, 0.9]} position={[-0.3, 0.66, front + 0.001]} material={vents} />
      {/* Battery cabinet: a perforated door, a latch and a hazard label. */}
      <Panel size={[0.5, UPS_H - 0.2]} position={[0.3, 0.08 + (UPS_H - 0.08) / 2, front + 0.001]} material={perforated} />
      <Box size={[0.016, 0.2, 0.02]} position={[0.07, 1.05, front + 0.01]} material={SRV.handle} cast={false} />
      <Box size={[0.12, 0.1, 0.004]} position={[0.3, 1.55, front + 0.004]} material={SRV.fibre} cast={false} />
      {/* The emergency power-off button on the UPS side. */}
      <Box size={[0.1, 0.12, 0.05]} position={[-0.3, 1.72, front - 0.02]} material={SRV.fibre} />
      <Cyl radius={0.03} height={0.03} position={[-0.3, 1.72, front + 0.01]} material={SRV.red} cast={false} />
    </group>
  );
}

/** Two clean-agent gas cylinders on a wall frame, their manifold and a control panel, over a hazard plate. */
export function FireSuppression() {
  const stripes = repeatedMaterial("server:hazard", 256, 64, drawHazardStripes, [2, 1], "#f5c518", 0.7);
  const sign = canvasMaterial("server:suppression-sign", 128, 128, drawSuppressionSign, { glow: 0.2, fallback: "#c8262b", roughness: 0.5 });
  const { w, d } = FURNITURE_SIZE.fireSuppression;
  const back = -d / 2 + 0.02;
  const bottles = [-0.26, 0.04];
  return (
    <group>
      <mesh position={[0, 0.008, 0]} rotation={[-Math.PI / 2, 0, 0]} material={stripes} receiveShadow>
        <planeGeometry args={[w - 0.02, d - 0.02]} />
      </mesh>
      <Box size={[w - 0.02, 1.84, 0.03]} position={[0, 0.94, back]} material={SRV.backplate} />
      {bottles.map((x) => (
        <group key={x} position={[x, 0, back + 0.17]}>
          <Cyl radius={0.13} height={1.2} position={[0, 0.62, 0]} material={SRV.red} />
          <mesh geometry={SGEO.dome} material={SRV.red} position={[0, 1.22, 0]} scale={[0.13, 0.1, 0.13]} castShadow />
          <Cyl radius={0.035} height={0.1} position={[0, 1.36, 0]} material={SRV.valve} />
          {[0.35, 0.95].map((y) => <Box key={y} size={[0.3, 0.04, 0.02]} position={[0, y, -0.13]} material={SRV.trayRail} cast={false} />)}
        </group>
      ))}
      {/* Manifold: a header pipe across both valves, rising to the ceiling line. */}
      <Cyl radius={0.022} height={0.1} position={[bottles[0], 1.44, back + 0.17]} material={SRV.valve} cast={false} />
      <Cyl radius={0.022} height={0.1} position={[bottles[1], 1.44, back + 0.17]} material={SRV.valve} cast={false} />
      <Box size={[0.4, 0.045, 0.045]} position={[(bottles[0] + bottles[1]) / 2 + 0.03, 1.5, back + 0.17]} material={SRV.red} cast={false} />
      <Box size={[0.045, 0.4, 0.045]} position={[bottles[1] + 0.1, 1.7, back + 0.17]} material={SRV.red} cast={false} />
      {/* Control panel with its status LEDs, and the pictogram sign above it. */}
      <Box size={[0.2, 0.3, 0.07]} position={[0.32, 1.25, back + 0.05]} material={SRV.cream} />
      <Box size={[0.2, 0.05, 0.072]} position={[0.32, 1.375, back + 0.05]} material={SRV.red} cast={false} />
      <Box size={[0.02, 0.02, 0.01]} position={[0.28, 1.28, back + 0.088]} material={SRV.ledGreen} cast={false} />
      <Box size={[0.02, 0.02, 0.01]} position={[0.32, 1.28, back + 0.088]} material={SRV.plinth} cast={false} />
      <Box size={[0.08, 0.05, 0.01]} position={[0.32, 1.18, back + 0.088]} material={SRV.plinth} cast={false} />
      <Panel size={[0.2, 0.2]} position={[0.32, 1.62, back + 0.017]} material={sign} />
    </group>
  );
}

export const SERVER_RENDERERS = {
  serverRack: ({ item }) => <ServerRack item={item} />,
  coldAisle: () => <ColdAisle />,
  nocConsole: () => <NocConsole />,
  statusWall: () => <StatusWall />,
  ups: () => <UpsCabinet />,
  fireSuppression: () => <FireSuppression />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

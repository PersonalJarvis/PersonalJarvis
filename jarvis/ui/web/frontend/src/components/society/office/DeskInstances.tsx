/**
 * Every workstation on the floor, drawn as instanced meshes: one draw call
 * per desk part instead of one per part per desk. The agent sits at local +z
 * and looks north at its monitor; a desk facing south is rotated half a turn.
 *
 * The workstation is a contemporary bench desk: a light-oak top on black
 * steel T-legs, a felt privacy screen, a slim monitor on an arm, a white
 * pedestal, and an ergonomic mesh-back task chair (OfficeChairs.tsx). The
 * felt screen and the seat fabric take the department's zone colour through
 * per-instance colours, so a department reads as one team at a glance. A
 * coding-floor studio passes a `tone` instead, which swaps whole materials by
 * part role.
 */
import { useLayoutEffect, useMemo, useRef } from "react";
import {
  BufferGeometry, Color, Euler, InstancedMesh, Matrix4, PlaneGeometry, Quaternion, Vector3, type Material, type MeshStandardMaterial,
} from "three";
import { RoundedBoxGeometry } from "three-stdlib";
import type { SocietyAgent } from "../data";
import { CHAIR_MAT, TASK_CHAIR_PARTS, type ChairFinish } from "./OfficeChairs";
import { GEO, MAT, screenMaterial } from "./OfficeFurniture";
import type { DeskSlot } from "./officeLayout";
import { DEPARTMENT_ZONES } from "./officePalette";
import type { ScreenFace } from "./screenTextures";

/** Which zone colour a tinted part takes: the felt screen's panel tone or the seat fabric. */
type Tint = "panel" | "seat";

/** Which surface of a workstation a part is; a desk tone recolours parts by role. */
type PartRole = "top" | "body" | "leg" | "monitor" | "keyboard" | "chair" | "seat" | "chrome" | "mesh";

/** Parts every room keeps as they are: the monitor, keyboard, and the chair's polished base and mesh back. */
type FixedRole = "monitor" | "keyboard" | "chrome" | "mesh";
const FIXED_ROLES: ReadonlySet<PartRole> = new Set<FixedRole>(["monitor", "keyboard", "chrome", "mesh"]);

/** Per-department materials for the parts that carry a room's style. */
export type DeskTone = Partial<Record<Exclude<PartRole, FixedRole>, MeshStandardMaterial>>;

interface Part {
  role: PartRole;
  geometry: BufferGeometry;
  material: Material;
  position: [number, number, number];
  scale?: [number, number, number];
  /** Turn about the part's own vertical axis (radians). */
  rotY?: number;
  tint?: Tint;
  cast: boolean;
}

const CHAIR_Z = 0.62;
const rounded = (w: number, h: number, d: number, r: number) => new RoundedBoxGeometry(w, h, d, 2, r);

/** The task chair's finishes as desk part roles: the frame and fabric follow a desk tone, metal and mesh stay. */
const CHAIR_ROLE: Record<ChairFinish, PartRole> = { frame: "chair", fabric: "seat", metal: "chrome", mesh: "mesh" };
const CHAIR_MATERIAL: Record<ChairFinish, Material> = { frame: MAT.chair, fabric: MAT.tinted, metal: CHAIR_MAT.aluminium, mesh: CHAIR_MAT.mesh };

const PARTS: Part[] = [
  // Desk: top surface at 0.77 m, as before, so hands and keyboards line up with the seated pose.
  { role: "top", geometry: rounded(1.5, 0.035, 0.8, 0.015), material: MAT.deskTop, position: [0, 0.7525, 0], cast: true },
  ...[-0.64, 0.64].flatMap((x): Part[] => [
    { role: "leg", geometry: GEO.box, material: MAT.deskLeg, position: [x, 0.36, 0], scale: [0.06, 0.72, 0.05], cast: true },
    { role: "leg", geometry: GEO.box, material: MAT.deskLeg, position: [x, 0.015, 0], scale: [0.06, 0.03, 0.72], cast: false },
    { role: "leg", geometry: GEO.box, material: MAT.deskLeg, position: [x, 0.72, 0], scale: [0.05, 0.03, 0.68], cast: false },
  ]),
  { role: "leg", geometry: GEO.box, material: MAT.deskLeg, position: [0, 0.68, -0.2], scale: [1.24, 0.05, 0.04], cast: false },
  // Mobile pedestal under the desk's right side.
  { role: "body", geometry: rounded(0.4, 0.56, 0.5, 0.03), material: MAT.deskBody, position: [0.44, 0.3, -0.12], cast: true },
  // Felt privacy screen between the back-to-back pair.
  { role: "body", geometry: rounded(1.46, 0.36, 0.03, 0.012), material: MAT.tinted, position: [0, 0.95, -0.41], tint: "panel", cast: true },
  // Slim monitor on an arm clamped to the back edge.
  { role: "monitor", geometry: GEO.box, material: MAT.monitorArm, position: [0, 0.795, -0.33], scale: [0.08, 0.05, 0.06], cast: false },
  { role: "monitor", geometry: GEO.cyl, material: MAT.monitorArm, position: [0, 0.95, -0.33], scale: [0.018, 0.32, 0.018], cast: true },
  { role: "monitor", geometry: GEO.box, material: MAT.monitorArm, position: [0, 1.1, -0.28], scale: [0.04, 0.03, 0.1], cast: false },
  { role: "monitor", geometry: rounded(0.72, 0.43, 0.022, 0.012), material: MAT.monitor, position: [0, 1.18, -0.224], cast: true },
  // Keyboard, mouse and a mug.
  { role: "keyboard", geometry: GEO.box, material: MAT.keyboard, position: [0, 0.777, 0.22], scale: [0.44, 0.014, 0.13], cast: true },
  { role: "keyboard", geometry: GEO.box, material: MAT.keyboard, position: [0.33, 0.78, 0.22], scale: [0.05, 0.02, 0.08], cast: false },
  { role: "keyboard", geometry: GEO.cyl, material: MAT.mug, position: [-0.52, 0.815, 0.02], scale: [0.035, 0.09, 0.035], cast: true },
  // Chair: the ergonomic task chair (seat top 0.52 m) from OfficeChairs, behind the desk.
  ...TASK_CHAIR_PARTS.map((part): Part => ({
    role: CHAIR_ROLE[part.finish], geometry: part.geometry, material: CHAIR_MATERIAL[part.finish], position: [0, 0, CHAIR_Z],
    tint: part.finish === "fabric" ? "seat" : undefined, cast: part.cast,
  })),
];

const SCREEN_GEOMETRY = new PlaneGeometry(0.66, 0.38);
const SCREEN_LOCAL: [number, number, number] = [0, 1.18, -0.212];
const FACES: ScreenFace[] = ["working", "idle", "waiting", "paused", "empty"];

function deskMatrix(desk: DeskSlot, local: [number, number, number], scale: [number, number, number] = [1, 1, 1], rotY = 0): Matrix4 {
  const turn = desk.facing === "north" ? 0 : Math.PI;
  const world = new Matrix4().compose(new Vector3(desk.x, 0, desk.z), new Quaternion().setFromEuler(new Euler(0, turn, 0)), new Vector3(1, 1, 1));
  const part = new Matrix4().compose(new Vector3(...local), new Quaternion().setFromEuler(new Euler(0, rotY, 0)), new Vector3(...scale));
  return world.multiply(part);
}

const zoneColours = DEPARTMENT_ZONES.map((z) => ({ panel: new Color(z.panel), seat: new Color(z.seat) }));

function PartInstances({ part, desks, zones, material, tinted }: {
  part: Part; desks: DeskSlot[]; zones: ReadonlyMap<string, number>; material: Material; tinted: boolean;
}) {
  const ref = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    desks.forEach((desk, i) => {
      mesh.setMatrixAt(i, deskMatrix(desk, part.position, part.scale, part.rotY));
      if (tinted && part.tint) mesh.setColorAt(i, zoneColours[(zones.get(desk.id) ?? 0) % zoneColours.length][part.tint]);
    });
    mesh.count = desks.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [desks, part, zones, tinted]);
  return <instancedMesh ref={ref} args={[part.geometry, material, Math.max(1, desks.length)]} castShadow={part.cast} receiveShadow frustumCulled={false} />;
}

function ScreenInstances({ face, desks }: { face: ScreenFace; desks: DeskSlot[] }) {
  const ref = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    desks.forEach((desk, i) => mesh.setMatrixAt(i, deskMatrix(desk, SCREEN_LOCAL)));
    mesh.count = desks.length;
    mesh.instanceMatrix.needsUpdate = true;
  }, [desks]);
  // Remount when the capacity must grow; InstancedMesh cannot resize in place.
  return <instancedMesh key={Math.max(1, desks.length)} ref={ref} args={[SCREEN_GEOMETRY, screenMaterial(face), Math.max(1, desks.length)]} frustumCulled={false} />;
}

const NO_ZONES: ReadonlyMap<string, number> = new Map();

/**
 * All desks with the monitor face each one's agent state calls for; `zones`
 * maps a desk id to its department's zone colour (desks without one take the
 * first), and `tone` swaps the desk and chair materials by role instead.
 */
export function DeskInstances({ desks, agents, zones = NO_ZONES, tone }: {
  desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent>; zones?: ReadonlyMap<string, number>; tone?: DeskTone;
}) {
  const byFace = useMemo(() => {
    const groups = new Map<ScreenFace, DeskSlot[]>(FACES.map((f) => [f, []]));
    for (const desk of desks) {
      const agent = desk.agentId ? agents.get(desk.agentId) : undefined;
      groups.get(agent ? agent.state : "empty")!.push(desk);
    }
    return groups;
  }, [desks, agents]);
  return (
    <group>
      {PARTS.map((part, i) => {
        const override = FIXED_ROLES.has(part.role) ? undefined : tone?.[part.role as Exclude<PartRole, FixedRole>];
        const material = override ?? part.material;
        return <PartInstances key={`${i}:${desks.length}:${material.uuid}`} part={part} desks={desks} zones={zones}
          material={material} tinted={!override} />;
      })}
      {FACES.map((face) => (byFace.get(face)!.length > 0 ? <ScreenInstances key={face} face={face} desks={byFace.get(face)!} /> : null))}
    </group>
  );
}

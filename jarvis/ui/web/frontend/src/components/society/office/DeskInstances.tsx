/**
 * Every workstation on the floor, drawn as instanced meshes: one draw call
 * per desk part instead of one per part per desk. Same parts and local
 * placement as `Desk` in OfficeFurniture (the agent sits at local +z and looks
 * north at its monitor); a desk facing south is rotated half a turn.
 */
import { useLayoutEffect, useMemo, useRef } from "react";
import { BufferGeometry, Euler, InstancedMesh, Matrix4, PlaneGeometry, Quaternion, Vector3, type Material } from "three";
import { RoundedBoxGeometry } from "three-stdlib";
import type { SocietyAgent } from "../data";
import { GEO, MAT, screenMaterial } from "./OfficeFurniture";
import type { DeskSlot } from "./officeLayout";
import type { ScreenFace } from "./screenTextures";

interface Part { geometry: BufferGeometry; material: Material; position: [number, number, number]; scale?: [number, number, number]; cast: boolean }

const CHAIR_Z = 0.62;
const rounded = (w: number, h: number, d: number, r: number) => new RoundedBoxGeometry(w, h, d, 2, r);

const PARTS: Part[] = [
  { geometry: rounded(1.5, 0.06, 0.8, 0.02), material: MAT.deskTop, position: [0, 0.74, 0], cast: true },
  { geometry: GEO.box, material: MAT.deskBody, position: [0.53, 0.36, 0], scale: [0.36, 0.7, 0.72], cast: true },
  { geometry: GEO.box, material: MAT.deskLeg, position: [-0.7, 0.36, 0], scale: [0.05, 0.7, 0.7], cast: true },
  { geometry: GEO.box, material: MAT.deskBody, position: [0, 0.94, -0.41], scale: [1.5, 0.34, 0.03], cast: true },
  { geometry: GEO.box, material: MAT.monitor, position: [0, 0.87, -0.22], scale: [0.08, 0.2, 0.08], cast: true },
  { geometry: GEO.box, material: MAT.monitor, position: [0, 0.78, -0.22], scale: [0.24, 0.02, 0.16], cast: true },
  { geometry: rounded(0.72, 0.44, 0.05, 0.02), material: MAT.monitor, position: [0, 1.18, -0.24], cast: true },
  { geometry: GEO.box, material: MAT.keyboard, position: [0, 0.78, 0.24], scale: [0.42, 0.02, 0.14], cast: true },
  { geometry: GEO.cyl, material: MAT.chair, position: [0, 0.03, CHAIR_Z], scale: [0.3, 0.04, 0.3], cast: true },
  { geometry: GEO.cyl, material: MAT.chair, position: [0, 0.25, CHAIR_Z], scale: [0.03, 0.42, 0.03], cast: false },
  { geometry: rounded(0.5, 0.08, 0.48, 0.03), material: MAT.chairSeat, position: [0, 0.48, CHAIR_Z], cast: true },
  { geometry: rounded(0.48, 0.5, 0.07, 0.03), material: MAT.chairSeat, position: [0, 0.78, CHAIR_Z + 0.24], cast: true },
];

const SCREEN_GEOMETRY = new PlaneGeometry(0.66, 0.38);
const SCREEN_LOCAL: [number, number, number] = [0, 1.18, -0.212];
const FACES: ScreenFace[] = ["working", "idle", "waiting", "paused", "empty"];

function deskMatrix(desk: DeskSlot, local: [number, number, number], scale: [number, number, number] = [1, 1, 1]): Matrix4 {
  const turn = desk.facing === "north" ? 0 : Math.PI;
  const world = new Matrix4().compose(new Vector3(desk.x, 0, desk.z), new Quaternion().setFromEuler(new Euler(0, turn, 0)), new Vector3(1, 1, 1));
  const part = new Matrix4().compose(new Vector3(...local), new Quaternion(), new Vector3(...scale));
  return world.multiply(part);
}

function PartInstances({ part, desks }: { part: Part; desks: DeskSlot[] }) {
  const ref = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    desks.forEach((desk, i) => mesh.setMatrixAt(i, deskMatrix(desk, part.position, part.scale)));
    mesh.count = desks.length;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [desks, part]);
  return <instancedMesh ref={ref} args={[part.geometry, part.material, Math.max(1, desks.length)]} castShadow={part.cast} receiveShadow frustumCulled={false} />;
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

/** All desks with the monitor face each one's agent state calls for. */
export function DeskInstances({ desks, agents }: { desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent> }) {
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
      {PARTS.map((part, i) => <PartInstances key={`${i}:${desks.length}`} part={part} desks={desks} />)}
      {FACES.map((face) => (byFace.get(face)!.length > 0 ? <ScreenInstances key={face} face={face} desks={byFace.get(face)!} /> : null))}
    </group>
  );
}

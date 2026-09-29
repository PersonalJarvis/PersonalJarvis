/**
 * The lead office's dog as a living figure: a chunky black-and-tan rottweiler
 * in the toy-office style with a jointed body (legs with knees, head, ears,
 * tail, tongue) whose pose blends between standing, trotting, sniffing,
 * sitting, lying and sleeping. Its day comes from dogLife.ts; it walks on the
 * office navigation grid, and the person can pet it (E or a click).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Billboard, Html } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import {
  CylinderGeometry, ExtrudeGeometry, MeshStandardMaterial, Shape, SphereGeometry, TorusGeometry, type Group, type Mesh,
} from "three";
import { useT } from "@/i18n";
import { Rounded } from "./OfficeFurniture";
import { createRng } from "./officeBehavior";
import {
  DOG_FOLLOW_FAR, DOG_FOLLOW_NEAR, DOG_PET_RANGE, DOG_PETTED_MS, DOG_POSE, DOG_RUN_SPEED, DOG_WALK_SPEED,
  followPoint, nextDogStep, useOfficeDog, type DogActivity, type DogPose,
} from "./dogLife";
import type { Furniture, Room } from "./officeLayout";
import { findPath, randomWalkablePoint, type NavGrid } from "./officeNav";
import { stepMover, turnToward, type Mover } from "./officeMotion";
import { player, useOfficeStore } from "./officeStore";

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

const C = { black: "#1d1a19", tan: "#a8612c", collar: "#7a4a2a", tongue: "#e8768a", eye: "#2a1a10", heart: "#ff5a7a" } as const;
const DM = {
  black: new MeshStandardMaterial({ color: C.black, roughness: 0.75 }),
  tan: new MeshStandardMaterial({ color: C.tan, roughness: 0.8 }),
  collar: new MeshStandardMaterial({ color: C.collar, roughness: 0.6 }),
  brass: new MeshStandardMaterial({ color: "#d8ae52", roughness: 0.3, metalness: 0.75 }),
  tongue: new MeshStandardMaterial({ color: C.tongue, roughness: 0.5 }),
  eye: new MeshStandardMaterial({ color: C.eye, roughness: 0.2 }),
  shine: new MeshStandardMaterial({ color: "#ffffff", emissive: "#ffffff", emissiveIntensity: 0.6 }),
  heart: new MeshStandardMaterial({ color: C.heart, emissive: C.heart, emissiveIntensity: 0.55, roughness: 0.4, transparent: true }),
};
const DG = {
  sphere: new SphereGeometry(1, 20, 14),
  cyl: new CylinderGeometry(1, 1, 1, 14),
  taper: new CylinderGeometry(0.75, 1, 1, 14),
  collar: new TorusGeometry(1, 0.16, 8, 28),
};

function heartGeometry(): ExtrudeGeometry {
  const s = new Shape();
  s.moveTo(0, -0.5);
  s.bezierCurveTo(-0.1, -0.35, -0.55, -0.15, -0.5, 0.15);
  s.bezierCurveTo(-0.45, 0.45, -0.1, 0.5, 0, 0.25);
  s.bezierCurveTo(0.1, 0.5, 0.45, 0.45, 0.5, 0.15);
  s.bezierCurveTo(0.55, -0.15, 0.1, -0.35, 0, -0.5);
  const g = new ExtrudeGeometry(s, { depth: 0.18, bevelEnabled: true, bevelSize: 0.06, bevelThickness: 0.06, bevelSegments: 2 });
  g.center();
  return g;
}
const HEART = heartGeometry();

type Vec3 = [number, number, number];
function Ball({ r, p, m, cast = true }: { r: number | Vec3; p: Vec3; m: MeshStandardMaterial; cast?: boolean }) {
  return <mesh geometry={DG.sphere} material={m} position={p} scale={r} castShadow={cast} />;
}
function Bone({ radius, length, m, y = 0, geometry = DG.cyl }: { radius: number; length: number; m: MeshStandardMaterial; y?: number; geometry?: CylinderGeometry }) {
  return <mesh geometry={geometry} material={m} position={[0, y - length / 2, 0]} scale={[radius, length, radius]} castShadow />;
}

/** Joint handles the animator writes every frame. */
interface DogRig {
  body: Group | null; torso: Group | null; head: Group | null; tail: Group | null; tongue: Mesh | null;
  eyesOpen: Group | null; eyesShut: Group | null;
  legs: { hip: Group | null; knee: Group | null }[];
}

/** One leg: an upper bone from the hip, a tan lower bone and paw from the knee. */
function Leg({ at, rig, index, thigh }: { at: Vec3; rig: DogRig; index: number; thigh: number }) {
  return (
    <group position={at} ref={(g) => { rig.legs[index].hip = g; }}>
      <Bone radius={thigh} length={0.19} m={DM.black} geometry={DG.taper} />
      <group position={[0, -0.18, 0]} ref={(g) => { rig.legs[index].knee = g; }}>
        <Bone radius={0.036} length={0.1} m={DM.black} />
        <Bone radius={0.034} length={0.07} m={DM.tan} y={-0.09} />
        <Ball r={[0.045, 0.028, 0.06]} p={[0, -0.165, 0.02]} m={DM.tan} />
      </group>
    </group>
  );
}

/**
 * The rottweiler, facing +z, paws on y = 0 when standing. `body` pivots at the
 * hind hips so sitting tips the front up; legs counter-rotate to stay planted.
 */
function DogModel({ rig }: { rig: DogRig }) {
  return (
    <group>
      <group position={[0, 0.36, -0.2]} ref={(g) => { rig.body = g; }}>
        <group ref={(g) => { rig.torso = g; }}>
          {/* Barrel, chest and rump, with the tan chest patch. */}
          <Rounded size={[0.3, 0.27, 0.56]} radius={0.12} position={[0, 0.07, 0.2]} material={DM.black} />
          <Ball r={[0.16, 0.16, 0.14]} p={[0, 0.05, 0.42]} m={DM.black} />
          <Ball r={[0.15, 0.14, 0.12]} p={[0, 0.07, -0.03]} m={DM.black} />
          <Ball r={[0.09, 0.07, 0.03]} p={[0, 0.0, 0.54]} m={DM.tan} cast={false} />
        </group>
        {/* Stub tail. */}
        <group position={[0, 0.14, -0.12]} ref={(g) => { rig.tail = g; }}>
          <mesh geometry={DG.cyl} material={DM.black} position={[0, 0.04, -0.03]} rotation={[-0.9, 0, 0]} scale={[0.028, 0.1, 0.028]} />
        </group>
        {/* Neck, collar and head. */}
        <Ball r={[0.11, 0.12, 0.11]} p={[0, 0.16, 0.5]} m={DM.black} />
        <group position={[0, 0.15, 0.52]} rotation={[Math.PI / 2 - 0.6, 0, 0]}>
          <mesh geometry={DG.collar} material={DM.collar} scale={[0.115, 0.115, 0.9]} />
          <Ball r={0.018} p={[0, -0.12, -0.02]} m={DM.brass} cast={false} />
        </group>
        <group position={[0, 0.25, 0.6]} scale={1.25} ref={(g) => { rig.head = g; }}>
          <Ball r={[0.13, 0.12, 0.14]} p={[0, 0.03, 0.02]} m={DM.black} />
          {/* Muzzle: tan sides and chin, black bridge and nose. */}
          <Rounded size={[0.13, 0.09, 0.13]} radius={0.04} position={[0, -0.03, 0.14]} material={DM.tan} />
          <Rounded size={[0.08, 0.03, 0.12]} radius={0.012} position={[0, 0.02, 0.14]} material={DM.black} />
          <Ball r={[0.035, 0.028, 0.025]} p={[0, 0.01, 0.21]} m={DM.black} />
          {[-1, 1].map((s) => (
            <group key={s}>
              <Ball r={[0.045, 0.04, 0.035]} p={[s * 0.06, -0.04, 0.09]} m={DM.tan} cast={false} />
              <Ball r={[0.022, 0.013, 0.012]} p={[s * 0.052, 0.095, 0.105]} m={DM.tan} cast={false} />
              {/* Floppy triangular ears. */}
              <mesh geometry={DG.sphere} material={DM.black} position={[s * 0.115, 0.07, 0.0]} rotation={[0.2, 0, s * 0.5]} scale={[0.02, 0.075, 0.055]} castShadow />
            </group>
          ))}
          <group ref={(g) => { rig.eyesOpen = g; }}>
            {[-1, 1].map((s) => (
              <group key={s}>
                <Ball r={0.02} p={[s * 0.05, 0.06, 0.13]} m={DM.eye} cast={false} />
                <Ball r={0.006} p={[s * 0.047, 0.066, 0.148]} m={DM.shine} cast={false} />
              </group>
            ))}
          </group>
          <group ref={(g) => { rig.eyesShut = g; }} visible={false}>
            {[-1, 1].map((s) => (
              <mesh key={s} geometry={DG.cyl} material={DM.eye} position={[s * 0.05, 0.058, 0.135]} rotation={[0, 0, Math.PI / 2]} scale={[0.004, 0.035, 0.004]} />
            ))}
          </group>
          <mesh ref={(m) => { rig.tongue = m; }} geometry={DG.sphere} material={DM.tongue} position={[0, -0.085, 0.17]} scale={[0.028, 0.008, 0.045]} visible={false} />
        </group>
        <Leg at={[-0.09, 0.0, 0.4]} rig={rig} index={0} thigh={0.05} />
        <Leg at={[0.09, 0.0, 0.4]} rig={rig} index={1} thigh={0.05} />
        <Leg at={[-0.1, 0.0, 0.0]} rig={rig} index={2} thigh={0.065} />
        <Leg at={[0.1, 0.0, 0.0]} rig={rig} index={3} thigh={0.065} />
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Pose blending
// ---------------------------------------------------------------------------

interface Pose { pitch: number; hipY: number; front: number; frontKnee: number; hind: number; hindKnee: number; head: number }

const POSES: Record<DogPose, Pose> = {
  stand: { pitch: 0, hipY: 0.36, front: 0, frontKnee: 0, hind: 0, hindKnee: 0, head: 0 },
  sniff: { pitch: 0.08, hipY: 0.36, front: -0.1, frontKnee: 0.15, hind: 0, hindKnee: 0, head: 0.75 },
  // Front up on straight front legs; thighs forward along the floor, hocks folded back.
  sit: { pitch: -0.62, hipY: 0.14, front: 0.62, frontKnee: 0, hind: -1.05, hindKnee: 1.7, head: -0.2 },
  // Sphinx: body low, forelegs stretched forward, hind legs tucked.
  lie: { pitch: 0, hipY: 0.15, front: -1.45, frontKnee: 0.1, hind: -1.25, hindKnee: 1.9, head: 0.05 },
  sleep: { pitch: 0, hipY: 0.14, front: -1.45, frontKnee: 0.1, hind: -1.25, hindKnee: 1.9, head: 0.55 },
};

const damp = (from: number, to: number, k: number) => from + (to - from) * k;

// ---------------------------------------------------------------------------
// The living dog
// ---------------------------------------------------------------------------

export interface OfficeDogProps {
  room: Room;
  bed: Furniture;
  grid: NavGrid;
  awake: boolean;
  reduced: boolean;
}

export function OfficeDog({ room, bed, grid, awake, reduced }: OfficeDogProps) {
  const t = useT();
  const root = useRef<Group>(null);
  const rig = useMemo<DogRig>(() => ({ body: null, torso: null, head: null, tail: null, tongue: null, eyesOpen: null, eyesShut: null, legs: [0, 1, 2, 3].map(() => ({ hip: null, knee: null })) }), []);
  const rng = useMemo(() => createRng("office-dog"), []);
  // Facing out of the corner, towards the middle of the room.
  const homeHeading = useMemo(() => Math.atan2((room.minX + room.maxX) / 2 - bed.x, (room.minZ + room.maxZ) / 2 - bed.z), [room, bed]);
  const mover = useRef<Mover>({ x: bed.x, z: bed.z, heading: homeHeading, path: [] });
  const brain = useRef({ activity: "sleep" as DogActivity, until: Date.now() + 8000, roamsLeft: 0, lastPet: useOfficeDog.getState().petSeq, repathAt: 0 });
  const pose = useRef<Pose>({ ...POSES.sleep });
  const gait = useRef(0);
  const [activity, setActivity] = useState<DogActivity>("sleep");
  const [hearts, setHearts] = useState(0);
  const near = useOfficeDog((s) => s.near);
  const within = useMemo(() => ({ minX: room.minX + 0.4, maxX: room.maxX - 0.4, minZ: room.minZ + 0.6, maxZ: room.maxZ - 0.4 }), [room]);

  // A floor plan change (someone joined) puts the dog back in its basket.
  useEffect(() => {
    mover.current = { x: bed.x, z: bed.z, heading: homeHeading, path: [] };
    brain.current.activity = "sleep";
    setActivity("sleep");
  }, [bed.x, bed.z, homeHeading]);
  useEffect(() => () => useOfficeDog.getState().set({ near: false, pending: false }), []);

  const begin = (next: DogActivity, durationMs: number) => {
    const b = brain.current, m = mover.current;
    b.activity = next;
    b.until = Date.now() + durationMs;
    m.path = [];
    if (next === "roam" && !reduced) {
      const target = randomWalkablePoint(grid, rng, within);
      m.path = (target && findPath(grid, m, target)) ?? [];
    } else if (next === "home" && !reduced) {
      m.path = findPath(grid, m, bed) ?? [];
    } else if (next === "home" || (next === "roam" && reduced)) {
      m.x = bed.x; m.z = bed.z; m.heading = homeHeading;
    }
    setActivity(next);
  };

  useFrame(({ clock }, rawDt) => {
    if (!awake) return;
    const dt = Math.min(rawDt, 0.1);
    const now = Date.now();
    const b = brain.current, m = mover.current, dog = useOfficeDog.getState();

    // Petting: a new pet wakes it and turns it to the person.
    if (dog.petSeq !== b.lastPet) {
      b.lastPet = dog.petSeq;
      begin("petted", DOG_PETTED_MS);
      setHearts((h) => h + 1);
    }
    const toPerson = Math.hypot(player.x - m.x, player.z - m.z);
    const isNear = toPerson <= DOG_PET_RANGE;
    if (isNear !== dog.near) dog.set({ near: isNear });
    if (dog.pending) {
      if (isNear) dog.pet();
      else if (player.path.length === 0) dog.set({ pending: false });
    }

    // Movement and the schedule.
    let moved = 0;
    if (b.activity === "follow") {
      if (now >= b.until) begin("home", Infinity);
      else if (!reduced) {
        if (toPerson > DOG_FOLLOW_FAR && now >= b.repathAt) {
          b.repathAt = now + 600;
          m.path = findPath(grid, m, followPoint(player, m)) ?? [];
        } else if (toPerson < DOG_FOLLOW_NEAR) m.path = [];
        moved = stepMover(m, toPerson > 4 ? DOG_RUN_SPEED : DOG_WALK_SPEED * 1.4, dt).moved;
        if (moved === 0) m.heading = turnToward(m.heading, Math.atan2(player.x - m.x, player.z - m.z), 6 * dt);
      }
    } else if (b.activity === "roam" || b.activity === "home") {
      const step = stepMover(m, DOG_WALK_SPEED, dt);
      moved = step.moved;
      if (step.arrived) {
        if (b.activity === "home") { m.x = bed.x; m.z = bed.z; }
        const next = nextDogStep(b.activity, b.roamsLeft, rng);
        b.roamsLeft = next.roamsLeft;
        begin(next.activity, next.durationMs);
      }
    } else {
      if (b.activity === "petted") m.heading = turnToward(m.heading, Math.atan2(player.x - m.x, player.z - m.z), 8 * dt);
      if (b.activity === "sleep" && m.x === bed.x && m.z === bed.z) m.heading = turnToward(m.heading, homeHeading, 4 * dt);
      if (now >= b.until && !(reduced && b.activity === "sleep")) {
        const next = nextDogStep(b.activity, b.roamsLeft, rng);
        b.roamsLeft = next.roamsLeft;
        begin(next.activity, next.durationMs);
      }
    }

    // Place the figure; it lies a little higher on the basket's cushion.
    const inBed = Math.hypot(m.x - bed.x, m.z - bed.z) < 0.05;
    if (root.current) {
      root.current.position.set(m.x, inBed ? 0.1 : 0, m.z);
      root.current.rotation.y = m.heading;
    }

    // Blend the pose, then layer the gait, breathing, wag and head life on top.
    const walking = moved > 1e-4;
    const target = POSES[walking ? "stand" : DOG_POSE[b.activity]];
    const k = 1 - Math.exp(-8 * dt), p = pose.current;
    (Object.keys(p) as (keyof Pose)[]).forEach((key) => { p[key] = damp(p[key], target[key], k); });
    const speed = moved / Math.max(dt, 1e-3);
    gait.current += dt * (walking ? 5 + speed * 3.2 : 0);
    const swing = walking ? Math.sin(gait.current) * Math.min(0.6, 0.25 + speed * 0.12) : 0;
    const time = clock.elapsedTime;
    const sleeping = b.activity === "sleep";
    const happy = b.activity === "petted" || b.activity === "follow";

    if (rig.body) {
      rig.body.position.y = p.hipY + (walking ? Math.abs(Math.sin(gait.current)) * 0.02 : 0);
      rig.body.rotation.x = p.pitch;
    }
    if (rig.torso) {
      const breath = 1 + Math.sin(time * (sleeping ? 1.4 : 2.6)) * (sleeping ? 0.03 : 0.012);
      rig.torso.scale.set(breath, breath, 1);
    }
    // Diagonal pairs swing together: front-left with hind-right.
    const phase = [swing, -swing, -swing, swing];
    rig.legs.forEach((leg, i) => {
      const front = i < 2;
      if (leg.hip) leg.hip.rotation.x = (front ? p.front : p.hind) + phase[i];
      if (leg.knee) leg.knee.rotation.x = (front ? p.frontKnee : p.hindKnee) + (walking ? Math.max(0, -phase[i]) * (front ? -0.6 : 0.9) : 0);
    });
    if (rig.head) {
      const sniffBob = b.activity === "sniff" ? Math.sin(time * 9) * 0.08 : 0;
      rig.head.rotation.x = p.head + sniffBob + (sleeping ? Math.sin(time * 1.4) * 0.02 : 0);
      const look = happy && !walking ? Math.sin(time * 1.3) * 0.25 : b.activity === "sniff" ? Math.sin(time * 1.7) * 0.4 : 0;
      rig.head.rotation.y = damp(rig.head.rotation.y, look, k);
      rig.head.rotation.z = damp(rig.head.rotation.z, b.activity === "petted" ? 0.22 : 0, k);
    }
    if (rig.tail) rig.tail.rotation.y = Math.sin(time * (happy ? 22 : 6)) * (sleeping ? 0.05 : happy ? 0.7 : walking ? 0.35 : 0.15);
    if (rig.tongue) rig.tongue.visible = happy || (walking && speed > 2) || b.activity === "sit";
    if (rig.eyesOpen) rig.eyesOpen.visible = !sleeping;
    if (rig.eyesShut) rig.eyesShut.visible = sleeping;
  });

  const onClick = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6) return;
    event.stopPropagation();
    const dog = useOfficeDog.getState();
    if (dog.near) { dog.pet(); return; }
    dog.set({ pending: true });
    useOfficeStore.getState().requestWalk({ x: mover.current.x, z: mover.current.z });
  };

  return (
    <group ref={root} name="office-dog">
      <DogModel rig={rig} />
      {/* Generous invisible hit box: the dog is small from the usual camera distance. */}
      <mesh position={[0, 0.35, 0.1]} visible={false} onClick={onClick}
        onPointerOver={() => { document.body.style.cursor = "pointer"; }} onPointerOut={() => { document.body.style.cursor = ""; }}>
        <boxGeometry args={[0.6, 0.7, 1.1]} />
      </mesh>
      {hearts > 0 && <Hearts key={hearts} animate={!reduced} />}
      {near && activity !== "petted" && (
        <Html center position={[0, 1.0, 0]} zIndexRange={[24, 0]}>
          <span className="office-plate office-seat-prompt" data-office-ui><kbd>E</kbd>{t("society.office.dog_pet")}</span>
        </Html>
      )}
    </group>
  );
}

/** Three hearts float up from the dog's head and fade out. */
function Hearts({ animate }: { animate: boolean }) {
  const group = useRef<Group>(null);
  const start = useRef(-1);
  const material = useMemo(() => DM.heart.clone(), []);
  useEffect(() => () => material.dispose(), [material]);
  useFrame(({ clock }) => {
    if (start.current < 0) start.current = clock.elapsedTime;
    const age = clock.elapsedTime - start.current;
    const g = group.current;
    if (!g) return;
    g.children.forEach((child, i) => {
      const a = Math.max(0, age - i * 0.35);
      child.position.set(Math.sin(a * 3 + i * 2) * 0.12 + (i - 1) * 0.12, 0.75 + (animate ? a * 0.45 : 0.1 * i), 0.25);
      const s = a > 0 ? Math.min(1, a * 4) * 0.09 : 0;
      child.scale.setScalar(s);
    });
    material.opacity = Math.max(0, 1 - Math.max(0, age - 1.6) / 1.2);
    g.visible = age < 3.2;
  });
  return (
    <group ref={group}>
      {[0, 1, 2].map((i) => (
        <Billboard key={i}><mesh geometry={HEART} material={material} /></Billboard>
      ))}
    </group>
  );
}

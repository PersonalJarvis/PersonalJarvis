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
  CylinderGeometry, ExtrudeGeometry, MeshStandardMaterial, OctahedronGeometry, Shape, SphereGeometry, TorusGeometry,
  type Group, type Mesh,
} from "three";
import { useT } from "@/i18n";
import { Rounded } from "./OfficeFurniture";
import { createRng } from "./officeBehavior";
import {
  DOG_CHEW_MS, DOG_FOLLOW_FAR, DOG_FOLLOW_NEAR, DOG_PET_RANGE, DOG_PETTED_MS, DOG_POSE, DOG_RUN_SPEED, DOG_TRICK_MS,
  DOG_WALK_SPEED, TREAT_JAR_RANGE, followPoint, insideRoom, nextDogStep, pickOtherBasket, useOfficeDog,
  type DogActivity, type DogPose,
} from "./dogLife";
import { TreatBone } from "./dogProps";
import type { Furniture, Point, Rect, Room } from "./officeLayout";
import { findPath, randomWalkablePoint, type NavGrid } from "./officeNav";
import { stepMoverAvoiding, turnToward, type Mover } from "./officeMotion";
import { player, useOfficeStore } from "./officeStore";
import { bodiesExcept, extraBodies } from "./walkerRegistry";
import { isWalkable } from "./officeNav";

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
  eyesOpen: Group | null; eyesShut: Group | null; mouthBone: Group | null;
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
          <group ref={(g) => { rig.mouthBone = g; }} position={[0, -0.06, 0.2]} visible={false}><TreatBone scale={0.75} /></group>
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

/** The dog's id among the solid bodies on the floor. */
const DOG_ID = "office-dog";

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
  // Begging: up on the hind legs, front paws tucked against the chest.
  beg: { pitch: -1.2, hipY: 0.2, front: 0.5, frontKnee: -1.6, hind: -0.35, hindKnee: 1.35, head: -0.45 },
};

const damp = (from: number, to: number, k: number) => from + (to - from) * k;

// ---------------------------------------------------------------------------
// The living dog
// ---------------------------------------------------------------------------

export interface OfficeDogProps {
  /** Every dog basket on the floor; the dog lives in the room of the one it last slept in. */
  beds: Furniture[];
  rooms: Room[];
  /** The treat jar that hands out bones, when the floor has one. */
  jar: Furniture | null;
  grid: NavGrid;
  awake: boolean;
  reduced: boolean;
}

/** The part of a room the dog roams: its floor, a little in from the walls. */
function roamArea(room: Room): Rect {
  return { minX: room.minX + 0.4, maxX: room.maxX - 0.4, minZ: room.minZ + 0.5, maxZ: room.maxZ - 0.4 };
}

export function OfficeDog({ beds, rooms, jar, grid, awake, reduced }: OfficeDogProps) {
  const t = useT();
  const root = useRef<Group>(null);
  const spin = useRef<Group>(null);
  const rig = useMemo<DogRig>(() => ({
    body: null, torso: null, head: null, tail: null, tongue: null, eyesOpen: null, eyesShut: null, mouthBone: null,
    legs: [0, 1, 2, 3].map(() => ({ hip: null, knee: null })),
  }), []);
  const rng = useMemo(() => createRng("office-dog"), []);
  const roomOf = useMemo(() => (bed: Furniture) => rooms.find((r) => r.kind === bed.room) ?? null, [rooms]);
  // Facing out of its corner, towards the middle of the basket's room.
  const headingAt = useMemo(() => (bed: Furniture) => {
    const room = roomOf(bed);
    return room ? Math.atan2((room.minX + room.maxX) / 2 - bed.x, (room.minZ + room.maxZ) / 2 - bed.z) : 0;
  }, [roomOf]);
  const basketKey = beds.map((b) => `${b.id}:${b.x.toFixed(2)}:${b.z.toFixed(2)}`).join("|");
  const first = beds[0];
  const mover = useRef<Mover>({ x: first.x, z: first.z, heading: headingAt(first), path: [] });
  const brain = useRef({
    activity: "sleep" as DogActivity, until: Date.now() + 8000, roamsLeft: 0, basket: 0, bone: false, startedAt: 0,
    lastPet: useOfficeDog.getState().petSeq, lastTreat: useOfficeDog.getState().treatSeq, repathAt: 0,
  });
  const pose = useRef<Pose>({ ...POSES.sleep });
  const gait = useRef(0);
  const [activity, setActivity] = useState<DogActivity>("sleep");
  const [hearts, setHearts] = useState(0);
  const [sparkles, setSparkles] = useState(0);
  const near = useOfficeDog((s) => s.near);
  const nearJar = useOfficeDog((s) => s.nearJar);
  const hasBone = useOfficeDog((s) => s.hasBone);

  // A changed floor plan (someone joined) puts the dog back in its first basket.
  useEffect(() => {
    const bed = beds[0];
    mover.current = { x: bed.x, z: bed.z, heading: headingAt(bed), path: [] };
    Object.assign(brain.current, { activity: "sleep", basket: 0, bone: false, until: Date.now() + 8000 });
    setActivity("sleep");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basketKey, headingAt]);
  useEffect(() => () => {
    useOfficeDog.getState().set({ near: false, nearJar: false, pending: null });
    extraBodies.delete(DOG_ID);
  }, []);
  // Dev-only handle for runtime checks (hold the dog still, read where it is).
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const w = window as unknown as Record<string, unknown>;
    w.__officeDog = { get brain() { return brain.current; }, get mover() { return mover.current; } };
    return () => { delete w.__officeDog; };
  }, []);

  const begin = (next: DogActivity, durationMs: number) => {
    const b = brain.current, m = mover.current;
    // Moving house: pick another basket; the walk there is the activity.
    if (next === "move") b.basket = pickOtherBasket(b.basket, beds.length, rng);
    const bed = beds[b.basket] ?? beds[0];
    const room = roomOf(bed);
    b.activity = next;
    b.until = Date.now() + durationMs;
    b.startedAt = Date.now();
    m.path = [];
    if (reduced) {
      // No walking with reduced motion: the dog stays where it is and just changes pose.
      if (next === "roam" || next === "home" || next === "move") { b.activity = "sleep"; b.until = Infinity; }
    } else if (next === "roam" && room) {
      const target = randomWalkablePoint(grid, rng, roamArea(room));
      m.path = (target && findPath(grid, m, target)) ?? [];
    } else if (next === "home" || next === "move") {
      m.path = findPath(grid, m, bed) ?? [];
    }
    setActivity(b.activity);
  };

  const walkable = useMemo(() => (q: Point) => isWalkable(grid, q), [grid]);
  const others = () => Array.from(bodiesExcept(DOG_ID, player));

  useFrame(({ clock }, rawDt) => {
    if (!awake) return;
    const dt = Math.min(rawDt, 0.1);
    const now = Date.now();
    const b = brain.current, m = mover.current, dog = useOfficeDog.getState();
    const bed = beds[b.basket] ?? beds[0];
    const room = roomOf(bed);

    // A bone beats everything; a pet wakes it and turns it to the person.
    if (dog.treatSeq !== b.lastTreat) {
      b.lastTreat = dog.treatSeq;
      b.bone = true;
      begin("trick", DOG_TRICK_MS);
      setSparkles((n) => n + 1);
    } else if (dog.petSeq !== b.lastPet) {
      b.lastPet = dog.petSeq;
      if (b.activity !== "trick") begin("petted", DOG_PETTED_MS);
      setHearts((h) => h + 1);
    }
    const toPerson = Math.hypot(player.x - m.x, player.z - m.z);
    const isNear = toPerson <= DOG_PET_RANGE;
    if (isNear !== dog.near) dog.set({ near: isNear });
    const atJar = !!jar && Math.hypot(player.x - jar.x, player.z - jar.z) <= TREAT_JAR_RANGE;
    if (atJar !== dog.nearJar) dog.set({ nearJar: atJar });
    if (dog.pending === "pet") {
      if (isNear) dog.interact();
      else if (player.path.length === 0) dog.set({ pending: null });
    } else if (dog.pending === "jar") {
      if (atJar) dog.takeBone();
      else if (player.path.length === 0) dog.set({ pending: null });
    }

    // Movement and the schedule.
    let moved = 0;
    const advance = () => {
      const next = nextDogStep(b.activity, b.roamsLeft, rng, beds.length);
      b.roamsLeft = next.roamsLeft;
      begin(next.activity, next.durationMs);
    };
    if (b.activity === "follow") {
      // It follows inside its own room only; when the person leaves, it goes back to bed.
      if (now >= b.until || !room || !insideRoom(room, player)) begin("home", Infinity);
      else {
        if (toPerson > DOG_FOLLOW_FAR && now >= b.repathAt) {
          b.repathAt = now + 600;
          m.path = findPath(grid, m, followPoint(player, m)) ?? [];
        } else if (toPerson < DOG_FOLLOW_NEAR) m.path = [];
        moved = stepMoverAvoiding(m, toPerson > 4 ? DOG_RUN_SPEED : DOG_WALK_SPEED * 1.4, dt, others(), walkable).moved;
        if (moved === 0) m.heading = turnToward(m.heading, Math.atan2(player.x - m.x, player.z - m.z), 6 * dt);
      }
    } else if (b.activity === "roam" || b.activity === "home" || b.activity === "move") {
      const step = stepMoverAvoiding(m, b.activity === "move" ? DOG_WALK_SPEED * 1.2 : DOG_WALK_SPEED, dt, others(), walkable);
      moved = step.moved;
      if (step.arrived) {
        if (b.activity !== "roam") { m.x = bed.x; m.z = bed.z; }
        // Carrying the bone home: settle down and chew on it.
        if (b.activity === "home" && b.bone) begin("chew", DOG_CHEW_MS);
        else advance();
      }
    } else {
      if (b.activity === "petted" || b.activity === "trick") m.heading = turnToward(m.heading, Math.atan2(player.x - m.x, player.z - m.z), 8 * dt);
      if ((b.activity === "sleep" || b.activity === "chew") && m.x === bed.x && m.z === bed.z) m.heading = turnToward(m.heading, headingAt(bed), 4 * dt);
      if (now >= b.until) {
        if (b.activity === "chew") b.bone = false;
        advance();
      }
    }

    // A solid body for everyone else: nobody walks through the dog.
    extraBodies.set(DOG_ID, { x: m.x, z: m.z });

    // Place the figure; it lies a little higher on the basket's cushion.
    const inBed = beds.some((d) => Math.hypot(m.x - d.x, m.z - d.z) < 0.05);
    const trickT = b.activity === "trick" ? (now - b.startedAt) / 1000 : -1;
    // The trick: beg (0–0.9 s), catch, then two spins in the air (0.9–2.7 s), land and sit proud.
    const spinning = trickT >= 0.9 && trickT < 2.7 && !reduced;
    const hop = spinning ? Math.abs(Math.sin(((trickT - 0.9) / 0.9) * Math.PI)) * 0.35 : 0;
    if (root.current) {
      root.current.position.set(m.x, (inBed ? 0.1 : 0) + hop, m.z);
      root.current.rotation.y = m.heading;
    }
    if (spin.current) spin.current.rotation.y = spinning ? ((trickT - 0.9) / 1.8) * Math.PI * 4 : 0;

    // Blend the pose, then layer the gait, breathing, wag and head life on top.
    const walking = moved > 1e-4;
    const trickPose: DogPose = trickT < 0 ? DOG_POSE[b.activity] : trickT < 0.9 ? "beg" : spinning ? "stand" : "sit";
    const target = POSES[walking ? "stand" : trickPose];
    const k = 1 - Math.exp(-(spinning ? 14 : 8) * dt), p = pose.current;
    (Object.keys(p) as (keyof Pose)[]).forEach((key) => { p[key] = damp(p[key], target[key], k); });
    const speed = moved / Math.max(dt, 1e-3);
    gait.current += dt * (walking ? 5 + speed * 3.2 : 0);
    const swing = walking ? Math.sin(gait.current) * Math.min(0.6, 0.25 + speed * 0.12) : spinning ? -0.5 : 0;
    const time = clock.elapsedTime;
    const sleeping = b.activity === "sleep";
    const chewing = b.activity === "chew";
    const happy = b.activity === "petted" || b.activity === "follow" || b.activity === "trick" || chewing;

    if (rig.body) {
      rig.body.position.y = p.hipY + (walking ? Math.abs(Math.sin(gait.current)) * 0.02 : 0);
      rig.body.rotation.x = p.pitch;
    }
    if (rig.torso) {
      const breath = 1 + Math.sin(time * (sleeping ? 1.4 : 2.6)) * (sleeping ? 0.03 : 0.012);
      rig.torso.scale.set(breath, breath, 1);
    }
    // Diagonal pairs swing together: front-left with hind-right. Mid-spin all legs tuck.
    const phase = spinning ? [swing, swing, -swing, -swing] : [swing, -swing, -swing, swing];
    rig.legs.forEach((leg, i) => {
      const front = i < 2;
      if (leg.hip) leg.hip.rotation.x = (front ? p.front : p.hind) + phase[i];
      if (leg.knee) leg.knee.rotation.x = (front ? p.frontKnee : p.hindKnee) + (walking ? Math.max(0, -phase[i]) * (front ? -0.6 : 0.9) : 0);
    });
    if (rig.head) {
      const sniffBob = b.activity === "sniff" ? Math.sin(time * 9) * 0.08 : 0;
      const chew = chewing ? Math.sin(time * 7) * 0.08 : 0;
      rig.head.rotation.x = p.head + sniffBob + chew + (sleeping ? Math.sin(time * 1.4) * 0.02 : 0);
      const look = happy && !walking && !chewing ? Math.sin(time * 1.3) * 0.25 : b.activity === "sniff" ? Math.sin(time * 1.7) * 0.4 : 0;
      rig.head.rotation.y = damp(rig.head.rotation.y, look, k);
      rig.head.rotation.z = damp(rig.head.rotation.z, b.activity === "petted" || chewing ? 0.22 : 0, k);
    }
    if (rig.tail) rig.tail.rotation.y = Math.sin(time * (happy ? 22 : 6)) * (sleeping ? 0.05 : happy ? 0.7 : walking ? 0.35 : 0.15);
    // The bone sits in its mouth from the catch until the chewing is done.
    const holding = b.bone && (trickT < 0 || trickT >= 0.7);
    if (rig.mouthBone) rig.mouthBone.visible = holding;
    if (rig.tongue) rig.tongue.visible = !holding && (happy || (walking && speed > 2) || b.activity === "sit");
    if (rig.eyesOpen) rig.eyesOpen.visible = !sleeping;
    if (rig.eyesShut) rig.eyesShut.visible = sleeping;
  });

  const onClick = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6) return;
    event.stopPropagation();
    const dog = useOfficeDog.getState();
    if (dog.near) { dog.interact(); return; }
    dog.set({ pending: "pet" });
    const m = mover.current;
    const dx = player.x - m.x, dz = player.z - m.z, d = Math.hypot(dx, dz) || 1;
    useOfficeStore.getState().requestWalk({ x: m.x + (dx / d) * 0.95, z: m.z + (dz / d) * 0.95 });
  };
  const onJarClick = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6 || !jar) return;
    event.stopPropagation();
    const dog = useOfficeDog.getState();
    if (dog.hasBone) return;
    if (dog.nearJar) { dog.takeBone(); return; }
    dog.set({ pending: "jar" });
    // The jar faces +z (turned by its rotation): stand in front of it.
    useOfficeStore.getState().requestWalk({ x: jar.x + Math.sin(jar.rotationY) * 0.8, z: jar.z + Math.cos(jar.rotationY) * 0.8 });
  };
  const hover = (cursor: string) => () => { document.body.style.cursor = cursor; };
  const busy = activity === "petted" || activity === "trick";

  return (
    <>
      <group ref={root} name="office-dog">
        <group ref={spin}><DogModel rig={rig} /></group>
        {/* Generous invisible hit box: the dog is small from the usual camera distance. */}
        <mesh position={[0, 0.35, 0.1]} visible={false} onClick={onClick} onPointerOver={hover("pointer")} onPointerOut={hover("")}>
          <boxGeometry args={[0.6, 0.7, 1.1]} />
        </mesh>
        {hearts > 0 && <Burst key={`h${hearts}`} kind="heart" animate={!reduced} />}
        {sparkles > 0 && <Burst key={`s${sparkles}`} kind="spark" animate={!reduced} />}
        {near && !busy && (
          <Html center position={[0, 1.0, 0]} zIndexRange={[24, 0]}>
            <span className="office-plate office-seat-prompt" data-office-ui>
              <kbd>E</kbd>{t(hasBone ? "society.office.dog_treat" : "society.office.dog_pet")}
            </span>
          </Html>
        )}
      </group>
      {jar && (
        <group position={[jar.x, 0, jar.z]}>
          <mesh position={[0, 0.6, 0]} visible={false} onClick={onJarClick} onPointerOver={hover("pointer")} onPointerOut={hover("")}>
            <boxGeometry args={[0.6, 1.2, 0.6]} />
          </mesh>
          {nearJar && !hasBone && (
            <Html center position={[0, 1.55, 0]} zIndexRange={[24, 0]}>
              <span className="office-plate office-seat-prompt" data-office-ui><kbd>E</kbd>{t("society.office.dog_take_bone")}</span>
            </Html>
          )}
        </group>
      )}
    </>
  );
}

const SPARK = new OctahedronGeometry(1, 0);
const SPARK_MAT = new MeshStandardMaterial({ color: "#ffd44d", emissive: "#ffb300", emissiveIntensity: 1.2, roughness: 0.3, transparent: true });

/** Hearts float up from the dog's head (petting); gold sparks burst around it (the treat trick). */
function Burst({ kind, animate }: { kind: "heart" | "spark"; animate: boolean }) {
  const group = useRef<Group>(null);
  const start = useRef(-1);
  const material = useMemo(() => (kind === "heart" ? DM.heart : SPARK_MAT).clone(), [kind]);
  useEffect(() => () => material.dispose(), [material]);
  const count = kind === "heart" ? 3 : 12;
  const life = kind === "heart" ? 3.2 : 2.6;
  useFrame(({ clock }) => {
    if (start.current < 0) start.current = clock.elapsedTime;
    const age = clock.elapsedTime - start.current;
    const g = group.current;
    if (!g) return;
    g.children.forEach((child, i) => {
      if (kind === "heart") {
        const a = Math.max(0, age - i * 0.35);
        child.position.set(Math.sin(a * 3 + i * 2) * 0.12 + (i - 1) * 0.12, 0.75 + (animate ? a * 0.45 : 0.1 * i), 0.25);
        child.scale.setScalar(a > 0 ? Math.min(1, a * 4) * 0.09 : 0);
      } else {
        // Sparks fly out in a ring from mid-trick and drift down.
        const a = Math.max(0, age - 0.9);
        const angle = (i / count) * Math.PI * 2;
        const r = animate ? 0.2 + a * 0.9 : 0.5;
        child.position.set(Math.cos(angle) * r, 0.55 + (animate ? a * 0.6 - a * a * 0.35 : 0.2), Math.sin(angle) * r);
        child.rotation.set(age * 4, age * 3, 0);
        child.scale.setScalar(a > 0 ? 0.035 * Math.min(1, a * 5) : 0);
      }
    });
    material.opacity = Math.max(0, 1 - Math.max(0, age - (life - 1.2)) / 1.2);
    g.visible = age < life;
  });
  return (
    <group ref={group}>
      {Array.from({ length: count }, (_, i) => (kind === "heart"
        ? <Billboard key={i}><mesh geometry={HEART} material={material} /></Billboard>
        : <mesh key={i} geometry={SPARK} material={material} />))}
    </group>
  );
}

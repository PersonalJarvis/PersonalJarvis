/**
 * Retiring an agent, played out on the island — the R3F half of
 * `retirement.ts`.
 *
 * The lead walks up to the condemned figure, levels a rifle, fires, and the
 * body goes over backwards. Two bearers trot in from the mine road, roll it
 * onto a stretcher, carry it out to the quarry and tip it into the tunnel.
 * When the body lands in the dark, and not one moment before, the deletion is
 * committed.
 *
 * Three things about how it is built:
 *
 *  - it drives the EXISTING walkers rather than drawing copies of them. The
 *    lead and the condemned are ordinary roster figures; the ceremony writes
 *    their pose into `retireStore`'s mutable map each frame and the walkers
 *    read it instead of running their own sim. Nothing is duplicated, so the
 *    figures keep their own character, palette and nameplate throughout;
 *  - only the props are new: the stretcher, its two bearers (box stand-ins —
 *    they are scenery, not roster rows) and the mark left on the ground;
 *  - the whole route is planned ONCE, when the ceremony starts
 *    (`retirementRoute.ts`). No pathfinding runs inside the animation.
 *
 * The one cut: carrying a body from the town square to the mine is a hundred
 * and twenty metres, and nobody wants to watch a two-minute walk. After a few
 * seconds of the crew setting off, the ceremony jumps them forward ALONG
 * THEIR OWN ROUTE to the last stretch, and the camera flies after them. It is
 * a cut in a film, not a teleport to somewhere they were never going.
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type { Group, Mesh } from "three";

import { useCameraStore } from "./cameraStore";
import { buildIsland, groundY } from "./islandLayout";
import * as R from "./retirement";
import {
  attachRetirement,
  buryRetired,
  poseAt,
  releaseRetirementPose,
  setRetirementPose,
  useRetireStore,
  type Ceremony,
} from "./retireStore";
import { planRetirement, type RetirementPlan } from "./retirementRoute";
import { NOMINAL_WALK_MPS, TURN_RATE_RAD_S, headingFor, isMoving, stepAlong, turnToward } from "./walkerKinematics";
import { walkerPins } from "./walkerRegistry";
import { BoxWalkerFigure, type WalkerAnim } from "./WalkerFigure";
import { useKit } from "./WorldKit";
import { PAL } from "./worldMaterials";

/** The lead does not stroll to an execution. */
const MARCH_MPS = NOMINAL_WALK_MPS * 1.25;
/** The camera is only re-aimed once the focus has moved this far, in metres. */
const CAMERA_DEADBAND_M = 0.75;
/** How wide the stage is framed while the ceremony runs (zoom step). */
const CEREMONY_ZOOM = 1;
/** The mark on the ground grows to this radius, in metres. */
const STAIN_RADIUS_M = 0.85;
/** How far behind the feet the head lies once the body is down, in metres. */
const HEAD_OFFSET_M = 1.1;

interface Body {
  x: number;
  z: number;
  /** Height of the figure's origin above the ground, in metres. */
  y: number;
  heading: number;
  pitch: number;
}

interface Run {
  plan: RetirementPlan;
  phase: R.RetirePhase;
  /** Seconds inside the current phase. */
  t: number;
  lead: { x: number; z: number; heading: number };
  leadIdx: number;
  body: Body;
  /** Where the body came to rest after the topple — the stretcher's target. */
  restAt: [number, number];
  /** The stretcher's centre and the heading it is carried on. */
  crew: { x: number; z: number; heading: number };
  crewIdx: number;
  /** The route the crew is currently walking (in, out, then into the dark). */
  crewPath: Array<[number, number]>;
  /** Where the body left the stretcher, once it is in the air. */
  launch: [number, number, number] | null;
  /** The last point the camera was aimed at. */
  camera: [number, number];
  /** True once the lead has been handed back to its own sim. */
  released: boolean;
  /** Seconds since the ceremony started drawing — the stretcher's sway clock. */
  clock: number;
}

export function RetirementScene({ paused }: { paused: boolean }) {
  const ceremony = useRetireStore((s) => s.ceremony);

  // Tell the store a stage exists, so its "nobody picked this up" watchdog
  // stands down and the ceremony is actually played.
  useEffect(() => {
    if (ceremony) attachRetirement();
  }, [ceremony]);

  // Reduced motion freezes the island; an execution nobody can see must not
  // hold an agent hostage, so it is retired without the ceremony.
  useEffect(() => {
    if (ceremony && paused) useRetireStore.getState().cutShort();
  }, [ceremony, paused]);

  if (!ceremony || paused) return null;
  return <Execution key={ceremony.agentId} ceremony={ceremony} />;
}

function Execution({ ceremony }: { ceremony: Ceremony }) {
  const kit = useKit();
  const setPhase = useRetireStore((s) => s.setPhase);

  const stretcher = useRef<Group>(null);
  const bearerA = useRef<Group>(null);
  const bearerB = useRef<Group>(null);
  const stain = useRef<Mesh>(null);
  const animA = useRef<WalkerAnim>({ mode: "carry", speed: 0 });
  const animB = useRef<WalkerAnim>({ mode: "carry", speed: 0 });

  const bearerPalette = useMemo(
    () => ({ primary: PAL.bearerCloth, secondary: PAL.bearerTrim, accent: PAL.bearerLamp }),
    [],
  );

  // The plan is made once, from where the two figures actually are.
  const run = useRef<Run | null>(null);
  if (run.current === null) {
    const pins = walkerPins();
    const victim = pins.get(ceremony.agentId);
    const lead = ceremony.executionerId ? pins.get(ceremony.executionerId) : undefined;
    const plan = victim && lead ? planRetirement([victim.x, victim.z], [lead.x, lead.z]) : null;
    if (plan && victim && lead) {
      run.current = {
        plan,
        phase: "march",
        t: 0,
        lead: { x: plan.leadStart.x, z: plan.leadStart.z, heading: plan.leadStart.heading },
        leadIdx: 0,
        body: { x: plan.body.x, z: plan.body.z, y: 0, heading: plan.body.heading, pitch: 0 },
        restAt: [plan.body.x, plan.body.z],
        crew: { x: plan.bearerPath[0]?.[0] ?? plan.body.x, z: plan.bearerPath[0]?.[1] ?? plan.body.z, heading: 0 },
        crewIdx: 0,
        crewPath: plan.bearerPath,
        launch: null,
        camera: [plan.body.x, plan.body.z],
        released: false,
        clock: 0,
      };
    }
  }

  // A body nothing can reach, or an executioner who is not on the island:
  // the agent is still retired, just without the ceremony.
  useEffect(() => {
    if (run.current === null) useRetireStore.getState().cutShort();
  }, []);

  // Frame the stage once, at the start; the per-frame aim below only pans.
  useEffect(() => {
    const r = run.current;
    if (!r) return;
    useCameraStore.getState().focusOn(r.body.x, r.body.z, CEREMONY_ZOOM);
  }, []);

  useFrame((_, delta) => {
    const r = run.current;
    if (!r) return;
    const { map } = buildIsland();
    const dt = Math.min(delta, 0.1);
    r.clock += dt;
    r.t += dt;

    const advance = () => {
      r.phase = R.nextPhase(r.phase);
      r.t = 0;
      onEnterPhase(r);
      setPhase(r.phase);
    };
    const sway = R.carrySway(r.clock);

    // ----- the beat --------------------------------------------------------
    if (r.phase === "march") {
      const step = stepAlong(r.lead.x, r.lead.z, r.plan.leadPath, r.leadIdx, MARCH_MPS, dt);
      r.lead.x = step.x;
      r.lead.z = step.z;
      r.leadIdx = step.index;
      if (isMoving(step.vx, step.vz)) {
        r.lead.heading = turnToward(r.lead.heading, headingFor(step.vx, step.vz), TURN_RATE_RAD_S * dt);
      }
      if (step.arrived) advance();
    } else if (R.isDistancePhase(r.phase)) {
      if (walkCrew(r, dt)) advance();
    } else {
      if (r.phase === "confront") {
        r.lead.heading = turnToward(r.lead.heading, r.plan.stand.heading, TURN_RATE_RAD_S * dt);
      } else if (r.phase === "fall") {
        const u = R.clamp01(r.t / R.PHASE_SECONDS.fall);
        r.body.pitch = R.fallPitch(u);
        const slide = R.fallSlide(u);
        r.body.x = r.plan.body.x - Math.sin(r.plan.body.heading) * slide;
        r.body.z = r.plan.body.z - Math.cos(r.plan.body.heading) * slide;
        r.body.y = R.fallLift(u);
        r.restAt = [r.body.x, r.body.z];
      } else if (r.phase === "load") {
        // Rolled onto the stretcher and lined up along it.
        const u = R.loadBlend(r.t);
        const down = R.fallLift(1);
        r.body.pitch = -Math.PI / 2;
        r.body.heading = turnToward(r.body.heading, r.crew.heading, TURN_RATE_RAD_S * dt);
        r.body.x = r.restAt[0] + (r.crew.x - r.restAt[0]) * u;
        r.body.z = r.restAt[1] + (r.crew.z - r.restAt[1]) * u;
        r.body.y = down + (R.STRETCHER_CARRY_Y - down) * u;
      } else if (r.phase === "carry") {
        walkCrew(r, dt);
      } else if (r.phase === "depart") {
        walkCrew(r, dt);
      }

      // Riding the stretcher: everything between the load and the release.
      const flying = r.phase === "toss" && R.tossProgress(r.t).released;
      if ((r.phase === "carry" || r.phase === "travel" || r.phase === "toss") && !flying) {
        r.body.x = r.crew.x;
        r.body.z = r.crew.z;
        r.body.heading = r.crew.heading;
        r.body.pitch = -Math.PI / 2;
        r.body.y = R.STRETCHER_CARRY_Y + sway.lift;
      }
      if (flying) {
        const toss = R.tossProgress(r.t);
        if (r.launch === null) {
          r.launch = [r.body.x, groundY(map, r.body.x, r.body.z) + r.body.y, r.body.z];
        }
        const [px, py, pz] = R.tossArc(toss.fly, r.launch, r.plan.portal, R.TOSS_APEX_M);
        r.body.x = px;
        r.body.z = pz;
        r.body.y = py - groundY(map, px, pz);
      }

      if (r.t >= R.PHASE_SECONDS[r.phase]) advance();
    }

    // The `arrive` leg rides the stretcher too, and it is a distance phase.
    if (r.phase === "arrive") {
      r.body.x = r.crew.x;
      r.body.z = r.crew.z;
      r.body.heading = r.crew.heading;
      r.body.pitch = -Math.PI / 2;
      r.body.y = R.STRETCHER_CARRY_Y + sway.lift;
    }

    // ----- the two figures the roster owns ---------------------------------
    const aim = R.aimBlend(r.phase, r.t);
    const recoil = R.recoilM(r.phase, r.t);
    if (ceremony.executionerId) {
      // Once the rifle is down the lead goes back to its own life; it has no
      // business standing over the body for the rest of the ceremony.
      const stillArmed = r.phase === "march" || R.riflePhase(r.phase);
      if (!stillArmed && !r.released) {
        r.released = true;
        releaseRetirementPose(ceremony.executionerId);
      } else if (!r.released) {
        const pose = poseAt(
          r.lead.x - Math.sin(r.lead.heading) * recoil,
          groundY(map, r.lead.x, r.lead.z),
          r.lead.z - Math.cos(r.lead.heading) * recoil,
          r.lead.heading,
        );
        pose.mode = r.phase === "march" ? "walk" : aim > 0.05 ? "aim" : "rest";
        pose.speed = r.phase === "march" ? MARCH_MPS : 0;
        pose.aim = aim;
        pose.flash = R.muzzleFlash(r.phase, r.t);
        setRetirementPose(ceremony.executionerId, pose);
      }
    }

    const bodyPose = poseAt(r.body.x, groundY(map, r.body.x, r.body.z) + r.body.y, r.body.z, r.body.heading);
    bodyPose.pitch = r.body.pitch;
    bodyPose.mode = r.phase === "march" || r.phase === "confront" ? "rest" : "sleep";
    if (r.phase === "toss") {
      const toss = R.tossProgress(r.t);
      // Tumbling on the way in, then swallowed by the dark.
      if (toss.released) bodyPose.pitch = -Math.PI / 2 - toss.fly * 2.2;
      bodyPose.hidden = R.tossFade(toss.fly) < 0.5;
    } else if (r.phase === "depart" || r.phase === "done") {
      bodyPose.hidden = true;
    }
    setRetirementPose(ceremony.agentId, bodyPose);

    // ----- the props -------------------------------------------------------
    const crewOut =
      r.phase === "bearers" ||
      r.phase === "load" ||
      r.phase === "carry" ||
      r.phase === "travel" ||
      r.phase === "arrive" ||
      r.phase === "toss" ||
      r.phase === "depart";
    const moving = r.phase === "bearers" || r.phase === "carry" || r.phase === "arrive" || r.phase === "depart";

    if (stretcher.current) {
      stretcher.current.visible = crewOut;
      if (crewOut) {
        const tilt = r.phase === "toss" ? R.tossProgress(r.t).wind * 0.55 : 0;
        stretcher.current.position.set(
          r.crew.x,
          groundY(map, r.crew.x, r.crew.z) + R.STRETCHER_CARRY_Y + sway.lift,
          r.crew.z,
        );
        stretcher.current.rotation.set(-tilt, r.crew.heading, sway.roll);
      }
    }

    const slots = R.bearerSlots([r.crew.x, r.crew.z], r.crew.heading);
    const bearers = [bearerA, bearerB];
    for (let i = 0; i < bearers.length; i++) {
      const g = bearers[i].current;
      if (!g) continue;
      g.visible = crewOut;
      if (!g.visible) continue;
      const [bx, bz] = slots[i];
      g.position.set(bx, groundY(map, bx, bz), bz);
      g.rotation.y = r.crew.heading;
    }
    animA.current.mode = moving ? "carry" : "rest";
    animA.current.speed = moving ? R.BEARER_MPS : 0;
    animB.current.mode = animA.current.mode;
    animB.current.speed = animA.current.speed;

    if (stain.current) {
      const marked = R.PHASE_ORDER.indexOf(r.phase) >= R.PHASE_ORDER.indexOf("fall");
      stain.current.visible = marked;
      if (marked) {
        const grow = r.phase === "fall" ? R.ease(r.t / R.PHASE_SECONDS.fall) : 1;
        // Under the head, which lies behind the feet the body pivoted on.
        const hx = r.restAt[0] - Math.sin(r.plan.body.heading) * HEAD_OFFSET_M;
        const hz = r.restAt[1] - Math.cos(r.plan.body.heading) * HEAD_OFFSET_M;
        stain.current.position.set(hx, groundY(map, hx, hz) + 0.03, hz);
        stain.current.scale.set(grow * STAIN_RADIUS_M, 1, grow * STAIN_RADIUS_M * 1.3);
      }
    }

    // ----- the camera ------------------------------------------------------
    const focus = focusPoint(r);
    if (
      !useCameraStore.getState().dragging &&
      Math.hypot(focus[0] - r.camera[0], focus[1] - r.camera[1]) > CAMERA_DEADBAND_M
    ) {
      r.camera = focus;
      useCameraStore.getState().jumpTo(focus[0], focus[1]);
    }

    if (r.phase === "done") {
      // The body is in the mine. Hold the figure off the island until the
      // roster refetch drops the row, then commit the deletion.
      buryRetired(ceremony.agentId);
      if (ceremony.executionerId && !r.released) releaseRetirementPose(ceremony.executionerId);
      run.current = null;
      useRetireStore.getState().finish();
    }
  });

  return (
    <group>
      <mesh ref={stain} geometry={kit.g.disc} material={kit.m.lit(PAL.stain)} visible={false} />
      {/* The stretcher yaws with the crew and tilts about its OWN long axis on
          the throw, so the pitch has to be applied before the yaw: YXZ. */}
      <group ref={stretcher} rotation-order="YXZ" visible={false}>
        {[-0.42, 0.42].map((px) => (
          <mesh
            key={px}
            geometry={kit.g.box}
            material={kit.m.lit(PAL.stretcherPole)}
            position={[px, 0, 0]}
            scale={[0.1, 0.1, 3.7]}
          />
        ))}
        <mesh
          geometry={kit.g.slab}
          material={kit.m.lit(PAL.stretcherCloth)}
          position={[0, 0.03, 0]}
          scale={[0.86, 0.05, 3.0]}
        />
      </group>
      <group ref={bearerA} visible={false}>
        <BoxWalkerFigure palette={bearerPalette} anim={animA} paused={false} selected={false} />
      </group>
      <group ref={bearerB} visible={false}>
        <BoxWalkerFigure palette={bearerPalette} anim={animB} paused={false} selected={false} />
      </group>
    </group>
  );
}

/** Move the stretcher crew one step along its current route; true on arrival. */
function walkCrew(r: Run, dt: number): boolean {
  const step = stepAlong(r.crew.x, r.crew.z, r.crewPath, r.crewIdx, R.BEARER_MPS, dt);
  r.crew.x = step.x;
  r.crew.z = step.z;
  r.crewIdx = step.index;
  if (isMoving(step.vx, step.vz)) {
    r.crew.heading = turnToward(r.crew.heading, headingFor(step.vx, step.vz), TURN_RATE_RAD_S * dt);
  }
  return step.arrived;
}

/** Per-phase setup the moment a beat begins. */
function onEnterPhase(r: Run): void {
  switch (r.phase) {
    case "bearers": {
      // The crew comes on down the road it will carry the body back along,
      // and walks in to wherever the body actually came to rest.
      const rest: [number, number] = [r.restAt[0], r.restAt[1]];
      const path = r.plan.bearerPath.slice();
      if (path.length > 0) path[path.length - 1] = rest;
      const entry = path[0] ?? rest;
      r.crew.x = entry[0];
      r.crew.z = entry[1];
      r.crew.heading = R.facingHeading(entry, rest);
      const tail = path.slice(1);
      r.crewPath = tail.length > 0 ? tail : [rest];
      r.crewIdx = 0;
      break;
    }
    case "carry": {
      // Out to the mine, starting from the body's own resting place.
      const path = r.plan.carryPath.slice();
      path[0] = [r.restAt[0], r.restAt[1]];
      r.crewPath = path;
      r.crewIdx = path.length > 1 ? 1 : 0;
      break;
    }
    case "travel": {
      // The cut: forward along their own route to the last stretch, so the
      // walk across the island is skipped instead of watched.
      const ahead = R.pointBackFrom(r.crewPath, R.MINE_APPROACH_M);
      if (ahead.index <= r.crewIdx) break; // already closer than the cut point
      r.crew.x = ahead.x;
      r.crew.z = ahead.z;
      r.crew.heading = ahead.heading;
      r.crewIdx = ahead.index;
      break;
    }
    case "depart": {
      // Into the tunnel, and gone.
      r.crewPath = [[r.plan.portal[0], r.plan.portal[2]]];
      r.crewIdx = 0;
      break;
    }
    default:
      break;
  }
}

/** What the camera should be looking at for this beat. */
function focusPoint(r: Run): [number, number] {
  switch (r.phase) {
    case "march":
      // Hold the condemned and let the lead walk into frame; the two can still
      // be far enough apart that their midpoint shows neither of them.
      return [r.body.x, r.body.z];
    case "confront":
    case "raise":
    case "hold":
    case "shot":
      return [(r.lead.x + r.body.x) / 2, (r.lead.z + r.body.z) / 2];
    case "fall":
    case "settle":
      return [r.body.x, r.body.z];
    case "bearers":
      return [(r.crew.x + r.restAt[0]) / 2, (r.crew.z + r.restAt[1]) / 2];
    case "toss":
    case "depart":
      return [(r.crew.x + r.plan.portal[0]) / 2, (r.crew.z + r.plan.portal[2]) / 2];
    default:
      return [r.crew.x, r.crew.z];
  }
}

export default RetirementScene;

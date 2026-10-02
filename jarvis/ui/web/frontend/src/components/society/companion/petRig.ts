/**
 * Procedural motion of the authored 3D pets (`petCompanions.ts`). The GLBs
 * carry no animation clips; every animated part hangs under a named pivot
 * (`miso_Leg_FL`, `ember_Wing_L`, …) and this module says how each pivot
 * moves, as an offset from its authored pose.
 *
 * Gait phase advances with ground actually covered (`strideM`), so a cat's
 * paws or a snail's ripple never slide over the floor; standing still settles
 * every gait back to rest. Pure and allocation-free per step when the caller
 * reuses `out`.
 */
import type { CompanionPet } from "./petCompanions";

/** Offset from a part's authored transform: radians, metres and scale factors. */
export interface PartMotion {
  rx: number;
  ry: number;
  rz: number;
  dy: number;
  sx: number;
  sy: number;
  sz: number;
}

export type PetMood = "idle" | "work" | "talk" | "sleep";

export interface RigState {
  /** Gait phase in radians; one full cycle covers `strideM` of ground. */
  phase: number;
  /** 0 standing .. 1 walking, smoothed so starts and stops ease. */
  stride: number;
  /** 0 open .. 1 shut. */
  blink: number;
  t: number;
}

export interface RigInput {
  dt: number;
  /** Ground speed, m/s. */
  speed: number;
  mood: PetMood;
  reduced: boolean;
}

/** Walking pace at which a gait reaches full swing, m/s. */
export const FULL_STRIDE_SPEED = 0.9;
const BLINK_PERIOD_S = 4.3;
const BLINK_S = 0.14;
const TAU = Math.PI * 2;

export function createRigState(): RigState {
  return { phase: 0, stride: 0, blink: 0, t: 0 };
}

export function restMotion(): PartMotion {
  return { rx: 0, ry: 0, rz: 0, dy: 0, sx: 1, sy: 1, sz: 1 };
}

function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value;
}

/** Advances phase, stride and blink. Reduced motion holds everything at rest. */
export function stepRig(state: RigState, pet: CompanionPet, input: RigInput): void {
  const dt = Number.isFinite(input.dt) ? clamp(input.dt, 0, 0.1) : 0;
  const speed = Number.isFinite(input.speed) ? Math.max(0, input.speed) : 0;
  if (input.reduced) {
    state.stride = 0;
    state.blink = input.mood === "sleep" ? 1 : 0;
    return;
  }
  state.t += dt;
  const target = clamp(speed / FULL_STRIDE_SPEED, 0, 1);
  state.stride += (target - state.stride) * (1 - Math.exp(-8 * dt));
  if (speed > 0.02) state.phase = (state.phase + (speed * dt / Math.max(0.05, pet.strideM)) * TAU) % TAU;
  else if (state.stride < 0.05) {
    // Standing: let the gait run out to a neutral pose instead of freezing mid-step.
    const settle = state.phase % Math.PI;
    if (settle > 0.02 && settle < Math.PI - 0.02) state.phase = (state.phase + dt * 3) % TAU;
  }
  if (input.mood === "sleep") state.blink = 1;
  else state.blink = state.t % BLINK_PERIOD_S < BLINK_S ? 1 : 0;
}

type Parts = Map<string, PartMotion>;

function part(out: Parts, name: string): PartMotion {
  let motion = out.get(name);
  if (!motion) { motion = restMotion(); out.set(name, motion); }
  else { motion.rx = motion.ry = motion.rz = motion.dy = 0; motion.sx = motion.sy = motion.sz = 1; }
  return motion;
}

function eyes(out: Parts, id: string, blink: number): void {
  for (const side of ["L", "R"]) part(out, `${id}_Eye_${side}`).sy = 1 - 0.9 * blink;
}

/**
 * The pose of every animated part for this frame. Parts that are not named
 * stay at their authored transform.
 */
export function rigPose(pet: CompanionPet, state: RigState, mood: PetMood, out: Parts = new Map()): Parts {
  const { phase: p, stride: s, t } = state;
  const talk = mood === "talk" ? 1 : 0;
  const sleep = mood === "sleep" ? 1 : 0;
  const work = mood === "work" ? 1 : 0;
  switch (pet.id) {
    case "miso": {
      // Trot: diagonal pairs swing together.
      const swing = 0.6 * s;
      part(out, "miso_Leg_FL").rx = swing * Math.sin(p);
      part(out, "miso_Leg_BR").rx = swing * Math.sin(p);
      part(out, "miso_Leg_FR").rx = swing * Math.sin(p + Math.PI);
      part(out, "miso_Leg_BL").rx = swing * Math.sin(p + Math.PI);
      const body = part(out, "miso_Body");
      body.dy = 0.01 * s * Math.abs(Math.cos(p)) - 0.03 * sleep;
      body.sy = 1 + 0.02 * Math.sin(t * 1.6) * (1 - s);
      const head = part(out, "miso_Head");
      head.rx = 0.06 * s * Math.sin(2 * p) + 0.12 * sleep;
      head.ry = 0.3 * Math.sin(t * 0.45) * (1 - s) * (1 - sleep);
      head.rz = 0.16 * talk * Math.sin(t * 3.2);
      head.dy = -0.04 * sleep;
      const tail = part(out, "miso_Tail");
      tail.rz = 0.32 * Math.sin(t * 2.1) * (1 - 0.5 * s) + 0.15 * s * Math.sin(p);
      tail.rx = 0.18 * s - 0.25 * sleep;
      // A quick ear flick every few seconds.
      const flick = (t % 5.3) < 0.18 ? Math.sin(((t % 5.3) / 0.18) * Math.PI) : 0;
      part(out, "miso_Ear_L").rz = 0.35 * flick;
      part(out, "miso_Ear_R").rz = -0.12 * flick;
      eyes(out, "miso", state.blink);
      break;
    }
    case "ember": {
      // Wings beat faster in travel; a lazy beat while hovering.
      const beat = Math.sin(t * TAU * (2.2 + 1.6 * s + 1.2 * talk));
      const flap = sleep ? 0.25 : 0.15 + 0.55 * beat;
      part(out, "ember_Wing_R").rz = flap;
      part(out, "ember_Wing_L").rz = -flap;
      const dangle = 0.2 + 0.25 * s + 0.08 * Math.sin(t * TAU * 1.1);
      part(out, "ember_Leg_L").rx = dangle;
      part(out, "ember_Leg_R").rx = dangle;
      part(out, "ember_Arm_L").rx = -0.25 + 0.12 * Math.sin(t * 3 + 1) - 0.5 * talk * Math.abs(Math.sin(t * 5));
      part(out, "ember_Arm_R").rx = -0.25 + 0.12 * Math.sin(t * 3);
      const tail = part(out, "ember_Tail");
      tail.ry = 0.35 * Math.sin(t * 1.8);
      tail.rx = -0.25 * s;
      const head = part(out, "ember_Head");
      head.rx = 0.08 * Math.sin(t * 1.3) + 0.1 * sleep;
      head.rz = 0.12 * talk * Math.sin(t * 3);
      eyes(out, "ember", state.blink);
      break;
    }
    case "bolt": {
      const step = Math.sin(p);
      const left = part(out, "bolt_Foot_L"), right = part(out, "bolt_Foot_R");
      left.rx = 0.5 * s * step; left.dy = 0.03 * s * Math.max(0, step);
      right.rx = -0.5 * s * step; right.dy = 0.03 * s * Math.max(0, -step);
      const body = part(out, "bolt_Body");
      body.rz = 0.1 * s * step + 0.05 * talk * Math.sin(t * 6);
      body.dy = 0.012 * s * Math.abs(step);
      part(out, "bolt_Arm_L").rx = -0.6 * s * step;
      const wave = part(out, "bolt_Arm_R");
      wave.rx = 0.6 * s * step;
      wave.rz = talk * (0.9 + 0.35 * Math.sin(t * 9));
      // Working: the charge bars fill one after another, then start over.
      const level = work ? Math.floor((t * 2.5) % 4) : 3;
      for (let i = 1; i <= 3; i++) {
        const bar = part(out, `bolt_Bar_${i}`);
        bar.sx = i <= level ? 1 : 0.02;
      }
      eyes(out, "bolt", state.blink);
      break;
    }
    case "brew": {
      const air = Math.max(0, Math.sin(p));
      const ground = Math.max(0, -Math.sin(p));
      const body = part(out, "brew_Body");
      body.dy = 0.06 * s * air;
      body.sy = 1 - 0.09 * s * ground + 0.015 * Math.sin(t * 1.7) * (1 - s);
      body.sx = body.sz = 1 + 0.045 * s * ground;
      body.rz = 0.05 * s * Math.sin(p * 0.5);
      // The lid rattles on every landing, and while Brew talks.
      const lid = part(out, "brew_Lid");
      lid.dy = 0.012 * s * ground + 0.012 * talk * Math.abs(Math.sin(t * 17));
      lid.rz = 0.1 * talk * Math.sin(t * 13) + 0.06 * s * Math.sin(p * 2);
      eyes(out, "brew", state.blink);
      break;
    }
    case "mochi": {
      const lift = Math.sin(p);
      const body = part(out, "mochi_Body");
      const jiggle = (0.035 + 0.04 * talk) * Math.sin(t * (2.4 + 6 * talk));
      body.sy = 1 + 0.16 * s * lift + jiggle * (1 - s) - 0.08 * sleep;
      const across = 1 / Math.sqrt(Math.max(0.5, body.sy));
      body.sx = across; body.sz = across;
      body.dy = 0.05 * s * Math.max(0, lift);
      eyes(out, "mochi", state.blink);
      break;
    }
    case "shelly": {
      // The foot ripples front to back; the shell rides the wave.
      const body = part(out, "shelly_Body");
      body.sz = 1 + 0.07 * s * Math.sin(p);
      body.sx = 1 - 0.025 * s * Math.sin(p);
      const shell = part(out, "shelly_Shell");
      shell.dy = 0.006 * s * Math.sin(p + 1) - 0.02 * sleep;
      shell.rz = 0.04 * s * Math.sin(p) + 0.05 * talk * Math.sin(t * 4);
      const head = part(out, "shelly_Head");
      head.ry = 0.25 * Math.sin(t * 0.5) * (1 - s);
      head.rx = 0.1 * talk * Math.sin(t * 5) + 0.3 * sleep;
      for (const [side, offset] of [["L", 0], ["R", 1.7]] as const) {
        const stalk = part(out, `shelly_Stalk_${side}`);
        stalk.rx = 0.16 * Math.sin(t * 1.3 + offset) - 0.15 * s;
        stalk.rz = 0.14 * Math.sin(t * 1.05 + offset * 2);
        stalk.sy = 1 - 0.55 * sleep;
      }
      eyes(out, "shelly", state.blink);
      break;
    }
    default:
      break;
  }
  return out;
}

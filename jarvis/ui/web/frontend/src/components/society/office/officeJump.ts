/**
 * The person's jump (Space): a short, snappy hop computed per frame, so it is
 * smooth at any frame rate. A jump taken while sprinting carries extra speed
 * for as long as the character is in the air, so hopping along at a sprint is
 * faster than sprinting on the ground. The boost eases in and out instead of
 * snapping, and a press just before landing is kept and fires on touchdown,
 * so chained hops never feel laggy.
 */

/** Take-off speed (m/s) and gravity (m/s²): about a 0.5 m hop, 0.5 s in the air. */
export const JUMP_VELOCITY = 4.2;
export const JUMP_GRAVITY = 16;
/** Ground speed multiplier while airborne on a sprint jump. */
export const SPRINT_JUMP_BOOST = 1.3;
/** How fast the speed multiplier follows its target (1/s); higher is snappier. */
const BOOST_EASE = 9;
/** A press this long before landing still jumps on touchdown (s). */
export const JUMP_BUFFER_S = 0.14;
/** Take-off stretch and landing squash length (s). */
const STRETCH_S = 0.16;
const SQUASH_S = 0.18;

export interface JumpState {
  /** Height of the feet above the floor (m). */
  y: number;
  /** Vertical speed (m/s). */
  vy: number;
  airborne: boolean;
  /** This hop started at a sprint (and keeps its boost while the sprint is held). */
  sprintJump: boolean;
  /** Current ground-speed multiplier, eased toward its target. */
  speedMul: number;
  /** Seconds left in which a buffered press still counts. */
  buffered: number;
  /** Seconds since take-off / since landing (drive the stretch and squash). */
  sinceTakeoff: number;
  sinceLanding: number;
}

export function newJump(): JumpState {
  return { y: 0, vy: 0, airborne: false, sprintJump: false, speedMul: 1, buffered: 0, sinceTakeoff: Infinity, sinceLanding: Infinity };
}

/** Space pressed: jump now if on the floor, else remember the press for a moment. */
export function pressJump(s: JumpState): void {
  s.buffered = JUMP_BUFFER_S;
}

/**
 * Advance one frame. `wantJump` is true while Space is held (holding keeps
 * hopping); `running` whether the character is sprinting and moving right now.
 * Returns true on the frame the character leaves the floor.
 */
export function stepJump(s: JumpState, dt: number, wantJump: boolean, running: boolean): boolean {
  let tookOff = false;
  s.buffered = Math.max(0, s.buffered - dt);
  if (!s.airborne && (s.buffered > 0 || wantJump)) {
    s.airborne = true;
    s.vy = JUMP_VELOCITY;
    s.sprintJump = running;
    s.buffered = 0;
    s.sinceTakeoff = 0;
    tookOff = true;
  }
  if (s.airborne) {
    // Semi-implicit Euler: speed first, then position; exact enough for a hop.
    s.vy -= JUMP_GRAVITY * dt;
    s.y += s.vy * dt;
    if (s.y <= 0) {
      s.y = 0;
      s.vy = 0;
      s.airborne = false;
      s.sinceLanding = 0;
    }
  }
  s.sinceTakeoff += dt;
  s.sinceLanding += dt;
  // The boost holds while airborne on a sprint jump that is still a sprint.
  const target = s.airborne && s.sprintJump && running ? SPRINT_JUMP_BOOST : 1;
  s.speedMul += (target - s.speedMul) * (1 - Math.exp(-BOOST_EASE * dt));
  if (Math.abs(target - s.speedMul) < 1e-3) s.speedMul = target;
  return tookOff;
}

/**
 * The figure's squash-and-stretch as [widthScale, heightScale]: a quick
 * stretch on take-off, a soft squash on landing, 1 in between. Volume stays
 * roughly constant (width shrinks as height grows).
 */
export function jumpSquash(s: JumpState): [number, number] {
  const pulse = (t: number, len: number) => (t < len ? Math.sin((t / len) * Math.PI) : 0);
  const stretch = pulse(s.sinceTakeoff, STRETCH_S) * 0.1;
  const squash = pulse(s.sinceLanding, SQUASH_S) * 0.12;
  const h = 1 + stretch - squash;
  return [1 / Math.sqrt(h), h];
}

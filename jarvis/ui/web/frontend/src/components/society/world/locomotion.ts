/** Fixed-step, deterministic actor simulation. No renderer or backend side effects. */
import { FIXED_STEP_S, type Navigation } from "./navigation";
import { avoidsAgents, type Disc, type Point } from "./spatial";
import { headingFor, NOMINAL_WALK_MPS, REVERSAL_RAD, shortestArc, stepAlong, TURN_RATE_RAD_S, turnToward } from "./walkerKinematics";
import { mulberry32, nextBeat, seedFromString } from "./wander";
import type { RoutePlanner } from "./routePlanner";

export type MotionMode = "rest" | "walk" | "work" | "sleep";
export interface Actor extends Disc {
  heading: number;
  mode: MotionMode;
  state: "idle" | "working" | "paused";
  path: Array<[number, number]>;
  index: number;
  target: [number, number] | null;
  desired: [number, number] | null;
  arrived: boolean;
  speed: number;
  wait: number;
  failures: number;
  exiting: boolean;
  hidden: boolean;
  planning: boolean;
  routeToken: number;
  rng: () => number;
}

export class MotionWorld {
  readonly actors = new Map<string, Actor>();
  planner: RoutePlanner | null = null;
  private accumulator = 0;
  private pathRequests: string[] = [];
  constructor(public nav: Navigation, private wanderTarget: (rng: () => number) => Point) {}

  add(id: string, start: Point, radius: number, state: Actor["state"], desired: Point | null): Actor | null {
    if (this.actors.has(id)) return this.actors.get(id)!;
    const p = this.nav.nearest(start, radius, this.actors.values(), id, 24);
    if (!p) return null;
    const actor: Actor = { id, x: p[0], z: p[1], radius, heading: 0, mode: state === "paused" ? "sleep" : "rest", state,
      path: [], index: 0, target: null, desired: null, arrived: false, speed: 0, wait: 1, failures: 0, exiting: false, hidden: false,
      planning: false, routeToken: 0, rng: mulberry32(seedFromString(id)) };
    this.actors.set(id, actor);
    this.target(id, desired);
    return actor;
  }
  remove(id: string): void { this.actors.delete(id); this.pathRequests = this.pathRequests.filter(v => v !== id); }
  target(id: string, target: Point | null): void {
    const a = this.actors.get(id); if (!a) return;
    if (a.desired === null && target === null) return;
    if (a.desired && target && Math.hypot(a.desired[0] - target[0], a.desired[1] - target[1]) < .01) return;
    a.routeToken++; a.planning = false;
    a.desired = target ? [...target] : null; a.target = null; a.arrived = false; a.path = []; a.index = 0; a.wait = .1; a.speed = 0;
    a.mode = a.state === "paused" ? "sleep" : "rest";
    if (target) this.request(id);
  }
  request(id: string): void { if (!this.pathRequests.includes(id)) this.pathRequests.push(id); }
  private plan(a: Actor): void {
    if (a.planning) return;
    const requested = a.desired ?? this.wanderTarget(a.rng);
    const reserved: Disc[] = [...this.actors.values()].filter(o => o.id !== a.id).map(o => ({
      id: o.id, x: o.target?.[0] ?? o.x, z: o.target?.[1] ?? o.z, radius: o.radius,
    }));
    const target = this.nav.nearest(requested, a.radius, reserved, a.id, a.desired ? 6 : 12);
    if (!target) { this.failed(a); return; }
    if (Math.hypot(a.x - target[0], a.z - target[1]) < .06) { a.target = target; this.arrive(a); return; }
    const token = ++a.routeToken, nav = this.nav, radius = a.radius;
    a.target = target; a.planning = true;
    const apply = (route: Array<[number, number]> | null) => {
      if (this.actors.get(a.id) !== a || token !== a.routeToken || this.nav !== nav) return;
      a.planning = false;
      if (radius !== a.radius) { this.request(a.id); return; }
      if (!route) { this.failed(a); return; }
      a.path = route; a.index = 0; a.mode = "walk"; a.arrived = false; a.wait = 0;
    };
    const occupied = a.failures ? [...this.actors.values()] : [];
    if (this.planner) {
      void this.planner([a.x, a.z], target, radius, occupied, a.id).then(apply).catch(error => {
        console.warn("World route request failed", error); apply(null);
      });
    } else apply(nav.route([a.x, a.z], target, radius, occupied, a.id));
  }
  private failed(a: Actor): void {
    a.path = []; a.speed = 0; a.arrived = false; a.mode = a.state === "paused" ? "sleep" : "rest";
    a.failures++; a.wait = Math.min(8, 1 + a.failures) + a.rng();
  }
  private arrive(a: Actor): void {
    a.arrived = true; a.speed = 0; a.path = []; a.failures = 0;
    a.mode = a.state === "paused" ? "sleep" : a.state === "working" && a.desired ? "work" : "rest";
    a.wait = 2 + a.rng() * 4;
  }
  replaceNavigation(nav: Navigation): void {
    this.nav = nav;
    for (const a of this.actors.values()) { a.routeToken++; a.planning = false; a.path = []; a.arrived = false; a.mode = "rest"; a.speed = 0; this.request(a.id); }
  }
  advance(dt: number): number {
    if (!Number.isFinite(dt) || dt <= 0) return 0;
    this.accumulator = Math.min(this.accumulator + dt, FIXED_STEP_S * 5);
    let steps = 0;
    while (this.accumulator + 1e-9 >= FIXED_STEP_S) { this.tick(); this.accumulator -= FIXED_STEP_S; steps++; }
    return steps;
  }
  private tick(): void {
    // Spread route requests across ticks so a roster refresh never launches
    // thirty synchronous searches on one animation frame.
    const id = this.pathRequests.shift(), request = id ? this.actors.get(id) : null;
    if (request && !request.hidden && request.state !== "paused") this.plan(request);
    const bodies = [...this.actors.values()].filter(a => !a.hidden);
    for (const a of bodies) {
      if (a.state === "paused" && !a.exiting) { a.mode = "sleep"; a.speed = 0; continue; }
      if (a.planning) { a.speed = 0; continue; }
      if (a.mode === "walk" && a.path.length) {
        const next = a.path[a.index];
        const heading = headingFor(next[0] - a.x, next[1] - a.z);
        const reversal = Math.abs(shortestArc(a.heading, heading)) > REVERSAL_RAD;
        a.heading = turnToward(a.heading, heading, TURN_RATE_RAD_S * FIXED_STEP_S);
        if (reversal) { a.speed = 0; continue; }
        const r = stepAlong(a.x, a.z, a.path, a.index, NOMINAL_WALK_MPS, FIXED_STEP_S);
        const from: Point = [a.x, a.z], to: Point = [r.x, r.z];
        if (!this.nav.segment(from, to, a.radius) || !avoidsAgents(a.id, from, to, a.radius, bodies)) {
          a.speed = 0; a.wait += FIXED_STEP_S;
          if (a.wait > .75 + a.rng() * .2) { a.failures++; a.mode = "rest"; a.path = []; a.wait = .3 + a.rng(); }
          continue;
        }
        a.x = r.x; a.z = r.z; a.index = r.index; a.wait = 0; a.speed = Math.hypot(r.vx, r.vz);
        if (r.arrived) this.arrive(a);
      } else {
        a.speed = 0;
        if (a.arrived && a.desired) { a.mode = a.state === "working" ? "work" : "rest"; continue; }
        a.wait -= FIXED_STEP_S;
        if (a.wait > 0) continue;
        if (a.desired) { this.request(a.id); a.wait = 1; }
        else {
          const beat = nextBeat(a.rng);
          if (beat.kind === "rest") a.wait = beat.seconds;
          else { this.request(a.id); a.wait = 1; }
        }
      }
    }
  }
}

/** Scene-local owner exposed to placement and the short exit presentation. */
let active: MotionWorld | null = null;
export function setMotionWorld(world: MotionWorld | null): void { active = world; }
export function getMotionWorld(): MotionWorld | null { return active; }

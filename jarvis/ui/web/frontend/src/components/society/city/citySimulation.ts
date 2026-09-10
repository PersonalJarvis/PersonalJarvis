import {
  distance,
  EDGES,
  mix,
  node,
  otherStop,
  route,
  routeSeconds,
  type Stop,
  type Vec3,
} from "./cityModel";

export const DWELL = 12;
export const RIDE = 16;
export const CAPACITY = 8;
export const CYCLE = 2 * (DWELL + RIDE);
export type JourneyStage =
  | "arrived"
  | "walking"
  | "waiting"
  | "boarding"
  | "riding"
  | "exiting"
  | "unreachable";
export interface TrainState {
  position: Vec3;
  stop: Stop | null;
  next: Stop;
  remaining: number;
  heading: number;
}
export function trainAt(time: number): TrainState {
  const t = ((time % CYCLE) + CYCLE) % CYCLE;
  if (t < DWELL)
    return {
      position: [-120, 12, 0],
      stop: "west",
      next: "east",
      remaining: DWELL - t,
      heading: Math.PI / 2,
    };
  if (t < DWELL + RIDE)
    return {
      position: [-120 + (240 * (t - DWELL)) / RIDE, 12, 0],
      stop: null,
      next: "east",
      remaining: DWELL + RIDE - t,
      heading: Math.PI / 2,
    };
  if (t < 2 * DWELL + RIDE)
    return {
      position: [120, 12, 0],
      stop: "east",
      next: "west",
      remaining: 2 * DWELL + RIDE - t,
      heading: Math.PI / 2,
    };
  return {
    position: [120 - (240 * (t - 2 * DWELL - RIDE)) / RIDE, 12, 0],
    stop: null,
    next: "west",
    remaining: CYCLE - t,
    heading: Math.PI / 2,
  };
}
export function nextDeparture(stop: Stop, time: number): number {
  const phase = stop === "west" ? DWELL : 2 * DWELL + RIDE;
  return Math.ceil((time + 8 - phase) / CYCLE) * CYCLE + phase;
}
export interface Citizen {
  id: string;
  position: Vec3;
  at: string;
  target: Stop;
  stage: JourneyStage;
  path: string[];
  edgeTime: number;
  speed: number;
  heading: number;
  seat: number | null;
  boardingAt: Stop | null;
  transition: number;
  transitionFrom: Vec3;
  queued: number;
  revision: number;
  lane: number;
}
export interface CitySimulation {
  time: number;
  citizens: Map<string, Citizen>;
  blocked: Set<string>;
}
export function createSimulation(): CitySimulation {
  return { time: 0, citizens: new Map(), blocked: new Set() };
}
export function safeDestination(sim: CitySimulation, citizen: Citizen): Stop {
  if (citizen.seat === null) return citizen.position[0] < 0 ? "west" : "east";
  const train = trainAt(sim.time);
  return train.stop && train.stop !== citizen.boardingAt ? train.stop : train.next;
}
export function addCitizen(
  sim: CitySimulation,
  id: string,
  stop: Stop,
): Citizen {
  const used = new Set([...sim.citizens.values()].map((c) => c.lane));
  let lane = 0;
  while (used.has(lane)) lane++;
  const position = [...node(`${stop}:work`)!.position] as Vec3;
  const c: Citizen = {
    id,
    position,
    at: `${stop}:work`,
    target: stop,
    stage: "arrived",
    path: [],
    edgeTime: 0,
    speed: 0,
    heading: 0,
    seat: null,
    boardingAt: null,
    transition: 0,
    transitionFrom: position,
    queued: sim.time,
    revision: -1,
    lane,
  };
  c.position = routePoint(c, c.at);
  sim.citizens.set(id, c);
  return c;
}
function routePoint(c: Citizen, id: string): Vec3 {
  const p = node(id)!.position;
  return [
    p[0] + ((c.lane % 5) - 2) * 0.7,
    p[1],
    p[2] + ((Math.floor(c.lane / 5) % 6) - 2.5) * 0.7,
  ];
}
export function setTarget(
  sim: CitySimulation,
  id: string,
  target: Stop,
  revision: number,
): void {
  const c = sim.citizens.get(id);
  if (!c || revision <= c.revision) return;
  c.revision = revision;
  c.target = target;
  if (["boarding", "riding", "exiting"].includes(c.stage)) return;
  // Finish the current physical edge before changing direction.
  if (c.stage === "walking" && c.path.length > 1) {
    c.path = c.path.slice(0, 2);
    return;
  }
  plan(sim, c);
}
/** Explicit preview walk, never used to override a live agent's activity. */
export function previewCrossing(sim: CitySimulation, id: string): boolean {
  const citizen = sim.citizens.get(id);
  if (
    !citizen ||
    !["arrived", "waiting", "unreachable"].includes(citizen.stage)
  )
    return false;
  const path = route(citizen.at, "bridge:north", sim.blocked);
  if (!path) return false;
  citizen.path = path;
  citizen.edgeTime = 0;
  citizen.stage = "walking";
  return true;
}
function plan(sim: CitySimulation, c: Citizen): void {
  c.edgeTime = 0;
  c.speed = 0;
  const direct = route(c.at, `${c.target}:work`, sim.blocked);
  const stops: Stop[] = ["west", "east"];
  let selected = direct;
  let best = routeSeconds(direct);
  for (const stop of stops.filter((s) => s !== c.target)) {
    const access = route(c.at, `${stop}:platform`, sim.blocked);
    const exit = route(`${c.target}:platform`, `${c.target}:work`, sim.blocked);
    const reach = sim.time + routeSeconds(access);
    const total =
      nextDeparture(stop, reach) - sim.time + RIDE + 8 + routeSeconds(exit);
    if (total < best) {
      best = total;
      selected = access;
    }
  }
  if (!selected) {
    c.stage = "unreachable";
    c.path = [];
    return;
  }
  c.path = selected;
  if (selected.length > 1) {
    c.stage = "walking";
    return;
  }
  if (c.at === `${c.target}:work`) c.stage = "arrived";
  else {
    c.stage = "waiting";
    c.queued = sim.time;
  }
}
function seatPosition(train: TrainState, seat: number): Vec3 {
  return [
    train.position[0] + ((seat % 4) - 1.5) * 1.6,
    12.3,
    0.5 + Math.floor(seat / 4) * 1.3,
  ];
}
function advance(sim: CitySimulation, dt: number): void {
  sim.time += dt;
  const train = trainAt(sim.time);
  for (const c of sim.citizens.values()) {
    if (c.stage === "walking") {
      const [from, to] = c.path;
      if (!to) {
        plan(sim, c);
        continue;
      }
      const edge = EDGES.find((e) => e.from === from && e.to === to)!;
      const a = routePoint(c, from),
        b = routePoint(c, to);
      c.edgeTime += dt;
      const duration = distance(a, b) / edge.speed;
      c.position = mix(a, b, Math.min(1, c.edgeTime / duration));
      c.speed = a[1] === b[1] ? edge.speed : 0;
      if (a[0] !== b[0] || a[2] !== b[2])
        c.heading = Math.atan2(b[0] - a[0], b[2] - a[2]);
      if (c.edgeTime >= duration) {
        c.at = to;
        c.path.shift();
        c.edgeTime = 0;
        if (c.path.length < 2) plan(sim, c);
      }
    } else if (c.stage === "boarding") {
      c.transition += dt;
      const seat = seatPosition(train, c.seat!);
      c.speed = distance(c.transitionFrom, seat) / 6;
      c.position = mix(c.transitionFrom, seat, Math.min(1, c.transition / 6));
      c.heading = Math.atan2(
        seat[0] - c.transitionFrom[0],
        seat[2] - c.transitionFrom[2],
      );
      if (c.transition >= 6) {
        c.stage = "riding";
        c.speed = 0;
      }
    } else if (c.stage === "riding") {
      c.position = seatPosition(train, c.seat!);
      c.speed = 0;
      if (train.stop && train.stop !== c.boardingAt) {
        c.stage = "exiting";
        c.transition = 0;
        c.transitionFrom = [...c.position];
      }
    } else if (c.stage === "exiting") {
      c.transition += dt;
      const stop = otherStop(c.boardingAt!);
      const platform = routePoint(c, `${stop}:platform`);
      c.speed = distance(c.transitionFrom, platform) / 6;
      c.position = mix(
        c.transitionFrom,
        platform,
        Math.min(1, c.transition / 6),
      );
      c.heading = Math.atan2(
        platform[0] - c.transitionFrom[0],
        platform[2] - c.transitionFrom[2],
      );
      if (c.transition >= 6) {
        c.at = `${stop}:platform`;
        c.seat = null;
        c.boardingAt = null;
        plan(sim, c);
      }
    }
  }
  const occupied = new Set(
    [...sim.citizens.values()]
      .filter((c) => c.seat !== null)
      .map((c) => c.seat),
  );
  if (train.stop && train.remaining >= 7) {
    for (const c of [...sim.citizens.values()]
      .filter((c) => c.stage === "waiting" && c.at === `${train.stop}:platform`)
      .sort((a, b) => a.queued - b.queued || a.id.localeCompare(b.id))) {
      const seat = Array.from({ length: CAPACITY }, (_, i) => i).find(
        (i) => !occupied.has(i),
      );
      if (seat === undefined) break;
      occupied.add(seat);
      c.seat = seat;
      c.stage = "boarding";
      c.boardingAt = train.stop;
      c.transition = 0;
      c.transitionFrom = [...c.position];
    }
  }
}
/** Fixed small steps retain physical boarding and route transitions during catch-up. */
export function stepSimulation(sim: CitySimulation, seconds: number): void {
  if (!Number.isFinite(seconds) || seconds <= 0) return;
  let left = seconds;
  while (left > 0.000001) {
    const dt = Math.min(0.1, left);
    advance(sim, dt);
    left -= dt;
  }
}
export function daylight(hour: number): number {
  return Math.max(0, Math.sin(((hour - 6) / 24) * Math.PI * 2));
}
export function advanceHour(hour: number, dt: number, paused: boolean): number {
  return paused ? hour : (hour + (dt * 24) / 1800) % 24;
}

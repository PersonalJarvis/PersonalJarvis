import {
  createRouteGraph, defaultPlacements, distance, district, mix, node,
  railHeading, railPoint, RAIL_POINTS, RAIL_SEGMENTS, route, routeSeconds,
  STOPS, transformPoint, PLOTS, validPlacement,
  type Placements, type RouteGraph, type Stop, type Vec3,
} from "./cityModel";

export const DWELL = 18;
export const CAPACITY = 24;
export const STARTUP_HOLD = 42;
const MAX_SPEED = 26;
const ACCELERATION = 3;
const STEP = 0.1;
const BOARD_SPEED = 1.8;
const CUMULATIVE = RAIL_POINTS.reduce<number[]>((lengths, p, i) => {
  lengths.push(i ? lengths[i - 1] + distance(RAIL_POINTS[i - 1], p) : 0);
  return lengths;
}, []);
export const LEG_LENGTHS = STOPS.map((_, i) => CUMULATIVE[(i + 1) * RAIL_SEGMENTS / STOPS.length] - CUMULATIVE[i * RAIL_SEGMENTS / STOPS.length]);
export const LEG_RIDES = LEG_LENGTHS.map((length) => length / MAX_SPEED + MAX_SPEED / ACCELERATION);
export const RIDE = LEG_RIDES[0];
export const CYCLE = LEG_RIDES.reduce((sum, duration) => sum + DWELL + duration, 0);
const PHASES = STOPS.map((_, i) => LEG_RIDES.slice(0, i).reduce((sum, duration) => sum + duration + DWELL, 0));
export type JourneyStage = "arrived" | "working" | "walking" | "waiting" | "boarding" | "riding" | "exiting" | "unreachable";
export interface TrainState {
  position: Vec3; stop: Stop | null; next: Stop; remaining: number; heading: number;
  doorsOpen: boolean; reservedBlock: string; speed: number;
}
function sampledProgress(length: number): number {
  let low = 0, high = CUMULATIVE.length - 1;
  while (low + 1 < high) {
    const mid = (low + high) >> 1;
    if (CUMULATIVE[mid] <= length) low = mid; else high = mid;
  }
  return (low + (length - CUMULATIVE[low]) / (CUMULATIVE[high] - CUMULATIVE[low])) / RAIL_SEGMENTS;
}
/** A single train holds one ring block at a time; smooth acceleration preserves passengers. */
export function trainAt(time: number): TrainState {
  const raw = Math.max(0, time) - STARTUP_HOLD;
  const t = raw < 0 ? 0 : raw % CYCLE;
  let leg = STOPS.length - 1;
  for (let i = 0; i < STOPS.length; i++) if (t < PHASES[i] + DWELL + LEG_RIDES[i] - 1e-9) { leg = i; break; }
  const stop = STOPS[leg], next = STOPS[(leg + 1) % STOPS.length];
  const phase = t - PHASES[leg];
  if (phase < DWELL) {
    const progress = leg / STOPS.length;
    const remaining = DWELL - phase + (raw < 0 ? -raw : 0);
    return { position: railPoint(progress), stop, next, remaining, heading: railHeading(progress), doorsOpen: remaining > 0.4, reservedBlock: `platform:${stop}`, speed: 0 };
  }
  const elapsed = phase - DWELL, duration = LEG_RIDES[leg], ramp = MAX_SPEED / ACCELERATION;
  let travelled: number, speed: number;
  if (elapsed < ramp) { travelled = ACCELERATION * elapsed * elapsed / 2; speed = ACCELERATION * elapsed; }
  else if (elapsed > duration - ramp) {
    const left = duration - elapsed;
    travelled = LEG_LENGTHS[leg] - ACCELERATION * left * left / 2; speed = ACCELERATION * left;
  } else { travelled = MAX_SPEED * (elapsed - ramp / 2); speed = MAX_SPEED; }
  const progress = sampledProgress(CUMULATIVE[leg * RAIL_SEGMENTS / STOPS.length] + Math.max(0, Math.min(LEG_LENGTHS[leg], travelled)));
  return { position: railPoint(progress), stop: null, next, remaining: duration - elapsed, heading: railHeading(progress), doorsOpen: false, reservedBlock: `track:${stop}:${next}`, speed };
}
export function nextDeparture(stop: Stop, time: number, boardingSeconds = 12): number {
  const first = STARTUP_HOLD + PHASES[STOPS.indexOf(stop)] + DWELL;
  return first + Math.max(0, Math.ceil((time + boardingSeconds + 0.5 - first) / CYCLE)) * CYCLE;
}
export function rideSeconds(from: Stop, to: Stop): number {
  let i = STOPS.indexOf(from), total = 0;
  while (STOPS[i] !== to) { total += LEG_RIDES[i]; i = (i + 1) % STOPS.length; if (STOPS[i] !== to) total += DWELL; }
  return total;
}
export interface Citizen {
  id: string; position: Vec3; at: string; target: Stop; stage: JourneyStage;
  path: string[]; edgeTime: number; speed: number; heading: number;
  seat: number | null; boardingAt: Stop | null; alightAt: Stop | null;
  transition: number; transitionFrom: Vec3; transitionPath: Vec3[];
  queued: number; revision: number; lane: number;
  taskActive: boolean; atWork: boolean; workIndex: number | null;
  waitingForWorkspace: boolean;
  previewTarget: string | null;
}
export interface CitySimulation {
  time: number; citizens: Map<string, Citizen>; blocked: Set<string>;
  graph: RouteGraph; placements: Placements; debt: number;
  reservedBlock: string;
  nextBoardAt: number;
  nextWorkCheck: number;
}
export function createSimulation(placements: Placements = defaultPlacements()): CitySimulation {
  return { time: 0, citizens: new Map(), blocked: new Set(), graph: createRouteGraph(placements), placements, debt: 0, reservedBlock: "platform:west", nextBoardAt: 0, nextWorkCheck: 0 };
}
export function safeDestination(sim: CitySimulation, citizen: Citizen): Stop {
  if (citizen.seat !== null) {
    const train = trainAt(sim.time);
    return citizen.stage === "exiting" && citizen.alightAt ? citizen.alightAt : canAlight(sim, citizen, train) ? train.stop! : train.next;
  }
  return STOPS.reduce((best, stop) => distance(citizen.position, node(`${stop}:plaza`, sim.graph)!.position) < distance(citizen.position, node(`${best}:plaza`, sim.graph)!.position) ? stop : best);
}
export function addCitizen(sim: CitySimulation, id: string, stop: Stop = "west"): Citizen {
  const existing = sim.citizens.get(id);
  if (existing) return existing;
  const used = new Set([...sim.citizens.values()].map((c) => c.lane));
  let lane = 0; while (used.has(lane)) lane++;
  const p = [...node(`${stop}:plaza`, sim.graph)!.position] as Vec3;
  const c: Citizen = { id, position: p, at: `${stop}:plaza`, target: stop, stage: "arrived", path: [], edgeTime: 0, speed: 0, heading: district(stop).buildingRotation, seat: null, boardingAt: null, alightAt: null, transition: 0, transitionFrom: p, transitionPath: [], queued: sim.time, revision: -1, lane, taskActive: false, atWork: false, workIndex: null, waitingForWorkspace: false, previewTarget: null };
  c.position = routePoint(sim, c, c.at); sim.citizens.set(id, c); return c;
}
function routePoint(sim: CitySimulation, c: Citizen, id: string): Vec3 {
  const n = node(id, sim.graph)!;
  if (id.endsWith(":foyer")) {
    const stop = id.split(":")[0] as Stop;
    return transformPoint(n.position, district(stop).buildingRotation, [((c.lane % 7) - 3) * 0.45, 0, Math.floor(c.lane / 7) % 5 * 0.45]);
  }
  if (n.layer === "interior" || id.endsWith(":entry") || id.includes("bridge") || id.startsWith("cross:")) return [...n.position];
  const stop = STOPS.find((s) => id.startsWith(`${s}:`));
  const offset: Vec3 = [((c.lane % 7) - 3) * 0.3, 0, Math.floor(c.lane / 7) % 5 * 0.3];
  return transformPoint(n.position, stop ? district(stop).stationYaw : 0, offset);
}
function assignWorkspace(sim: CitySimulation, c: Citizen): void {
  const reserved = new Set([...sim.citizens.values()].filter((other) => other.id !== c.id && other.target === c.target && other.workIndex !== null).map((other) => other.workIndex));
  if (c.workIndex !== null && !reserved.has(c.workIndex)) return;
  c.workIndex = Array.from({ length: 30 }, (_, i) => i).find((i) => !reserved.has(i)) ?? null;
}
export function setTaskActive(sim: CitySimulation, id: string, active: boolean): void {
  const c = sim.citizens.get(id);
  if (!c || c.taskActive === active) return;
  c.taskActive = active;
  if (c.atWork && ["arrived", "working"].includes(c.stage) && c.at.startsWith(`${c.target}:work:`)) { c.stage = active ? "working" : "arrived"; return; }
  if (!active && c.waitingForWorkspace && c.stage === "waiting") { plan(sim, c); return; }
  if (active && c.stage === "walking") c.path = c.path.slice(0, 2);
  if (active && c.stage === "arrived") plan(sim, c);
}
export function setTarget(sim: CitySimulation, id: string, target: Stop, revision: number): void {
  const c = sim.citizens.get(id);
  if (!c || !STOPS.includes(target) || revision <= c.revision) return;
  c.revision = revision;
  if (c.target === target && !c.previewTarget && !["arrived", "unreachable"].includes(c.stage)) return;
  if (c.target !== target) c.workIndex = null;
  c.target = target; c.previewTarget = null; c.atWork = false;
  if (c.stage === "boarding" || c.stage === "riding") {
    const train = trainAt(sim.time);
    // Retargeting is an intent change; disembark at the next physical opportunity.
    c.alightAt = canAlight(sim, c, train) ? train.stop! : train.next;
    return;
  }
  if (c.stage === "exiting") return;
  if (c.stage === "walking" && c.path.length > 1) { c.path = c.path.slice(0, 2); return; }
  plan(sim, c);
}
export function cancelJourney(sim: CitySimulation, id: string, revision: number): void {
  const c = sim.citizens.get(id);
  if (!c || revision <= c.revision) return;
  c.taskActive = false;
  c.workIndex = null;
  setTarget(sim, id, safeDestination(sim, c), revision);
  // Cancelling within the current district must also trim a same-target access route.
  if (c.stage === "walking" && c.path.length > 1) c.path = c.path.slice(0, 2);
  else if (c.waitingForWorkspace || ["arrived", "working", "unreachable"].includes(c.stage)) plan(sim, c);
}
export function previewCrossing(sim: CitySimulation, id: string): boolean {
  const c = sim.citizens.get(id);
  if (!c || !["arrived", "working", "waiting", "unreachable"].includes(c.stage)) return false;
  const path = route(c.at, "bridge:north", sim.blocked, sim.graph);
  if (!path) return false;
  c.previewTarget = "bridge:north"; c.atWork = false; c.waitingForWorkspace = false; c.path = path; c.edgeTime = 0; c.stage = "walking"; return true;
}
function destination(sim: CitySimulation, c: Citizen): string {
  if (c.previewTarget) return c.previewTarget;
  if (!c.taskActive) return `${c.target}:plaza`;
  assignWorkspace(sim, c);
  return c.workIndex === null ? `${c.target}:foyer` : `${c.target}:work:${c.workIndex}`;
}
function plan(sim: CitySimulation, c: Citizen): void {
  c.edgeTime = 0; c.speed = 0; c.atWork = false; c.waitingForWorkspace = false;
  const targetNode = destination(sim, c);
  if (c.at === targetNode) {
    c.path = []; c.atWork = targetNode.includes(":work:");
    c.waitingForWorkspace = c.taskActive && c.workIndex === null && targetNode === `${c.target}:foyer`;
    c.stage = c.waitingForWorkspace ? "waiting" : c.atWork && c.taskActive ? "working" : "arrived";
    c.heading = district(c.target).buildingRotation; return;
  }
  const direct = route(c.at, targetNode, sim.blocked, sim.graph);
  let selected = direct, best = routeSeconds(direct, sim.graph), alight: Stop | null = null;
  if (!c.previewTarget && !sim.blocked.has(targetNode)) {
    const exit = route(`${c.target}:platform`, targetNode, sim.blocked, sim.graph);
    for (const stop of STOPS) {
      if (stop === c.target || !exit) continue;
      const access = route(c.at, `${stop}:platform`, sim.blocked, sim.graph);
      if (!access) continue;
      const reach = sim.time + routeSeconds(access, sim.graph);
      const total = nextDeparture(stop, reach) - sim.time + rideSeconds(stop, c.target) + 12 + routeSeconds(exit, sim.graph);
      if (total < best) { best = total; selected = access; alight = c.target; }
    }
  }
  c.alightAt = alight;
  if (!selected) { c.stage = "unreachable"; c.path = []; return; }
  c.path = selected;
  if (selected.length > 1) c.stage = "walking";
  else { c.stage = "waiting"; c.queued = sim.time; }
}
function seatLocal(seat: number): Vec3 {
  return [(seat % 12 - 5.5) * 1.5, 0.08, seat < 12 ? -0.8 : 0.8];
}
export function seatPosition(train: TrainState, seat: number): Vec3 { return transformPoint(train.position, train.heading, seatLocal(seat)); }
function doorPath(train: TrainState, seat: number): Vec3[] {
  const local = seatLocal(seat);
  return [[0, 0.08, 4.2], [0, 0.08, 2.55], [0, 0.08, 0], [local[0], 0.08, 0], local].map((p) => transformPoint(train.position, train.heading, p as Vec3));
}
function pathLength(points: Vec3[]): number { return points.slice(1).reduce((sum, p, i) => sum + distance(points[i], p), 0); }
function alightingPath(sim: CitySimulation, c: Citizen, train: TrainState): Vec3[] {
  return [...doorPath(train, c.seat!).reverse(), routePoint(sim, c, `${train.stop}:platform`)];
}
function canAlight(sim: CitySimulation, c: Citizen, train: TrainState): boolean {
  return train.stop !== null && train.stop !== c.boardingAt && train.remaining >= pathLength(alightingPath(sim, c, train)) / BOARD_SPEED + 0.5;
}
function advanceTransition(c: Citizen, dt: number): boolean {
  c.transition += dt;
  let travelled = c.transition * BOARD_SPEED;
  const points = c.transitionPath;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], length = distance(a, b);
    if (travelled < length && length > 1e-8) {
      c.position = mix(a, b, travelled / length); c.speed = BOARD_SPEED;
      c.heading = Math.atan2(b[0] - a[0], b[2] - a[2]); return false;
    }
    travelled -= length;
  }
  c.position = [...points[points.length - 1]]; c.speed = 0; return true;
}
function advanceWalk(sim: CitySimulation, c: Citizen, dt: number): void {
  let left = dt;
  while (left > 1e-8 && c.stage === "walking") {
    const [from, to] = c.path;
    if (!to) { plan(sim, c); break; }
    const edge = sim.graph.outgoing.get(from)?.find((e) => e.to === to);
    if (!edge || (sim.blocked.has(to) && c.edgeTime === 0)) { plan(sim, c); break; }
    const a = routePoint(sim, c, from), b = routePoint(sim, c, to);
    const duration = distance(a, b) / edge.speed;
    const consumed = Math.min(left, Math.max(0, duration - c.edgeTime));
    c.edgeTime += consumed; left -= consumed;
    c.position = mix(a, b, duration ? Math.min(1, c.edgeTime / duration) : 1);
    c.speed = edge.kind === "lift" ? 0 : edge.speed;
    if (a[0] !== b[0] || a[2] !== b[2]) c.heading = Math.atan2(b[0] - a[0], b[2] - a[2]);
    if (c.edgeTime + 1e-8 < duration) break;
    c.at = to; c.path.shift(); c.edgeTime = 0;
    if (c.path.length < 2) plan(sim, c);
  }
}
function advance(sim: CitySimulation, dt: number): void {
  sim.time += dt;
  const train = trainAt(sim.time);
  sim.reservedBlock = train.reservedBlock;
  if (sim.time >= sim.nextWorkCheck) {
    sim.nextWorkCheck = sim.time + 1;
    for (const c of sim.citizens.values()) if (c.waitingForWorkspace && c.stage === "waiting") plan(sim, c);
  }
  for (const c of sim.citizens.values()) {
    if (c.stage === "walking") advanceWalk(sim, c, dt);
    else if (c.stage === "boarding") {
      if (advanceTransition(c, dt)) { c.stage = "riding"; c.position = seatPosition(train, c.seat!); }
    } else if (c.stage === "riding") {
      c.position = seatPosition(train, c.seat!); c.speed = 0; c.heading = train.heading + Math.PI / 2;
      if (train.stop && train.stop === c.alightAt && canAlight(sim, c, train)) {
        const path = alightingPath(sim, c, train);
        c.stage = "exiting"; c.transition = 0; c.transitionFrom = [...c.position]; c.transitionPath = path;
      }
    } else if (c.stage === "exiting" && advanceTransition(c, dt)) {
      c.at = `${c.alightAt}:platform`; c.seat = null; c.boardingAt = null;
      plan(sim, c);
    }
  }
  if (!train.stop || !train.doorsOpen || sim.time < sim.nextBoardAt) return;
  if ([...sim.citizens.values()].some((c) => c.stage === "exiting")) return;
  const occupied = new Set([...sim.citizens.values()].filter((c) => c.seat !== null).map((c) => c.seat));
  for (const c of [...sim.citizens.values()].filter((c) => c.stage === "waiting" && c.at === `${train.stop}:platform` && sim.time - c.queued >= 0.4).sort((a, b) => a.queued - b.queued || a.id.localeCompare(b.id))) {
    const seat = Array.from({ length: CAPACITY }, (_, i) => i).find((i) => !occupied.has(i));
    if (seat === undefined) break;
    const path = [[...c.position] as Vec3, ...doorPath(train, seat)];
    if (pathLength(path) / BOARD_SPEED + 0.5 > train.remaining) continue;
    occupied.add(seat); c.seat = seat; c.stage = "boarding"; c.boardingAt = train.stop;
    c.transition = 0; c.transitionFrom = [...c.position]; c.transitionPath = path;
    sim.nextBoardAt = sim.time + 0.35;
    break;
  }
}
/** Test/offline advance with physical substeps; runtime should use the budgeted driver. */
export function stepSimulation(sim: CitySimulation, seconds: number): void {
  if (!Number.isFinite(seconds) || seconds <= 0) return;
  let left = seconds;
  while (left > 1e-8) { const dt = Math.min(STEP, left); advance(sim, dt); left -= dt; }
}
/** Retains elapsed time as debt while bounding catch-up work for one event-loop turn. */
export function stepWithBudget(sim: CitySimulation, seconds: number, maxSteps = 40): void {
  if (!Number.isFinite(seconds) || seconds < 0 || !Number.isFinite(maxSteps) || maxSteps < 1) return;
  sim.debt += seconds;
  if ([...sim.citizens.values()].every((c) => ["arrived", "working", "unreachable"].includes(c.stage))) {
    sim.time += sim.debt; sim.debt = 0; sim.reservedBlock = trainAt(sim.time).reservedBlock; return;
  }
  for (let i = 0; i < Math.floor(maxSteps) && sim.debt >= STEP - 1e-8; i++) {
    advance(sim, STEP); sim.debt = Math.max(0, sim.debt - STEP);
  }
}
/** Occupied interiors cannot be moved under a physical journey. */
export function moveBuilding(sim: CitySimulation, stop: Stop, position: Vec3): boolean {
  const plot = PLOTS.find((p) => p.id === stop)!;
  if (position[1] !== 0 || !validPlacement(plot, position[0], position[2], 58, 58, [])) return false;
  const interior = (id: string) => id.startsWith(`${stop}:`) && ["entry", "foyer", "aisle", "approach", "work"].includes(id.split(":")[1]);
  if ([...sim.citizens.values()].some((c) => interior(c.at) || c.path.some(interior))) return false;
  sim.placements = { ...sim.placements, [stop]: [...position] };
  sim.graph = createRouteGraph(sim.placements);
  return true;
}
export function daylight(hour: number): number { return Math.max(0, Math.sin(((hour - 6) / 24) * Math.PI * 2)); }
export function advanceHour(hour: number, dt: number, paused: boolean): number { return paused ? hour : ((hour + dt * 24 / 1800) % 24 + 24) % 24; }

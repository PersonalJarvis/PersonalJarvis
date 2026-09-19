import type { AgentRecord, WorldSnapshot } from "./types";
export type Point = [number, number, number];
export interface WorldNode { id: string; title: string; position: Point; agent?: AgentRecord; group?: string; count?: string; level: number }
export function stableHash(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) hash = Math.imul(hash ^ value.charCodeAt(i), 16777619);
  return hash >>> 0;
}
/** Stable identity-derived positions: replay cannot shuffle an unchanged roster. */
export function worldNodes(snapshot: WorldSnapshot): WorldNode[] {
  // A drilled group contains individual agents even if an older server retains
  // the team's aggregate flag. Never discard those workers for an empty group list.
  if (snapshot.aggregated && snapshot.groups.length > 0) return snapshot.groups.slice(0, 50).map((group, index, groups) => {
    const angle = index * Math.PI * 2 / Math.max(groups.length, 1);
    const radius = groups.length > 1 ? Math.max(7, groups.length * .55) : 0;
    return { id: `group:${group.id}`, title: group.title, position: [Math.cos(angle) * radius, 0, Math.sin(angle) * radius], group: group.id, count: group.agents, level: group.level };
  });
  const agents = snapshot.agents.slice(0, 100);
  const groups = [...new Set(agents.filter(a => a.role !== "lead").map(a => a.group_id))].sort();
  const seats = new Map<string, number>();
  return [...agents].sort((a, b) => a.id.localeCompare(b.id)).map(agent => {
    if (agent.role === "lead") return { id: agent.id, title: agent.name, agent, position: [0, 0, 0], level: agent.level };
    const index = groups.indexOf(agent.group_id);
    const angle = index * Math.PI * 2 / Math.max(groups.length, 1);
    const seat = seats.get(agent.group_id) ?? 0;
    seats.set(agent.group_id, seat + 1);
    const radius = (agents.length <= 8 ? 4.5 : 8) + Math.floor(seat / 8) * 3.2;
    const spread = (seat % 8 - 3.5) * .18;
    return { id: agent.id, title: agent.name, agent, level: agent.level,
      position: [Math.cos(angle + spread) * radius, 0, Math.sin(angle + spread) * radius] };
  });
}
/** Keep the reused memory landmark clear of every team figure. */
export function memoryPosition(nodes: WorldNode[]): Point {
  return [Math.min(0, ...nodes.map(node => node.position[0])) - 4.8, 0, -1.5];
}
/** Small teams should be readable without an initial manual zoom. */
export function worldRadius(nodes: WorldNode[]): number {
  const memory = memoryPosition(nodes);
  return Math.max(11, Math.hypot(memory[0], memory[2]) * 1.5,
    ...nodes.map(node => (Math.hypot(node.position[0], node.position[2]) + 2) * 1.45));
}
export interface CameraState { yaw: number; zoom: number; focus: [number, number]; elevation?: number }
export const DEFAULT_CAMERA: CameraState = { yaw: .65, zoom: 1, focus: [0, 0] };
export const cameraKey = (teamId: string) => `jarvis.swarm.v1.${teamId}.camera`;
export function readCamera(teamId: string): CameraState {
  try {
    const saved = JSON.parse(sessionStorage.getItem(cameraKey(teamId)) ?? "null");
    if (saved && Number.isFinite(saved.yaw) && Number.isFinite(saved.zoom) && saved.zoom >= .4 && saved.zoom <= 3 &&
      Array.isArray(saved.focus) && saved.focus.length === 2 && saved.focus.every(Number.isFinite) &&
      (saved.elevation === undefined || (Number.isFinite(saved.elevation) && saved.elevation > 0 && saved.elevation < 100))) return saved;
  } catch { /* Blocked storage or stale preferences do not block a world. */ }
  return { ...DEFAULT_CAMERA, focus: [0, 0] };
}
export function saveCamera(teamId: string, camera: CameraState) {
  try { sessionStorage.setItem(cameraKey(teamId), JSON.stringify(camera)); }
  catch { /* Camera persistence is optional in private browser sessions. */ }
}

import { buildingObstacles, type Navigation } from "./navigation";
import { buildIsland } from "./islandLayout";
import type { Disc, Point } from "./spatial";

export type RoutePlanner = (from: Point, to: Point, radius: number, occupied: Disc[], id: string) => Promise<Array<[number, number]> | null>;

/** One worker per scene/navigation revision, terminated on replacement/unmount. */
export function createRoutePlanner(nav: Navigation): { plan: RoutePlanner; dispose: () => void } {
  const directOnly = { plan: async (a: Point, b: Point, r: number) => nav.segment(a, b, r) ? [[...b] as [number, number]] : null, dispose: () => undefined };
  if (typeof Worker === "undefined") return directOnly;
  let worker: Worker;
  try { worker = new Worker(new URL("./navigation.worker.ts", import.meta.url), { type: "module" }); }
  catch (error) { console.warn("Background navigation unavailable; using direct safe routes", error); return directOnly; }
  let sequence = 0, disposed = false;
  const pending = new Map<number, (path: Array<[number, number]> | null) => void>();
  worker.postMessage({ type: "init", map: nav.map, obstacles: buildingObstacles(buildIsland()) });
  const dispose = () => {
    if (disposed) return; disposed = true; worker.terminate();
    for (const resolve of pending.values()) resolve(null);
    pending.clear();
  };
  worker.onmessage = e => {
    if (e.data.error) console.warn("World route search failed", e.data.error);
    pending.get(e.data.key)?.(e.data.path); pending.delete(e.data.key);
  };
  worker.onerror = e => { console.warn("World route worker failed", e.message); dispose(); };
  return {
    plan: (from, to, radius, occupied, id) => new Promise(resolve => {
      if (disposed) { resolve(null); return; }
      const key = ++sequence; pending.set(key, resolve);
      worker.postMessage({ type: "route", key, from, to, radius, occupied: occupied.map(a => ({ id: a.id, x: a.x, z: a.z, radius: a.radius })), id });
    }),
    dispose,
  };
}

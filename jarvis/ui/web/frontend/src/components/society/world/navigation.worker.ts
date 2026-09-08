/// <reference lib="webworker" />
/** Expensive route searches never occupy the UI/voice thread. */
import { Navigation } from "./navigation";
import type { IslandMap } from "./islandLayout";
import type { Disc, Obstacle, Point } from "./spatial";

let nav: Navigation | null = null;
self.onmessage = (event: MessageEvent<{
  type: "init" | "route"; key?: number; map?: IslandMap; obstacles?: Obstacle[];
  from?: Point; to?: Point; radius?: number; occupied?: Disc[]; id?: string;
}>) => {
  const request = event.data;
  if (request.type === "init") { nav = new Navigation(request.map!, request.obstacles!); return; }
  try {
    const path = nav?.route(request.from!, request.to!, request.radius!, request.occupied, request.id) ?? null;
    self.postMessage({ key: request.key, path });
  } catch (error) {
    self.postMessage({ key: request.key, path: null, error: String(error) });
  }
};

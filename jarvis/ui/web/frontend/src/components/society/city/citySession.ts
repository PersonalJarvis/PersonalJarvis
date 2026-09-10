import { createCityClock, type CityClock } from "./cityClock";
import { createSimulation, type CitySimulation } from "./citySimulation";
import type { Placements } from "./cityModel";

interface Session { sim: CitySimulation; clock: CityClock }
/** Lazy, browser-local state survives section switches without keeping a canvas alive. */
export function createCitySessions() {
  const sessions = new Map<"live" | "demo" | "sample", Session>();
  return {
    get(kind: "live" | "demo" | "sample", placements: () => Placements, now: number): Session {
      let session = sessions.get(kind);
      if (!session) { session = { sim: createSimulation(placements()), clock: createCityClock(now) }; sessions.set(kind, session); }
      return session;
    },
  };
}
export const citySessions = createCitySessions();

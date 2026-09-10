import {
  advanceHour,
  stepWithBudget,
  type CitySimulation,
} from "./citySimulation";

export interface CityClock {
  lastMs: number;
  hour: number;
  dayPaused: boolean;
}
export const createCityClock = (now: number): CityClock => ({
  lastMs: now,
  hour: 10,
  dayPaused: false,
});
/** Both foreground frames and background ticks consume the SAME monotonic clock. */
export function tickCityClock(
  clock: CityClock,
  sim: CitySimulation,
  now: number,
  paused: boolean,
): void {
  const elapsed = Math.max(0, (now - clock.lastMs) / 1000);
  clock.lastMs = Math.max(clock.lastMs, now);
  if (paused) {
    sim.debt = 0;
    return;
  }
  stepWithBudget(sim, elapsed, 20);
  clock.hour = advanceHour(clock.hour, elapsed, clock.dayPaused);
}

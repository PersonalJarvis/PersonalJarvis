/**
 * The elevator ride over the stage: two brushed-steel doors slide shut, a hall
 * lantern on the seam counts through the floors with an arrow pointing the
 * way, light bands sweep past while the car moves, and the doors open on the
 * new floor. The floor switches behind the closed doors (OfficeStage owns the
 * timing); this component only draws the phase it is given.
 *
 * Part of the diorama, like the call button's pill: it keeps one palette in
 * light and dark mode. Reduced motion: no sliding, sweeping or counting —
 * the lantern simply shows the floor being ridden to.
 */
import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { floorLevel, OFFICE_FLOORS, type OfficeFloor } from "./officeStore";

export type RidePhase = "closing" | "riding" | "opening";

/** How long the lantern shows each floor while the car moves. */
export const RIDE_STEP_MS = 420;

/** The floor levels a ride passes, from where it starts to where it stops (both included). */
export function rideLevels(from: OfficeFloor, to: OfficeFloor): number[] {
  const a = floorLevel(from), b = floorLevel(to);
  const step = b >= a ? 1 : -1;
  const out: number[] = [];
  for (let level = a; level !== b + step; level += step) out.push(level);
  return out;
}

/** How long the car should at least be under way so the lantern can count every floor. */
export function minRideMs(from: OfficeFloor, to: OfficeFloor): number {
  return Math.max(1, rideLevels(from, to).length - 1) * RIDE_STEP_MS;
}

/** A chevron pointing up (or down when `down`). */
function Arrow({ down }: { down: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden className="office-elevator-arrow" data-down={down || undefined}>
      <path d="M12 5 L20 15 H4 Z" fill="currentColor" />
    </svg>
  );
}

export function ElevatorRide({ from, to, phase, reduced }: { from: OfficeFloor; to: OfficeFloor; phase: RidePhase; reduced: boolean }) {
  const t = useT();
  const levels = rideLevels(from, to);
  const up = floorLevel(to) > floorLevel(from);
  // Which of the passed floors the lantern shows: the start while the doors close, one step per RIDE_STEP_MS under way, the stop on arrival.
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (reduced || phase !== "riding") return;
    const timer = window.setInterval(() => setStep((s) => Math.min(levels.length - 1, s + 1)), RIDE_STEP_MS);
    return () => window.clearInterval(timer);
  }, [phase, reduced, levels.length]);
  const index = reduced || phase === "opening" ? levels.length - 1 : phase === "closing" ? 0 : step;
  const level = levels[index] ?? floorLevel(to);
  const shown = OFFICE_FLOORS[level] ?? to;
  const caption = t(up ? "society.office.riding_up_to" : "society.office.riding_down_to").replace("{0}", t(`society.office.floor_name_${to}`));
  return (
    <div className="office-elevator" data-phase={phase} data-direction={up ? "up" : "down"} data-reduced={reduced || undefined}
      role="status" aria-live="polite" aria-label={caption}>
      <i className="office-elevator-door" data-side="left" aria-hidden />
      <i className="office-elevator-door" data-side="right" aria-hidden />
      <i className="office-elevator-sweep" aria-hidden />
      <div className="office-elevator-lantern" aria-hidden>
        <Arrow down={!up} />
        <b key={level} className="office-elevator-level">{level}</b>
        <span className="office-elevator-name">{t(`society.office.floor_name_${shown}`)}</span>
      </div>
    </div>
  );
}

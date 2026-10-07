/**
 * The floor change: the elevator's two brushed-steel doors slide shut over
 * the stage, the floor switches behind them, and they slide open on the new
 * floor. OfficeStage owns the timing; this only draws the phase it is given.
 * Part of the diorama: one palette in light and dark mode. Reduced motion:
 * the doors appear and disappear without sliding.
 */
import { useT } from "@/i18n";
import { floorLevel, type OfficeFloor } from "./officeStore";

export type DoorsPhase = "closing" | "closed" | "opening";

export function ElevatorDoors({ from, to, phase }: { from: OfficeFloor; to: OfficeFloor; phase: DoorsPhase }) {
  const t = useT();
  const up = floorLevel(to) > floorLevel(from);
  return (
    <div className="office-doors" data-phase={phase} role="status" aria-live="polite"
      aria-label={t(up ? "society.office.riding_up_to" : "society.office.riding_down_to").replace("{0}", t(`society.office.floor_name_${to}`))}>
      <i className="office-doors-leaf" data-side="left" aria-hidden />
      <i className="office-doors-leaf" data-side="right" aria-hidden />
    </div>
  );
}

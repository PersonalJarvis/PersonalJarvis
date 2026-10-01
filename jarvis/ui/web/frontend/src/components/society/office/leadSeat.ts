/**
 * The person's character can take an executive chair: the lead's downstairs,
 * Mission Control's upstairs. Walk up and press E (or click the chair), sit,
 * then click the monitors (the lead agent, or the Mission Control panel).
 * Seated at Mission Control the view turns first person onto its monitors.
 * Any movement stands the character up again.
 *
 * Kept apart from the general office store: only executive chairs are
 * sittable, and each chair's prompt lives in the scene next to it.
 */
import { create } from "zustand";
import { seatOf, type DeskSlot, type OfficeLayout, type Point } from "./officeLayout";

/** How close the character must stand to the chair to sit down with E, in metres. */
export const SEAT_REACH = 1.1;

interface LeadSeatState {
  /** Desk id whose chair the character sits on, or null. */
  seated: string | null;
  /** Desk id whose chair is within reach (the E prompt), or null. */
  near: string | null;
  /** Desk id the character is walking to in order to sit (the chair was clicked from afar). */
  pending: string | null;
  /** One-shot: the character just stood up and must step out of the chair's footprint. */
  standUp: boolean;
  set: (patch: Partial<Pick<LeadSeatState, "seated" | "near" | "pending" | "standUp">>) => void;
}

export const useLeadSeat = create<LeadSeatState>((set) => ({
  seated: null,
  near: null,
  pending: null,
  standUp: false,
  set: (patch) => set((s) => (Object.entries(patch).every(([k, v]) => s[k as keyof typeof patch] === v) ? s : patch)),
}));

/** Every desk whose chair the person can take on this floor: the lead desks, and Mission Control's. */
export function seatDesks(layout: Pick<OfficeLayout, "lead" | "command">): DeskSlot[] {
  return layout.command ? [...layout.lead.desks, layout.command] : layout.lead.desks;
}

/** The executive chair within reach of `at`, if any (sittable desks carry a size; bench desks never do). */
export function chairInReach(desks: readonly DeskSlot[], at: Point): DeskSlot | null {
  let best: DeskSlot | null = null;
  let bestDistance = SEAT_REACH;
  for (const desk of desks) {
    if (!desk.size) continue;
    const seat = seatOf(desk);
    const d = Math.hypot(seat.x - at.x, seat.z - at.z);
    if (d <= bestDistance) { best = desk; bestDistance = d; }
  }
  return best;
}

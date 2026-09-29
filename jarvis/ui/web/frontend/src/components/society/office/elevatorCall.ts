/**
 * The elevator's call button: where it sits on the shaft and who may press it.
 *
 * Riding takes a deliberate press of the button beside the doors, the way a
 * real elevator works: the person walks up to the elevator and clicks the
 * button (or presses E there). A click from across the floor only walks the
 * character over; it never rides from afar.
 */
import type { Checkpoint, Furniture, Point } from "./officeLayout";

/** The button plate in the elevator prop's own frame: right of the doors, at hand height, just proud of the shaft face. */
export const CALL_BUTTON_LOCAL = { x: 0.9, y: 1.15, z: 0.13 } as const;

/** How long the lit button shows before the doors start to close. */
export const CALL_PRESS_MS = 650;

export interface CallButtonPose { x: number; y: number; z: number; rotationY: number }

/** World pose of the call button for the elevator prop `item` (rotation about +y, as the prop is drawn). */
export function callButtonPose(item: Pick<Furniture, "x" | "z" | "rotationY">): CallButtonPose {
  const { x: lx, y, z: lz } = CALL_BUTTON_LOCAL;
  const cos = Math.cos(item.rotationY), sin = Math.sin(item.rotationY);
  return { x: item.x + lx * cos + lz * sin, y, z: item.z - lx * sin + lz * cos, rotationY: item.rotationY };
}

/** Is the character standing at the elevator, close enough to press its button? */
export function atElevator(body: Point, checkpoint: Pick<Checkpoint, "x" | "z" | "radius"> | undefined): boolean {
  return !!checkpoint && Math.hypot(body.x - checkpoint.x, body.z - checkpoint.z) <= checkpoint.radius;
}

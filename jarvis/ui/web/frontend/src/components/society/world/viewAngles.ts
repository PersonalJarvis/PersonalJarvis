/**
 * The angles the island is being DRAWN at this frame — the camera rig's eased
 * yaw and pitch, one step behind the store while a turn swings into place.
 *
 * Anything that has to line up with the picture on screen reads these, not the
 * store: the minimap's viewport rectangle, the sun's shadow frustum and the
 * water's glint. Going through the store instead would make them lead the
 * camera during the ease, and reading them per frame through zustand would
 * cost a subscription where a plain mutable record does.
 */
import { CAMERA_PITCH_DEG, CAMERA_YAW_DEG } from "./worldCamera";

export interface ViewAngles {
  /** Compass direction the camera stands in, degrees. */
  yaw: number;
  /** Angle below the horizon, degrees. */
  pitch: number;
}

const live: ViewAngles = { yaw: CAMERA_YAW_DEG, pitch: CAMERA_PITCH_DEG };

/** Written once per frame by `WorldCameraRig`; nothing else may write it. */
export function setViewAngles(yaw: number, pitch: number): void {
  live.yaw = yaw;
  live.pitch = pitch;
}

/** The shared record — read `.yaw` / `.pitch`, never hold on to the object. */
export function viewAngles(): Readonly<ViewAngles> {
  return live;
}

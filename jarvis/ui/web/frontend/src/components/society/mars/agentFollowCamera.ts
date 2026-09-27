import { avoidCameraCollision } from "./camera";
import type { CameraPose } from "./viewPreferences";
import { BUILDING_COLLIDERS, terrainHeight, type Vec3 } from "./world";

/** Follow the rendered agent with a stable offset and the world's collision rules. */
export function createAgentFollowPose(position: Vec3, colliders = BUILDING_COLLIDERS, ground = terrainHeight): CameraPose {
  const target: Vec3 = [position[0], position[1] + 1.4, position[2]];
  const desired: Vec3 = [target[0] + 6, target[1] + 4, target[2] + 8];
  return { target, position: avoidCameraCollision(target, desired, colliders, ground) };
}

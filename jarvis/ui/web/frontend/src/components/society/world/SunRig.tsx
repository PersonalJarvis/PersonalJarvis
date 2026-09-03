/**
 * The sun: one warm directional light with a soft shadow map whose orthographic
 * shadow camera FOLLOWS the view — fitted to the ground rectangle the player
 * sees, so shadows are crisp where they are looked at and nothing is rendered
 * for the rest of the island (world-masterplan-v2.md §3.2).
 */
import { useEffect, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { DirectionalLight, Vector3 } from "three";

import { useCameraStore } from "./cameraStore";
import { viewAngles } from "./viewAngles";
import { ZOOM_WIDTHS_M, orthoHalfExtents } from "./worldCamera";
import { SKY } from "./worldPalette";
import { useWorldSettings } from "./worldSettings";

/** Sun direction (from the target toward the sun), normalised at build. */
const SUN_DIR = new Vector3(SKY.sunFrom[0], SKY.sunFrom[1], SKY.sunFrom[2]).normalize();
/** Distance of the light from its target — outside every object, inside the far plane. */
const SUN_DISTANCE = 220;
/** The shadow frustum covers this many times the visible ground width (shadows come from off-screen). */
const COVER = 1.35;

export function SunRig() {
  const light = useRef<DirectionalLight>(null);
  const size = useThree((s) => s.size);
  const shadows = useWorldSettings((s) => s.shadows);

  useEffect(() => {
    const l = light.current;
    if (!l) return;
    l.shadow.mapSize.set(2048, 2048);
    l.shadow.bias = -0.0006;
    l.shadow.normalBias = 0.04;
    l.shadow.camera.near = 1;
    l.shadow.camera.far = SUN_DISTANCE * 2.5;
  }, []);

  useFrame(() => {
    const l = light.current;
    if (!l) return;
    const { target, zoom } = useCameraStore.getState();
    const aspect = size.width / Math.max(1, size.height);
    const width = ZOOM_WIDTHS_M[zoom] * COVER;
    const { halfW, halfH } = orthoHalfExtents(width, aspect);
    // A flatter view sees far deeper into the island, so the shadow frustum
    // has to grow with the tilt or shadows cut off at the top of the screen.
    const reach = Math.max(halfW, halfH / Math.sin((viewAngles().pitch * Math.PI) / 180));
    l.target.position.set(target[0], 0, target[1]);
    l.target.updateMatrixWorld();
    l.position.set(
      target[0] + SUN_DIR.x * SUN_DISTANCE,
      SUN_DIR.y * SUN_DISTANCE,
      target[1] + SUN_DIR.z * SUN_DISTANCE,
    );
    const cam = l.shadow.camera;
    if (cam.left !== -reach) {
      cam.left = -reach;
      cam.right = reach;
      cam.top = reach;
      cam.bottom = -reach;
      cam.updateProjectionMatrix();
    }
  });

  return (
    <directionalLight
      ref={light}
      color={SKY.sun}
      intensity={SKY.sunIntensity}
      castShadow={shadows}
    />
  );
}

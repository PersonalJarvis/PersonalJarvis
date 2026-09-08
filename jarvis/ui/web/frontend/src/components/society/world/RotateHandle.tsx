/**
 * The handle that turns a building: a knob on the front of its selection
 * ring, an arrow on the ground that marks the door side, and a badge with
 * the heading and the way back to the designed one.
 *
 * Drag the knob around the building and the building follows the pointer —
 * the heading is the ground point under the pointer seen from the building's
 * centre. Shift snaps to 15° steps; close to the designed heading it snaps
 * back to that. The drag never reaches the stage's own pointer handlers
 * (`useWorldControls`), so the camera holds still, and it never bubbles to the
 * building, so no drawer opens from a turn.
 *
 * Rendered INSIDE the building's rotated group: local +z is the front, so the
 * knob sits at [0, ·, radius] and turns with the building for free.
 */
import { useEffect, useRef, useState } from "react";
import { Html } from "@react-three/drei";
import { useThree, type ThreeEvent } from "@react-three/fiber";
import { RotateCcw } from "lucide-react";
import type { Ray } from "three";

import { useT } from "@/i18n";
import { useBuildingPoses, useBuildingYaw, useIsTurned } from "./buildingPoses";
import type { BuildingId } from "./islandLayout";

const KNOB = "#ffd166";
const KNOB_HOT = "#ffe9a8";

/** Where a world-space ray meets the horizontal plane at height `y`; null when it never does. */
export function groundPoint(ray: Ray, y: number): [number, number] | null {
  const t = (y - ray.origin.y) / ray.direction.y;
  if (!Number.isFinite(t) || t < 0) return null;
  return [ray.origin.x + ray.direction.x * t, ray.origin.z + ray.direction.z * t];
}

export function RotateHandle({
  id,
  x,
  y,
  z,
  radius,
}: {
  id: BuildingId;
  /** The building's centre and ground height, world metres. */
  x: number;
  y: number;
  z: number;
  /** Radius of the selection ring the knob sits on. */
  radius: number;
}) {
  const t = useT();
  const gl = useThree((s) => s.gl);
  const yaw = useBuildingYaw(id);
  const turned = useIsTurned(id);
  const rejected = useBuildingPoses(s => s.rejected === id);
  const [hover, setHover] = useState(false);
  const dragging = useRef(false);

  useEffect(() => {
    if (!hover && !dragging.current) return;
    gl.domElement.style.cursor = dragging.current ? "grabbing" : "grab";
    return () => {
      gl.domElement.style.cursor = "";
    };
  }, [hover, gl]);

  const onPointerDown = (e: ThreeEvent<PointerEvent>) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.nativeEvent.stopPropagation(); // the stage must not start a pan
    (e.target as Element).setPointerCapture(e.pointerId);
    dragging.current = true;
    gl.domElement.style.cursor = "grabbing";
    useBuildingPoses.getState().setRotating(true);
  };

  const onPointerMove = (e: ThreeEvent<PointerEvent>) => {
    if (!dragging.current) return;
    e.stopPropagation();
    e.nativeEvent.stopPropagation();
    const p = groundPoint(e.ray, y);
    if (!p) return;
    useBuildingPoses.getState().setYaw(id, Math.atan2(p[0] - x, p[1] - z), e.shiftKey);
  };

  const onPointerUp = (e: ThreeEvent<PointerEvent>) => {
    if (!dragging.current) return;
    dragging.current = false;
    e.stopPropagation();
    e.nativeEvent.stopPropagation();
    (e.target as Element).releasePointerCapture(e.pointerId);
    gl.domElement.style.cursor = hover ? "grab" : "";
    // Let the click that ends the drag pass first, then re-enable figure clicks.
    window.setTimeout(() => useBuildingPoses.getState().setRotating(false), 0);
  };

  const degrees = Math.round((((yaw * 180) / Math.PI) % 360) + 360) % 360;

  return (
    <group>
      {/* the door-side arrow on the ground, inside the ring */}
      <mesh position={[0, 0.1, radius - 1.3]} rotation={[Math.PI / 2, 0, 0]}>
        <coneGeometry args={[0.55, 1.4, 3]} />
        <meshBasicMaterial color={KNOB} transparent opacity={0.9} />
      </mesh>
      {/* the stem from the ring up to the knob */}
      <mesh position={[0, 0.45, radius + 0.3]}>
        <cylinderGeometry args={[0.08, 0.08, 0.9, 6]} />
        <meshBasicMaterial color={KNOB} />
      </mesh>
      <mesh
        position={[0, 1.0, radius + 0.3]}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={(e) => e.stopPropagation()}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHover(true);
        }}
        onPointerOut={() => setHover(false)}
      >
        <sphereGeometry args={[0.55, 14, 10]} />
        <meshBasicMaterial color={hover || dragging.current ? KNOB_HOT : KNOB} />
      </mesh>
      <Html position={[0, 1.0, radius + 2.6]} center zIndexRange={[40, 20]}>
        <div
          className="sw-rotate"
          data-turned={turned || undefined}
          onPointerDown={(e) => e.nativeEvent.stopPropagation()}
          onClick={(e) => e.nativeEvent.stopPropagation()}
        >
          <span className="sw-rotate-deg" title={t("society.world.rotate_hint")}>
            {degrees}°
          </span>
          {rejected && <span role="status">{t("society.world.rotate_blocked")}</span>}
          {turned && (
            <button
              type="button"
              className="sw-rotate-reset"
              onClick={() => useBuildingPoses.getState().resetYaw(id)}
              title={t("society.world.rotate_reset")}
              aria-label={t("society.world.rotate_reset")}
            >
              <RotateCcw size={12} />
              <span>{t("society.world.rotate_reset_short")}</span>
            </button>
          )}
        </div>
      </Html>
    </group>
  );
}

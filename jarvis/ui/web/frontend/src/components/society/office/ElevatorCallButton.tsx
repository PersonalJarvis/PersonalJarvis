/**
 * The elevator's call button, on the shaft to the right of the doors.
 *
 * Riding needs a press of this button: a click on it (or E while standing at
 * the elevator) opens the floor picker; picking a floor lights it, then the
 * doors close. The ground floor shows an up arrow, the top floor a down
 * arrow, the floors between both. While the person stands at the elevator it
 * breathes softly and a pill above it invites a press; clicking it from afar
 * only walks the character over (see `elevatorCall.ts`).
 */
import { useEffect, useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { MeshStandardMaterial } from "three";
import { useT } from "@/i18n";
import { cachedCanvasTexture } from "./canvasMaterials";
import { callButtonPose } from "./elevatorCall";
import type { Furniture } from "./officeLayout";
import { floorLevel, OFFICE_FLOORS, type OfficeFloor } from "./officeStore";

const BUTTON_RADIUS = 0.09;
const IDLE = "#3b3f48";
const LIT = "#f5b83d";

type ArrowDirection = "up" | "down" | "both";

/** Which way the elevator can go from `floor`: up from the bottom, down from the top, both in between. */
export function callDirection(floor: OfficeFloor): ArrowDirection {
  const level = floorLevel(floor);
  return level <= 0 ? "up" : level >= OFFICE_FLOORS.length - 1 ? "down" : "both";
}

/** White arrows on a transparent square; "up" points to +y, "both" stacks a small up and down arrow. */
function arrowTexture(direction: ArrowDirection) {
  return cachedCanvasTexture(`elevator-arrow:${direction}`, 128, 128, (ctx, w, h) => {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#ffffff";
    const arrow = (up: boolean, top: number, bottom: number, half: number) => {
      ctx.beginPath();
      ctx.moveTo(w / 2, up ? top : bottom);
      ctx.lineTo(w / 2 - half, up ? bottom : top);
      ctx.lineTo(w / 2 + half, up ? bottom : top);
      ctx.closePath();
      ctx.fill();
    };
    if (direction === "both") {
      arrow(true, h * 0.14, h * 0.44, w * 0.22);
      arrow(false, h * 0.56, h * 0.86, w * 0.22);
    } else {
      arrow(direction === "up", h * 0.24, h * 0.76, w * 0.26);
    }
  });
}

export function ElevatorCallButton({ shaft, floor, near, lit, animate, onPress }: {
  shaft: Furniture;
  floor: OfficeFloor;
  /** The person stands at the elevator: the button invites a press. */
  near: boolean;
  /** Pressed: glowing until the ride starts. */
  lit: boolean;
  animate: boolean;
  onPress: () => void;
}) {
  const t = useT();
  const pose = callButtonPose(shaft);
  const direction = callDirection(floor);
  const button = useMemo(() => new MeshStandardMaterial({ color: IDLE, roughness: 0.35, metalness: 0.3, emissive: LIT, emissiveIntensity: 0 }), []);
  const arrow = useMemo(() => new MeshStandardMaterial({
    map: arrowTexture(direction), transparent: true, depthWrite: false, roughness: 0.5,
    emissive: "#ffffff", emissiveIntensity: 0.25,
  }), [direction]);
  useEffect(() => () => button.dispose(), [button]);
  useEffect(() => () => arrow.dispose(), [arrow]);
  useEffect(() => () => { document.body.style.cursor = ""; }, []);

  const pulse = useRef(0);
  useFrame((_, delta) => {
    if (lit) { button.color.set(LIT); button.emissiveIntensity = 1.1; return; }
    button.color.set(IDLE);
    if (near && animate) {
      pulse.current += delta;
      button.emissiveIntensity = 0.25 + (Math.sin(pulse.current * 4) + 1) * 0.2;
    } else {
      button.emissiveIntensity = near ? 0.45 : 0;
    }
  });

  const press = (event: ThreeEvent<MouseEvent>) => {
    // A drag that ends on the button rotated the camera; only a real click presses.
    if (event.delta > 6) return;
    event.stopPropagation();
    onPress();
  };

  return (
    <group position={[pose.x, pose.y, pose.z]} rotation={[0, pose.rotationY, 0]} name="elevator-call-button">
      {/* The steel plate covers the shaft's painted indicator. */}
      <mesh position={[0, 0, -0.005]}>
        <boxGeometry args={[0.26, 0.44, 0.02]} />
        <meshStandardMaterial color="#c7ccd4" roughness={0.35} metalness={0.3} />
      </mesh>
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, 0.012]} material={button}>
        <cylinderGeometry args={[BUTTON_RADIUS, BUTTON_RADIUS, 0.02, 32]} />
      </mesh>
      <mesh position={[0, 0, 0.0231]} material={arrow} raycast={() => null}>
        <planeGeometry args={[BUTTON_RADIUS * 1.6, BUTTON_RADIUS * 1.6]} />
      </mesh>
      {near && !lit && (
        <Html center position={[0, 0.62, 0.05]} zIndexRange={[24, 0]} pointerEvents="none">
          <span className="office-call-pill" data-office-ui>
            <b>{t("society.office.elevator_choose")}</b>
            <small>{t("society.office.elevator_press")}</small>
          </span>
        </Html>
      )}
      {/* The click target: a see-through pad a little larger than the button, so it is easy to hit from a distance. */}
      <mesh position={[0, 0, 0.03]} onClick={press}
        onPointerOver={(event) => { event.stopPropagation(); document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { document.body.style.cursor = ""; }}>
        <planeGeometry args={[0.32, 0.5]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  );
}

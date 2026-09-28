/**
 * One camera for walking and for the overview. OrbitControls always own
 * rotate and zoom; in follow mode the orbit target glides after the
 * character, so zooming out turns the same view into an overview without a
 * mode switch. A right-drag pan or a fly-to leaves follow mode; moving the
 * character re-enters it.
 */
import { useEffect, useRef } from "react";
import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { cameraHome, CAMERA_LIMITS, HOME_PITCH_RAD, HOME_YAW_RAD } from "./officeCamera";
import type { OfficeLayout } from "./officeLayout";
import { player, useOfficeStore } from "./officeStore";

/** Where the camera starts: close behind the character, south-east, looking down. */
export const FOLLOW_DISTANCE = 15;
const TARGET_HEIGHT = 0.8;

export function OfficeCameraRig({ layout, overview }: { layout: OfficeLayout; overview: number }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const lastFocus = useRef(0);
  const flight = useRef<{ from: Vector3; to: Vector3; t: number } | null>(null);
  const desired = useRef(new Vector3());
  const delta = useRef(new Vector3());

  // Start close to the character.
  useEffect(() => {
    const target = new Vector3(player.x, TARGET_HEIGHT, player.z);
    const horizontal = Math.cos(HOME_PITCH_RAD) * FOLLOW_DISTANCE;
    camera.position.set(target.x + Math.sin(HOME_YAW_RAD) * horizontal, Math.sin(HOME_PITCH_RAD) * FOLLOW_DISTANCE + TARGET_HEIGHT,
      target.z + Math.cos(HOME_YAW_RAD) * horizontal);
    controls.current?.target.copy(target);
    controls.current?.update();
    useOfficeStore.getState().setFollow(true);
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__office = { camera, controls: controls.current, gl, scene };
  }, [camera, gl, scene, layout.spawn]);

  // "Overview": frame the whole floor and stop following.
  useEffect(() => {
    if (overview === 0) return;
    const home = cameraHome(layout.bounds, size.width / Math.max(1, size.height));
    flight.current = null;
    useOfficeStore.getState().setFollow(false);
    camera.position.set(...home.position);
    controls.current?.target.set(...home.target);
    controls.current?.update();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overview]);

  // A right-drag (or Shift/Ctrl-drag) pans: the person takes the camera, so stop following.
  useEffect(() => {
    const el = gl.domElement;
    const down = (event: PointerEvent) => {
      if (event.button === 2 || event.shiftKey || event.ctrlKey || event.metaKey) useOfficeStore.getState().setFollow(false);
    };
    el.addEventListener("pointerdown", down);
    return () => el.removeEventListener("pointerdown", down);
  }, [gl]);

  useFrame((_, rawDt) => {
    const c = controls.current;
    if (!c) return;
    const dt = Math.min(rawDt, 0.1);
    const store = useOfficeStore.getState();
    if (store.focus && store.focus.seq !== lastFocus.current) {
      lastFocus.current = store.focus.seq;
      flight.current = { from: c.target.clone(), to: new Vector3(store.focus.point.x, TARGET_HEIGHT, store.focus.point.z), t: 0 };
    }
    if (flight.current) {
      const f = flight.current;
      f.t = Math.min(1, f.t + dt / 0.7);
      const ease = f.t < 0.5 ? 2 * f.t * f.t : 1 - (-2 * f.t + 2) ** 2 / 2;
      desired.current.copy(f.from).lerp(f.to, ease);
      if (f.t >= 1) flight.current = null;
    } else if (store.follow) {
      desired.current.set(player.x, TARGET_HEIGHT, player.z);
      desired.current.lerp(c.target, Math.exp(-8 * dt));
    } else {
      return;
    }
    // Move camera and target together: rotation and zoom stay what the person chose.
    delta.current.copy(desired.current).sub(c.target);
    c.target.add(delta.current);
    camera.position.add(delta.current);
    c.update();
  });

  return (
    <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.12} screenSpacePanning={false}
      minPolarAngle={CAMERA_LIMITS.minPolar} maxPolarAngle={CAMERA_LIMITS.maxPolar}
      minDistance={CAMERA_LIMITS.minDistance} maxDistance={CAMERA_LIMITS.maxDistance} />
  );
}

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
import { cameraView, player, useOfficeStore } from "./officeStore";

/** Where the camera starts: close behind the character, south-east, looking down. */
export const FOLLOW_DISTANCE = 15;
const TARGET_HEIGHT = 0.8;
/** How long the dive into a monitor takes before the chat opens. */
export const ZOOM_SECONDS = 0.9;

export function OfficeCameraRig({ layout, overview }: { layout: OfficeLayout; overview: number }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const lastFocus = useRef(0);
  const lastZoom = useRef(0);
  const backOff = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(backOff.current), []);
  const dive = useRef<{ fromEye: Vector3; toEye: Vector3; fromTarget: Vector3; toTarget: Vector3; t: number } | null>(null);
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
    // Publish the view for the minimap: where the camera stands and which way it looks.
    const lens = camera as unknown as { fov: number; aspect: number };
    cameraView.x = camera.position.x; cameraView.z = camera.position.z;
    cameraView.yaw = Math.atan2(c.target.x - camera.position.x, c.target.z - camera.position.z);
    cameraView.halfWidth = Math.atan(Math.tan(((lens.fov ?? 35) * Math.PI) / 360) * (lens.aspect ?? 1.6));
    cameraView.ready = true;
    const dt = Math.min(rawDt, 0.1);
    const store = useOfficeStore.getState();
    if (store.zoom && store.zoom.seq !== lastZoom.current) {
      lastZoom.current = store.zoom.seq;
      flight.current = null;
      dive.current = { fromEye: camera.position.clone(), toEye: new Vector3(...store.zoom.eye),
        fromTarget: c.target.clone(), toTarget: new Vector3(...store.zoom.target), t: 0 };
      // Orbit limits (minimum distance, pitch) would stop the camera short of the glass.
      c.enabled = false;
    }
    if (dive.current) {
      // Dive: eye and target travel together into the monitor, easing in at the end.
      const d = dive.current;
      d.t = Math.min(1, d.t + dt / ZOOM_SECONDS);
      const ease = 1 - (1 - d.t) ** 3;
      camera.position.lerpVectors(d.fromEye, d.toEye, ease);
      c.target.lerpVectors(d.fromTarget, d.toTarget, ease);
      camera.lookAt(c.target);
      if (d.t >= 1) {
        dive.current = null;
        // If the chat does not take over (no handler), hand the camera back a step away.
        backOff.current = setTimeout(() => {
          const away = camera.position.clone().sub(c.target).setLength(CAMERA_LIMITS.minDistance + 1);
          camera.position.copy(c.target).add(away);
          c.enabled = true;
          c.update();
        }, 1500);
      }
      return;
    }
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

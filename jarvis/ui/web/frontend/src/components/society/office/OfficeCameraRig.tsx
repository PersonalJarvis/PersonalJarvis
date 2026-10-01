/**
 * One camera for walking and for the overview. OrbitControls always own
 * rotate and zoom; in follow mode the orbit target glides after the
 * character, so zooming out turns the same view into an overview without a
 * mode switch. A right-drag pan or a fly-to leaves follow mode; moving the
 * character re-enters it. Seated at Mission Control's desk the camera glides
 * into the character's eyes (first person onto the monitors) and back out
 * when it stands up; asking for the overview or a fly-to stands it up.
 *
 * A press that turned the camera never ends in a click, and the wheel zooms
 * by how far it turned (see officeCamera.ts).
 */
import { useEffect, useRef } from "react";
import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { MOUSE, Vector3, type PerspectiveCamera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { cameraHome, CAMERA_LIMITS, DRAG_CLICK_PX, HOME_PITCH_RAD, HOME_YAW_RAD, wheelZoomSpeed } from "./officeCamera";
import { seatOf, type OfficeLayout } from "./officeLayout";
import { useLeadSeat } from "./leadSeat";
import { cameraView, officeSession, player, useOfficeStore } from "./officeStore";
import "./officeCameraRig.css";

/** Where the camera starts: close behind the character, south-east, looking down. */
export const FOLLOW_DISTANCE = 15;
const TARGET_HEIGHT = 0.8;
/** How long the dive into a monitor takes before the chat opens. */
export const ZOOM_SECONDS = 1.15;
/** Monitor screen size in metres (see LiveMonitors): the dive ends with it filling the view. */
const SCREEN_W = 0.66, SCREEN_H = 0.38;
/**
 * First person at Mission Control: eye height, how far behind the seat centre
 * the eyes sit, the height looked at, a wider lens so all three monitors fit,
 * and how long the glide in and out takes.
 */
const FIRST_PERSON = { eyeY: 1.2, back: 0.3, lookY: 1.08, lookAhead: 0.2, fov: 64, seconds: 0.7 };

/** How far in front of a screen the camera must stand for the screen to fill the view. */
export function screenFillDistance(fovDeg: number, aspect: number, width = SCREEN_W, height = SCREEN_H): number {
  const v = Math.tan((fovDeg * Math.PI) / 360);
  return Math.max(height / 2 / v, width / 2 / (v * Math.max(0.2, aspect)));
}

export function OfficeCameraRig({ layout, overview }: { layout: OfficeLayout; overview: number }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  // The element R3F and the orbit controls listen on (the canvas wrapper, which also holds the <Html> labels).
  const connected = useThree((s) => s.events.connected) as HTMLElement | undefined;
  const lastFocus = useRef(0);
  const lastZoom = useRef(0);
  const backOff = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(backOff.current), []);
  const dive = useRef<{ fromEye: Vector3; toEye: Vector3; fromTarget: Vector3; toTarget: Vector3; t: number } | null>(null);
  const flight = useRef<{ from: Vector3; to: Vector3; t: number } | null>(null);
  const desired = useRef(new Vector3());
  // First person: 0 = the normal view, 1 = the character's eyes; the view it left is kept to glide back to.
  const seated = useRef<{ blend: number; before: { position: Vector3; target: Vector3; fov: number } | null }>({ blend: 0, before: null });
  const eye = useRef(new Vector3());
  const look = useRef(new Vector3());
  const delta = useRef(new Vector3());
  const shownFirstPerson = useRef(false);

  // First person for the rest of the scene: Gigi steps aside (cameraView), and the floating labels
  // of the rest of the floor hide over the monitors (officeCameraRig.css keys on the attribute).
  const publishFirstPerson = (on: boolean) => {
    cameraView.firstPerson = on;
    if (shownFirstPerson.current === on) return;
    shownFirstPerson.current = on;
    const viewport = gl.domElement.closest(".office-viewport");
    if (on) viewport?.setAttribute("data-first-person", "");
    else viewport?.removeAttribute("data-first-person");
  };
  useEffect(() => () => {
    cameraView.firstPerson = false;
    gl.domElement.closest(".office-viewport")?.removeAttribute("data-first-person");
  }, [gl]);

  // Out of first person at once, back to the view it left: the person asked for another view.
  const leaveFirstPerson = () => {
    const c = controls.current;
    const fp = seated.current;
    const desk = layout.command;
    if (desk && useLeadSeat.getState().seated === desk.id) useLeadSeat.getState().set({ seated: null, standUp: true });
    const lensCam = camera as PerspectiveCamera;
    if (fp.before) {
      camera.position.copy(fp.before.position);
      c?.target.copy(fp.before.target);
      lensCam.fov = fp.before.fov;
      lensCam.updateProjectionMatrix();
    }
    fp.before = null;
    fp.blend = 0;
    if (c) { c.enabled = true; c.update(); }
  };

  // Remember the view when leaving: the pose before a dive, never the inside of a monitor,
  // and the view before sitting down, never the character's eyes.
  const preDive = useRef<{ position: [number, number, number]; target: [number, number, number] } | null>(null);
  useEffect(() => () => {
    const c = controls.current;
    const beforeSeat = seated.current.before;
    officeSession.camera = beforeSeat
      ? { position: beforeSeat.position.toArray() as [number, number, number], target: beforeSeat.target.toArray() as [number, number, number] }
      : preDive.current ?? (c
      ? { position: camera.position.toArray() as [number, number, number], target: c.target.toArray() as [number, number, number] }
      : null);
    officeSession.follow = useOfficeStore.getState().follow;
    // The camera belongs to the canvas and can outlive this rig: hand it back with its own lens.
    if (beforeSeat) { (camera as PerspectiveCamera).fov = beforeSeat.fov; (camera as PerspectiveCamera).updateProjectionMatrix(); }
  }, [camera]);

  // Start where the last visit left off, or close to the character.
  useEffect(() => {
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__office = { camera, controls: controls.current, gl, scene };
    const saved = officeSession.camera;
    if (saved) {
      camera.position.set(...saved.position);
      controls.current?.target.set(...saved.target);
      controls.current?.update();
      useOfficeStore.getState().setFollow(officeSession.follow);
      return;
    }
    const target = new Vector3(player.x, TARGET_HEIGHT, player.z);
    const horizontal = Math.cos(HOME_PITCH_RAD) * FOLLOW_DISTANCE;
    camera.position.set(target.x + Math.sin(HOME_YAW_RAD) * horizontal, Math.sin(HOME_PITCH_RAD) * FOLLOW_DISTANCE + TARGET_HEIGHT,
      target.z + Math.cos(HOME_YAW_RAD) * horizontal);
    controls.current?.target.copy(target);
    controls.current?.update();
    useOfficeStore.getState().setFollow(true);
  }, [camera, gl, scene, layout.spawn]);

  // "Overview": frame the whole floor and stop following.
  useEffect(() => {
    if (overview === 0) return;
    // Seated in first person the glide would keep the camera in the chair: stand up first.
    if (seated.current.blend > 0 || (layout.command && useLeadSeat.getState().seated === layout.command.id)) leaveFirstPerson();
    const home = cameraHome(layout.bounds, size.width / Math.max(1, size.height));
    flight.current = null;
    useOfficeStore.getState().setFollow(false);
    camera.position.set(...home.position);
    controls.current?.target.set(...home.target);
    controls.current?.update();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overview]);

  // A right-drag (or Ctrl-drag) pans: the person takes the camera, so stop following.
  // Shift is the sprint key, so a Shift-drag must still rotate: OrbitControls
  // turns Shift+left into a pan, which slid the view off a running character
  // while the follow kept dragging it back. Flipping the left button to "pan"
  // for that press makes the controls' own Shift inversion land on "rotate".
  // (Capture phase: this runs before the controls read the button.)
  useEffect(() => {
    const el = gl.domElement;
    const down = (event: PointerEvent) => {
      const c = controls.current;
      const sprintDrag = event.shiftKey && !event.ctrlKey && !event.metaKey;
      if (c) c.mouseButtons.LEFT = sprintDrag ? MOUSE.PAN : MOUSE.ROTATE;
      if (event.button === 2 || event.ctrlKey || event.metaKey) useOfficeStore.getState().setFollow(false);
    };
    el.addEventListener("pointerdown", down, true);
    return () => el.removeEventListener("pointerdown", down, true);
  }, [gl]);

  // A drag turns the camera and nothing else. R3F still delivers a click when
  // the press ends on the object it started on, so a small turn over the
  // Mission Control wall, a place token or an agent opened it (a handler's own
  // `delta` check only sees start to end, and many had none). The click is
  // swallowed here, in the capture phase, before R3F or a label sees it.
  // The wheel zooms by how far it turned, not a fixed step per event.
  useEffect(() => {
    const el = connected ?? gl.domElement;
    let origin: { x: number; y: number } | null = null;
    let dragged = false;
    const down = (event: PointerEvent) => {
      origin = { x: event.clientX, y: event.clientY };
      dragged = false;
      // A pinch zoom uses the same speed; only the wheel sets it per event.
      if (controls.current) controls.current.zoomSpeed = 1;
    };
    const move = (event: PointerEvent) => {
      if (origin && !dragged && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > DRAG_CLICK_PX) dragged = true;
    };
    const up = () => { origin = null; };
    const click = (event: MouseEvent) => {
      if (!dragged) return;
      dragged = false;
      event.stopImmediatePropagation();
      event.preventDefault();
    };
    const wheel = (event: WheelEvent) => {
      if (controls.current) controls.current.zoomSpeed = wheelZoomSpeed(event.deltaY, event.deltaMode);
    };
    el.addEventListener("pointerdown", down, true);
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    el.addEventListener("click", click, true);
    el.addEventListener("wheel", wheel, { capture: true, passive: true });
    return () => {
      el.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      el.removeEventListener("click", click, true);
      el.removeEventListener("wheel", wheel, true);
    };
  }, [connected, gl]);

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
    // Seated at Mission Control: glide into first person, and back out on standing up.
    const desk = layout.command;
    const fp = seated.current;
    // A fly-to (an agent picked from a list, the minimap) is a request for another view: stand up for it,
    // or it would wait until the person stands up and then fly somewhere stale.
    const flyTo = !!store.focus && store.focus.seq !== lastFocus.current;
    if (flyTo && !dive.current && (fp.blend > 0 || (!!desk && useLeadSeat.getState().seated === desk.id))) leaveFirstPerson();
    const inSeat = !!desk && useLeadSeat.getState().seated === desk.id;
    publishFirstPerson(inSeat || fp.blend > 0);
    // A monitor dive from the chair runs as usual, starting from the eyes.
    const diving = !!dive.current || (!!store.zoom && store.zoom.seq !== lastZoom.current);
    if (desk && (inSeat || fp.blend > 0) && !diving) {
      const lensCam = camera as PerspectiveCamera;
      if (inSeat && !fp.before) fp.before = { position: camera.position.clone(), target: c.target.clone(), fov: lensCam.fov };
      fp.blend = Math.min(1, Math.max(0, fp.blend + ((inSeat ? 1 : -1) * dt) / FIRST_PERSON.seconds));
      const e = fp.blend < 0.5 ? 4 * fp.blend ** 3 : 1 - (-2 * fp.blend + 2) ** 3 / 2;
      const before = fp.before;
      if (before) {
        const seat = seatOf(desk);
        const dx = Math.sin(seat.facing), dz = Math.cos(seat.facing);
        eye.current.set(seat.x - dx * FIRST_PERSON.back, FIRST_PERSON.eyeY, seat.z - dz * FIRST_PERSON.back);
        look.current.set(desk.x + dx * FIRST_PERSON.lookAhead, FIRST_PERSON.lookY, desk.z + dz * FIRST_PERSON.lookAhead);
        camera.position.lerpVectors(before.position, eye.current, e);
        c.target.lerpVectors(before.target, look.current, e);
        lensCam.fov = before.fov + (FIRST_PERSON.fov - before.fov) * e;
        lensCam.updateProjectionMatrix();
        camera.lookAt(c.target);
      }
      // Orbit limits would pull the camera out of the chair; the controls rest until the glide back ends.
      c.enabled = false;
      if (fp.blend === 0) {
        if (before) { lensCam.fov = before.fov; lensCam.updateProjectionMatrix(); }
        fp.before = null;
        c.enabled = true;
        c.update();
      }
      return;
    }
    if (store.zoom && store.zoom.seq !== lastZoom.current) {
      lastZoom.current = store.zoom.seq;
      flight.current = null;
      // A second dive within the last one's wait: that wait must not yank the camera back mid-dive.
      clearTimeout(backOff.current);
      // End squarely in front of the screen, far enough back that it exactly fills the view.
      const lens = camera as unknown as { fov: number; aspect: number };
      const reach = screenFillDistance(lens.fov ?? 35, lens.aspect ?? 1.6, ...(store.zoom.size ?? [SCREEN_W, SCREEN_H])) * 1.02;
      const [tx, ty, tz] = store.zoom.target;
      // Still set while the last dive's wait runs: the view to come back to is the one before the first dive.
      preDive.current ??= { position: camera.position.toArray() as [number, number, number], target: c.target.toArray() as [number, number, number] };
      dive.current = { fromEye: camera.position.clone(),
        toEye: new Vector3(tx + Math.sin(store.zoom.facing) * reach, ty, tz + Math.cos(store.zoom.facing) * reach),
        fromTarget: c.target.clone(), toTarget: new Vector3(tx, ty, tz), t: 0 };
      // Orbit limits (minimum distance, pitch) would stop the camera short of the glass.
      c.enabled = false;
    }
    if (dive.current) {
      // Dive: the look turns to the screen first, then the camera glides in (smooth in and out).
      const d = dive.current;
      d.t = Math.min(1, d.t + dt / ZOOM_SECONDS);
      const ease = d.t < 0.5 ? 4 * d.t ** 3 : 1 - (-2 * d.t + 2) ** 3 / 2;
      const look = Math.min(1, d.t / 0.45);
      c.target.lerpVectors(d.fromTarget, d.toTarget, 1 - (1 - look) ** 3);
      camera.position.lerpVectors(d.fromEye, d.toEye, ease);
      camera.lookAt(c.target);
      if (d.t >= 1) {
        dive.current = null;
        // If the chat does not take over (no handler), return to the view before the dive.
        backOff.current = setTimeout(() => {
          const back = preDive.current;
          if (back) { camera.position.set(...back.position); c.target.set(...back.target); }
          else camera.position.copy(c.target).add(camera.position.clone().sub(c.target).setLength(CAMERA_LIMITS.minDistance + 1));
          preDive.current = null;
          c.enabled = true;
          c.update();
        }, 2500);
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

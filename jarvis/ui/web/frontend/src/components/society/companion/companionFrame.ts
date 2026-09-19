import type { Camera, Group, Object3D } from "three";
import { advanceMotion, clearAnchor, followAnchor, planLocalRoute, recoverAt, type CompanionWorld, type Expression, type Motion, type PlayerPose, type Point } from "./kinematics";

type Ref<T> = { current: T };
interface Frame { camera: Camera; clock: { elapsedTime: number }; size: { height: number } }
export interface CompanionFrameContext {
  visible: boolean; awake: boolean; reducedMotion: boolean; failed: boolean;
  root: Ref<Group | null>; body: Ref<Group | null>; marker: Ref<HTMLButtonElement | null>;
  motion: Ref<Motion>; player: Ref<PlayerPose>; target: Ref<Point>; scratch: Ref<Point>;
  destination?: Point | null; focusTarget?: Point | null;
  world: CompanionWorld; previousRecall: Ref<number>; recallSequence: number;
  route: Ref<Point[]>; nextPlan: Ref<number>; plannedTarget: Ref<Point>;
  publishedPosition: Ref<boolean>; positionRef?: Ref<Point | null>; onPositionReady?: () => void;
  parts: { eyes: (Object3D | undefined)[]; mouth: Object3D | undefined; arms: (Object3D | undefined)[] };
  expression: Expression; invalidate: () => void;
}

/** The actual useFrame callback. Test with real Three.js transforms without a GPU. */
export function createCompanionFrame(context: CompanionFrameContext) {
  const { visible, awake, root, motion, player, target, destination, world, previousRecall, recallSequence, route, nextPlan, plannedTarget, reducedMotion, scratch, publishedPosition, positionRef, onPositionReady, focusTarget, body, parts, expression, marker, failed, invalidate } = context;
  return ({ camera, clock, size }: Frame, delta: number) => {
    if (!visible || !awake || !root.current) return;
    const state = motion.current;
    followAnchor(player.current, target.current);
    if (destination) { target.current[0] = destination[0]; target.current[1] = destination[1]; target.current[2] = destination[2]; }
    const anchorReady = clearAnchor(target.current, world);
    if (previousRecall.current !== recallSequence) {
      // Recall changes presentation only and cannot resume or complete a task.
      if (anchorReady) recoverAt(state, target.current, world);
      route.current = []; nextPlan.current = 0;
      previousRecall.current = recallSequence;
    }
    const anchorChanged = target.current.some((value, axis) => value !== plannedTarget.current[axis]);
    if (state.initialized && (clock.elapsedTime >= nextPlan.current || (reducedMotion && anchorChanged))) {
      route.current = anchorReady ? planLocalRoute(state.position, target.current, world) : [];
      nextPlan.current = clock.elapsedTime + 0.5;
      plannedTarget.current[0] = target.current[0]; plannedTarget.current[1] = target.current[1]; plannedTarget.current[2] = target.current[2];
    }
    const waypoint = route.current[0];
    if (!state.initialized || waypoint) advanceMotion(state, waypoint ?? target.current, world, delta, scratch.current);
    else { state.speed = 0; state.blocked = true; }
    if (route.current.length > 1 && waypoint && Math.hypot(waypoint[0] - state.position[0], waypoint[1] - state.position[1], waypoint[2] - state.position[2]) < 0.08) route.current.shift();
    root.current.visible = state.initialized;
    if (!state.initialized) return;
    root.current.position.fromArray(state.position);
    if (positionRef) positionRef.current = state.position;
    if (!publishedPosition.current) { publishedPosition.current = true; onPositionReady?.(); }
    const glanceAtPlayer = !reducedMotion && clock.elapsedTime % 12 > 10;
    const attention = focusTarget ?? (glanceAtPlayer ? player.current.position : null);
    const yaw = Math.atan2((attention?.[0] ?? camera.position.x) - state.position[0], (attention?.[2] ?? camera.position.z) - state.position[2]);
    const angle = Math.atan2(Math.sin(yaw - state.yaw), Math.cos(yaw - state.yaw));
    state.yaw += angle * (1 - Math.exp(-6 * Math.min(delta, 0.1)));
    root.current.rotation.y = state.yaw;
    const time = clock.elapsedTime;
    if (body.current) {
      body.current.position.y = reducedMotion ? 0 : Math.sin(time * 2) * 0.012;
      body.current.rotation.z = reducedMotion ? 0 : Math.sin(time * 1.1) * 0.015;
    }
    // Independent expression never controls motion or logical outcomes.
    const blink = reducedMotion ? 1 : (time % 5.5 > 5.32 ? 0.15 : 1);
    for (const eye of parts.eyes) if (eye) eye.scale.y = blink * (expression === "listening" ? 1.08 : expression === "error" ? 0.72 : 1);
    if (parts.mouth) parts.mouth.scale.y = expression === "speaking" && !reducedMotion ? 0.9 + Math.sin(time * 12) * 0.18 : 1;
    for (let i = 0; i < parts.arms.length; i++) {
      const arm = parts.arms[i];
      if (arm) arm.rotation.z = (i === 0 ? -1 : 1) * (expression === "approval" ? 0.45 : expression === "complete" ? 0.3 : 0.15);
    }
    if (marker.current) {
      // Marker takes over only when the physical body is genuinely too small.
      const distance = camera.position.distanceTo(root.current.position);
      const fov = "fov" in camera ? Number(camera.fov) : 50;
      const pixels = 0.4 * size.height / (2 * Math.max(distance, 0.1) * Math.tan(fov * Math.PI / 360));
      marker.current.style.display = pixels < 22 || failed ? "block" : "none";
      marker.current.dataset.gigiPixels = pixels.toFixed(1);
      marker.current.dataset.gigiBlocked = String(state.blocked);
    }
    // Reduced motion still converges after a deliberate player/camera action.
    if (reducedMotion && state.speed > 0.015 && !state.blocked) invalidate();
  };
}

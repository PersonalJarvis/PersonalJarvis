import { Group, PerspectiveCamera } from "three";
import { expect, it } from "vitest";
import { createMotion } from "./kinematics";
import { createCompanionFrame, type CompanionFrameContext } from "./companionFrame";

it("finishes a single short movement in demand mode without unrelated input", () => {
  let invalidations = 0, ready = 0;
  const context: CompanionFrameContext = {
    visible: true, awake: true, reducedMotion: true, failed: false,
    root: { current: new Group() }, body: { current: new Group() }, marker: { current: document.createElement("button") },
    motion: { current: createMotion() }, player: { current: { position: [0, 0, 0], yaw: 0 } },
    target: { current: [0, 0, 0] }, scratch: { current: [0, 0, 0] },
    world: { colliders: [], getGround: () => 0 }, previousRecall: { current: 0 }, recallSequence: 0,
    route: { current: [] }, nextPlan: { current: 0 }, plannedTarget: { current: [NaN, NaN, NaN] },
    publishedPosition: { current: false }, positionRef: { current: null }, onPositionReady: () => { ready++; },
    parts: { eyes: [], mouth: undefined, arms: [] }, expression: "idle", invalidate: () => { invalidations++; },
  };
  const callback = createCompanionFrame(context);
  const frame = { camera: new PerspectiveCamera(45), clock: { elapsedTime: 100 }, size: { height: 700 } };
  frame.camera.position.set(0, 2.5, 5);
  callback(frame, 1 / 60);
  frame.clock.elapsedTime = 100.01;
  callback(frame, 1 / 60);
  expect(context.nextPlan.current).toBeCloseTo(100.51);
  expect(context.motion.current.position).toEqual([0.95, 1.15, 0.35]);
  expect(ready).toBe(1);
  invalidations = 0;
  context.player.current.position[0] += 0.1;
  frame.clock.elapsedTime = 100.02;
  callback(frame, 1 / 60);
  expect(context.motion.current.position[0]).toBeGreaterThan(0.95);
  expect(invalidations).toBeGreaterThan(0);
  let frames = 0;
  while (invalidations > 0 && frames < 200) {
    invalidations = 0;
    frame.clock.elapsedTime += 1 / 60;
    callback(frame, 1 / 60);
    frames++;
  }
  expect(frames).toBeLessThan(200);
  expect(Math.abs(context.motion.current.position[0] - 1.05)).toBeLessThan(0.004);
  expect(invalidations).toBe(0);
  expect(ready).toBe(1);
});

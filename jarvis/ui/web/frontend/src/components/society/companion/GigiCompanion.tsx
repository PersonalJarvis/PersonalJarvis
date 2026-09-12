import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import { Group, Material, Mesh, Object3D, Texture } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import gigiUrl from "../../../../../../../../art/studies/gigi-hover-companion/exports/gigi.glb";
import { advanceMotion, clearAnchor, createMotion, expressionFor, followAnchor, planLocalRoute, recoverAt, type AssistantPresentation, type CompanionWorld, type PlayerPose, type Point } from "./kinematics";

const QUIET: AssistantPresentation = { audio: "idle", task: "idle", muted: false };

export interface GigiCompanionProps extends CompanionWorld {
  /** Remount the host with a new key when user/world/swarm context changes. */
  player: MutableRefObject<PlayerPose>;
  presentation?: AssistantPresentation;
  reducedMotion: boolean;
  awake: boolean;
  visible?: boolean;
  focusTarget?: Point | null;
  /** Validated navigation waypoint or vehicle docking anchor from the host. */
  destination?: Point | null;
  recallSequence?: number;
  positionRef?: MutableRefObject<Point | null>;
  onOpenAssistant: () => void;
  onFocus: () => void;
  markerLabel: string;
  unavailableLabel: string;
}

function disposeModel(root: Group) {
  const materials = new Set<Material>();
  const textures = new Set<Texture>();
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
    }
  });
  for (const value of textures) value.dispose();
  for (const value of materials) value.dispose();
}

/** One owned GLB per mounted companion; no cached orphan GPU resources or sessions. */
function useModel(enabled: boolean) {
  const [model, setModel] = useState<Group | null>(null);
  const [failed, setFailed] = useState(false);
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => {
    if (!enabled) { setModel(null); return; }
    const controller = new AbortController();
    let retired = false;
    let owned: Group | null = null;
    setModel(null); setFailed(false);
    void fetch(gigiUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("companion_model_unavailable");
        return response.arrayBuffer();
      })
      .then((buffer) => new GLTFLoader().parseAsync(buffer, ""))
      .then((asset) => {
        if (retired) { disposeModel(asset.scene); return; }
        owned = asset.scene;
        owned.traverse((object) => {
          if (object instanceof Mesh) { object.castShadow = true; object.receiveShadow = true; }
        });
        setModel(owned); invalidate();
      })
      .catch((error: unknown) => {
        if (retired) return;
        console.warn("Companion model could not load", error instanceof Error ? error.name : "Error");
        setFailed(true); invalidate();
      });
    return () => { retired = true; controller.abort(); if (owned) disposeModel(owned); };
  }, [enabled, invalidate]);
  return { model, failed };
}

export function GigiCompanion({ player, colliders, getGround, isLoaded, presentation = QUIET,
  reducedMotion, awake, visible = true, focusTarget, destination, recallSequence = 0,
  positionRef, onOpenAssistant, onFocus, markerLabel, unavailableLabel }: GigiCompanionProps) {
  const { model, failed } = useModel(visible);
  const motion = useRef(createMotion());
  const root = useRef<Group>(null), body = useRef<Group>(null);
  const marker = useRef<HTMLButtonElement>(null);
  const scratch = useRef<Point>([0, 0, 0]), target = useRef<Point>([0, 0, 0]);
  const previousRecall = useRef(recallSequence);
  const route = useRef<Point[]>([]), nextPlan = useRef(0);
  const world = useMemo(() => ({ colliders, getGround, isLoaded }), [colliders, getGround, isLoaded]);
  const invalidate = useThree((state) => state.invalidate);
  const parts = useMemo(() => {
    const find = (name: string): Object3D | undefined => {
      let result: Object3D | undefined;
      model?.traverse((object) => { if (object.userData.name === name) result = object; });
      return result;
    };
    return { eyes: [find("Gigi.Eye.L"), find("Gigi.Eye.R")], mouth: find("Gigi.Mouth"), arms: [find("Gigi.Arm.L"), find("Gigi.Arm.R")] };
  }, [model]);
  const expression = expressionFor(presentation);
  useEffect(() => { invalidate(); }, [expression, visible, destination, recallSequence, invalidate]);
  useEffect(() => () => { if (positionRef) positionRef.current = null; }, [positionRef]);

  useFrame(({ camera, clock, size }, delta) => {
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
    if (state.initialized && clock.elapsedTime >= nextPlan.current) {
      route.current = anchorReady ? planLocalRoute(state.position, target.current, world) : [];
      nextPlan.current = clock.elapsedTime + 0.5;
    }
    const waypoint = route.current[0];
    if (!state.initialized || waypoint) advanceMotion(state, waypoint ?? target.current, world, delta, scratch.current);
    else { state.speed = 0; state.blocked = true; }
    if (route.current.length > 1 && waypoint && Math.hypot(waypoint[0] - state.position[0], waypoint[1] - state.position[1], waypoint[2] - state.position[2]) < 0.08) route.current.shift();
    root.current.visible = state.initialized;
    if (!state.initialized) return;
    root.current.position.fromArray(state.position);
    if (positionRef) positionRef.current = state.position;
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
  });

  if (!visible) return null;
  const open = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 4) return;
    event.stopPropagation(); onOpenAssistant();
  };
  return (
    <group ref={root}>
      <group ref={body} onClick={open}>
        {model && <primitive object={model} dispose={null} />}
      </group>
      <Html center position={[0, 0.52, 0]} zIndexRange={[15, 0]}>
        <button ref={marker} type="button" className="gigi-focus-marker" onClick={(event) => { event.stopPropagation(); onFocus(); }} aria-label={markerLabel}>
          {failed ? unavailableLabel : "Gigi"}
        </button>
      </Html>
    </group>
  );
}

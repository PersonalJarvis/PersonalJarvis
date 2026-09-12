import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import { Group, Material, Mesh, Object3D, Texture } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import gigiUrl from "../../../../../../../../art/studies/gigi-hover-companion/exports/gigi.glb";
import { createMotion, expressionFor, type AssistantPresentation, type CompanionWorld, type PlayerPose, type Point } from "./kinematics";

import { createCompanionFrame } from "./companionFrame";

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
  onPositionReady?: () => void;
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
  positionRef, onPositionReady, onOpenAssistant, onFocus, markerLabel, unavailableLabel }: GigiCompanionProps) {
  const { model, failed } = useModel(visible);
  const motion = useRef(createMotion());
  const root = useRef<Group>(null), body = useRef<Group>(null);
  const marker = useRef<HTMLButtonElement>(null);
  const scratch = useRef<Point>([0, 0, 0]), target = useRef<Point>([0, 0, 0]);
  const previousRecall = useRef(recallSequence);
  const route = useRef<Point[]>([]), nextPlan = useRef(0);
  const plannedTarget = useRef<Point>([NaN, NaN, NaN]);
  const publishedPosition = useRef(false);
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
  useEffect(() => {
    if (!visible) { publishedPosition.current = false; if (positionRef) positionRef.current = null; }
  }, [visible, positionRef]);

  useFrame(createCompanionFrame({ visible, awake, root, motion, player, target, destination, world, previousRecall, recallSequence, route, nextPlan, plannedTarget, reducedMotion, scratch, publishedPosition, positionRef, onPositionReady, focusTarget, body, parts, expression, marker, failed, invalidate }));

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

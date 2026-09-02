/**
 * The building card's stage: one island building, turned by hand — its GLB
 * restyled to the world's toon ramp, so the card shows the very building
 * that stands on the map — presented on the same lobby platform the agent
 * card uses (LobbyStage, sized to the building's footprint), in the app's
 * own room. A flat sky and a grass disc read as a stock render (maintainer,
 * 2026-09-02); a stage in the theme reads as the product.
 *
 * Drag turns it and tilts the camera (never under the ground), the wheel
 * zooms, a double-click resets, and it turns slowly on its own while nobody
 * touches it (not under `prefers-reduced-motion`). The camera frames the
 * model by its bounding sphere, so a tower and a wide hall both fit.
 *
 * Mounts through `useWebglSurface` (AP-32) and renders only while on screen
 * (`useCanvasAwake`), like every other society canvas.
 */
import { Component, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ErrorInfo, PointerEvent as ReactPointerEvent, ReactNode, WheelEvent } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useGLTF } from "@react-three/drei";
import { useReducedMotion } from "framer-motion";
import * as THREE from "three";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";

import memoryHouseUrl from "@/assets/society/world/kit/memory-house.glb";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { KIT_URLS, restyle, type KitId } from "../world/KitBuilding";
import { createToonRamp } from "../world/worldMaterials";
import { LobbyStage } from "./LobbyStage";

/** Every model a card can show: the five kit hubs and the Memory House. */
export type BuildingModelId = KitId | "memory-house";

const MODEL_URLS: Record<BuildingModelId, string> = {
  ...KIT_URLS,
  "memory-house": memoryHouseUrl,
};

const PITCH_MIN = (6 * Math.PI) / 180;
const PITCH_MAX = (70 * Math.PI) / 180;
const ZOOM_MIN = 0.6;
const ZOOM_MAX = 2.2;
const FOV = 30;
/** Slow idle turn, radians per second. */
const IDLE_TURN = 0.12;
const REST_VIEW = { yaw: -0.7, pitch: 0.42, zoom: 1 };

interface OrbitState {
  yaw: number;
  pitch: number;
  zoom: number;
}

export interface BuildingViewerProps {
  model: BuildingModelId;
  className?: string;
}

export function BuildingViewer({ model, className }: BuildingViewerProps) {
  const t = useT();
  const hostRef = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(hostRef);
  const awake = useCanvasAwake(hostRef);
  const reduced = useReducedMotion() ?? false;
  const orbit = useRef<OrbitState>({ ...REST_VIEW });
  const [orbitTick, setOrbitTick] = useState(0);
  const drag = useRef<{ x: number; y: number; id: number } | null>(null);
  // The idle turn pauses while a hand is on it and for a moment after.
  const touchedAt = useRef(0);

  const onPointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
    touchedAt.current = performance.now();
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);
  const onPointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    d.x = e.clientX;
    d.y = e.clientY;
    const o = orbit.current;
    o.yaw += dx * 0.01;
    o.pitch = Math.max(PITCH_MIN, Math.min(PITCH_MAX, o.pitch + dy * 0.008));
    touchedAt.current = performance.now();
    setOrbitTick((n) => n + 1);
  }, []);
  const onPointerUp = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === e.pointerId) drag.current = null;
  }, []);
  const onWheel = useCallback((e: WheelEvent<HTMLDivElement>) => {
    const o = orbit.current;
    o.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, o.zoom * (1 - e.deltaY * 0.0012)));
    touchedAt.current = performance.now();
    setOrbitTick((n) => n + 1);
  }, []);
  const onDoubleClick = useCallback(() => {
    orbit.current = { ...REST_VIEW };
    setOrbitTick((n) => n + 1);
  }, []);

  const frameloop = awake && !reduced ? "always" : "demand";

  return (
    <div
      ref={hostRef}
      data-testid="building-viewer"
      className={cn(
        "relative h-full min-h-0 w-full select-none overflow-hidden touch-none",
        drag.current ? "cursor-grabbing" : "cursor-grab",
        className,
      )}
      // The app's room around the lobby, as on the agent card (v4 tokens).
      style={{
        background:
          "radial-gradient(120% 90% at 50% 40%, hsl(var(--secondary)) 0%, hsl(var(--background)) 72%)",
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onWheel={onWheel}
      onDoubleClick={onDoubleClick}
    >
      <ViewerErrorBoundary fallback={<Unavailable label={t("society.world.card_unavailable")} />}>
        <Canvas
          key={generation}
          dpr={1}
          flat
          frameloop={frameloop}
          gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
          camera={{ fov: FOV, near: 0.5, far: 400, position: [0, 20, 60] }}
          onCreated={({ gl }) => {
            gl.setClearColor(0x000000, 0);
          }}
        >
          <Suspense fallback={null}>
            <BuildingScene
              model={model}
              orbit={orbit}
              orbitTick={orbitTick}
              idle={!reduced && awake}
              touchedAt={touchedAt}
            />
          </Suspense>
          <HostSizeSync />
        </Canvas>
      </ViewerErrorBoundary>
      <p className="pointer-events-none absolute inset-x-0 bottom-2 text-center text-xs text-muted-foreground">
        {reduced ? t("society.world.card_reduced_motion") : t("society.world.card_drag_hint")}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the scene: the restyled GLB on a patch of grass, camera framed by its bounds
// ---------------------------------------------------------------------------

function BuildingScene({
  model,
  orbit,
  orbitTick,
  idle,
  touchedAt,
}: {
  model: BuildingModelId;
  orbit: { current: OrbitState };
  orbitTick: number;
  idle: boolean;
  touchedAt: { current: number };
}) {
  const { scene } = useGLTF(MODEL_URLS[model]);
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  const groupRef = useRef<THREE.Group>(null);
  const ramp = useMemo(createToonRamp, []);
  const instance = useMemo(() => {
    const clone = SkeletonUtils.clone(scene);
    restyle(clone, ramp);
    return clone;
  }, [scene, ramp]);

  useEffect(
    () => () => {
      instance.traverse((o) => {
        if (o instanceof THREE.Mesh) (o.material as THREE.Material).dispose();
      });
      ramp.dispose();
    },
    [instance, ramp],
  );

  // The building's footprint and reach decide the ground patch and the camera.
  const bounds = useMemo(() => {
    const box = new THREE.Box3().setFromObject(instance);
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.x, size.z) * 0.5;
    return { size, centre, radius, top: box.max.y, bottom: box.min.y };
  }, [instance]);

  const place = useCallback(() => {
    const o = orbit.current;
    const group = groupRef.current;
    if (group) group.rotation.y = o.yaw;
    // The sphere that holds the whole model, seen through the vertical fov.
    const sphere = Math.hypot(bounds.radius, bounds.size.y * 0.5) * 1.12;
    const distance = (sphere / Math.sin((FOV * Math.PI) / 360)) / o.zoom;
    const mid = bounds.size.y * 0.42;
    camera.position.set(0, mid + Math.sin(o.pitch) * distance, Math.cos(o.pitch) * distance);
    camera.lookAt(0, mid, 0);
    camera.updateProjectionMatrix();
  }, [bounds, camera, orbit]);

  useEffect(() => {
    place();
    invalidate();
  }, [place, orbitTick, invalidate]);

  useFrame((_, dt) => {
    if (!idle) return;
    if (performance.now() - touchedAt.current < 2500) return;
    orbit.current.yaw += IDLE_TURN * Math.min(dt, 0.1);
    place();
  });

  return (
    <>
      {/* The building turns; the lobby around it stays put, lights included. */}
      <group ref={groupRef}>
        <primitive object={instance} position={[-bounds.centre.x, -bounds.bottom, -bounds.centre.z]} />
      </group>
      <LobbyStage heightM={bounds.size.y} platformR={bounds.radius * 1.3} paused={!idle} />
    </>
  );
}

/** Keeps the renderer sized to its container (see AgentFigureViewer for why). */
function HostSizeSync() {
  const gl = useThree((s) => s.gl);
  const setSize = useThree((s) => s.setSize);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    const wrapper = gl.domElement.parentElement;
    if (!wrapper || typeof ResizeObserver === "undefined") return;
    const sync = () => {
      const rect = wrapper.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setSize(rect.width, rect.height);
        invalidate();
      }
    };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(wrapper);
    return () => observer.disconnect();
  }, [gl, setSize, invalidate]);
  return null;
}

function Unavailable({ label }: { label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <p className="max-w-[28ch] text-center text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

class ViewerErrorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.warn("[society] building viewer fell back:", error.message, info.componentStack);
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export default BuildingViewer;

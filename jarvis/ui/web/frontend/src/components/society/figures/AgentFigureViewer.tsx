/**
 * The model card's centre column: one agent's figure, rendered through the
 * same pixelated pass the island uses, turned by hand.
 *
 * Drag turns the figure and tilts the camera — all the way to a view from
 * above and one from below — the wheel zooms, a double-click resets. The
 * figure idles; on open it waves once. Under `prefers-reduced-motion` it
 * holds its pose and still turns under the pointer (a person asked for
 * that). Every canvas here mounts through `useWebglSurface` (AP-32: the
 * context is handed back on unmount and rebuilt when the browser takes it)
 * and renders only while on screen (`useCanvasAwake`, never `document.hidden`).
 *
 * Without a recipe or without WebGL the column shows the palette tile — a
 * declared fallback, not an empty box (docs/agent-society/character-pipeline.md §9).
 */
import { Component, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ErrorInfo, PointerEvent as ReactPointerEvent, ReactNode, WheelEvent } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPixelatedPass } from "three/examples/jsm/postprocessing/RenderPixelatedPass.js";

import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { assembleFigure, playClip, type AssembledFigure } from "./assembleFigure";
import { recipeKey, resolvePalette, type FigureRecipe } from "./figureRecipe";
import { figureAssetFor } from "./figureRegistry";
import { LobbyStage } from "./LobbyStage";
import { useFigureAssets } from "./useFigureAssets";

/** Target resolution the pixel pass renders the column at (docs §9.3). */
const PIXEL_TARGET_WIDTH = 240;

/** Camera pitch limit upwards: straight down is +90°; stop short of the pole. */
const PITCH_LIMIT = (80 * Math.PI) / 180;
/**
 * How far the camera may dip below eye level. The figure stands on a lobby
 * platform (LobbyStage); a camera under that platform sees its base cap and
 * nothing else, so "from below" is the low, heroic angle — a little under
 * the platform's top — never a view from beneath the floor.
 */
const PITCH_FLOOR = (12 * Math.PI) / 180;

const ZOOM_MIN = 0.55;
const ZOOM_MAX = 2.4;

/** The look a fresh open starts from: a little above eye level, three-quarter turned. */
const REST_VIEW = { yaw: -0.45, pitch: 0.1, zoom: 1 };

interface OrbitState {
  yaw: number;
  pitch: number;
  zoom: number;
}

export interface AgentFigureViewerProps {
  recipe: FigureRecipe | null;
  /** The clip to hold; the viewer waves once on mount and returns to it. */
  clip?: string;
  /** Skip the opening wave (the creator re-renders often). */
  quiet?: boolean;
  className?: string;
}

export function AgentFigureViewer({ recipe, clip = "idle", quiet = false, className }: AgentFigureViewerProps) {
  const t = useT();
  const hostRef = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(hostRef);
  const awake = useCanvasAwake(hostRef);
  const reduced = useReducedMotion() ?? false;
  const orbit = useRef<OrbitState>({ ...REST_VIEW });
  // Bumped on every pointer change so a demand-mode canvas draws the new view.
  const [orbitTick, setOrbitTick] = useState(0);
  const drag = useRef<{ x: number; y: number; id: number } | null>(null);

  const asset = recipe ? figureAssetFor(recipe) : null;
  const palette = useMemo(() => resolvePalette(recipe), [recipe]);
  const heightM = recipe?.heightM ?? asset?.defaultHeightM ?? 1.75;
  const look = recipe ? recipeKey(recipe) : "";

  const onPointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
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
    o.yaw += dx * 0.012;
    o.pitch = Math.max(-PITCH_FLOOR, Math.min(PITCH_LIMIT, o.pitch + dy * 0.01));
    setOrbitTick((n) => n + 1);
  }, []);
  const onPointerUp = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === e.pointerId) drag.current = null;
  }, []);
  const onWheel = useCallback((e: WheelEvent<HTMLDivElement>) => {
    const o = orbit.current;
    o.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, o.zoom * (1 - e.deltaY * 0.0012)));
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
      data-testid="agent-figure-viewer"
      className={cn(
        "society-figure-stage relative h-full min-h-0 w-full select-none overflow-hidden touch-none",
        drag.current ? "cursor-grabbing" : "cursor-grab",
        className,
      )}
      // The room around the lobby is the app's own room (v4 tokens): a dark
      // figure is separated from it by the lobby's accent rim light, not by
      // painting the stage a warm colour of its own (LobbyStage).
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
      {asset && recipe ? (
        <FigureErrorBoundary fallback={<PaletteTile palette={palette} label={t("society.figure.unavailable")} />}>
          <Canvas
            key={generation}
            dpr={1}
            frameloop={frameloop}
            gl={{ antialias: false, alpha: true, powerPreference: "low-power" }}
            camera={{ fov: 26, near: 0.1, far: 40, position: [0, heightM * 0.55, heightM * 2.5] }}
            onCreated={({ gl }) => {
              gl.setClearColor(0x000000, 0);
            }}
          >
            <Suspense fallback={null}>
              <FigureScene
                recipe={recipe}
                look={look}
                palette={palette}
                heightM={heightM}
                clip={clip}
                quiet={quiet}
                paused={reduced || !awake}
                orbit={orbit}
                orbitTick={orbitTick}
              />
            </Suspense>
            <HostSizeSync />
            <PixelPass />
          </Canvas>
        </FigureErrorBoundary>
      ) : (
        <PaletteTile palette={palette} label={t("society.figure.no_figure")} />
      )}
      <p className="pointer-events-none absolute inset-x-0 bottom-2 text-center text-xs text-muted-foreground">
        {reduced ? t("society.figure.reduced_motion") : t("society.figure.drag_hint")}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the scene: figure, lights, ground blob, camera rig
// ---------------------------------------------------------------------------

interface SceneProps {
  recipe: FigureRecipe;
  look: string;
  palette: ReturnType<typeof resolvePalette>;
  heightM: number;
  clip: string;
  quiet: boolean;
  paused: boolean;
  orbit: { current: OrbitState };
  orbitTick: number;
}

function FigureScene({ recipe, look, palette, heightM, clip, quiet, paused, orbit, orbitTick }: SceneProps) {
  const assets = useFigureAssets(recipe);
  const gltf = assets.base;
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  const groupRef = useRef<THREE.Group>(null);
  const figureRef = useRef<AssembledFigure | null>(null);

  // One assembled figure per look; the previous one is disposed first.
  useEffect(() => {
    const figure = assembleFigure(
      gltf,
      palette,
      heightM,
      assets.parts.map((p) => p.gltf),
    );
    figureRef.current = figure;
    const group = groupRef.current;
    if (figure && group) group.add(figure.root);
    if (figure) {
      if (!quiet && figure.actions.wave) playClip(figure, "wave", clip);
      else playClip(figure, clip, clip, 0);
      if (paused) figure.mixer.update(0.001);
    }
    // The first frames after the figure lands: the pixel pass and the size
    // sync may still be settling, so ask for a few draws, not one.
    invalidate();
    let frames = 0;
    const kick = () => {
      invalidate();
      if (++frames < 6) requestAnimationFrame(kick);
    };
    requestAnimationFrame(kick);
    return () => {
      if (figure && group) group.remove(figure.root);
      figure?.dispose();
      figureRef.current = null;
    };
    // `look` stands in for the palette object identity; heightM and the
    // gltf are part of the same identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gltf, look, heightM]);

  useEffect(() => {
    const figure = figureRef.current;
    if (figure) playClip(figure, clip, clip);
    invalidate();
  }, [clip, invalidate]);

  // The camera orbits the figure's middle: yaw turns the figure itself (so
  // the light stays put), pitch and zoom move the camera.
  useEffect(() => {
    const group = groupRef.current;
    const o = orbit.current;
    if (group) group.rotation.y = o.yaw;
    // 2.4 heights of distance: larger than the 2.6 it opened with (maintainer:
    // "about a fifth"), with the crown and the hem still inside the frame.
    const mid = heightM * 0.5;
    const distance = (heightM * 2.5) / o.zoom;
    camera.position.set(0, mid + Math.sin(o.pitch) * distance, Math.cos(o.pitch) * distance);
    camera.lookAt(0, mid, 0);
    camera.updateProjectionMatrix();
    invalidate();
  }, [orbitTick, heightM, camera, invalidate, orbit]);

  useFrame((_, dt) => {
    if (paused) return;
    figureRef.current?.mixer.update(Math.min(dt, 0.1));
  });

  return (
    <>
      <group ref={groupRef} />
      {/* The room the agent spawns into, lights included (LobbyStage). */}
      <LobbyStage heightM={heightM} paused={paused} />
    </>
  );
}

/**
 * Keeps the renderer sized to its container. R3F measures its wrapper once
 * on mount and then through its own ResizeObserver; inside a dialog that
 * mounts mid-animation the first measure has come back 300×150 and the
 * observer never corrected it, so the figure was drawn into a thumbnail.
 * Observing the wrapper ourselves and pushing the size into the store is
 * cheap insurance.
 */
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
        // A demand-driven loop draws nothing on its own after a resize.
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

/** The island's look, in the card: the scene through a low-resolution, nearest-filtered target. */
function PixelPass() {
  const { gl, scene, camera, size } = useThree();
  const composer = useMemo(() => {
    const c = new EffectComposer(gl);
    const pass = new RenderPixelatedPass(2, scene, camera, { normalEdgeStrength: 0.3, depthEdgeStrength: 0.4 });
    c.addPass(pass);
    c.addPass(new OutputPass());
    return { composer: c, pass };
  }, [gl, scene, camera]);

  useEffect(() => {
    const pixelSize = Math.max(2, Math.round(size.width / PIXEL_TARGET_WIDTH));
    composer.pass.setPixelSize(pixelSize);
    composer.composer.setSize(size.width, size.height);
  }, [composer, size]);

  useEffect(() => () => composer.composer.dispose(), [composer]);

  useFrame(() => composer.composer.render(), 1);
  return null;
}

// ---------------------------------------------------------------------------
// fallbacks
// ---------------------------------------------------------------------------

/** The declared stand-in: the agent's colours as a tile, never an empty box. */
function PaletteTile({ palette, label }: { palette: ReturnType<typeof resolvePalette>; label: string }) {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3 p-6">
      <div
        aria-hidden
        className="flex h-24 w-24 items-end justify-center rounded-2xl"
        style={{ background: `linear-gradient(160deg, ${palette.primary}, ${palette.primary_shade})` }}
      >
        <div className="mb-3 h-9 w-9 rounded-full" style={{ background: palette.skin, boxShadow: `0 -10px 0 0 ${palette.hair}` }} />
      </div>
      <p className="max-w-[26ch] text-center text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

class FigureErrorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // A WebGL that cannot be created is a stated fallback, not a silent one.
    console.warn("[society] figure viewer fell back to the palette tile:", error.message, info.componentStack);
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export default AgentFigureViewer;

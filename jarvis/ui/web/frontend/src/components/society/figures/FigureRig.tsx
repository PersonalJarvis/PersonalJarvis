/**
 * One agent's figure as an R3F node — the piece the island's walkers and the
 * model card share (docs/agent-society/character-pipeline.md §9.2).
 *
 * It renders INSIDE someone else's Canvas and owns no WebGL context; the
 * mount (WorldStage, AgentFigureViewer) is what goes through `useWebglSurface`.
 *
 * Driving it: `drive.current` carries the wanted clip and the ground speed
 * every frame, without a React render on the way (the walker sim writes it
 * in `useFrame`). The rig picks walk/run by the figure's own stride
 * (`extras.clips.*.stride_m`), so the feet plant at any speed and any size.
 */
import { Suspense, useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type { Group } from "three";

import { assembleFigure, playClip, type AssembledFigure } from "./assembleFigure";
import { recipeKey, resolvePalette, type FigureRecipe } from "./figureRecipe";
import { figureAssetFor } from "./figureRegistry";
import { preloadFigureAssets, useFigureAssets } from "./useFigureAssets";

export type FigureMode = "idle" | "walk" | "work" | "talk" | "sit" | "sleep" | "celebrate" | "wave";

export interface FigureDrive {
  mode: FigureMode;
  /** Ground speed in m/s; only read while `mode === "walk"`. */
  speed: number;
}

/** Play the walk this much slower/faster at most before switching clips (§6.3). */
const TIME_SCALE_MIN = 0.7;
const TIME_SCALE_MAX = 1.4;

export interface FigureRigProps {
  recipe: FigureRecipe;
  drive: { current: FigureDrive };
  paused: boolean;
  /** Rendered height; the recipe's or the base default when absent. */
  heightM?: number;
  onReady?: (figure: AssembledFigure) => void;
}

export function FigureRig(props: FigureRigProps) {
  if (!figureAssetFor(props.recipe)) return null;
  return (
    <Suspense fallback={null}>
      <LoadedRig {...props} />
    </Suspense>
  );
}

function LoadedRig({ recipe, drive, paused, heightM, onReady }: FigureRigProps) {
  const assets = useFigureAssets(recipe);
  const height = heightM ?? recipe.heightM ?? assets.defaultHeightM;
  const groupRef = useRef<Group>(null);
  const figureRef = useRef<AssembledFigure | null>(null);
  const playing = useRef<{ clip: string; timeScale: number }>({ clip: "", timeScale: 1 });
  const look = recipeKey(recipe);

  useEffect(() => {
    const palette = resolvePalette(recipe);
    const figure = assembleFigure(
      assets.base,
      palette,
      height,
      assets.parts.map((p) => p.gltf),
      assets.clips,
    );
    const group = groupRef.current;
    figureRef.current = figure;
    playing.current = { clip: "", timeScale: 1 };
    if (figure && group) group.add(figure.root);
    if (figure) onReady?.(figure);
    return () => {
      if (figure && group) group.remove(figure.root);
      figure?.dispose();
      figureRef.current = null;
    };
    // `look` is the recipe's identity (base, parts, palette, height).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets.base, look, height]);

  useFrame((_, dt) => {
    const figure = figureRef.current;
    if (!figure) return;
    const d = drive.current;
    let clip: string = d.mode;
    let timeScale = 1;
    if (d.mode === "walk" && d.speed <= 0.05) clip = "idle";
    else if (d.mode === "walk") {
      const walk = figure.extras.clips.walk;
      const run = figure.extras.clips.run;
      const nominal = (walk?.stride_m ?? 0) > 0 ? ((walk.stride_m ?? 0) * figure.scale) / walk.duration : 0;
      const walkScale = nominal > 0 ? d.speed / nominal : 1;
      if (run && run.stride_m && walkScale > TIME_SCALE_MAX) {
        const runNominal = (run.stride_m * figure.scale) / run.duration;
        clip = "run";
        timeScale = clamp(d.speed / runNominal, TIME_SCALE_MIN, TIME_SCALE_MAX);
      } else {
        timeScale = Math.max(0, Math.min(TIME_SCALE_MAX, walkScale));
      }
      if (d.speed <= 0.05) clip = "idle";
    }
    if (!figure.actions[clip]) clip = "idle";
    const current = playing.current;
    if (current.clip !== clip) {
      playClip(figure, clip, "idle");
      current.clip = clip;
    }
    if (Math.abs(current.timeScale - timeScale) > 0.01) {
      figure.actions[clip]?.setEffectiveTimeScale(timeScale);
      current.timeScale = timeScale;
    }
    if (!paused) figure.mixer.update(Math.min(dt, 0.1));
  });

  return <group ref={groupRef} />;
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

export const preloadFigures = preloadFigureAssets;

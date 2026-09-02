/**
 * The island, mounted — the world view of the Jarvis Agents section
 * (MASTERPLAN §4.1); look and behaviour per world-masterplan-v2.md.
 *
 * Discipline every world canvas owes (society README):
 *  - the R3F Canvas mounts through `useWebglSurface` (AP-32: context released
 *    on unmount, rebuilt after a loss, degraded after two losses);
 *  - the render loop runs only while the host is on screen, gated by an
 *    IntersectionObserver (`useCanvasAwake`) — never `document.hidden`;
 *  - `prefers-reduced-motion` freezes the island (demand-driven frames, no
 *    wander, no water drift, no clouds) instead of animating it;
 *  - no WebGL at all → an honest fallback that points to the ledger.
 *
 * Everything inside the canvas wears the world's own branding (§4.3); the
 * switch the section hands in for the top-right corner is app chrome.
 */
import "@fontsource/pixelify-sans/500.css";
import "@fontsource/pixelify-sans/600.css";
import "./world.css";

import { useCallback, useRef, useState, type ReactNode } from "react";
import { Canvas } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";

import { useLocaleChunk, useT } from "@/i18n";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useSocietyRoster } from "../data";
import { SAMPLE_ROSTER } from "../mockRoster";
import { useCameraStore } from "./cameraStore";
import { Clouds } from "./Clouds";
import { Landmarks } from "./Landmarks";
import { PlaceLabels } from "./PlaceLabels";
import { Shadowed } from "./Shadowed";
import { SunRig } from "./SunRig";
import { Terrain, Water } from "./Terrain";
import { Trees } from "./Trees";
import { Village } from "./Village";
import { Walkers } from "./Walkers";
import { useWorldControls } from "./useWorldControls";
import { WorldCameraRig } from "./WorldCameraRig";
import { WorldComposer } from "./WorldComposer";
import { WorldHud } from "./WorldHud";
import { WorldKitProvider } from "./WorldKit";
import { cameraOffset } from "./worldCamera";
import { SKY } from "./worldPalette";
import { useWorldSettings } from "./worldSettings";

export interface WorldStageProps {
  /** App-chrome content for the HUD's top-right corner (the World / Ledger switch). */
  topRight?: ReactNode;
  /** Where the fallback sends someone whose window cannot draw 3D. */
  onOpenLedger: () => void;
  /** A figure was clicked (or the selection cleared). The model card hooks in here. */
  onSelectAgent?: (agentId: string | null) => void;
}

const CAMERA_START = cameraOffset();

export function WorldStage({ topRight, onOpenLedger, onSelectAgent }: WorldStageProps) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const hostRef = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(hostRef);
  const awake = useCanvasAwake(hostRef);
  const reduced = useReducedMotion() ?? false;
  const webgl = useWebglSupported();
  const grain = useWorldSettings((s) => s.grain);
  const shadows = useWorldSettings((s) => s.shadows);
  const roster = useSocietyRoster();
  const agents = roster.data ?? [];
  const sample = roster.data === SAMPLE_ROSTER;
  const [selected, setSelected] = useState<string | null>(null);

  useWorldControls(hostRef, webgl);

  const select = useCallback(
    (agentId: string | null) => {
      setSelected(agentId);
      onSelectAgent?.(agentId);
    },
    [onSelectAgent],
  );

  if (!webgl) {
    return (
      <div className="sw-fallback bg-background">
        <div className="max-w-sm space-y-3">
          <h3 className="font-display text-base font-semibold text-foreground">
            {t("society.world.webgl_missing_title")}
          </h3>
          <p className="text-sm text-muted-foreground">{t("society.world.webgl_missing_body")}</p>
          <button
            type="button"
            onClick={onOpenLedger}
            className="inline-flex h-8 items-center rounded-md bg-secondary px-3 text-sm font-medium text-foreground hover:bg-muted"
          >
            {t("society.world.open_ledger")}
          </button>
        </div>
      </div>
    );
  }

  const frameloop = !awake ? "never" : reduced ? "demand" : "always";

  return (
    <div className="relative h-full min-h-0 w-full">
      <div
        ref={hostRef}
        className="sw-stage"
        data-grain={grain > 0 ? grain : undefined}
        tabIndex={0}
        role="application"
        aria-label={ready ? t("society.world.mode_world") : undefined}
      >
        <Canvas
          key={generation}
          orthographic
          camera={{ position: CAMERA_START, near: 1, far: 1200, zoom: 1 }}
          dpr={1}
          flat
          shadows={shadows ? "soft" : false}
          gl={{ antialias: grain === 0, alpha: false, powerPreference: "high-performance", stencil: false }}
          frameloop={frameloop}
          onPointerMissed={() => {
            if (!useCameraStore.getState().dragging) select(null);
          }}
        >
          <color attach="background" args={[SKY.clear]} />
          <hemisphereLight args={[SKY.hemiSky, SKY.hemiGround, SKY.hemiIntensity]} />
          <SunRig />
          <WorldKitProvider>
            <Terrain />
            <Water paused={reduced} />
            <Shadowed>
              <Village paused={reduced} />
              <Landmarks paused={reduced} />
              <Trees />
              <Walkers agents={agents} paused={reduced} selectedId={selected} onSelect={select} />
            </Shadowed>
            <Clouds paused={reduced} />
            {ready && <PlaceLabels />}
          </WorldKitProvider>
          <WorldCameraRig />
          <WorldComposer />
        </Canvas>
      </div>
      {ready && (
        <WorldHud agents={agents} sample={sample} awake={awake} reducedMotion={reduced} topRight={topRight} />
      )}
    </div>
  );
}

export default WorldStage;

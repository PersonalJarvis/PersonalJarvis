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

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Canvas } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";

import { useLocaleChunk, useT } from "@/i18n";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useSocietyRoster } from "../data";
import { syncBuildingPoses, useBuildingPoses } from "./buildingPoses";
import { useCameraStore } from "./cameraStore";
import { Clouds } from "./Clouds";
import { FoundryDrawer } from "./FoundryDrawer";
import { Landmarks } from "./Landmarks";
import { MemoryDrawer } from "./MemoryDrawer";
import { PlaceLabels } from "./PlaceLabels";
import { HubDrawer } from "./HubDrawer";
import { QuestBoardDrawer } from "./QuestBoardDrawer";
import { RetirementScene } from "./RetirementScene";
import { ACTIVE_STATES } from "./questBoard";
import { useSocietyQuests } from "./questsData";
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
import { foundryWalkOut, isKitPlace, type KitPlace, type PlaceId } from "./islandLayout";
import { FOCUS_GRACE_MS, useSpawnStore } from "./spawnStore";

/** Only kit hubs open a drawer; other places are scenery. */
function asHub(place: PlaceId): KitPlace | null {
  // Not "is it in the house ring" — the foundry crowns the mountain instead.
  return isKitPlace(place) ? place : null;
}
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
  /**
   * A building with a card of its own was clicked (the kit hubs, the Memory
   * House). When set, the building card opens instead of the drawer — the
   * society view hooks in here; standalone, the drawers stay.
   */
  onSelectPlace?: (place: PlaceId) => void;
}

const CAMERA_START = cameraOffset();

/** Zoom step the island snaps to when it shows a newborn leaving the foundry. */
const SPAWN_ZOOM = 1;

export function WorldStage({ topRight, onOpenLedger, onSelectAgent, onSelectPlace }: WorldStageProps) {
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
  const agents = roster.data?.agents ?? [];
  const sample = roster.data?.sample ?? true;
  const [selected, setSelected] = useState<string | null>(null);
  const [openHub, setOpenHub] = useState<KitPlace | null>(null);
  const [questOpen, setQuestOpen] = useState(false);
  const quests = useSocietyQuests(webgl);
  const questsOnBoard = (quests.data ?? []).filter((q) => ACTIVE_STATES.has(q.state)).length;
  const [memoryOpen, setMemoryOpen] = useState(false);

  useWorldControls(hostRef, webgl);

  // An agent created in this window: look at the foundry, so its maker sees
  // the figure come out instead of it happening on a mountain off screen.
  // The frame holds the portal and the whole conveyor, so the walk reads.
  const focusRequestedMs = useSpawnStore((s) => s.focusRequestedMs);
  useEffect(() => {
    if (focusRequestedMs === 0) return;
    useSpawnStore.getState().clearFocus();
    if (Date.now() - focusRequestedMs > FOCUS_GRACE_MS) return; // stale request
    const walk = foundryWalkOut();
    useCameraStore
      .getState()
      .focusOn((walk.from[0] + walk.to[0]) / 2, (walk.from[1] + walk.to[1]) / 2, SPAWN_ZOOM);
  }, [focusRequestedMs]);
  // The viewer's turned buildings go into the island before the first frame.
  useEffect(syncBuildingPoses, []);

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
            const poses = useBuildingPoses.getState();
            if (useCameraStore.getState().dragging || poses.rotating) return;
            select(null);
            poses.select(null);
          }}
        >
          <color attach="background" args={[SKY.clear]} />
          <hemisphereLight args={[SKY.hemiSky, SKY.hemiGround, SKY.hemiIntensity]} />
          <SunRig />
          <WorldKitProvider>
            <Terrain />
            <Water paused={reduced} />
            <Shadowed>
              <Village
                paused={reduced}
                questOpen={questOpen}
                onQuestClick={() => {
                  setOpenHub(null);
                  setMemoryOpen(false);
                  setQuestOpen((cur) => !cur);
                }}
              />
              <Landmarks
                paused={reduced}
                onHubClick={(p) => {
                  setQuestOpen(false);
                  if (onSelectPlace && (isKitPlace(p) || p === "archive")) {
                    setOpenHub(null);
                    setMemoryOpen(false);
                    onSelectPlace(p);
                    return;
                  }
                  if (p === "archive") {
                    setOpenHub(null);
                    setMemoryOpen((cur) => !cur);
                    return;
                  }
                  setMemoryOpen(false);
                  const hub = asHub(p);
                  if (hub) setOpenHub((cur) => (cur === hub ? null : hub));
                }}
                openHub={openHub}
                memoryOpen={memoryOpen}
                atMemory={agents.filter((a) => a.checkpoint === "archive").length}
              />
              <Trees />
              <Walkers agents={agents} paused={reduced} selectedId={selected} onSelect={select} />
              {/* A retirement drives the walkers above; it must mount after them. */}
              <RetirementScene paused={reduced} />
            </Shadowed>
            {shadows && <Clouds paused={reduced} />}
            {ready && <PlaceLabels />}
          </WorldKitProvider>
          <WorldCameraRig />
          <WorldComposer />
        </Canvas>
      </div>
      {ready && (
        <WorldHud
          agents={agents}
          sample={sample}
          awake={awake}
          reducedMotion={reduced}
          topRight={topRight}
          questsOnBoard={questsOnBoard}
          questsOpen={questOpen}
          onOpenQuests={() => {
            setOpenHub(null);
            setMemoryOpen(false);
            setQuestOpen((cur) => !cur);
          }}
        />
      )}
      {/* Every hub lists what it stands for; the foundry also BUILDS, so it
          has its own drawer with the creator in it. */}
      {ready && openHub && openHub !== "foundry" && (
        <HubDrawer hub={openHub} onClose={() => setOpenHub(null)} />
      )}
      {ready && openHub === "foundry" && (
        <FoundryDrawer onClose={() => setOpenHub(null)} onSelectAgent={select} />
      )}
      {ready && memoryOpen && <MemoryDrawer onClose={() => setMemoryOpen(false)} />}
      {ready && questOpen && <QuestBoardDrawer onClose={() => setQuestOpen(false)} onOpenAgent={select} />}
    </div>
  );
}

export default WorldStage;

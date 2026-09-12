import { Component, Suspense, useCallback, useRef, useState, type ReactNode } from "react";
import { Canvas } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useLocaleChunk, useT } from "@/i18n";
import { CAMERA_FOV, fitWorldBounds } from "./camera";
import { MarsScene, type CameraMode } from "./MarsScene";
import { WORLD, WORLD_BOUNDS } from "./world";
import { MarsBackgroundControl } from "./MarsBackgroundControl";
import { useEventStore } from "@/store/events";
import { useCompanionPresentation } from "../companion/useCompanionPresentation";
import { readCompanionVisible, writeCompanionVisible } from "../companion/preferences";
import "./mars.css";

export interface MarsWorldStageProps {
  topRight?: ReactNode;
  onOpenLedger: () => void;
  onSelectAgent?: (id: string | null) => void;
  stationPanel?: ReactNode;
  onOpenStation?: () => void;
}

class RenderBoundary extends Component<{ children: ReactNode; fallbackText: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Mars prototype renderer unavailable", error); }
  render() {
    return this.state.failed
      ? <div className="mars-render-fallback" role="status">{this.props.fallbackText}</div>
      : this.props.children;
  }
}

const INITIAL_CAMERA = fitWorldBounds(WORLD_BOUNDS, 1.6).position;

/** New integrated Mars foundation. This stage claims neither final art nor backend job completion. */
export function MarsWorldStage({ topRight, onOpenLedger, onSelectAgent, stationPanel, onOpenStation }: MarsWorldStageProps) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const hostRef = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(hostRef);
  const { generation } = useWebglSurface(hostRef);
  const webgl = useWebglSupported();
  const reduced = useReducedMotion() ?? false;
  const [mode, setMode] = useState<CameraMode>("overview");
  const [neutral, setNeutral] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [reset, setReset] = useState(0);
  const [gigiVisible, setGigiVisible] = useState(() => readCompanionVisible(WORLD.world_id));
  const showGigi = (visible: boolean) => { setGigiVisible(visible); writeCompanionVisible(WORLD.world_id, visible); };
  const [gigiFocus, setGigiFocus] = useState(0);
  const [gigiRecall, setGigiRecall] = useState(0);
  const gigiPresentation = useCompanionPresentation(awake);
  const openAssistant = useCallback(() => useEventStore.getState().setActiveSection("chats"), []);
  const focusGigi = useCallback(() => { setGigiVisible(true); writeCompanionVisible(WORLD.world_id, true); setMode("orbit"); setGigiFocus((value) => value + 1); }, []);
  const orbit = useCallback(() => setMode("orbit"), []);
  const select = useCallback((id: string) => { setSelected(id); setMode("orbit"); }, []);
  const choose = (next: CameraMode) => {
    setMode(next); setSelected(null);
    if (next === "overview" || next === "outpost") setReset((value) => value + 1);
    if (next === "player") hostRef.current?.focus({ preventScroll: true });
  };
  const selectedBuilding = WORLD.buildings.find((building) => building.id === selected);
  if (!ready) return null;
  return (
    <section className="mars-stage" data-mars-world={WORLD.world_id} data-mars-layout={WORLD.layout_version} data-mars-stage="blockout" data-mars-mode={mode} aria-label={t("society.mars.foundation")}>
      <div className="mars-toolbar" data-mars-ui>
        <div className="mars-title"><strong>{t("society.mars.colony")}</strong><span>{t("society.mars.foundation")}</span></div>
        <div className="mars-actions">
          <button type="button" aria-pressed={mode === "overview"} onClick={() => choose("overview")}>{t("society.mars.overview")}</button>
          <button type="button" aria-pressed={mode === "outpost"} onClick={() => choose("outpost")}>{t("society.mars.outpost")}</button>
          <button type="button" aria-pressed={mode === "player"} onClick={() => choose("player")}>{t("society.mars.walk")}</button>
          <button type="button" aria-pressed={neutral} onClick={() => setNeutral((value) => !value)}>{t("society.mars.neutral")}</button>
          {onOpenStation && <button type="button" onClick={onOpenStation}>{t("society.mars.station_title")}</button>}
          <button type="button" onClick={onOpenLedger}>{t("society.mars.ledger")}</button>
          <button type="button" onClick={focusGigi}>{t("society.mars.gigi_focus")}</button>
          <button type="button" onClick={() => { showGigi(true); setGigiRecall((value) => value + 1); }}>{t("society.mars.gigi_recall")}</button>
          <button type="button" aria-pressed={gigiVisible} onClick={() => showGigi(!gigiVisible)}>{t(gigiVisible ? "society.mars.gigi_hide" : "society.mars.gigi_show")}</button>
          {topRight}
        </div>
      </div>
      <div className="mars-scene-container">
      <div ref={hostRef} className="mars-viewport" tabIndex={0} role="application" aria-label={t("society.mars.viewport")} data-mars-player="pending" data-mars-frames="0">
        {webgl ? (
          <RenderBoundary key={generation} fallbackText={t("society.mars.no_graphics")}>
            <Suspense fallback={<div className="mars-render-fallback" role="status">{t("society.mars.loading")}</div>}>
              <Canvas shadows="percentage" camera={{ position: INITIAL_CAMERA, fov: CAMERA_FOV, near: 0.12, far: 20000 }} dpr={[1, 1.5]} gl={{ antialias: true, alpha: false }} frameloop={!awake ? "never" : reduced ? "demand" : "always"} onPointerMissed={() => { setSelected(null); onSelectAgent?.(null); }}>
                <MarsScene hostRef={hostRef} mode={mode} neutral={neutral} awake={awake && !stationPanel} selected={selected} onSelect={select} onOrbit={orbit} onOpenStation={onOpenStation} reset={reset}
                  gigiVisible={gigiVisible} gigiFocus={gigiFocus} gigiRecall={gigiRecall}
                  reducedMotion={reduced} gigiPresentation={gigiPresentation}
                  onOpenAssistant={openAssistant} onFocusGigi={focusGigi} />
              </Canvas>
            </Suspense>
          </RenderBoundary>
        ) : <div className="mars-render-fallback" role="status">{t("society.mars.no_graphics")}</div>}
      </div>
      {stationPanel && <div className="mars-station-slot" data-mars-ui>{stationPanel}</div>}
      </div>
      <div className="mars-footer" data-mars-ui>
        <MarsBackgroundControl />
        <p>{t(mode === "player" ? "society.mars.player_help" : "society.mars.orbit_help")}</p>
        <p role="status" aria-live="polite">{selectedBuilding ? `${selectedBuilding.name} — ${t(selectedBuilding.access === "required-interior" ? "society.mars.interior_pending" : "society.mars.access_pending")}` : t("society.mars.placeholder_actor")}</p>
        {selected === "operations" && onOpenStation && <button type="button" className="mars-use-station" onClick={onOpenStation}>{t("society.mars.station_title")}</button>}
      </div>
    </section>
  );
}

export default MarsWorldStage;

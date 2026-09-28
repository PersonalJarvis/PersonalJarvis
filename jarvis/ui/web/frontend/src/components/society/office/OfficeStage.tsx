/**
 * Agents > Map: the office. One overview of every agent at its desk, grouped
 * into departments, with a live working/idle count. Clicking an agent opens
 * its card; the map itself never starts or stops work.
 */
import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Canvas } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useT } from "@/i18n";
import { useSocietyRoster, type SocietyAgent } from "../data";
import { OfficeScene } from "./OfficeScene";
import { buildOfficeLayout, countStates, MAX_SEATED } from "./officeLayout";
import { CAMERA_FOV } from "./officeCamera";
import "./office.css";

/** Status refresh while the office is on screen; jittered so windows never poll in lockstep (AP-33). */
const REFRESH_MS = 5000;
const REFRESH_JITTER_MS = 1500;

class RenderBoundary extends Component<{ children: ReactNode; fallbackText: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Office renderer unavailable", error); }
  render() {
    return this.state.failed ? <div className="office-fallback" role="status">{this.props.fallbackText}</div> : this.props.children;
  }
}

export interface OfficeStageProps {
  onOpenLedger: () => void;
  onSelectAgent?: (id: string | null) => void;
}

export function OfficeStage({ onOpenLedger, onSelectAgent }: OfficeStageProps) {
  const t = useT();
  const hostRef = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(hostRef);
  const reduced = useReducedMotion() ?? false;
  const { generation } = useWebglSurface(hostRef);
  const webgl = useWebglSupported();
  const roster = useSocietyRoster();
  const client = useQueryClient();
  const [reset, setReset] = useState(0);

  useEffect(() => {
    if (!awake) return;
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(() => {
        void client.invalidateQueries({ queryKey: ["society", "roster"] });
        schedule();
      }, REFRESH_MS + Math.random() * REFRESH_JITTER_MS);
    };
    schedule();
    return () => clearTimeout(timer);
  }, [awake, client]);

  const active = useMemo(() => (roster.data?.agents ?? []).filter((a) => a.lifecycle !== "archived"), [roster.data]);
  const agents = useMemo(() => new Map<string, SocietyAgent>(active.map((a) => [a.agentId, a])), [active]);
  // The layout only depends on who works where, never on run state, so a
  // status refresh repaints monitors without re-seating anyone.
  const seatingKey = active.map((a) => `${a.agentId}|${a.tier}|${a.providerLabel}|${a.createdMs}`).join(",");
  const layout = useMemo(() => buildOfficeLayout(active),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [seatingKey]);
  const counts = countStates(active);
  const select = (id: string) => onSelectAgent?.(id);

  return (
    <section className="office-stage" aria-label={t("society.office.title")} data-office-agents={active.length}>
      <div ref={hostRef} className="office-viewport" tabIndex={0} role="application" aria-label={t("society.office.viewport")}>
        {webgl ? (
          <RenderBoundary key={generation} fallbackText={t("society.office.no_graphics")}>
            <Suspense fallback={<div className="office-fallback" role="status">{t("society.office.loading")}</div>}>
              <Canvas shadows="percentage" camera={{ fov: CAMERA_FOV, near: 0.2, far: 800, position: [30, 30, 30] }} dpr={[1, 1.75]}
                gl={{ antialias: true, alpha: false, preserveDrawingBuffer: import.meta.env.DEV }} frameloop={!awake ? "never" : reduced ? "demand" : "always"}
                onPointerMissed={() => onSelectAgent?.(null)}>
                <OfficeScene layout={layout} agents={agents} awake={awake} reduced={reduced} reset={reset} onSelect={select} />
              </Canvas>
            </Suspense>
          </RenderBoundary>
        ) : <div className="office-fallback" role="status">{t("society.office.no_graphics")}</div>}
      </div>

      <div className="office-hud office-hud-left" data-office-ui>
        <div className="office-card office-title">
          <strong>{t("society.office.title")}</strong>
          <span>{t("society.office.subtitle").replace("{0}", String(active.length))}</span>
        </div>
        <div className="office-card office-counts" role="status" aria-live="polite">
          <span data-tone="working"><i aria-hidden />{t("society.office.count_working").replace("{0}", String(counts.working))}</span>
          <span data-tone="waiting"><i aria-hidden />{t("society.office.count_waiting").replace("{0}", String(counts.waiting))}</span>
          <span data-tone="idle"><i aria-hidden />{t("society.office.count_idle").replace("{0}", String(counts.idle + counts.paused))}</span>
        </div>
        {roster.data?.sample && <p className="office-card office-note">{t("society.office.sample")}</p>}
        {active.length > MAX_SEATED && <p className="office-card office-note">{t("society.office.overflow").replace("{0}", String(MAX_SEATED))}</p>}
      </div>

      <div className="office-hud office-hud-right" data-office-ui>
        <button type="button" className="office-button" onClick={() => setReset((v) => v + 1)}>{t("society.office.overview")}</button>
        <button type="button" className="office-button" onClick={onOpenLedger}>{t("society.office.ledger")}</button>
      </div>

      <p className="office-hud office-help" data-office-ui>{t("society.office.help")}</p>
    </section>
  );
}

export default OfficeStage;

/**
 * Agents > Map: the office. The person walks their own character through the
 * floor (or zooms out and clicks), agents live their day according to their
 * real run state, and checkpoints turn rooms into actions: create, manage,
 * team up, dress up, talk to the lead, call a coffee break.
 */
import { Component, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Canvas } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useT } from "@/i18n";
import { useSocietyRoster, type SocietyAgent } from "../data";
import { OfficeScene } from "./OfficeScene";
import { allDesks, buildOfficeLayout, countStates, MAX_SEATED } from "./officeLayout";
import { buildNavGrid } from "./officeNav";
import { SpotBook } from "./officeBehavior";
import { CAMERA_FOV } from "./officeCamera";
import { player, useOfficeStore } from "./officeStore";
import { knownAgents } from "./walkerRegistry";
import { loadProfile, saveProfile, type PlayerProfile } from "./playerProfile";
import { AgentPanel, CheckpointPanel, type OfficeActions } from "./OfficePanels";
import type { WalkerContext } from "./OfficeAgents";
import "./office.css";
import "./officeHud.css";

// Dev-only handles for runtime checks of walking and panels.
if (import.meta.env.DEV && typeof window !== "undefined") Object.assign(window, { __officeStore: useOfficeStore, __officePlayer: player });

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
  onCreateAgent?: () => void;
  onOpenGroup?: (groupId: string) => void;
}

function useRosterRefresh(awake: boolean) {
  const client = useQueryClient();
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
}

/** Agents created while the office is open arrive by the elevator; everyone else is simply there. */
function useNewcomers(active: SocietyAgent[]): ReadonlySet<string> {
  const [newcomers, setNewcomers] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (active.length === 0) return;
    if (knownAgents.size === 0) { active.forEach((a) => knownAgents.add(a.agentId)); return; }
    const fresh = active.filter((a) => !knownAgents.has(a.agentId)).map((a) => a.agentId);
    if (fresh.length === 0) return;
    fresh.forEach((id) => knownAgents.add(id));
    setNewcomers((prev) => new Set([...prev, ...fresh]));
  }, [active]);
  return newcomers;
}

export function OfficeStage({ onOpenLedger, onSelectAgent, onCreateAgent, onOpenGroup }: OfficeStageProps) {
  const t = useT();
  const hostRef = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(hostRef);
  const reduced = useReducedMotion() ?? false;
  const { generation } = useWebglSurface(hostRef);
  const webgl = useWebglSupported();
  const roster = useSocietyRoster();
  useRosterRefresh(awake);
  const [overview, setOverview] = useState(0);
  const [profile, setProfile] = useState<PlayerProfile>(loadProfile);
  const selection = useOfficeStore((s) => s.selection);
  const nearby = useOfficeStore((s) => s.nearby);
  const follow = useOfficeStore((s) => s.follow);
  const select = useOfficeStore((s) => s.select);

  const active = useMemo(() => (roster.data?.agents ?? []).filter((a) => a.lifecycle !== "archived"), [roster.data]);
  const agents = useMemo(() => new Map<string, SocietyAgent>(active.map((a) => [a.agentId, a])), [active]);
  const newcomers = useNewcomers(active);
  // The floor plan depends on who works where, never on run state: a status
  // refresh changes monitors and behaviour without re-seating anyone.
  const seatingKey = active.map((a) => `${a.agentId}|${a.tier}|${a.providerLabel}|${a.createdMs}`).join(",");
  const layout = useMemo(() => buildOfficeLayout(active),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [seatingKey]);
  const grid = useMemo(() => buildNavGrid(layout), [layout]);
  const book = useMemo(() => new SpotBook(), [layout]);
  const agentsRef = useRef(agents);
  agentsRef.current = agents;
  const walkers = useMemo<WalkerContext>(() => {
    const desks = allDesks(layout);
    return {
      layout, grid, book, spawn: layout.spawn,
      colleagues: () => desks.filter((d) => d.agentId && agentsRef.current.get(d.agentId)?.state === "working")
        .map((d) => ({ agentId: d.agentId as string, desk: d })),
    };
  }, [layout, grid, book]);

  const counts = countStates(active);
  const updateProfile = useCallback((next: PlayerProfile) => { setProfile(next); saveProfile(next); }, []);
  const actions = useMemo<OfficeActions>(() => ({
    onOpenAgent: (id) => onSelectAgent?.(id),
    onOpenLedger, onCreateAgent, onOpenGroup,
  }), [onSelectAgent, onOpenLedger, onCreateAgent, onOpenGroup]);

  // Escape closes an open panel before it can leave the map.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !useOfficeStore.getState().selection) return;
      event.preventDefault();
      event.stopPropagation();
      select(null);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [select]);

  // A panel for an agent that left the roster closes itself.
  useEffect(() => {
    if (selection?.kind === "agent" && roster.data && !agents.has(selection.id)) select(null);
  }, [selection, agents, roster.data, select]);

  const selectedAgent = selection?.kind === "agent" ? agents.get(selection.id) ?? null : null;
  const nearbyLabel = nearby
    ? nearby.kind === "agent"
      ? t("society.office.prompt_agent").replace("{0}", agents.get(nearby.id)?.name ?? "")
      : t("society.office.prompt_checkpoint").replace("{0}", t(`society.office.cp_${nearby.id}`))
    : null;
  const playerName = profile.name.trim() || t("society.office.you");

  return (
    <section className="office-stage" aria-label={t("society.office.title")} data-office-agents={active.length}>
      <div ref={hostRef} className="office-viewport" tabIndex={0} role="application" aria-label={t("society.office.viewport")}>
        {webgl ? (
          <RenderBoundary key={generation} fallbackText={t("society.office.no_graphics")}>
            <Suspense fallback={<div className="office-fallback" role="status">{t("society.office.loading")}</div>}>
              <Canvas shadows="percentage" camera={{ fov: CAMERA_FOV, near: 0.2, far: 800, position: [30, 30, 30] }} dpr={[1, 1.75]}
                gl={{ antialias: true, alpha: false, preserveDrawingBuffer: import.meta.env.DEV }}
                frameloop={!awake ? "never" : "always"}
                onPointerMissed={() => select(null)}>
                <OfficeScene layout={layout} grid={grid} walkers={walkers} agents={agents} newcomers={newcomers}
                  awake={awake} reduced={reduced} overview={overview} player={{ recipe: profile.recipe, name: playerName }}
                  selection={selection} nearby={nearby} />
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
        <button type="button" className="office-button" aria-pressed={follow} onClick={() => useOfficeStore.getState().setFollow(true)}>{t("society.office.me")}</button>
        <button type="button" className="office-button" onClick={() => setOverview((v) => v + 1)}>{t("society.office.overview")}</button>
        <button type="button" className="office-button" onClick={() => select({ kind: "checkpoint", id: "wardrobe" })}>{t("society.office.cp_wardrobe")}</button>
        <button type="button" className="office-button" onClick={onOpenLedger}>{t("society.office.ledger")}</button>
      </div>

      {selection && (
        <div className="office-panel-slot">
          {selection.kind === "agent" && selectedAgent && (
            <AgentPanel agent={selectedAgent} actions={actions} onClose={() => select(null)} />
          )}
          {selection.kind === "checkpoint" && (
            <CheckpointPanel id={selection.id} agents={active} layout={layout} sample={roster.data?.sample ?? false}
              profile={profile} onProfile={updateProfile} actions={actions} onClose={() => select(null)} />
          )}
        </div>
      )}

      {nearbyLabel && !selection && (
        <button type="button" className="office-hud office-prompt" data-office-ui onClick={() => nearby && select(nearby)}>
          <kbd>E</kbd>{nearbyLabel}
        </button>
      )}
      <p className="office-hud office-help" data-office-ui>{t("society.office.help")}</p>
    </section>
  );
}

export default OfficeStage;

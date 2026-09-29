/**
 * Agents > Map: the office. The person walks their own character through the
 * floor (or zooms out and clicks), agents live their day according to their
 * real run state, and checkpoints turn rooms into actions: create, manage,
 * team up, dress up, talk to the lead, call a coffee break.
 */
import { Component, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { advance, Canvas } from "@react-three/fiber";
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
import { ZOOM_SECONDS } from "./OfficeCameraRig";
import { useDeskChats } from "./useDeskChats";
import type { Point } from "./officeLayout";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions, knownAgents, seatedAtDesk } from "./walkerRegistry";
import { loadProfile, playerLook, saveProfile, type PlayerProfile } from "./playerProfile";
import { AgentPanel, CheckpointPanel, type OfficeActions } from "./OfficePanels";
import type { WalkerContext } from "./OfficeAgents";
import { ownsKeyboard } from "./OfficePlayer";
import "./office.css";
import "./officeHud.css";
import "./officeMinimap.css";
import { OfficeMinimap } from "./OfficeMinimap";
import { OfficeCompass } from "./OfficeCompass";
import { OfficeFullMap } from "./OfficeFullMap";

// Dev-only handles for runtime checks of walking and panels.
if (import.meta.env.DEV && typeof window !== "undefined") Object.assign(window, { __officeStore: useOfficeStore, __officePlayer: player, __officeAgents: agentPositions, __officeSeated: seatedAtDesk,
  // Steps the scene without requestAnimationFrame (hidden test tabs): __officeStep(frames, dtMs).
  __officeStep: (frames = 60, dtMs = 16) => { let t = performance.now(); for (let i = 0; i < frames; i += 1) { t += dtMs; advance(t); } } });

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
    // Forget agents that left, so the set never outgrows the roster.
    const present = new Set(active.map((a) => a.agentId));
    for (const id of [...knownAgents]) if (!present.has(id)) knownAgents.delete(id);
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
  const [mapOpen, setMapOpen] = useState(false);
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
  // One reservation book for the whole visit: a rebuilt floor plan (someone
  // joined or left) must not forget who already sits on which couch.
  const [book] = useState(() => new SpotBook());
  useEffect(() => { book.retain(new Set(layout.spots.map((spot) => spot.id))); }, [book, layout]);
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

  const sessions = useMemo(() => new Map(active.filter((a) => a.chatSessionId).map((a) => [a.agentId, a.chatSessionId as string])), [active]);
  const chats = useDeskChats(sessions, awake);
  // Clicking a monitor dives into it, then opens that agent's chat.
  const openTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(openTimer.current), []);
  const [diving, setDiving] = useState(false);
  const openScreen = useCallback((agentId: string, screen: Point & { y: number }, facing: number) => {
    select(null);
    clearTimeout(openTimer.current);
    if (reduced) { onSelectAgent?.(agentId); return; }
    useOfficeStore.getState().zoomInto([screen.x, screen.y, screen.z], facing);
    // The screen fills the view, then the real chat view fades in over it.
    setDiving(true);
    openTimer.current = setTimeout(() => { onSelectAgent?.(agentId); setDiving(false); }, ZOOM_SECONDS * 1000 + 260);
  }, [onSelectAgent, reduced, select]);

  const counts = countStates(active);
  const updateProfile = useCallback((next: PlayerProfile) => { setProfile(next); saveProfile(next); }, []);
  const actions = useMemo<OfficeActions>(() => ({
    onOpenAgent: (id) => onSelectAgent?.(id),
    onOpenLedger, onCreateAgent, onOpenGroup,
  }), [onSelectAgent, onOpenLedger, onCreateAgent, onOpenGroup]);

  // Escape closes an open panel before it can leave the map.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // A field or a dialog (e.g. the create dialog opened from reception) owns its own Escape.
      if (event.key !== "Escape" || !useOfficeStore.getState().selection || ownsKeyboard(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      select(null);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [select]);

  // Leaving the map forgets panels and calls; the office opens fresh next time.
  useEffect(() => () => {
    const store = useOfficeStore.getState();
    store.select(null);
    store.setNearby(null);
    store.clearSummons();
  }, []);

  // A panel for an agent that left the roster closes itself.
  useEffect(() => {
    if (selection?.kind === "agent" && roster.data && !agents.has(selection.id)) select(null);
  }, [selection, agents, roster.data, select]);

  const selectedAgent = selection?.kind === "agent" ? agents.get(selection.id) ?? null : null;
  const nearbyLabel = nearby
    ? nearby.kind === "agent"
      ? t("society.office.prompt_agent").replace("{0}", agents.get(nearby.id)?.name ?? "")
      : t(`society.office.cp_${nearby.id}_hint`)
    : null;
  const playerName = profile.name.trim() || t("society.office.you");

  return (
    <section className="office-stage" aria-label={t("society.office.title")} data-office-agents={active.length}>
      <div ref={hostRef} className="office-viewport" tabIndex={0} role="application" aria-label={t("society.office.viewport")}>
        {webgl ? (
          <RenderBoundary key={generation} fallbackText={t("society.office.no_graphics")}>
            <Suspense fallback={<div className="office-fallback" role="status">{t("society.office.loading")}</div>}>
              <Canvas shadows="percentage" camera={{ fov: CAMERA_FOV, near: 0.2, far: 800, position: [30, 30, 30] }} dpr={[1, 1.75]}
                gl={{ antialias: true, alpha: true, preserveDrawingBuffer: import.meta.env.DEV }}
                frameloop={!awake ? "never" : "always"}
                onPointerMissed={() => select(null)}>
                <OfficeScene layout={layout} grid={grid} walkers={walkers} agents={agents} newcomers={newcomers}
                  awake={awake} reduced={reduced} overview={overview} player={{ look: playerLook(profile), name: playerName }}
                  selection={selection} nearby={nearby} chats={chats} onOpenScreen={openScreen} />
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
          <span data-tone="idle"><i aria-hidden />{t("society.office.count_idle").replace("{0}", String(counts.idle))}</span>
          {counts.paused > 0 && <span data-tone="paused"><i aria-hidden />{t("society.office.count_paused").replace("{0}", String(counts.paused))}</span>}
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
      <OfficeCompass layout={layout} agents={agents} selectedId={selection?.kind === "agent" ? selection.id : null} />
      {diving && <div className="office-dive-fade" aria-hidden />}
      <OfficeMinimap layout={layout} agents={agents} selectedId={selection?.kind === "agent" ? selection.id : null}
        onOpenMap={() => setMapOpen(true)} />
      <OfficeFullMap open={mapOpen} onOpen={() => setMapOpen(true)} onClose={() => setMapOpen(false)}
        layout={layout} agents={agents} selectedId={selection?.kind === "agent" ? selection.id : null} />
      <p className="office-hud office-help" data-office-ui>{t("society.office.help")}</p>
    </section>
  );
}

export default OfficeStage;

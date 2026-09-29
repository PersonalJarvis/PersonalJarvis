/**
 * DOM panels for what the person selects in the office: an agent (or, on the
 * coding floor, an IDE session), or one of the checkpoints (reception, agent
 * board, team room, wardrobe, lead office, break room, elevator). Every action here uses an existing app path — create dialog,
 * agent card/chat, chat groups — the office adds no new backend contract.
 */
import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { CheckpointKind, OfficeLayout, Point } from "./officeLayout";
import { otherFloor, player, useOfficeStore, type OfficeFloor } from "./officeStore";
import type { PaneOccupant } from "./codingFloor";
import { agentPositions } from "./walkerRegistry";
import { AgentTalkPanel, CALL_MS } from "./AgentTalkPanel";
import type { PlayerProfile } from "./playerProfile";
import { WardrobePanel } from "./WardrobePanel";
import { ReceptionPanel } from "./ReceptionPanel";
import { TeamRoomPanel } from "./TeamRoomPanel";
import { CHECKPOINT_ICON, IconSvg, type CheckpointIcon } from "./CheckpointMarker";


export interface OfficeActions {
  onOpenAgent: (id: string) => void;
  onOpenLedger: () => void;
  onCreateAgent?: () => void;
  onOpenGroup?: (groupId: string) => void;
}

function StateDot({ state }: { state: SocietyAgent["state"] }) {
  return <i className="office-dot" data-state={state} aria-hidden />;
}

function PanelShell({ title, subtitle, icon, kind, onClose, children }: {
  title: string; subtitle?: string; icon?: CheckpointIcon; kind?: string; onClose: () => void; children: React.ReactNode;
}) {
  const t = useT();
  const headingId = useId();
  const panel = useRef<HTMLElement>(null);
  // Move focus into a freshly opened panel so keyboard and screen-reader users
  // land on it; walking keys keep working because they listen on the window.
  useEffect(() => { panel.current?.focus({ preventScroll: true }); }, [title]);
  return (
    <aside ref={panel} className="office-card office-panel" data-office-ui data-panel={kind} aria-labelledby={headingId} tabIndex={-1}>
      <header className="office-panel-head">
        {icon && <span className="office-panel-badge" aria-hidden><IconSvg icon={icon} /></span>}
        <div>
          <h2 id={headingId}>{title}</h2>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
        <button type="button" className="office-icon-button" onClick={onClose} aria-label={t("society.office.close")}>×</button>
      </header>
      <div className="office-panel-body">{children}</div>
    </aside>
  );
}

function spotCentre(layout: OfficeLayout, kind: "couch"): Point {
  const spots = layout.spots.filter((s) => s.kind === kind);
  if (spots.length === 0) return layout.spawn;
  return { x: spots.reduce((s, p) => s + p.x, 0) / spots.length, z: spots.reduce((s, p) => s + p.z, 0) / spots.length };
}

/** The agent panel is a walkie-talkie to that agent (AgentTalkPanel). */
export function AgentPanel({ agent, actions, onClose }: { agent: SocietyAgent; actions: OfficeActions; onClose: () => void }) {
  return <AgentTalkPanel agent={agent} actions={actions} onClose={onClose} />;
}

/** How long ago an epoch-seconds moment was, in the viewer's words ("" when unknown). */
function sinceLabel(at: number | null, t: (key: string) => string): string {
  if (!at) return "";
  const seconds = Math.max(0, Date.now() / 1000 - at);
  if (seconds < 60) return t("society.office.since_now");
  if (seconds < 3600) return t("society.office.since_minutes").replace("{0}", String(Math.floor(seconds / 60)));
  if (seconds < 86400) return t("society.office.since_hours").replace("{0}", String(Math.floor(seconds / 3600)));
  return t("society.office.since_days").replace("{0}", String(Math.floor(seconds / 86400)));
}

/**
 * A coding session on the coding floor: what it is doing, where it lives and
 * what it was last asked. It has no chat or team — its home is the IDE pane.
 */
export function PaneAgentPanel({ occupant, onOpen, onClose }: { occupant: PaneOccupant; onOpen: () => void; onClose: () => void }) {
  const t = useT();
  const store = useOfficeStore();
  const { agent, pane } = occupant;
  const since = sinceLabel(pane.activity_since || null, t);
  const prompt = pane.last_prompt.trim();
  const recap = pane.recap.trim();
  const place = () => agentPositions.get(agent.agentId);
  return (
    <PanelShell title={agent.name} subtitle={pane.display_name || pane.agent} onClose={onClose}>
      <p className="office-panel-status" data-pane-state={occupant.stateKey}>
        <i className="office-dot" data-state={agent.state} data-dot={occupant.dot} aria-hidden />
        {t(`society.office.pane_state_${occupant.stateKey}`)}{since ? <span className="office-hint"> · {since}</span> : null}
      </p>
      <dl className="office-facts">
        <dt>{t("society.office.pane_workspace")}</dt>
        <dd title={pane.folder}>{agent.providerLabel}</dd>
        {recap && recap !== prompt && (<><dt>{t("society.office.pane_recap")}</dt><dd>{recap}</dd></>)}
        {prompt && (<><dt>{t("society.office.pane_prompt")}</dt><dd className="office-facts-quote">{prompt}</dd></>)}
      </dl>
      {!recap && !prompt && <p className="office-note-inline">{t("society.office.pane_unasked")}</p>}
      <div className="office-actions">
        <button type="button" className="office-action office-action-primary" onClick={onOpen}>{t("society.office.pane_open")}</button>
        <button type="button" className="office-action" onClick={() => { const p = place(); if (p) store.focusOn(p); }}>
          {t("society.office.action_focus")}
        </button>
        <button type="button" className="office-action" onClick={() => { const p = place(); if (p) store.requestWalk(p); }}>
          {t("society.office.action_walk")}
        </button>
      </div>
    </PanelShell>
  );
}

/** Counts of both floors shown in the elevator; `null` = not known yet. */
export interface ElevatorInfo { jarvis: number; coding: number | null; onRide: (to: OfficeFloor) => void }

function ElevatorPanel({ floor, info }: { floor: OfficeFloor; info: ElevatorInfo }) {
  const t = useT();
  const to = otherFloor(floor);
  const count = to === "coding" ? info.coding : info.jarvis;
  return (
    <>
      <p>{t(to === "coding" ? "society.office.elevator_up_body" : "society.office.elevator_down_body")}</p>
      {count !== null && (
        <p className="office-hint" role="status">
          {t(to === "coding" ? "society.office.elevator_up_count" : "society.office.elevator_down_count").replace("{0}", String(count))}
        </p>
      )}
      <button type="button" className="office-action office-action-primary" onClick={() => info.onRide(to)}>
        {t(to === "coding" ? "society.office.elevator_up" : "society.office.elevator_down")}
      </button>
    </>
  );
}

function ManagePanel({ agents, actions }: { agents: SocietyAgent[]; actions: OfficeActions }) {
  const t = useT();
  const store = useOfficeStore();
  return (
    <>
      {agents.length === 0 ? <p className="office-note-inline">{t("society.office.manage_empty")}</p> : (
        <ul className="office-list">
          {agents.map((agent) => (
            <li key={agent.agentId}>
              <StateDot state={agent.state} />
              <span className="office-list-name" title={agent.name}>{agent.name}</span>
              <button type="button" className="office-mini" aria-label={t("society.office.show_agent").replace("{0}", agent.name)} onClick={() => {
                const p = agentPositions.get(agent.agentId);
                if (p) store.focusOn(p);
                store.select({ kind: "agent", id: agent.agentId });
              }}>{t("society.office.action_show")}</button>
              <button type="button" className="office-mini" aria-label={t("society.office.open_agent").replace("{0}", agent.name)}
                onClick={() => actions.onOpenAgent(agent.agentId)}>{t("society.office.action_open")}</button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="office-action office-action-primary" onClick={actions.onOpenLedger}>{t("society.office.manage_action")}</button>
    </>
  );
}

function LeadPanel({ agents, actions }: { agents: SocietyAgent[]; actions: OfficeActions }) {
  const t = useT();
  const store = useOfficeStore();
  // The lead office seats up to two leads (officeLayout, arrival order); offer both.
  const leads = agents.filter((a) => a.tier === "lead")
    .sort((a, b) => a.createdMs - b.createdMs || a.agentId.localeCompare(b.agentId)).slice(0, 2);
  if (leads.length === 0) return <p>{t("society.office.lead_empty")}</p>;
  const named = (action: string, name: string) => (leads.length > 1 ? `${action}: ${name}` : undefined);
  return (
    <>
      <p>{t("society.office.lead_body")}</p>
      {leads.map((lead) => (
        <div key={lead.agentId}>
          <p className="office-panel-status"><StateDot state={lead.state} />{lead.name} · {t(`society.office.state_${lead.state}`)}</p>
          <div className="office-actions">
            <button type="button" className="office-action office-action-primary" aria-label={named(t("society.office.action_talk"), lead.name)}
              onClick={() => store.select({ kind: "agent", id: lead.agentId })}>{t("society.office.action_talk")}</button>
            <button type="button" className="office-action" aria-label={named(t("society.office.action_chat"), lead.name)}
              onClick={() => actions.onOpenAgent(lead.agentId)}>{t("society.office.action_chat")}</button>
            <button type="button" className="office-action" aria-label={named(t("society.office.action_call"), lead.name)}
              onClick={() => store.summon([lead.agentId], { x: player.x, z: player.z }, CALL_MS)}>{t("society.office.action_call")}</button>
          </div>
        </div>
      ))}
    </>
  );
}

function BreakPanel({ agents, layout }: { agents: SocietyAgent[]; layout: OfficeLayout }) {
  const t = useT();
  const store = useOfficeStore();
  const idle = agents.filter((a) => a.state === "idle");
  const [called, setCalled] = useState(0);
  // Calls expire on their own (CALL_MS); only a live one is worth releasing.
  const now = Date.now();
  const anyoneCalled = Object.values(store.summons).some((summon) => summon.untilMs > now);
  return (
    <>
      <p>{idle.length > 0 ? t("society.office.break_body").replace("{0}", String(idle.length)) : t("society.office.break_none")}</p>
      <div className="office-actions">
        <button type="button" className="office-action office-action-primary" disabled={idle.length === 0}
          onClick={() => {
            store.summon(idle.map((a) => a.agentId), spotCentre(layout, "couch"), CALL_MS);
            setCalled(idle.length);
          }}>{t("society.office.break_call")}</button>
        <button type="button" className="office-action" disabled={!anyoneCalled}
          onClick={() => { store.clearSummons(); setCalled(0); }}>{t("society.office.break_release")}</button>
      </div>
      {called > 0 && anyoneCalled && <p className="office-hint" role="status">{t("society.office.break_called").replace("{0}", String(called))}</p>}
    </>
  );
}

export function CheckpointPanel({ id, floor = "agents", agents, layout, sample, profile, onProfile, actions, onClose, elevator }: {
  id: CheckpointKind; floor?: OfficeFloor; agents: SocietyAgent[]; layout: OfficeLayout; sample: boolean;
  profile: PlayerProfile; onProfile: (next: PlayerProfile) => void; actions: OfficeActions; onClose: () => void;
  elevator?: ElevatorInfo;
}) {
  const t = useT();
  return (
    <PanelShell title={t(`society.office.cp_${id}`)} subtitle={t(`society.office.cp_${id}_hint`)} icon={CHECKPOINT_ICON[id]} kind={id} onClose={onClose}>
      {id === "create" && <ReceptionPanel floor={floor} layout={layout} onCreateAgent={actions.onCreateAgent} />}
      {id === "manage" && <ManagePanel agents={agents} actions={actions} />}
      {id === "team" && <TeamRoomPanel agents={agents} layout={layout} sample={sample} actions={actions} />}
      {id === "wardrobe" && <WardrobePanel profile={profile} onProfile={onProfile} agents={agents} sample={sample} />}
      {id === "lead" && <LeadPanel agents={agents} actions={actions} />}
      {id === "break" && <BreakPanel agents={agents} layout={layout} />}
      {id === "elevator" && elevator && <ElevatorPanel floor={floor} info={elevator} />}
    </PanelShell>
  );
}

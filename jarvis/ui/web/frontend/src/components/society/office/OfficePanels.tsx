/**
 * DOM panels for what the person selects in the office: an agent (an IDE
 * session on the coding floor has its own PaneCommandPanel), or one of the
 * checkpoints (the spawn point, reception, agent board, team room, wardrobe,
 * lead office, break room, elevator, Mission Control). Every action here uses an existing app path — create dialog,
 * agent card/chat, chat groups — the office adds no new backend contract.
 */
import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { CheckpointKind, OfficeLayout, Point } from "./officeLayout";
import { player, useOfficeStore, type OfficeFloor } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { AgentTalkPanel, CALL_MS } from "./AgentTalkPanel";
import type { PlayerProfile } from "./playerProfile";
import { WardrobePanel } from "./WardrobePanel";
import { ReceptionPanel } from "./ReceptionPanel";
import { TeamRoomPanel } from "./TeamRoomPanel";
import { MissionControlPanel } from "./MissionControlPanel";
import { LaunchPanel, SpawnPanel } from "./SpawnPanel";
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

export function CheckpointPanel({ id, floor = "agents", agents, layout, sample, profile, onProfile, actions, onClose }: {
  id: CheckpointKind; floor?: OfficeFloor; agents: SocietyAgent[]; layout: OfficeLayout; sample: boolean;
  profile: PlayerProfile; onProfile: (next: PlayerProfile) => void; actions: OfficeActions; onClose: () => void;
}) {
  const t = useT();
  return (
    <PanelShell title={t(`society.office.cp_${id}`)} subtitle={t(`society.office.cp_${id}_hint`)} icon={CHECKPOINT_ICON[id]} kind={id} onClose={onClose}>
      {id === "spawn" && <SpawnPanel agents={agents} onCreateAgent={actions.onCreateAgent} />}
      {id === "launch" && <LaunchPanel />}
      {id === "create" && <ReceptionPanel floor={floor} layout={layout} onCreateAgent={actions.onCreateAgent} />}
      {id === "manage" && <ManagePanel agents={agents} actions={actions} />}
      {id === "team" && <TeamRoomPanel agents={agents} layout={layout} sample={sample} actions={actions} />}
      {id === "wardrobe" && <WardrobePanel profile={profile} onProfile={onProfile} agents={agents} sample={sample} />}
      {id === "lead" && <LeadPanel agents={agents} actions={actions} />}
      {id === "break" && <BreakPanel agents={agents} layout={layout} />}
      {id === "mission" && <MissionControlPanel />}
    </PanelShell>
  );
}

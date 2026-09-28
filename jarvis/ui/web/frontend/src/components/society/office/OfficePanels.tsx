/**
 * DOM panels for what the person selects in the office: an agent, or one of
 * the checkpoints (reception, agent board, team room, wardrobe, lead office,
 * break room). Every action here uses an existing app path — create dialog,
 * agent card/chat, chat groups — the office adds no new backend contract.
 */
import { useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { createSocietyChatGroup, useSocietyChatGroups } from "@/lib/societyChatGroups";
import type { SocietyAgent } from "../data";
import type { CheckpointKind, OfficeLayout, Point } from "./officeLayout";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { HAIR_STYLES, playerLook, withHairStyle, withShuffledColours, type PlayerProfile } from "./playerProfile";

/** How long called or gathered agents stay before they drift back to their day. */
const CALL_MS = 25_000;

export interface OfficeActions {
  onOpenAgent: (id: string) => void;
  onOpenLedger: () => void;
  onCreateAgent?: () => void;
  onOpenGroup?: (groupId: string) => void;
}

function StateDot({ state }: { state: SocietyAgent["state"] }) {
  return <i className="office-dot" data-state={state} aria-hidden />;
}

function PanelShell({ title, subtitle, onClose, children }: { title: string; subtitle?: string; onClose: () => void; children: React.ReactNode }) {
  const t = useT();
  const headingId = useId();
  const panel = useRef<HTMLElement>(null);
  // Move focus into a freshly opened panel so keyboard and screen-reader users
  // land on it; walking keys keep working because they listen on the window.
  useEffect(() => { panel.current?.focus({ preventScroll: true }); }, [title]);
  return (
    <aside ref={panel} className="office-card office-panel" data-office-ui aria-labelledby={headingId} tabIndex={-1}>
      <header className="office-panel-head">
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

function spotCentre(layout: OfficeLayout, kind: "meeting" | "couch"): Point {
  const spots = layout.spots.filter((s) => s.kind === kind);
  if (spots.length === 0) return layout.spawn;
  return { x: spots.reduce((s, p) => s + p.x, 0) / spots.length, z: spots.reduce((s, p) => s + p.z, 0) / spots.length };
}

export function AgentPanel({ agent, actions, onClose }: { agent: SocietyAgent; actions: OfficeActions; onClose: () => void }) {
  const t = useT();
  const store = useOfficeStore();
  const inDraft = store.teamDraft.includes(agent.agentId);
  const where = agentPositions.get(agent.agentId);
  const [called, setCalled] = useState(false);
  // A new agent in the same panel starts without the previous "coming over" note.
  useEffect(() => setCalled(false), [agent.agentId]);
  const subtitle = agent.title || agent.providerLabel;
  return (
    <PanelShell title={agent.name} subtitle={subtitle} onClose={onClose}>
      <p className="office-panel-status"><StateDot state={agent.state} />{t(`society.office.state_${agent.state}`)}
        {agent.providerLabel && agent.providerLabel !== subtitle ? <span> · {agent.providerLabel}</span> : null}</p>
      <div className="office-actions">
        <button type="button" className="office-action office-action-primary" onClick={() => actions.onOpenAgent(agent.agentId)}>{t("society.office.action_chat")}</button>
        <button type="button" className="office-action" disabled={!where} onClick={() => where && store.requestWalk(where)}>{t("society.office.action_walk")}</button>
        <button type="button" className="office-action" onClick={() => {
          store.summon([agent.agentId], { x: player.x, z: player.z }, CALL_MS);
          setCalled(true);
        }}>{t("society.office.action_call")}</button>
        <button type="button" className="office-action" disabled={!where} onClick={() => where && store.focusOn(where)}>{t("society.office.action_focus")}</button>
        <button type="button" className="office-action" aria-pressed={inDraft} onClick={() => store.toggleDraft(agent.agentId)}>
          {t(inDraft ? "society.office.action_undraft" : "society.office.action_draft")}
        </button>
      </div>
      {called && <p className="office-hint" role="status">{t("society.office.call_sent").replace("{0}", agent.name)}</p>}
      {store.teamDraft.length > 0 && (
        <button type="button" className="office-link" onClick={() => store.select({ kind: "checkpoint", id: "team" })}>
          {t("society.office.draft_count").replace("{0}", String(store.teamDraft.length))}
        </button>
      )}
    </PanelShell>
  );
}

function CreatePanel({ actions }: { actions: OfficeActions }) {
  const t = useT();
  return (
    <>
      <p>{t("society.office.create_body")}</p>
      <button type="button" className="office-action office-action-primary" disabled={!actions.onCreateAgent} onClick={() => actions.onCreateAgent?.()}>
        {t("society.office.create_action")}
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

function TeamPanel({ agents, layout, sample, actions }: { agents: SocietyAgent[]; layout: OfficeLayout; sample: boolean; actions: OfficeActions }) {
  const t = useT();
  const store = useOfficeStore();
  const client = useQueryClient();
  const groups = useSocietyChatGroups(!sample);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null);
  const table = spotCentre(layout, "meeting");
  const members = store.teamDraft.filter((id) => agents.some((a) => a.agentId === id));
  const canCreate = !sample && !busy && members.length >= 2;
  const create = async () => {
    if (!canCreate) return;
    setBusy(true); setError(""); setCreated(null);
    try {
      const names = members.map((id) => agents.find((a) => a.agentId === id)?.name ?? id);
      const group = await createSocietyChatGroup(name.trim() || names.join(" + "), members);
      await client.invalidateQueries({ queryKey: ["society", "chat-groups"] });
      store.summon(members, table, CALL_MS);
      store.clearDraft();
      setName("");
      setCreated({ id: group.group_id, name: group.name });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <p>{t("society.office.team_body")}</p>
      {sample && <p className="office-note-inline">{t("society.office.sample")}</p>}
      <label className="office-field">
        <span>{t("society.office.team_name")}</span>
        <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder={t("society.office.team_name_placeholder")}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void create(); } }} />
      </label>
      {agents.length === 0 ? <p className="office-note-inline">{t("society.office.manage_empty")}</p> : (
        <ul className="office-list office-list-check">
          {agents.map((agent) => (
            <li key={agent.agentId}>
              <label>
                <input type="checkbox" checked={store.teamDraft.includes(agent.agentId)} onChange={() => store.toggleDraft(agent.agentId)} />
                <StateDot state={agent.state} /><span className="office-list-name" title={agent.name}>{agent.name}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="office-action office-action-primary" disabled={!canCreate} aria-busy={busy} onClick={() => void create()}>
        {t("society.office.team_create").replace("{0}", String(members.length))}
      </button>
      {!sample && members.length < 2 && agents.length >= 2 && <p className="office-hint">{t("society.office.team_need_more")}</p>}
      {error && <p role="alert" className="office-error">{error}</p>}
      {created && (
        <p className="office-success" role="status">
          {t("society.office.team_created").replace("{0}", created.name)}
          {actions.onOpenGroup && <button type="button" className="office-link" onClick={() => actions.onOpenGroup?.(created.id)}>{t("society.office.team_open")}</button>}
        </p>
      )}
      {(groups.data?.length ?? 0) > 0 && (
        <>
          <h4 className="office-subhead">{t("society.office.team_existing")}</h4>
          <ul className="office-list">
            {groups.data!.map((group) => (
              <li key={group.group_id}>
                <span className="office-list-name" title={group.name}>{group.name}</span>
                <button type="button" className="office-mini" aria-label={t("society.office.team_gather_label").replace("{0}", group.name)}
                  onClick={() => store.summon(group.members, table, CALL_MS)}>{t("society.office.team_gather")}</button>
                {actions.onOpenGroup && <button type="button" className="office-mini" aria-label={t("society.office.team_open_label").replace("{0}", group.name)}
                  onClick={() => actions.onOpenGroup?.(group.group_id)}>{t("society.office.action_open")}</button>}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function WardrobePanel({ profile, onProfile }: { profile: PlayerProfile; onProfile: (next: PlayerProfile) => void }) {
  const t = useT();
  const bodiesId = useId();
  return (
    <>
      <label className="office-field">
        <span>{t("society.office.wardrobe_name")}</span>
        <input value={profile.name} maxLength={40} placeholder={t("society.office.you")} autoComplete="off" spellCheck={false}
          onChange={(e) => onProfile({ ...profile, name: e.target.value })} />
        <span>{t("society.office.wardrobe_saved")}</span>
      </label>
      <p className="office-subhead" id={bodiesId}>{t("society.office.wardrobe_body")}</p>
      <div className="office-chips" role="group" aria-labelledby={bodiesId}>
        {HAIR_STYLES.map((style) => (
          <button key={style} type="button" className="office-chip" aria-pressed={playerLook(profile).hairStyle === style} onClick={() => onProfile(withHairStyle(profile, style))}>
            {t(`society.office.hair_${style}`)}
          </button>
        ))}
      </div>
      <button type="button" className="office-action" onClick={() => onProfile(withShuffledColours(profile))}>{t("society.office.wardrobe_shuffle")}</button>
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
            <button type="button" className="office-action office-action-primary" aria-label={named(t("society.office.action_chat"), lead.name)}
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

export function CheckpointPanel({ id, agents, layout, sample, profile, onProfile, actions, onClose }: {
  id: CheckpointKind; agents: SocietyAgent[]; layout: OfficeLayout; sample: boolean;
  profile: PlayerProfile; onProfile: (next: PlayerProfile) => void; actions: OfficeActions; onClose: () => void;
}) {
  const t = useT();
  return (
    <PanelShell title={t(`society.office.cp_${id}`)} subtitle={t(`society.office.cp_${id}_hint`)} onClose={onClose}>
      {id === "create" && <CreatePanel actions={actions} />}
      {id === "manage" && <ManagePanel agents={agents} actions={actions} />}
      {id === "team" && <TeamPanel agents={agents} layout={layout} sample={sample} actions={actions} />}
      {id === "wardrobe" && <WardrobePanel profile={profile} onProfile={onProfile} />}
      {id === "lead" && <LeadPanel agents={agents} actions={actions} />}
      {id === "break" && <BreakPanel agents={agents} layout={layout} />}
    </PanelShell>
  );
}

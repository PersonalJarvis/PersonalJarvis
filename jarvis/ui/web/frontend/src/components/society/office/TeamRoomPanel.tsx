/**
 * The team room checkpoint: your teams as cards (meet at the table, open the
 * team chat, rename, change members, delete), a live meeting banner while a
 * team sits at the table, and a quick way to put a new team together.
 *
 * Teams are the existing society chat groups; the office adds no backend
 * contract. A meeting is purely spatial — members walk to the team room and
 * sit down — and never interrupts an agent that is working.
 */
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import {
  createSocietyChatGroup, deleteSocietyChatGroup, updateSocietyChatGroup, useSocietyChatGroups, type SocietyChatGroup,
} from "@/lib/societyChatGroups";
import type { SocietyAgent } from "../data";
import type { OfficeLayout, Point } from "./officeLayout";
import { useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { MEETING_MS, meetingProgress, meetingSeats, minutesLeft } from "./teamMeeting";
import type { OfficeActions } from "./OfficePanels";
import "./teamRoom.css";

const GROUPS_KEY = ["society", "chat-groups"] as const;
/** Above this many agents the picker gets a search field. */
const SEARCH_FROM = 7;

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)).toUpperCase();
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** A member as a small round badge in the agent's colour, with its state dot. */
function MemberBadge({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  return (
    <span className="team-badge" style={{ background: agent.palette.primary }}
      title={`${agent.name} · ${t(`society.office.state_${agent.state}`)}`}>
      {initials(agent.name)}
      <i className="office-dot" data-state={agent.state} aria-hidden />
    </span>
  );
}

/** Where the person stands to join a meeting: just south of the chairs, facing the table. */
function tableApproach(layout: OfficeLayout): Point | null {
  const table = layout.furniture.find((f) => f.kind === "meetingTable");
  return table ? { x: table.x, z: table.z + 2.1 } : null;
}

/** Checkbox list of agents with an optional search field and quick picks. */
function MemberPicker({ agents, selected, onToggle, onSet }: {
  agents: SocietyAgent[]; selected: string[]; onToggle: (id: string) => void; onSet: (ids: string[]) => void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const shown = needle ? agents.filter((a) => `${a.name} ${a.title}`.toLowerCase().includes(needle)) : agents;
  const free = agents.filter((a) => a.state === "idle").map((a) => a.agentId);
  return (
    <div className="team-picker">
      {agents.length >= SEARCH_FROM && (
        <label className="office-field">
          <span className="sr-only">{t("society.office.team_search")}</span>
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("society.office.team_search")} />
        </label>
      )}
      <div className="office-actions">
        <button type="button" className="office-mini" disabled={free.length === 0} onClick={() => onSet([...new Set([...selected, ...free])])}>
          {t("society.office.team_pick_idle")}
        </button>
        <button type="button" className="office-mini" disabled={selected.length === 0} onClick={() => onSet([])}>
          {t("society.office.team_pick_clear")}
        </button>
        <span className="office-hint team-picker-count" aria-live="polite">
          {t("society.office.team_selected").replace("{0}", String(selected.length))}
        </span>
      </div>
      {shown.length === 0 ? <p className="office-note-inline">{t("society.office.team_no_match")}</p> : (
        <ul className="office-list office-list-check">
          {shown.map((agent) => (
            <li key={agent.agentId}>
              <label>
                <input type="checkbox" checked={selected.includes(agent.agentId)} onChange={() => onToggle(agent.agentId)} />
                <i className="office-dot" data-state={agent.state} aria-hidden />
                <span className="office-list-name" title={agent.title ? `${agent.name} · ${agent.title}` : agent.name}>{agent.name}</span>
                {agent.title && <span className="team-picker-title">{agent.title}</span>}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The meeting at the table right now: who sits, who is coming, who keeps working. */
function MeetingBanner({ agents, layout, actions }: { agents: SocietyAgent[]; layout: OfficeLayout; actions: OfficeActions }) {
  const t = useT();
  const meeting = useOfficeStore((s) => s.meeting);
  const summons = useOfficeStore((s) => s.summons);
  const endMeeting = useOfficeStore((s) => s.endMeeting);
  const requestWalk = useOfficeStore((s) => s.requestWalk);
  const [now, setNow] = useState(() => Date.now());
  // Positions live outside React; a one-second tick keeps the seat count honest.
  useEffect(() => {
    if (!meeting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [meeting]);
  useEffect(() => { if (meeting && meeting.untilMs <= now) endMeeting(); }, [meeting, now, endMeeting]);
  if (!meeting || meeting.untilMs <= now) return null;
  const progress = meetingProgress(meeting, agents, summons, agentPositions);
  const present = meeting.members.length - progress.missing.length;
  const nameOf = (id: string) => agents.find((a) => a.agentId === id)?.name ?? id;
  const approach = tableApproach(layout);
  return (
    <section className="team-meeting" aria-live="polite">
      <p className="team-meeting-title"><span className="team-meeting-live" aria-hidden />{t("society.office.meeting_title").replace("{0}", meeting.name)}</p>
      <p className="office-hint">
        {t("society.office.meeting_status")
          .replace("{0}", String(progress.seated.length)).replace("{1}", String(present))
          .replace("{2}", String(minutesLeft(meeting, now)))}
      </p>
      {progress.busy.length > 0 && (
        <p className="office-hint">{t("society.office.meeting_busy").replace("{0}", progress.busy.map(nameOf).join(", "))}</p>
      )}
      <div className="office-actions">
        {actions.onOpenGroup && (
          <button type="button" className="office-action office-action-primary" onClick={() => actions.onOpenGroup?.(meeting.groupId)}>
            {t("society.office.team_chat")}
          </button>
        )}
        {approach && <button type="button" className="office-action" onClick={() => requestWalk(approach)}>{t("society.office.meeting_join")}</button>}
        <button type="button" className="office-action" onClick={endMeeting}>{t("society.office.meeting_end")}</button>
      </div>
    </section>
  );
}

/** One team: members at a glance, meet / chat, and an inline editor. */
function TeamCard({ group, agents, layout, sample, actions }: {
  group: SocietyChatGroup; agents: SocietyAgent[]; layout: OfficeLayout; sample: boolean; actions: OfficeActions;
}) {
  const t = useT();
  const client = useQueryClient();
  const meeting = useOfficeStore((s) => s.meeting);
  const startMeeting = useOfficeStore((s) => s.startMeeting);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(group.name);
  const [members, setMembers] = useState<string[]>(group.members);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const present = group.members.map((id) => agents.find((a) => a.agentId === id)).filter((a): a is SocietyAgent => !!a);
  const working = present.filter((a) => a.state === "working").length;
  const inMeeting = !!meeting && meeting.groupId === group.group_id && meeting.untilMs > Date.now();

  const meet = () => {
    const ids = present.map((a) => a.agentId);
    startMeeting({ groupId: group.group_id, name: group.name, members: ids, untilMs: Date.now() + MEETING_MS }, meetingSeats(layout, ids));
  };
  const openEditor = () => { setName(group.name); setMembers(group.members); setConfirmDelete(false); setError(""); setEditing(true); };
  const save = async () => {
    if (members.length < 2 || busy) return;
    setBusy(true); setError("");
    try {
      await updateSocietyChatGroup(group.group_id, name.trim() || group.name, members);
      await client.invalidateQueries({ queryKey: GROUPS_KEY });
      setEditing(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await deleteSocietyChatGroup(group.group_id);
      if (inMeeting) useOfficeStore.getState().endMeeting();
      await client.invalidateQueries({ queryKey: GROUPS_KEY });
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <li className="team-card" data-meeting={inMeeting || undefined}>
      <div className="team-card-head">
        <span className="team-card-name" title={group.name}>{group.name}</span>
        <span className="office-hint">
          {t("society.office.team_members").replace("{0}", String(present.length))}
          {working > 0 ? ` · ${t("society.office.team_working").replace("{0}", String(working))}` : ""}
        </span>
      </div>
      <div className="team-badges">
        {present.map((agent) => <MemberBadge key={agent.agentId} agent={agent} />)}
      </div>
      {!editing ? (
        <div className="office-actions">
          <button type="button" className="office-action office-action-primary" disabled={inMeeting || present.length === 0}
            aria-label={t("society.office.team_gather_label").replace("{0}", group.name)} onClick={meet}>
            {inMeeting ? t("society.office.team_meeting_now") : t("society.office.team_meet")}
          </button>
          {actions.onOpenGroup && (
            <button type="button" className="office-action" aria-label={t("society.office.team_open_label").replace("{0}", group.name)}
              onClick={() => actions.onOpenGroup?.(group.group_id)}>{t("society.office.team_chat")}</button>
          )}
          {!sample && <button type="button" className="office-action" onClick={openEditor}>{t("society.office.team_edit")}</button>}
        </div>
      ) : (
        <div className="team-editor">
          <label className="office-field">
            <span>{t("society.office.team_name")}</span>
            <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />
          </label>
          <MemberPicker agents={agents} selected={members}
            onToggle={(id) => setMembers((m) => (m.includes(id) ? m.filter((x) => x !== id) : [...m, id]))} onSet={setMembers} />
          {members.length < 2 && <p className="office-hint">{t("society.office.team_edit_need_more")}</p>}
          <div className="office-actions">
            <button type="button" className="office-action office-action-primary" disabled={busy || members.length < 2} aria-busy={busy}
              onClick={() => void save()}>{t("society.office.team_save")}</button>
            <button type="button" className="office-action" disabled={busy} onClick={() => setEditing(false)}>{t("society.office.team_cancel")}</button>
            {confirmDelete ? (
              <button type="button" className="office-action team-danger" disabled={busy} onClick={() => void remove()}>
                {t("society.office.team_delete_confirm")}
              </button>
            ) : (
              <button type="button" className="office-action" disabled={busy} onClick={() => setConfirmDelete(true)}>{t("society.office.team_delete")}</button>
            )}
          </div>
        </div>
      )}
      {error && <p role="alert" className="office-error">{error}</p>}
    </li>
  );
}

export function TeamRoomPanel({ agents, layout, sample, actions }: { agents: SocietyAgent[]; layout: OfficeLayout; sample: boolean; actions: OfficeActions }) {
  const t = useT();
  const client = useQueryClient();
  const store = useOfficeStore();
  const groups = useSocietyChatGroups(!sample);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState("");
  const teams = useMemo(() => [...(groups.data ?? [])].sort((a, b) => b.updated_ms - a.updated_ms), [groups.data]);
  const members = store.teamDraft.filter((id) => agents.some((a) => a.agentId === id));
  // The new-team form stays folded behind a button once teams exist, unless agents were already picked on the map.
  const [composing, setComposing] = useState(false);
  const showComposer = composing || teams.length === 0 || members.length > 0;
  const canCreate = !sample && !busy && members.length >= 2;

  const setDraft = (ids: string[]) => {
    store.clearDraft();
    ids.forEach((id) => store.toggleDraft(id));
  };
  const create = async () => {
    if (!canCreate) return;
    setBusy(true); setError(""); setCreated("");
    try {
      const names = members.map((id) => agents.find((a) => a.agentId === id)?.name ?? id);
      const group = await createSocietyChatGroup(name.trim() || names.join(" + "), members);
      await client.invalidateQueries({ queryKey: GROUPS_KEY });
      store.startMeeting({ groupId: group.group_id, name: group.name, members, untilMs: Date.now() + MEETING_MS }, meetingSeats(layout, members));
      store.clearDraft();
      setName("");
      setComposing(false);
      setCreated(group.name);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <MeetingBanner agents={agents} layout={layout} actions={actions} />
      {sample && <p className="office-note-inline">{t("society.office.sample")}</p>}
      {created && <p className="office-success" role="status">{t("society.office.team_created").replace("{0}", created)}</p>}

      <h3 className="office-subhead">{t("society.office.team_yours")}</h3>
      {groups.isError && <p role="alert" className="office-error">{t("society.office.team_load_error").replace("{0}", errorText(groups.error))}</p>}
      {teams.length === 0 && !groups.isLoading && !groups.isError && <p className="office-note-inline">{t("society.office.team_none")}</p>}
      {teams.length > 0 && (
        <ul className="team-cards">
          {teams.map((group) => <TeamCard key={group.group_id} group={group} agents={agents} layout={layout} sample={sample} actions={actions} />)}
        </ul>
      )}

      {!showComposer ? (
        <button type="button" className="office-action" onClick={() => setComposing(true)}>{t("society.office.team_new")}</button>
      ) : (
        <section className="team-composer" aria-label={t("society.office.team_new")}>
          <h3 className="office-subhead">{t("society.office.team_new")}</h3>
          <p className="office-hint">{t("society.office.team_body")}</p>
          {agents.length === 0 ? <p className="office-note-inline">{t("society.office.manage_empty")}</p> : (
            <>
              <label className="office-field">
                <span>{t("society.office.team_name")}</span>
                <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder={t("society.office.team_name_placeholder")}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void create(); } }} />
              </label>
              <MemberPicker agents={agents} selected={members} onToggle={store.toggleDraft} onSet={setDraft} />
            </>
          )}
          <div className="office-actions">
            <button type="button" className="office-action office-action-primary" disabled={!canCreate} aria-busy={busy} onClick={() => void create()}>
              {t("society.office.team_create").replace("{0}", String(members.length))}
            </button>
            {teams.length > 0 && (
              <button type="button" className="office-action" onClick={() => { setComposing(false); store.clearDraft(); }}>{t("society.office.team_cancel")}</button>
            )}
          </div>
          {!sample && members.length < 2 && agents.length >= 2 && <p className="office-hint">{t("society.office.team_need_more")}</p>}
          {error && <p role="alert" className="office-error">{error}</p>}
        </section>
      )}
    </>
  );
}

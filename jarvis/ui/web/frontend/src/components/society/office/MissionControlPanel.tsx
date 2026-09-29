/**
 * Mission Control, the coding floor's centre hub: start new coding agents and
 * brief several of them at once.
 *
 * "New agents" opens panes through the IDE's own `POST /terminals` (workspace,
 * CLI, how many) and, when a first task is given, waits for each pane to come
 * up and hands it the task through `/terminals/{name}/prompt` — the path voice
 * and the grid use, so every prompt is recorded and receipted the same way.
 * "Brief the floor" sends one message to every picked session. The office adds
 * no backend contract of its own.
 */
import { useCallback, useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { Radio, Rocket } from "lucide-react";
import { useT } from "@/i18n";
import {
  fetchIdeAgents, fetchIdeState, fetchWorkspacePanes, interruptTerminal, openTerminal, promptTerminal,
  type AgentStatus, type WorkspaceCard,
} from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { paneOccupants, type PaneOccupant } from "./codingFloor";
import "./missionControl.css";

/** At most this many agents per launch; the IDE caps the workspace on its own too. */
export const MAX_LAUNCH = 4;
/** A new pane gets this long to start before its first task is given up on. */
const START_TIMEOUT_MS = 30_000;
const START_POLL_MS = 600;
const START_JITTER_MS = 300;

type Tab = "new" | "fleet";
const TABS: readonly Tab[] = ["new", "fleet"];

type LaunchLine = { name: string; tone: "busy" | "ok" | "warn" | "error"; text: string };

/** Coding CLIs a new agent can run: installed, and not a plain shell. Pure. */
export function launchableAgents(agents: readonly AgentStatus[]): AgentStatus[] {
  return agents.filter((a) => a.installed && (a.kind ?? "cli") !== "shell");
}

/** Which sessions a fleet filter picks. Pure. */
export function pickFleet(occupants: readonly PaneOccupant[], filter: "all" | "working" | "waiting" | "idle" | "none"): Set<string> {
  if (filter === "none") return new Set();
  return new Set(occupants.filter((o) => filter === "all" || o.agent.state === filter).map((o) => o.agent.agentId));
}

/** Wait until the pane `name` of `workspaceId` is live; false when it failed or the clock ran out. */
async function waitLive(workspaceId: string, name: string): Promise<boolean> {
  const deadline = Date.now() + START_TIMEOUT_MS;
  for (;;) {
    try {
      const { panes } = await fetchWorkspacePanes();
      const row = panes.find((p) => p.workspace_id === workspaceId && p.name === name);
      if (row?.status === "live") return true;
      if (row && row.status !== "pending") return false;
    } catch (err) {
      // One failed poll is not an answer; the next one decides.
      console.warn("Mission Control: pane poll failed", err);
    }
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, START_POLL_MS + Math.random() * START_JITTER_MS));
  }
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function NewAgentsTab() {
  const t = useT();
  const [workspaces, setWorkspaces] = useState<WorkspaceCard[] | null>(null);
  const [agents, setAgents] = useState<AgentStatus[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState("");
  const [agentName, setAgentName] = useState("");
  const [count, setCount] = useState(1);
  const [task, setTask] = useState("");
  const [sharpen, setSharpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<LaunchLine[]>([]);

  useEffect(() => {
    let alive = true;
    Promise.all([fetchIdeState(), fetchIdeAgents(true)])
      .then(([state, list]) => {
        if (!alive) return;
        setWorkspaces(state.workspaces);
        setWorkspaceId((current) => current || state.active_id || state.workspaces[0]?.id || "");
        const usable = launchableAgents(list.agents);
        setAgents(usable);
        setAgentName((current) => current || usable[0]?.name || "");
      })
      .catch((err) => { if (alive) setLoadError(errorText(err)); });
    return () => { alive = false; };
  }, []);

  const setLine = (index: number, line: LaunchLine) =>
    setLines((prev) => { const next = [...prev]; next[index] = line; return next; });

  const launch = async () => {
    if (running || !workspaceId || !agentName) return;
    setRunning(true);
    setLines([]);
    const brief = task.trim();
    for (let i = 0; i < count; i += 1) {
      setLine(i, { name: "", tone: "busy", text: t("society.office.mission_opening") });
      let name = "";
      try {
        name = (await openTerminal({ workspace_id: workspaceId, agent: agentName })).name;
      } catch (err) {
        setLine(i, { name: "", tone: "error", text: t("society.office.mission_failed").replace("{0}", errorText(err)) });
        break;
      }
      void useWorkspacePanesStore.getState().load();
      if (!brief) { setLine(i, { name, tone: "ok", text: t("society.office.mission_done_open").replace("{0}", name) }); continue; }
      setLine(i, { name, tone: "busy", text: t("society.office.mission_waiting").replace("{0}", name) });
      if (!(await waitLive(workspaceId, name))) {
        setLine(i, { name, tone: "warn", text: t("society.office.mission_not_started").replace("{0}", name) });
        continue;
      }
      setLine(i, { name, tone: "busy", text: t("society.office.mission_briefing").replace("{0}", name) });
      try {
        const result = await promptTerminal(name, brief, { compose: sharpen, workspaceId });
        setLine(i, result.submitted === true
          ? { name, tone: "ok", text: t("society.office.mission_done_briefed").replace("{0}", name) }
          : { name, tone: "warn", text: result.detail || t("society.office.cmd_unconfirmed") });
      } catch (err) {
        setLine(i, { name, tone: "error", text: errorText(err) });
      }
    }
    void useWorkspacePanesStore.getState().load();
    setRunning(false);
  };

  if (loadError) return <p className="office-hint" role="alert">{loadError}</p>;
  if (!workspaces || !agents) return <p className="office-hint" role="status">{t("society.office.mission_loading")}</p>;
  if (workspaces.length === 0) {
    return (
      <>
        <p>{t("society.office.mission_no_workspace")}</p>
        <button type="button" className="office-action office-action-primary"
          onClick={() => useEventStore.getState().setActiveSection("agentic-ide")}>{t("society.office.open_ide")}</button>
      </>
    );
  }
  return (
    <div className="office-mc-form">
      <p className="office-mc-intro">{t("society.office.mission_intro")}</p>
      <label className="office-mc-field">
        <span>{t("society.office.mission_workspace")}</span>
        <select value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)} disabled={running}>
          {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}{w.branch ? ` · ${w.branch}` : ""}</option>)}
        </select>
      </label>
      <fieldset className="office-mc-field" disabled={running}>
        <legend>{t("society.office.mission_agent")}</legend>
        {agents.length === 0 ? <p className="office-hint">{t("society.office.mission_no_agents")}</p> : (
          <div className="office-mc-chips">
            {agents.map((a) => (
              <button key={a.name} type="button" aria-pressed={agentName === a.name} title={a.description || a.display_name}
                onClick={() => setAgentName(a.name)}>{a.display_name}</button>
            ))}
          </div>
        )}
      </fieldset>
      <fieldset className="office-mc-field" disabled={running}>
        <legend>{t("society.office.mission_count")}</legend>
        <div className="office-mc-chips">
          {Array.from({ length: MAX_LAUNCH }, (_, i) => i + 1).map((n) => (
            <button key={n} type="button" aria-pressed={count === n} onClick={() => setCount(n)}>{n}</button>
          ))}
        </div>
      </fieldset>
      <label className="office-mc-field">
        <span>{t("society.office.mission_task")}</span>
        <textarea rows={3} value={task} maxLength={8000} disabled={running} placeholder={t("society.office.mission_task_placeholder")}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void launch(); } }} />
      </label>
      <label className="office-cmd-check">
        <input type="checkbox" checked={sharpen} disabled={running || !task.trim()} onChange={(e) => setSharpen(e.target.checked)} />
        <span>{t("society.office.cmd_sharpen")}</span>
      </label>
      <button type="button" className="office-action office-action-primary office-mc-go" disabled={running || !workspaceId || !agentName}
        onClick={() => void launch()}>
        <Rocket aria-hidden />
        {count === 1 ? t("society.office.mission_start_one") : t("society.office.mission_start_many").replace("{0}", String(count))}
      </button>
      {lines.length > 0 && (
        <ul className="office-mc-log" aria-live="polite">
          {lines.map((line, i) => <li key={i} data-tone={line.tone}>{line.text}</li>)}
        </ul>
      )}
    </div>
  );
}

function FleetTab() {
  const t = useT();
  const panes = useWorkspacePanesStore((s) => s.panes);
  const occupants = useMemo(() => paneOccupants(panes), [panes]);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const chosen = occupants.filter((o) => picked.has(o.agent.agentId));

  const toggle = (id: string) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const broadcast = useCallback(async (kind: "send" | "stop") => {
    const text = message.trim();
    if (sending || chosen.length === 0 || (kind === "send" && !text)) return;
    setSending(true);
    setResult(null);
    const targets = kind === "stop" ? chosen.filter((o) => o.dot === "working") : chosen;
    const outcomes = await Promise.allSettled(targets.map(async (o) => {
      if (kind === "stop") { await interruptTerminal(o.pane.name, o.pane.workspace_id); return true; }
      if (o.pane.status !== "live" || !o.pane.accepts_prompts) throw new Error(t("society.office.cmd_not_running"));
      const res = await promptTerminal(o.pane.name, text, { workspaceId: o.pane.workspace_id });
      if (res.submitted !== true) throw new Error(res.detail || t("society.office.cmd_unconfirmed"));
      return true;
    }));
    const missed = targets.filter((_, i) => outcomes[i].status === "rejected").map((o) => o.agent.name);
    const reached = targets.length - missed.length;
    const summary = t("society.office.fleet_result").replace("{0}", String(reached)).replace("{1}", String(targets.length));
    setResult({ tone: missed.length ? "warn" : "ok", text: missed.length ? `${summary} ${t("society.office.fleet_result_missed").replace("{0}", missed.join(", "))}` : summary });
    if (kind === "send" && missed.length === 0) setMessage("");
    void useWorkspacePanesStore.getState().load();
    setSending(false);
  }, [chosen, message, sending, t]);

  if (occupants.length === 0) return <p>{t("society.office.fleet_empty")}</p>;
  return (
    <div className="office-mc-form">
      <p className="office-mc-intro">{t("society.office.fleet_intro")}</p>
      <div className="office-mc-chips" role="group" aria-label={t("society.office.fleet_pick")}>
        {(["all", "working", "waiting", "idle", "none"] as const).map((filter) => (
          <button key={filter} type="button" onClick={() => setPicked(pickFleet(occupants, filter))}>{t(`society.office.fleet_pick_${filter}`)}</button>
        ))}
      </div>
      <ul className="office-mc-fleet">
        {occupants.map((o) => (
          <li key={o.agent.agentId}>
            <label>
              <input type="checkbox" checked={picked.has(o.agent.agentId)} onChange={() => toggle(o.agent.agentId)} />
              <i className="office-dot" data-state={o.agent.state} data-dot={o.dot} aria-hidden />
              <span className="office-mc-fleet-name" title={o.pane.recap || o.pane.last_prompt}>{o.agent.name}</span>
              <span className="office-mc-fleet-state">{t(`society.office.pane_state_${o.stateKey}`)} · {o.agent.providerLabel}</span>
            </label>
          </li>
        ))}
      </ul>
      <textarea className="office-mc-message" rows={3} value={message} maxLength={8000} disabled={sending}
        placeholder={t("society.office.fleet_placeholder")} aria-label={t("society.office.fleet_placeholder")}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void broadcast("send"); } }} />
      <div className="office-actions">
        <button type="button" className="office-action office-action-primary" disabled={sending || chosen.length === 0 || !message.trim()}
          onClick={() => void broadcast("send")}>
          <Radio aria-hidden />{t("society.office.fleet_send").replace("{0}", String(chosen.length))}
        </button>
        <button type="button" className="office-action" disabled={sending || !chosen.some((o) => o.dot === "working")}
          onClick={() => void broadcast("stop")}>
          {t("society.office.fleet_stop").replace("{0}", String(chosen.filter((o) => o.dot === "working").length))}
        </button>
      </div>
      {result && <p className="office-hint office-mc-result" role="status" data-tone={result.tone}>{result.text}</p>}
    </div>
  );
}

export function MissionControlPanel() {
  const t = useT();
  const baseId = useId();
  const [tab, setTab] = useState<Tab>("new");
  return (
    <div className="office-mc">
      <div className="office-mc-tabs" role="tablist" aria-label={t("society.office.cp_mission")}>
        {TABS.map((id) => (
          <button key={id} type="button" role="tab" id={`${baseId}-${id}`} aria-selected={tab === id} aria-controls={`${baseId}-${id}-panel`}
            tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)}
            onKeyDown={(e) => {
              if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
              e.preventDefault();
              e.stopPropagation();
              setTab(tab === "new" ? "fleet" : "new");
            }}>
            {t(`society.office.mission_tab_${id}`)}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${baseId}-${tab}-panel`} aria-labelledby={`${baseId}-${tab}`}>
        {tab === "new" ? <NewAgentsTab /> : <FleetTab />}
      </div>
    </div>
  );
}

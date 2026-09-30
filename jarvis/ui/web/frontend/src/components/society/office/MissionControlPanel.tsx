/**
 * Mission Control, the screen in the middle of the coding floor: start new
 * coding agents, and message several running ones at once.
 *
 * "Start an agent" opens panes through the IDE's own `POST /terminals`
 * (workspace, CLI, how many) and, when a task is given, waits for each pane to
 * come up and hands it the task through `/terminals/{name}/prompt` — the path
 * voice and the grid use, so every prompt is recorded and receipted the same
 * way. Files dropped on the launcher are held until each pane is live and
 * then go through the pane's own `/terminals/{name}/attach`. "New workspace"
 * opens a folder as another workspace through the IDE's `POST /session`,
 * with the new agents as its panes. "Message several agents" sends one
 * message to every picked session. The office adds no backend contract of
 * its own.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Minus, Paperclip, Plus, X } from "lucide-react";
import { useT } from "@/i18n";
import {
  attachToTerminal, fetchIdeAgents, fetchIdeProjects, fetchIdeState, fetchWorkspacePanes, interruptTerminal,
  openIdeWorkspace, openTerminal, promptTerminal,
  type AgentStatus, type DropAttachment, type IdeProject, type WorkspaceCard,
} from "@/lib/agenticIdeApi";
import { openProject } from "@/lib/chatLibraryApi";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { AgentMark } from "@/components/agentic/AgentMark";
import { BrandedSelect } from "@/components/ui/select";
import { paneOccupants, type PaneOccupant } from "./codingFloor";
import { NewWorkspaceFields, type NewWorkspaceTarget } from "./NewWorkspaceFields";
import { briefWithFiles, heldPayload, useSpawnFiles, type HeldFile } from "./spawnFiles";
import "./missionControl.css";

/** At most this many agents per launch; the IDE caps the workspace on its own too. */
export const MAX_LAUNCH = 4;
/** A new pane gets this long to start before its first task is given up on. */
const START_TIMEOUT_MS = 30_000;
const START_POLL_MS = 600;
const START_JITTER_MS = 300;

/** The workspace picker's value for "open a new workspace". */
export const NEW_WORKSPACE = "__new__";

type LaunchLine = { tone: "busy" | "ok" | "warn" | "error"; text: string };

/** Coding CLIs a new agent can run: installed, and not a plain shell. Pure. */
export function launchableAgents(agents: readonly AgentStatus[]): AgentStatus[] {
  return agents.filter((a) => a.installed && (a.kind ?? "cli") !== "shell");
}

/** Projects a new workspace can open in: real, reachable folders the user connected. Pure. */
export function workspaceProjects(projects: readonly IdeProject[]): IdeProject[] {
  return projects.filter((p) => !p.scratch && !p.archived && p.exists);
}

/** Every session on the floor, or none: the one bulk pick the list offers. Pure. */
export function pickAll(occupants: readonly PaneOccupant[], picked: ReadonlySet<string>): Set<string> {
  const everyone = occupants.every((o) => picked.has(o.agent.agentId));
  return everyone ? new Set() : new Set(occupants.map((o) => o.agent.agentId));
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

/** What a launch read once and every pane shares: the attached files, as references and analysis. */
type Handover = { references: string[]; analysis: DropAttachment[] };

/** Start coding agents: CLI, workspace (or a new one), how many, an optional first task and files. Also the coding floor's spawn point. */
export function StartAgent() {
  const t = useT();
  const [workspaces, setWorkspaces] = useState<WorkspaceCard[] | null>(null);
  const [projects, setProjects] = useState<IdeProject[]>([]);
  const [agents, setAgents] = useState<AgentStatus[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState("");
  const [newTarget, setNewTarget] = useState<NewWorkspaceTarget | null>(null);
  const [newName, setNewName] = useState("");
  const [agentName, setAgentName] = useState("");
  const [count, setCount] = useState(1);
  const [task, setTask] = useState("");
  const [sharpen, setSharpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<LaunchLine[]>([]);
  const files = useSpawnFiles();
  const fileInput = useRef<HTMLInputElement>(null);

  const loadProjects = useCallback(() => fetchIdeProjects()
    .then((listing) => setProjects(workspaceProjects(listing.projects)))
    // Only the new-workspace picker lists these; without them it still offers "choose a folder".
    .catch((err) => console.warn("Mission Control: projects did not load", err)), []);

  useEffect(() => {
    let alive = true;
    Promise.all([fetchIdeState(), fetchIdeAgents(true)])
      .then(([state, list]) => {
        if (!alive) return;
        setWorkspaces(state.workspaces);
        setWorkspaceId((current) => current || state.active_id || state.workspaces[0]?.id || NEW_WORKSPACE);
        const usable = launchableAgents(list.agents);
        setAgents(usable);
        setAgentName((current) => current || usable[0]?.name || "");
      })
      .catch((err) => { if (alive) setLoadError(errorText(err)); });
    void loadProjects();
    return () => { alive = false; };
  }, [loadProjects]);

  const setLine = (index: number, line: LaunchLine) =>
    setLines((prev) => { const next = [...prev]; next[index] = line; return next; });

  /** Wait for one new pane, then hand it the files and the task. */
  const settle = async (
    i: number, name: string, wsId: string, brief: string, held: readonly HeldFile[], shared: { handover?: Handover },
  ) => {
    if (!brief && held.length === 0) {
      setLine(i, { tone: "ok", text: t("society.office.mission_done_open").replace("{0}", name) });
      return;
    }
    setLine(i, { tone: "busy", text: t("society.office.mission_waiting").replace("{0}", name) });
    if (!(await waitLive(wsId, name))) {
      setLine(i, { tone: "warn", text: t("society.office.mission_not_started").replace("{0}", name) });
      return;
    }
    let text = brief;
    let attachments: DropAttachment[] = [];
    if (held.length > 0) {
      setLine(i, { tone: "busy", text: t("society.office.mission_files_handing").replace("{0}", name) });
      let handover: Handover;
      try {
        if (!brief) {
          // Files alone: typed into the pane's input, as a drop on the pane itself would be.
          await attachToTerminal(name, heldPayload(held));
          setLine(i, { tone: "ok", text: t("society.office.mission_files_typed").replace("{0}", name) });
          return;
        }
        // Read once per launch: every pane shares the workspace and the CLI, so
        // the references are the same, and describing a picture again for each
        // pane would bill the user again for the same answer.
        handover = shared.handover ??= await attachToTerminal(name, { ...heldPayload(held), analyze: true, deliver: false })
          .then((result) => ({ references: result.references, analysis: result.analysis ?? [] }));
      } catch (err) {
        setLine(i, { tone: "error", text: t("society.office.mission_files_failed").replace("{0}", name).replace("{1}", errorText(err)) });
        return;
      }
      attachments = handover.analysis;
      text = briefWithFiles(brief, handover.references, attachments);
    }
    setLine(i, { tone: "busy", text: t("society.office.mission_briefing").replace("{0}", name) });
    try {
      const result = await promptTerminal(name, text, { compose: sharpen, workspaceId: wsId, attachments });
      setLine(i, result.submitted === true
        ? { tone: "ok", text: t("society.office.mission_done_briefed").replace("{0}", name) }
        : { tone: "warn", text: result.detail || t("society.office.cmd_unconfirmed") });
    } catch (err) {
      setLine(i, { tone: "error", text: errorText(err) });
    }
  };

  const creating = workspaceId === NEW_WORKSPACE;
  const ready = Boolean(agentName) && (creating ? newTarget !== null : Boolean(workspaceId));

  const launch = async () => {
    if (running || !ready) return;
    setRunning(true);
    setLines([]);
    const brief = task.trim();
    const held = files.held;
    const shared: { handover?: Handover } = {};
    let opened = 0;
    if (creating && newTarget) {
      setLine(0, { tone: "busy", text: t("society.office.mission_ws_creating") });
      try {
        const project = newTarget.projectId ? { id: newTarget.projectId, path: newTarget.path } : await openProject(newTarget.path);
        const { session, state } = await openIdeWorkspace(
          project.path, Array.from({ length: count }, () => ({ agent: agentName })),
          { projectId: project.id, name: newName.trim() || undefined },
        );
        // The next launch lands beside these agents, not in yet another new workspace.
        setWorkspaces(state.workspaces);
        setWorkspaceId(session.id);
        setNewTarget(null);
        setNewName("");
        void loadProjects();
        void useWorkspacePanesStore.getState().load();
        opened = session.terminals.length;
        for (const [i, term] of session.terminals.entries()) await settle(i, term.name, session.id, brief, held, shared);
      } catch (err) {
        setLine(0, { tone: "error", text: t("society.office.mission_failed").replace("{0}", errorText(err)) });
      }
    } else {
      for (let i = 0; i < count; i += 1) {
        setLine(i, { tone: "busy", text: t("society.office.mission_opening") });
        let name = "";
        try {
          name = (await openTerminal({ workspace_id: workspaceId, agent: agentName })).name;
        } catch (err) {
          setLine(i, { tone: "error", text: t("society.office.mission_failed").replace("{0}", errorText(err)) });
          break;
        }
        opened += 1;
        void useWorkspacePanesStore.getState().load();
        await settle(i, name, workspaceId, brief, held, shared);
      }
    }
    // Kept for another try when not a single agent opened.
    if (opened > 0) {
      if (brief) setTask("");
      files.release(held);
    }
    void useWorkspacePanesStore.getState().load();
    setRunning(false);
  };

  if (loadError) return <p className="office-mc-note" role="alert">{loadError}</p>;
  if (!workspaces || !agents) return <p className="office-mc-note" role="status">{t("society.office.mission_loading")}</p>;
  return (
    <div className="office-mc-block office-mc-dropzone" data-dragging={files.dragging || undefined} {...files.handlers}>
      {agents.length === 0 ? <p className="office-mc-note">{t("society.office.mission_no_agents")}</p> : (
        <div className="office-mc-agents" role="radiogroup" aria-label={t("society.office.mission_agent")}>
          {agents.map((a) => (
            <button key={a.name} type="button" role="radio" aria-checked={agentName === a.name} disabled={running}
              title={a.description || a.display_name} onClick={() => setAgentName(a.name)}>
              <AgentMark agent={a.name} label={a.display_name} logoUrl={a.logo_url || undefined} size="sm" />
              <span>{a.display_name}</span>
            </button>
          ))}
        </div>
      )}

      {workspaces.length === 0 ? <p className="office-mc-note">{t("society.office.mission_no_workspace")}</p> : (
        <div className="office-mc-row">
          <span>{t("society.office.mission_workspace")}</span>
          <BrandedSelect value={workspaceId} onValueChange={setWorkspaceId} disabled={running}
            ariaLabel={t("society.office.mission_workspace")} className="office-mc-select px-2 py-1.5 text-xs"
            options={[
              ...workspaces.map((w) => ({ value: w.id, label: w.name, hint: w.branch || undefined })),
              { value: NEW_WORKSPACE, label: t("society.office.mission_ws_new") },
            ]} />
        </div>
      )}
      {creating && (
        <NewWorkspaceFields projects={projects} target={newTarget} onTarget={setNewTarget}
          name={newName} onName={setNewName} disabled={running} />
      )}

      <textarea className="office-mc-task" rows={3} value={task} maxLength={8000} disabled={running}
        placeholder={t("society.office.mission_task_placeholder")} aria-label={t("society.office.mission_task")}
        onChange={(e) => setTask(e.target.value)} onPaste={files.onPaste}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void launch(); } }} />
      {files.held.length > 0 && (
        <ul className="office-mc-files" aria-label={t("society.office.mission_files_attach")}>
          {files.held.map((h) => (
            <li key={h.key} title={h.path ?? h.name}>
              <span>{h.name}</span>
              <button type="button" disabled={running} onClick={() => files.remove(h.key)}
                aria-label={t("society.office.mission_files_remove").replace("{0}", h.name)}><X aria-hidden /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="office-mc-tools">
        <button type="button" className="office-mc-link office-mc-attach" disabled={running} onClick={() => fileInput.current?.click()}>
          <Paperclip aria-hidden />{t("society.office.mission_files_attach")}
        </button>
        <input ref={fileInput} type="file" multiple hidden tabIndex={-1}
          onChange={(e) => { files.addFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        {task.trim() && (
          <label className="office-cmd-check">
            <input type="checkbox" checked={sharpen} disabled={running} onChange={(e) => setSharpen(e.target.checked)} />
            <span>{t("society.office.cmd_sharpen")}</span>
          </label>
        )}
      </div>

      <div className="office-mc-go">
        <div className="office-mc-stepper" role="group" aria-label={t("society.office.mission_count")}>
          <button type="button" disabled={running || count <= 1} onClick={() => setCount((n) => n - 1)}
            aria-label={t("society.office.mission_fewer")}><Minus aria-hidden /></button>
          <output aria-live="polite">{count}</output>
          <button type="button" disabled={running || count >= MAX_LAUNCH} onClick={() => setCount((n) => n + 1)}
            aria-label={t("society.office.mission_more")}><Plus aria-hidden /></button>
        </div>
        <button type="button" className="office-mc-primary" disabled={running || !ready} onClick={() => void launch()}>
          {count === 1 ? t("society.office.mission_start_one") : t("society.office.mission_start_many").replace("{0}", String(count))}
        </button>
      </div>

      {lines.length > 0 && (
        <ul className="office-mc-log" aria-live="polite">
          {lines.map((line, i) => <li key={i} data-tone={line.tone}><i aria-hidden />{line.text}</li>)}
        </ul>
      )}
      {files.dragging && <div className="office-mc-drophint" aria-hidden>{t("society.office.mission_files_drop")}</div>}
    </div>
  );
}

function MessageSeveral() {
  const t = useT();
  const panes = useWorkspacePanesStore((s) => s.panes);
  const occupants = useMemo(() => paneOccupants(panes), [panes]);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const chosen = occupants.filter((o) => picked.has(o.agent.agentId));
  const chosenWorking = chosen.filter((o) => o.dot === "working");

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
    const targets = kind === "stop" ? chosenWorking : chosen;
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
  }, [chosen, chosenWorking, message, sending, t]);

  if (occupants.length === 0) return <p className="office-mc-note">{t("society.office.fleet_empty")}</p>;
  const everyone = occupants.every((o) => picked.has(o.agent.agentId));
  return (
    <div className="office-mc-block">
      <ul className="office-mc-fleet">
        {occupants.map((o) => (
          <li key={o.agent.agentId}>
            <label>
              <input type="checkbox" checked={picked.has(o.agent.agentId)} onChange={() => toggle(o.agent.agentId)} />
              <span className="office-mc-fleet-name" title={o.pane.recap || o.pane.last_prompt}>{o.agent.name}</span>
              <span className="office-mc-fleet-state"><i className="office-dot" data-state={o.agent.state} data-dot={o.dot} aria-hidden />{t(`society.office.pane_state_${o.stateKey}`)}</span>
            </label>
          </li>
        ))}
      </ul>
      <button type="button" className="office-mc-link" onClick={() => setPicked(pickAll(occupants, picked))}>
        {t(everyone ? "society.office.fleet_select_none" : "society.office.fleet_select_all")}
      </button>
      <textarea className="office-mc-task" rows={2} value={message} maxLength={8000} disabled={sending}
        placeholder={t("society.office.fleet_placeholder")} aria-label={t("society.office.fleet_placeholder")}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void broadcast("send"); } }} />
      <div className="office-mc-go">
        <button type="button" className="office-mc-secondary" disabled={sending || chosenWorking.length === 0} onClick={() => void broadcast("stop")}>
          {t("society.office.fleet_stop").replace("{0}", String(chosenWorking.length))}
        </button>
        <button type="button" className="office-mc-primary" disabled={sending || chosen.length === 0 || !message.trim()} onClick={() => void broadcast("send")}>
          {t("society.office.fleet_send").replace("{0}", String(chosen.length))}
        </button>
      </div>
      {result && <p className="office-mc-note" role="status" data-tone={result.tone}>{result.text}</p>}
    </div>
  );
}

export function MissionControlPanel() {
  const t = useT();
  return (
    <div className="office-mc">
      <section aria-labelledby="office-mc-start">
        <h3 id="office-mc-start" className="office-mc-heading">{t("society.office.mission_new_title")}</h3>
        <StartAgent />
      </section>
      <details className="office-mc-more">
        <summary className="office-mc-heading">{t("society.office.fleet_title")}</summary>
        <MessageSeveral />
      </details>
    </div>
  );
}

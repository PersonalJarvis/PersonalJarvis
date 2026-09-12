/** The agent's routine editor, backed by the existing task scheduler. */
import { useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Clock, Plus, X } from "lucide-react";
import { useT } from "@/i18n";
import { Switch } from "@/components/ui/switch";
import { cancelAndDeleteTask, cancelTask, fetchTask, runTaskNow, setTaskEnabled } from "@/hooks/useAutomations";
import { describeTrigger, displayRoutineTitle, type LiveRoutine } from "../cardData";
import type { TaskDetail, TaskStep } from "@/views/automations/automationsModel";
import { TriggerBuilder } from "./TriggerBuilder";
import { WebhookConnection } from "./WebhookConnection";
import { SourceControls } from "./SourceControls";

const field = "w-full rounded-lg border border-border bg-background px-3 py-2 text-[12px] text-foreground";
const button = "rounded-md bg-secondary px-2.5 py-1.5 text-[12px] text-foreground hover:bg-secondary/70 disabled:opacity-50";
const recurring = ["every", "calendar", "cron", "webhook", "event_hook", "source", "on_event"];
const terminal = ["completed", "failed", "cancelled", "interrupted"];
const timeKinds = ["every", "calendar", "cron"];

interface Run { id: string; timestamp: number; status: string; text: string }

/** New runs have explicit boundaries; retain older result/error records too. */
export function routineRuns(taskId: string, steps: TaskStep[]): Run[] {
  const runs: Run[] = [];
  let current: Run | undefined;
  for (const step of steps) {
    const event = step.payload.event;
    const text = String(step.payload.message ?? step.payload.text ?? "");
    if (event === "run_started") {
      if (current?.status === "running") current.status = "interrupted";
      current = { id: `${taskId}:${step.seq}`, timestamp: step.timestamp_ns / 1e6, status: "running", text: "" };
      runs.push(current);
    } else if (event === "agent_result" && current) {
      current.text = text;
    } else if (["run_completed", "run_cancelled", "error", "deferred"].includes(String(event))) {
      const status = event === "run_completed" ? "completed" : event === "run_cancelled" ? "cancelled" : event === "deferred" ? "pending" : "failed";
      if (current) { current.status = status; if (text) current.text = text; current = undefined; }
      else runs.push({ id: `${taskId}:${step.seq}`, timestamp: step.timestamp_ns / 1e6, status, text });
    } else if (event === "agent_result") {
      // Legacy runners persisted the result before the final state transition.
      runs.push({ id: `${taskId}:${step.seq}`, timestamp: step.timestamp_ns / 1e6, status: "legacy", text });
    }
  }
  return runs;
}

async function writeRoutine(url: string, body: unknown, method = "PATCH") {
  const response = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof error?.detail === "string" ? error.detail : `HTTP ${response.status}`);
  }
}

export function AgentRoutineDetail({ agentId, routine, onClose }: {
  agentId: string; routine: LiveRoutine; onClose: () => void;
}) {
  const t = useT();
  const label = (key: string) => t(`society.routine_detail.${key}`);
  const client = useQueryClient();
  const members = routine.members ?? [routine];
  const queries = useQueries({ queries: members.map((member) => ({
    queryKey: ["tasks", "detail", member.id], queryFn: () => fetchTask(member.id),
    retry: false, refetchInterval: import.meta.env.MODE === "test" ? false : 3000,
  })) });
  const [draft, setDraft] = useState<{ title: string; prompt: string } | null>(null);
  const [editing, setEditing] = useState<LiveRoutine | "new" | null>(null);
  const [schedule, setSchedule] = useState<Record<string, unknown> | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const details = queries.map((query) => query.data);
  const first = details[0];
  const rawPrompt = String((first?.spec?.action as { prompt?: string } | undefined)?.prompt ?? "");
  const marker = rawPrompt.indexOf("\nRoutine:\n");
  const saved = { title: displayRoutineTitle(first?.title ?? routine.title), prompt: marker >= 0 ? rawPrompt.slice(marker + 10) : routine.prompt ?? rawPrompt };
  const values = draft ?? saved;
  const ready = details.every(Boolean);
  const running = details.some((detail) => detail?.state === "running");
  const active = details.some((detail) => detail && detail.state !== "paused" && !terminal.includes(detail.state));
  const editable = ready && !running && details.every((detail, index) => detail && (
    index > 0 && terminal.includes(detail.state) || recurring.includes(detail.trigger_type) && !terminal.includes(detail.state)
  ));
  const url = `/api/society/agents/${encodeURIComponent(agentId)}/routines`;
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["tasks"] }),
    client.invalidateQueries({ queryKey: ["society", "agent-routines", agentId] }),
  ]);
  const act = async (fn: () => Promise<void>) => {
    setPending(true); setError(""); setNotice("");
    try { await fn(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { await refresh(); setPending(false); }
  };
  const update = (member: LiveRoutine, nextSchedule: unknown) => writeRoutine(`${url}/${encodeURIComponent(member.id)}`, {
    title: values.title.trim(), prompt: values.prompt.trim(), schedule: nextSchedule,
  });
  const valid = Boolean(values.title.trim() && values.prompt.trim());
  const runs = details.flatMap((detail) => detail ? routineRuns(detail.id, detail.steps) : []).sort((a, b) => b.timestamp - a.timestamp);
  const renderConnection = (detail: TaskDetail) => {
    if (detail.trigger_type === "webhook") return <WebhookConnection taskId={detail.id} />;
    if (detail.trigger_type === "source") return <SourceControls taskId={detail.id} source={(detail.trigger as { source: { kind: string } }).source} />;
    return null;
  };

  return <section className="min-h-0 flex-1 space-y-4 overflow-y-auto pb-4 text-foreground" data-testid="agent-routine-detail">
    <button type="button" className="flex items-center gap-1 text-[12px] text-muted-foreground" disabled={pending} onClick={onClose}><ArrowLeft size={14} />{t("society.card.routines")}</button>
    <div className="flex flex-wrap items-center gap-2">
      <label className="mr-auto flex items-center gap-2 text-[12px]"><Switch aria-label={label("active")} checked={active} disabled={pending || !editable} onCheckedChange={(enabled) => void act(async () => {
        for (const detail of details) if (detail && !terminal.includes(detail.state) && (detail.state === "paused") === enabled) await setTaskEnabled(detail.id, enabled);
      })} />{label("active")}</label>
      <button type="button" className={button} disabled={pending || !ready || running} onClick={() => setConfirmDelete(true)}>{label("delete")}</button>
      <button type="button" className={button} disabled={pending || !ready || running || Boolean(draft) || Boolean(editing) || !first || terminal.includes(first.state) || first.trigger_type === "source"} onClick={() => void act(async () => {
        await runTaskNow(routine.id); setNotice(label("test_started"));
      })}>{label("test")}</button>
    </div>
    {confirmDelete && <div className="space-y-2 rounded-lg border border-border p-3" role="alert">
      <p className="text-[12px]">{label("delete_confirm")}</p>
      <button className={button} disabled={pending} onClick={() => void act(async () => {
        for (const detail of [...details].reverse()) if (detail) await cancelAndDeleteTask(detail.id, !terminal.includes(detail.state));
        onClose();
      })}>{label("delete")}</button>{" "}<button className={button} disabled={pending} onClick={() => setConfirmDelete(false)}>{label("cancel")}</button>
    </div>}
    {queries.some((query) => query.isLoading) && <p role="status" className="text-[12px]">{t("tasks_view.loading_details")}</p>}
    {queries.some((query) => query.error) && <p role="alert" className="text-[12px] text-destructive">{t("tasks_view.load_error")}</p>}
    {error && <p role="alert" className="break-words text-[12px] text-destructive">{error}</p>}
    {notice && <p role="status" className="text-[12px] text-success">{notice}</p>}
    <label className="block space-y-1 text-[11px] text-muted-foreground">{label("name")}<input className={field} aria-label={label("name")} maxLength={200} disabled={pending || !editable || Boolean(editing)} value={values.title} onChange={(event) => setDraft({ ...values, title: event.target.value })} /></label>
    <label className="block space-y-1 text-[11px] text-muted-foreground">{label("prompt")}<textarea className={field} aria-label={label("prompt")} maxLength={16000} rows={7} disabled={pending || !editable || Boolean(editing) || (first?.spec?.action as { kind?: string } | undefined)?.kind !== "agent"} value={values.prompt} onChange={(event) => setDraft({ ...values, prompt: event.target.value })} /></label>
    {draft && <div className="flex gap-2"><button className={button} disabled={pending || !valid || !editable || Boolean(editing)} onClick={() => void act(async () => {
      for (const [index, member] of members.entries()) if (!terminal.includes(details[index]!.state)) await update(member, details[index]?.trigger);
      setDraft(null); setNotice(label("saved"));
    })}>{label("save")}</button><button className={button} disabled={pending} onClick={() => setDraft(null)}>{label("cancel")}</button></div>}
    <div className="space-y-2">
      <h4 className="text-[11px] text-muted-foreground">{label("when")}</h4>
      <div className="space-y-2 rounded-xl border border-border p-3">
        {members.map((member, index) => {
          const detail = details[index];
          if (index > 0 && detail && terminal.includes(detail.state)) return null;
          return <div key={member.id} className="space-y-1">
            <button className="flex w-full items-start gap-2 rounded text-left text-[12px] disabled:opacity-70" disabled={pending || !editable || Boolean(draft) || !detail || !timeKinds.includes(detail.trigger_type)} onClick={() => { setEditing({ ...member, trigger: detail?.trigger }); setSchedule(null); }}>
              <Clock size={14} className="mt-0.5 shrink-0" /><span>{describeTrigger(detail?.trigger ?? member.trigger, t)}</span>
            </button>
            {detail?.due_at_ns && detail.state === "scheduled" ? <p className="pl-5 text-[11px] text-muted-foreground">{t("automations_view.next_run")}: {new Date(detail.due_at_ns / 1e6).toLocaleString(undefined, { timeZoneName: "short" })}</p> : null}
            {detail && renderConnection(detail)}
            {index > 0 && <button className="pl-5 text-[11px] text-muted-foreground" disabled={pending || !editable || Boolean(draft)} onClick={() => void act(async () => { await cancelTask(member.id); })}>{label("remove_schedule")}</button>}
          </div>;
        })}
        <button className="flex items-center gap-1 text-[12px] text-muted-foreground disabled:opacity-50" disabled={pending || !editable || Boolean(draft)} onClick={() => { setEditing("new"); setSchedule(null); }}><Plus size={14} />{label("add_schedule")}</button>
      </div>
      {editing && <div className="space-y-2 rounded-lg border border-border p-3">
        <TriggerBuilder key={editing === "new" ? "new" : editing.id} timeOnly initialValue={editing === "new" ? undefined : editing.trigger as Record<string, unknown>} onChange={setSchedule} />
        <button className={button} disabled={pending || !schedule || !valid} onClick={() => void act(async () => {
          if (editing === "new") await writeRoutine(url, { title: values.title, prompt: values.prompt, schedule, parent_task_id: routine.id }, "POST");
          else await update(editing, schedule);
          setEditing(null); setNotice(label("saved"));
        })}>{label("save")}</button>{" "}<button className={button} disabled={pending} onClick={() => setEditing(null)}>{label("cancel")}</button>
      </div>}
    </div>
    <div className="space-y-2">
      <h4 className="text-[11px] text-muted-foreground">{label("history")}</h4>
      {ready && runs.length === 0 && <p className="text-[12px] text-muted-foreground">{label("no_runs")}</p>}
      {runs.map((run) => <details key={run.id} className="text-[12px]">
        <summary className="flex cursor-pointer items-center justify-between gap-2 rounded py-1 hover:bg-secondary"><time dateTime={new Date(run.timestamp).toISOString()}>{new Date(run.timestamp).toLocaleString()}</time><span className="flex items-center gap-1" title={run.status === "legacy" ? label("legacy") : t(`tasks_view.state.${run.status}`)}>
          {run.status === "completed" ? <Check size={15} className="text-success" aria-label={t("tasks_view.state.completed")} /> : run.status === "failed" || run.status === "cancelled" ? <X size={15} className="text-destructive" aria-label={t(`tasks_view.state.${run.status}`)} /> : <Clock size={15} aria-label={run.status === "legacy" ? label("legacy") : t(`tasks_view.state.${run.status}`)} />}
        </span></summary>
        <p className="whitespace-pre-wrap break-words rounded bg-secondary p-2">{run.text || (run.status === "legacy" ? label("legacy") : t(`tasks_view.state.${run.status}`))}</p>
      </details>)}
    </div>
  </section>;
}

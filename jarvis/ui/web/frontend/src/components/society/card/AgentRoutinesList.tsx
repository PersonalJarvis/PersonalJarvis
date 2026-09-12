/** Per-agent routines use the same task store and trigger editor as Automations. */
import { useEffect, useMemo, useState } from "react";
import { Clock, Plus, X } from "lucide-react";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { displayRoutineTitle, routineScheduleLine, useAgentRoutines, useCreateAgentRoutine, type LiveRoutine } from "../cardData";
import type { AgentRoutine } from "../data";
import { WebhookConnection } from "./WebhookConnection";
import { TriggerBuilder } from "./TriggerBuilder";
import { SourceControls } from "./SourceControls";
import { AgentRoutineDetail } from "./AgentRoutineDetail";

export interface AgentRoutinesListProps { agentId: string; sampleRoutines?: AgentRoutine[]; variant?: "rail" | "sheet"; className?: string; onDetailOpenChange?: (open: boolean) => void; }
export function AgentRoutinesList({ agentId, sampleRoutines, variant = "rail", className, onDetailOpenChange }: AgentRoutinesListProps) {
  const t = useT(); useLocaleChunk("society");
  const live = useAgentRoutines(agentId); const create = useCreateAgentRoutine();
  const [open, setOpen] = useState(false); const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState(""); const [schedule, setSchedule] = useState<Record<string, unknown> | null>(null);
  const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const sample = useMemo(() => (sampleRoutines ?? []).map((row) => ({ id: row.id, title: row.label, state: "scheduled", trigger: null, schedule: row.schedule, dueMs: row.nextFire ? Date.parse(row.nextFire) : null, lastRunMs: null })), [sampleRoutines]);
  const rows: LiveRoutine[] = live.data ?? sample;
  const selectedRoutine = rows.find((row) => row.id === selected);
  const detailOpen = Boolean(selectedRoutine);
  useEffect(() => { onDetailOpenChange?.(detailOpen); }, [detailOpen, onDetailOpenChange]);
  const field = "w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground placeholder:text-muted-foreground";
  const submit = async () => {
    if (!title.trim() || !prompt.trim() || !schedule) return;
    setSaving(true); setError("");
    try { await create(agentId, { title: title.trim(), prompt: prompt.trim(), schedule }); setTitle(""); setPrompt(""); setOpen(false); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setSaving(false); }
  };
  if (selectedRoutine) return <AgentRoutineDetail key={`${agentId}:${selected}`} agentId={agentId} routine={selectedRoutine} onClose={() => setSelected(null)} />;
  return <section className={cn("flex min-h-0 flex-col", className)} data-testid="agent-routines">
    <div className="mb-1.5 flex shrink-0 items-center justify-between gap-2">
      <h3 className={variant === "sheet" ? "ac-head" : "font-display text-[13px] font-semibold text-foreground"}>{t("society.card.routines")}{rows.length > 0 && <span className="ml-1.5 text-muted-foreground">{rows.length}</span>}</h3>
      <button type="button" data-testid="agent-routines-add" className="rounded p-1 text-muted-foreground hover:bg-secondary" aria-label={t("society.card.routines_add")} onClick={() => setOpen((v) => !v)}>{open ? <X size={14} /> : <Plus size={14} />}</button>
    </div>
    {open && <form className="mb-3 space-y-2 rounded-md border border-border p-2" data-testid="agent-routines-composer" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <input className={field} aria-label={t("society.card.routines_title")} placeholder={t("society.card.routines_title")} value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className={field} aria-label={t("society.card.routines_prompt")} placeholder={t("society.card.routines_prompt")} value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} />
      <TriggerBuilder onChange={setSchedule} />
      <button type="submit" disabled={saving || !title.trim() || !prompt.trim() || !schedule} className="rounded bg-primary px-2.5 py-1 text-[12px] text-primary-foreground disabled:opacity-50">{t(saving ? "society.card.saving" : "society.card.routines_save")}</button>
      {error && <p role="alert" className="text-[11px] text-destructive">{error}</p>}
    </form>}
    <ul className="min-h-0 flex-1 overflow-y-auto">
      {live.error && <li role="alert" className="text-[12px] text-destructive">{t("tasks_view.load_error")}</li>}
      {rows.length === 0 && <li className="text-[12px] text-muted-foreground">{t("society.card.no_routines")}</li>}
      {rows.map((routine) => {
        const active = ["scheduled", "active", "enabled", ""].includes(routine.state);
        const trigger = routine.trigger as { type?: string; source?: { kind: string } } | null;
        return <li key={routine.id} className="flex items-start gap-2 rounded-md px-0.5 py-1.5 hover:bg-secondary/60">
          <Clock size={16} aria-hidden className={cn("mt-0.5 shrink-0", active ? "text-success" : "text-muted-foreground")} />
          <div className="min-w-0 flex-1">
            <button type="button" className="block w-full truncate rounded text-left text-[13px] font-medium text-foreground focus-visible:outline focus-visible:outline-ring" onClick={() => setSelected(routine.id)}>{displayRoutineTitle(routine.title)}</button>
            <span className="block break-words text-[11px] leading-snug text-muted-foreground">{routineScheduleLine(routine, t)}{routine.state === "paused" ? ` · ${t("tasks_view.state.paused")}` : ""}</span>
            {routine.dueMs && active ? <span className="block text-[11px] text-muted-foreground">{t("automations_view.next_run")}: {new Date(routine.dueMs).toLocaleString(undefined, { timeZoneName: "short" })}</span> : null}
            {trigger?.type === "webhook" && <WebhookConnection key={routine.id} taskId={routine.id} />}
            {trigger?.type === "source" && trigger.source && <SourceControls key={routine.id} taskId={routine.id} source={trigger.source} />}
          </div>
        </li>;
      })}
    </ul>
  </section>;
}
export default AgentRoutinesList;

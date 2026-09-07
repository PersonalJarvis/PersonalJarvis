/**
 * One agent's routines: the Grok-shaped list with a + that opens an inline
 * composer. Shared by the chat-face Options rail and the profile spec sheet
 * so the two faces cannot drift.
 *
 * A routine is a tagged Automations task (`POST /api/society/agents/{id}/routines`).
 * The composer stays inside the rail — the card is already a dialog.
 */
import { useMemo, useState } from "react";
import { Clock, Plus, X } from "lucide-react";

import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import {
  displayRoutineTitle,
  routineScheduleLine,
  useAgentRoutines,
  useCreateAgentRoutine,
  type LiveRoutine,
} from "../cardData";
import type { AgentRoutine } from "../data";

type Kind = "every" | "daily" | "on_event";
type Unit = "hours" | "minutes";

export interface AgentRoutinesListProps {
  agentId: string;
  /** Sample-roster fallback when the live store is not there. */
  sampleRoutines?: AgentRoutine[];
  /** `rail` is the Grok list; `sheet` keeps the spec card's section heading. */
  variant?: "rail" | "sheet";
  className?: string;
}

function sampleToLive(rows: AgentRoutine[]): LiveRoutine[] {
  return rows.map((row) => ({
    id: row.id,
    title: row.label,
    state: "scheduled",
    trigger: null,
    schedule: row.schedule,
    dueMs: row.nextFire ? Date.parse(row.nextFire) : null,
    lastRunMs: null,
  }));
}

function buildSchedule(kind: Kind, amount: number, unit: Unit, time: string, eventName: string): Record<string, unknown> {
  if (kind === "on_event") {
    return { kind: "on_event", event_name: eventName.trim() };
  }
  if (kind === "daily") {
    const [hours, minutes] = time.split(":").map((part) => Number(part));
    const start = new Date();
    start.setHours(Number.isFinite(hours) ? hours : 8, Number.isFinite(minutes) ? minutes : 0, 0, 0);
    return { kind: "every", interval_seconds: 86_400, start_at: start.toISOString() };
  }
  const n = Math.max(1, amount);
  const seconds = unit === "minutes" ? n * 60 : n * 3_600;
  return { kind: "every", interval_seconds: seconds };
}

function isActive(state: string): boolean {
  const s = state.toLowerCase();
  return s === "scheduled" || s === "active" || s === "enabled" || s === "";
}

export function AgentRoutinesList({
  agentId,
  sampleRoutines,
  variant = "rail",
  className,
}: AgentRoutinesListProps) {
  const t = useT();
  const live = useAgentRoutines(agentId);
  const create = useCreateAgentRoutine();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState<Kind>("every");
  const [amount, setAmount] = useState("5");
  const [unit, setUnit] = useState<Unit>("hours");
  const [time, setTime] = useState("08:00");
  const [eventName, setEventName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const sample = useMemo(() => sampleToLive(sampleRoutines ?? []), [sampleRoutines]);
  const rows: LiveRoutine[] =
    live.data && live.data.length > 0 ? live.data : sampleRoutines && sampleRoutines.length > 0 && !live.data?.length ? sample : (live.data ?? []);

  const kindOptions = [
    { value: "every", label: t("society.card.routines_every") },
    { value: "daily", label: t("society.card.routines_daily") },
    { value: "on_event", label: t("society.card.routines_on_event") },
  ];
  const unitOptions = [
    { value: "hours", label: t("society.card.routines_hours") },
    { value: "minutes", label: t("society.card.routines_minutes") },
  ];

  const valid =
    title.trim().length > 0 &&
    prompt.trim().length > 0 &&
    (kind !== "on_event" || eventName.trim().length > 0) &&
    (kind !== "every" || Number.parseInt(amount, 10) > 0) &&
    (kind !== "daily" || /^\d{2}:\d{2}$/.test(time));

  const reset = () => {
    setTitle("");
    setPrompt("");
    setKind("every");
    setAmount("5");
    setUnit("hours");
    setTime("08:00");
    setEventName("");
    setError("");
  };

  const submit = async () => {
    if (!valid) return;
    setSaving(true);
    setError("");
    try {
      await create(agentId, {
        title: title.trim(),
        prompt: prompt.trim(),
        schedule: buildSchedule(kind, Number.parseInt(amount, 10) || 1, unit, time, eventName),
      });
      reset();
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const fieldCls =
    "w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground placeholder:text-muted-foreground outline-none focus:border-border-strong";

  return (
    <section className={cn("flex min-h-0 flex-col", className)} data-testid="agent-routines">
      <div className="mb-1.5 flex shrink-0 items-center justify-between gap-2">
        {variant === "sheet" ? (
          <h3 className="ac-head">{t("society.card.routines")}</h3>
        ) : (
          <h3 className="font-display text-[13px] font-semibold tracking-tight text-foreground">
            {t("society.card.routines")}
            {rows.length > 0 ? <span className="ml-1.5 tabular-nums text-muted-foreground">{rows.length}</span> : null}
          </h3>
        )}
        <button
          type="button"
          className="rounded p-0.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
          aria-label={t("society.card.routines_add")}
          title={t("society.card.routines_add")}
          aria-expanded={open}
          onClick={() => {
            setOpen((v) => !v);
            setError("");
          }}
          data-testid="agent-routines-add"
        >
          {open ? <X size={14} aria-hidden /> : <Plus size={14} aria-hidden />}
        </button>
      </div>

      {open ? (
        <form
          className="mb-2 flex shrink-0 flex-col gap-1.5 rounded-md border border-border bg-background/60 p-2"
          data-testid="agent-routines-composer"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label className="sr-only" htmlFor={`routine-title-${agentId}`}>
            {t("society.card.routines_title")}
          </label>
          <input
            id={`routine-title-${agentId}`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("society.card.routines_title")}
            className={fieldCls}
            autoComplete="off"
          />
          <label className="sr-only" htmlFor={`routine-prompt-${agentId}`}>
            {t("society.card.routines_prompt")}
          </label>
          <textarea
            id={`routine-prompt-${agentId}`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("society.card.routines_prompt")}
            rows={3}
            className={cn(fieldCls, "resize-none")}
          />
          <BrandedSelect
            value={kind}
            onValueChange={(v) => setKind(v as Kind)}
            options={kindOptions}
            ariaLabel={t("society.card.routines_kind")}
            testId="agent-routines-kind"
          />
          {kind === "every" ? (
            <div className="flex gap-1.5">
              <input
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                aria-label={t("society.card.routines_hours")}
                className={cn(fieldCls, "w-16")}
              />
              <div className="min-w-0 flex-1">
                <BrandedSelect
                  value={unit}
                  onValueChange={(v) => setUnit(v as Unit)}
                  options={unitOptions}
                  ariaLabel={t("society.card.routines_unit")}
                />
              </div>
            </div>
          ) : null}
          {kind === "daily" ? (
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              aria-label={t("society.card.routines_daily")}
              className={fieldCls}
            />
          ) : null}
          {kind === "on_event" ? (
            <input
              value={eventName}
              onChange={(e) => setEventName(e.target.value)}
              placeholder={t("society.card.routines_event_name")}
              className={fieldCls}
              autoComplete="off"
            />
          ) : null}
          <button
            type="submit"
            disabled={!valid || saving}
            className="rounded-md bg-primary px-2.5 py-1 text-[12px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? t("society.card.saving") : t("society.card.routines_save")}
          </button>
          {error ? <p className="text-[11px] text-destructive">{error}</p> : null}
        </form>
      ) : null}

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <li className="text-[12px] text-muted-foreground">{t("society.card.no_routines")}</li>
        ) : (
          rows.map((routine) => (
            <li key={routine.id}>
              <div className="flex items-start gap-2 rounded-md px-0.5 py-1.5 hover:bg-secondary/60">
                <Clock
                  size={16}
                  aria-hidden
                  className={cn("mt-0.5 shrink-0", isActive(routine.state) ? "text-success" : "text-muted-foreground")}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium leading-tight text-foreground">
                    {displayRoutineTitle(routine.title)}
                  </span>
                  <span className="block truncate text-[11px] leading-tight text-muted-foreground">
                    {routineScheduleLine(routine, t)}
                  </span>
                </span>
              </div>
            </li>
          ))
        )}
      </ul>
    </section>
  );
}

export default AgentRoutinesList;

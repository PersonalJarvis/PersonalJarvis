/**
 * The compact "Add automation" dialog for a catalogue template: title
 * (prefilled), the template's inputs, and a schedule editor
 * (hourly / daily + time / weekly + weekday + time). Posts to
 * `POST /api/tasks/templates/{key}/add`.
 */
import { useMemo, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fill, useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import { ApiError, useAddTemplate } from "@/hooks/useAutomations";
import { templateIcon } from "./automationIcons";
import {
  humanizeMissing,
  type AutomationTemplate,
  type ScheduleKind,
  type TemplateSchedule,
} from "./automationsModel";
import { SectionLabel } from "./shared";

const inputCls =
  "w-full rounded-lg border border-border bg-background/60 px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground/60 focus:border-primary/60 focus:outline-none focus:ring-1 focus:ring-primary/40";

const KINDS: ScheduleKind[] = ["hourly", "daily", "weekly"];

export interface TemplateAddDialogProps {
  template: AutomationTemplate;
  onClose: () => void;
  onAdded: (taskId: string, template: AutomationTemplate) => void;
}

export function TemplateAddDialog({ template, onClose, onAdded }: TemplateAddDialogProps) {
  const t = useT();
  const locale = useUiLanguage();
  const Icon = templateIcon(template.icon);

  const [title, setTitle] = useState(template.name);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(template.inputs.map((i) => [i.key, i.default ?? ""])),
  );
  const [kind, setKind] = useState<ScheduleKind>(template.schedule.kind);
  const [time, setTime] = useState(template.schedule.time || "08:00");
  const [weekday, setWeekday] = useState(template.schedule.weekday ?? 0);
  const [touched, setTouched] = useState(false);

  const addMut = useAddTemplate();

  const missingRequired = useMemo(
    () => template.inputs.filter((i) => i.required && !(values[i.key] ?? "").trim()),
    [template.inputs, values],
  );
  const valid = title.trim().length > 0 && missingRequired.length === 0 && /^\d{2}:\d{2}$/.test(time);

  const weekdays = [0, 1, 2, 3, 4, 5, 6].map((d) => t(`automations_view.weekday.${d}`));

  function submit() {
    setTouched(true);
    if (!valid) return;
    const schedule: TemplateSchedule = { kind, time, weekday };
    addMut.mutate(
      {
        key: template.key,
        payload: {
          inputs: Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()])),
          schedule,
          title: title.trim(),
          locale,
        },
      },
      { onSuccess: (res) => onAdded(res.id, template) },
    );
  }

  const errorText = addMut.error
    ? addMut.error instanceof ApiError && addMut.error.status === 404
      ? t("automations_view.catalogue_unavailable")
      : `${t("automations_view.add_error")} (${(addMut.error as Error).message})`
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label={fill(t("automations_view.add_dialog_title"), { title: template.name })}
        className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/30 bg-primary/10">
              <Icon className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold">
                {fill(t("automations_view.add_dialog_title"), { title: template.name })}
              </h2>
              <p className="truncate text-[11px] text-muted-foreground">{template.description}</p>
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("tasks_view.create.cancel")}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
          <div className="space-y-4 px-5 py-4">
            {!template.ready && template.missing.length > 0 && (
              <p className="flex items-start gap-1.5 rounded-lg border border-amber-500/40 px-3 py-2 text-[11px] leading-relaxed text-amber-700 dark:text-amber-300/90">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {fill(t("automations_view.needs_warning"), { tools: humanizeMissing(template.missing) })}
              </p>
            )}

            <label className="block space-y-1.5">
              <SectionLabel>{t("tasks_view.create.name_label")}</SectionLabel>
              <input
                className={inputCls}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={256}
              />
            </label>

            {template.inputs.map((input) => {
              const invalid = touched && input.required && !(values[input.key] ?? "").trim();
              return (
                <label key={input.key} className="block space-y-1.5">
                  <SectionLabel>
                    {input.label}
                    {input.required && <span className="ml-1 text-primary">*</span>}
                  </SectionLabel>
                  <input
                    className={cn(inputCls, invalid && "border-destructive/70")}
                    value={values[input.key] ?? ""}
                    placeholder={input.placeholder || undefined}
                    onChange={(e) => setValues((prev) => ({ ...prev, [input.key]: e.target.value }))}
                    aria-invalid={invalid || undefined}
                    maxLength={2048}
                  />
                  {invalid && (
                    <span className="text-[11px] text-destructive">{t("automations_view.required")}</span>
                  )}
                </label>
              );
            })}

            <div className="space-y-3 rounded-xl border border-border/70 p-4">
              <SectionLabel>{t("tasks_view.create.schedule_label")}</SectionLabel>
              <div className="inline-flex rounded-lg border border-border p-0.5">
                {KINDS.map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                      kind === k ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`automations_view.kind.${k}`)}
                  </button>
                ))}
              </div>
              {kind !== "hourly" && (
                <div className="flex flex-wrap items-center gap-2">
                  {kind === "weekly" && (
                    <select
                      className={cn(inputCls, "w-auto")}
                      value={weekday}
                      onChange={(e) => setWeekday(Number(e.target.value))}
                      aria-label={t("automations_view.weekday_label")}
                    >
                      {weekdays.map((name, idx) => (
                        <option key={idx} value={idx}>
                          {name}
                        </option>
                      ))}
                    </select>
                  )}
                  <span className="text-xs text-muted-foreground">{t("tasks_view.create.at")}</span>
                  <input
                    type="time"
                    className={cn(inputCls, "w-32")}
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                    aria-label={t("automations_view.time_label")}
                  />
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          {errorText && <span className="mr-auto text-xs text-destructive">{errorText}</span>}
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t("tasks_view.create.cancel")}
          </Button>
          <Button size="sm" disabled={addMut.isPending || (touched && !valid)} onClick={submit}>
            {addMut.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t("automations_view.add")}
          </Button>
        </div>
      </div>
    </div>
  );
}

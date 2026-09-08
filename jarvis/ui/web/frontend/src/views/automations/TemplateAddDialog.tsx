/**
 * The compact "Add automation" dialog for a catalogue template: title
 * (prefilled), the template's inputs, and a schedule editor
 * (hourly / daily + time / weekly + weekday + time). Posts to
 * `POST /api/tasks/templates/{key}/add`.
 */
import { useMemo, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
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

/*
 * A field inside a dialog.
 *
 * The dialog IS the floating layer, so there is no surface left above it to
 * fill a field with — a child darker than its parent is the one nesting error
 * this system calls hard. So the field is described by a rim instead, the way
 * the composer is, and focus thickens that same rim rather than inventing a
 * third colour.
 */
const inputCls =
  "w-full rounded-md border border-border-strong bg-transparent px-3 py-2 text-body text-foreground " +
  "placeholder:text-faint-foreground focus:outline-none focus:ring-2 focus:ring-border-strong";

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
    const schedule: TemplateSchedule = { kind, time, weekday, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
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
        className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-lg bg-popover shadow-float"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div className="flex items-center gap-2.5">
            <Icon className="h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <h2 className="truncate text-title font-semibold text-foreground-strong">
                {fill(t("automations_view.add_dialog_title"), { title: template.name })}
              </h2>
              <p className="truncate text-meta text-muted-foreground">
                {template.description}
              </p>
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("tasks_view.create.cancel")}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
          <div className="space-y-group px-5 py-4">
            {/* Missing prerequisites are a degraded state, not a headline. */}
            {!template.ready && template.missing.length > 0 && (
              <p className="flex items-start gap-1.5 rounded-md border border-border-strong px-3 py-2 text-meta text-warning">
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
                    {input.required && (
                      <span className="ml-1 text-muted-foreground">*</span>
                    )}
                  </SectionLabel>
                  <input
                    className={cn(inputCls, invalid && "border-destructive")}
                    value={values[input.key] ?? ""}
                    placeholder={input.placeholder || undefined}
                    onChange={(e) => setValues((prev) => ({ ...prev, [input.key]: e.target.value }))}
                    aria-invalid={invalid || undefined}
                    maxLength={2048}
                  />
                  {invalid && (
                    <span className="text-meta text-destructive">
                      {t("automations_view.required")}
                    </span>
                  )}
                </label>
              );
            })}

            <div className="space-y-stack rounded-lg border border-border p-4">
              <SectionLabel>{t("tasks_view.create.schedule_label")}</SectionLabel>
              {/* On the floating layer the surface ladder has run out, so the
                  chosen segment is an --primary FILL (an active indicator, the
                  one job that token has) rather than a fourth grey. */}
              <div className="inline-flex rounded-md border border-border-strong p-0.5">
                {KINDS.map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-meta font-medium transition-colors",
                      kind === k
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`automations_view.kind.${k}`)}
                  </button>
                ))}
              </div>
              {kind !== "hourly" && (
                <div className="flex flex-wrap items-center gap-2">
                  {kind === "weekly" && (
                    <BrandedSelect
                      className={cn(inputCls, "w-auto")}
                      value={String(weekday)}
                      onValueChange={(value) => setWeekday(Number(value))}
                      ariaLabel={t("automations_view.weekday_label")}
                      options={weekdays.map((name, idx) => ({
                        value: String(idx),
                        label: name,
                      }))}
                    />
                  )}
                  <span className="text-body text-muted-foreground">
                    {t("tasks_view.create.at")}
                  </span>
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
          {errorText && (
            <span className="mr-auto text-meta text-destructive">{errorText}</span>
          )}
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

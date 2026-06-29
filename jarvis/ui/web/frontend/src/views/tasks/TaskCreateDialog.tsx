import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  X,
  Clock,
  CalendarClock,
  Repeat,
  Loader2,
  Plug,
  Sparkles,
  AlertTriangle,
  CalendarDays,
  Zap,
  MonitorPlay,
  Bot,
  Bell,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useT, useUiLanguage } from "@/i18n";
import {
  buildTaskSpec,
  defaultAtTimeLocal,
  formatWatermarkDate,
  type ModelTier,
  type ScopeValue,
  type TaskDraft,
  type ThenKind,
  type WhenKey,
} from "./taskSpec";

interface PluginItem {
  id: string;
  name?: string;
  status: string;
  live_callable?: boolean;
}

interface PluginsResponse {
  plugins: PluginItem[];
  connected: number;
}

async function fetchPlugins(): Promise<PluginsResponse> {
  // `cache: "no-store"` bypasses the embedded WebView2 HTTP cache so the
  // desktop app never shows a stale connection state for plugins (see the
  // matching note in PluginsView.fetchCatalog).
  const res = await fetch("/api/marketplace/plugins", { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function createTask(spec: ReturnType<typeof buildTaskSpec>): Promise<{ id: string }> {
  const res = await fetch("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const SCOPES: ScopeValue[] = ["read", "write", "full"];
const TIERS: ModelTier[] = ["fast", "auto", "deep"];

/** A small inline segmented control — used for mode pickers and scope. */
function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = "md",
}: {
  value: T;
  options: { id: T; label: string; icon?: typeof Clock }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-background/40 p-0.5">
      {options.map((o) => {
        const Icon = o.icon;
        const active = value === o.id;
        return (
          <button
            key={o.id}
            type="button"
            onClick={() => onChange(o.id)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md font-medium transition-colors",
              size === "sm" ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs",
              active
                ? "bg-primary/15 text-primary shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {Icon && <Icon className="h-3.5 w-3.5" />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-border bg-background/60 px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground/60 focus:border-primary/60 focus:outline-none focus:ring-1 focus:ring-primary/40";

export function TaskCreateDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const uiLang = useUiLanguage();
  const qc = useQueryClient();

  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [triggerMode, setTriggerMode] = useState<"schedule" | "event">("schedule");
  const [whenKey, setWhenKey] = useState<WhenKey>("mission_succeeded");
  const [thenKind, setThenKind] = useState<ThenKind>("computer_use");
  const [cuPrompt, setCuPrompt] = useState("");
  const [announceText, setAnnounceText] = useState("");
  const [scheduleMode, setScheduleMode] = useState<"once" | "recurring">("recurring");
  const [onceMode, setOnceMode] = useState<"delay" | "at_time">("at_time");
  const [delayValue, setDelayValue] = useState(1);
  const [delayUnit, setDelayUnit] = useState<"minutes" | "hours">("hours");
  // Pre-fill with a real, editable value (now + 1h) so the native picker never
  // shows the browser's raw, locale-ugly "TT.mm.jjjj --:--" empty placeholder.
  const [atTimeLocal, setAtTimeLocal] = useState(() => defaultAtTimeLocal());
  const [recurringMode, setRecurringMode] = useState<"hourly" | "daily" | "custom">("daily");
  const [dailyTime, setDailyTime] = useState("07:00");
  const [customValue, setCustomValue] = useState(30);
  const [customUnit, setCustomUnit] = useState<"minutes" | "hours">("minutes");
  const [modelTier, setModelTier] = useState<ModelTier>("auto");
  const [grants, setGrants] = useState<Record<string, ScopeValue>>({});

  const { data: pluginsData, isLoading: pluginsLoading } = useQuery({
    queryKey: ["marketplace-plugins"],
    queryFn: fetchPlugins,
  });
  // Only plugins the user has actually connected belong here. `live_callable` is a
  // catalog property (the plugin ships an MCP transport or a native ROUTER_TOOLS
  // tool) and is `true` even for unconnected plugins, so it must NOT widen this
  // list — a scheduled task can only use a plugin whose auth is live ("connected"),
  // never a `not_connected` or `needs_reauth` one.
  const plugins = (pluginsData?.plugins ?? []).filter((p) => p.status === "connected");

  const draft: TaskDraft = useMemo(
    () => ({
      title,
      prompt,
      triggerMode,
      scheduleMode,
      onceMode,
      delaySeconds: delayValue * (delayUnit === "hours" ? 3600 : 60),
      atTimeLocal,
      recurringMode,
      customIntervalSeconds: customValue * (customUnit === "hours" ? 3600 : 60),
      dailyTime,
      modelTier,
      grants: Object.entries(grants).map(([plugin_id, scope]) => ({ plugin_id, scope })),
      whenKey,
      thenKind,
      cuPrompt,
      announceText,
    }),
    [title, prompt, triggerMode, scheduleMode, onceMode, delayValue, delayUnit, atTimeLocal,
     recurringMode, customValue, customUnit, dailyTime, modelTier, grants,
     whenKey, thenKind, cuPrompt, announceText],
  );

  const createMut = useMutation({
    mutationFn: () => createTask(buildTaskSpec(draft)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    },
  });

  // The agent prompt + plugins + model picker only apply to an agentic action:
  // every time-based task, and an event rule whose "then" is an agent turn.
  const showAgentConfig = triggerMode === "schedule" || thenKind === "agent";

  const valid =
    title.trim().length > 0 &&
    (triggerMode === "event"
      ? thenKind === "computer_use"
        ? cuPrompt.trim().length > 0
        : thenKind === "agent"
          ? prompt.trim().length > 0
          : announceText.trim().length > 0 // notify
      : prompt.trim().length > 0 &&
        (scheduleMode !== "once" || onceMode !== "at_time" || atTimeLocal !== ""));

  const hasElevatedGrant = Object.values(grants).some(
    (s) => s === "write" || s === "full",
  );

  function togglePlugin(id: string) {
    setGrants((prev) => {
      const next = { ...prev };
      if (id in next) delete next[id];
      else next[id] = "read";
      return next;
    });
  }

  function setScope(id: string, scope: ScopeValue) {
    setGrants((prev) => ({ ...prev, [id]: scope }));
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/30 bg-primary/10">
              <Sparkles className="h-4 w-4 text-primary" />
            </div>
            <div>
              <h2 className="text-sm font-semibold">{t("tasks_view.create.title")}</h2>
              <p className="text-[11px] text-muted-foreground">
                {t("tasks_view.create.subtitle")}
              </p>
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Body — a native overflow scroll container (the ConductorView pattern).
            A Radix <ScrollArea> nests a `height:100%` viewport that never resolves
            inside this `max-h-[90vh]` flex column, so Radix kept `overflow:hidden`
            and clipped the lower plugins + the model picker. `min-h-0` lets this
            flex child shrink below its content so it actually scrolls. */}
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
          <div className="space-y-5 px-6 py-5">
            <Field label={t("tasks_view.create.name_label")}>
              <input
                className={inputCls}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t("tasks_view.create.name_placeholder")}
                maxLength={256}
              />
            </Field>

            {/* Trigger mode: a time-based schedule vs. an event-driven
                "When-Then" rule (e.g. when a mission finishes → do X). */}
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {t("tasks_view.create.trigger_mode_label")}
              </span>
              <Segmented
                value={triggerMode}
                onChange={setTriggerMode}
                options={[
                  { id: "schedule", label: t("tasks_view.create.mode_schedule"), icon: CalendarDays },
                  { id: "event", label: t("tasks_view.create.mode_event"), icon: Zap },
                ]}
              />
            </div>

            {/* When-Then rule: curated "when" event + "then" action */}
            {triggerMode === "event" && (
              <div className="space-y-4 rounded-xl border border-border/70 bg-background/30 p-4">
                <div className="space-y-2">
                  <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    {t("tasks_view.create.when_label")}
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    <Segmented
                      size="sm"
                      value={whenKey}
                      onChange={setWhenKey}
                      options={[
                        { id: "mission_succeeded", label: t("tasks_view.create.when_mission_succeeded") },
                        { id: "mission_failed", label: t("tasks_view.create.when_mission_failed") },
                        { id: "mission_cancelled", label: t("tasks_view.create.when_mission_cancelled") },
                      ]}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    {t("tasks_view.create.then_label")}
                  </span>
                  <Segmented
                    size="sm"
                    value={thenKind}
                    onChange={setThenKind}
                    options={[
                      { id: "computer_use", label: t("tasks_view.create.then_computer_use"), icon: MonitorPlay },
                      { id: "agent", label: t("tasks_view.create.then_agent"), icon: Bot },
                      { id: "notify", label: t("tasks_view.create.then_notify"), icon: Bell },
                    ]}
                  />
                </div>

                {thenKind === "computer_use" && (
                  <Field label={t("tasks_view.create.cu_goal_label")}>
                    <textarea
                      className={cn(inputCls, "min-h-[72px] resize-y leading-relaxed")}
                      value={cuPrompt}
                      onChange={(e) => setCuPrompt(e.target.value)}
                      placeholder={t("tasks_view.create.cu_goal_placeholder")}
                      maxLength={16384}
                    />
                  </Field>
                )}

                <Field
                  label={
                    thenKind === "notify"
                      ? t("tasks_view.create.notify_text_label")
                      : t("tasks_view.create.announce_label")
                  }
                >
                  <input
                    className={inputCls}
                    value={announceText}
                    onChange={(e) => setAnnounceText(e.target.value)}
                    placeholder={t("tasks_view.create.announce_placeholder")}
                    maxLength={2048}
                  />
                </Field>
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  {t("tasks_view.create.when_then_hint")}
                </p>
              </div>
            )}

            {/* Schedule */}
            {triggerMode === "schedule" && (
            <div className="space-y-3 rounded-xl border border-border/70 bg-background/30 p-4">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {t("tasks_view.create.schedule_label")}
                </span>
                <Segmented
                  value={scheduleMode}
                  onChange={setScheduleMode}
                  options={[
                    { id: "once", label: t("tasks_view.create.once"), icon: Clock },
                    { id: "recurring", label: t("tasks_view.create.recurring"), icon: Repeat },
                  ]}
                />
              </div>

              {scheduleMode === "once" ? (
                <div className="space-y-3">
                  <Segmented
                    size="sm"
                    value={onceMode}
                    onChange={setOnceMode}
                    options={[
                      { id: "at_time", label: t("tasks_view.create.at_time"), icon: CalendarClock },
                      { id: "delay", label: t("tasks_view.create.after_delay"), icon: Clock },
                    ]}
                  />
                  {onceMode === "at_time" ? (
                    <div className="relative overflow-hidden rounded-lg">
                      {/* The selected date, written out long-form and very faint,
                          sitting behind the picker — "the current date, but a
                          little in the background". It tracks whatever value the
                          picker holds and is purely decorative (no pointer
                          events), so the native control stays fully usable. */}
                      <div
                        aria-hidden
                        className="pointer-events-none absolute inset-y-0 left-3 right-11 flex items-center justify-end"
                      >
                        <span className="truncate text-lg font-semibold leading-none text-foreground/[0.06]">
                          {formatWatermarkDate(atTimeLocal, uiLang)}
                        </span>
                      </div>
                      <input
                        type="datetime-local"
                        className={cn(inputCls, "relative bg-transparent [color-scheme:dark]")}
                        value={atTimeLocal}
                        onChange={(e) => setAtTimeLocal(e.target.value)}
                      />
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">
                        {t("tasks_view.create.in")}
                      </span>
                      <input
                        type="number"
                        min={1}
                        className={cn(inputCls, "w-24")}
                        value={delayValue}
                        onChange={(e) => setDelayValue(Math.max(1, Number(e.target.value)))}
                      />
                      <Segmented
                        size="sm"
                        value={delayUnit}
                        onChange={setDelayUnit}
                        options={[
                          { id: "minutes", label: t("tasks_view.create.minutes") },
                          { id: "hours", label: t("tasks_view.create.hours") },
                        ]}
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  <Segmented
                    size="sm"
                    value={recurringMode}
                    onChange={setRecurringMode}
                    options={[
                      { id: "hourly", label: t("tasks_view.create.hourly") },
                      { id: "daily", label: t("tasks_view.create.daily") },
                      { id: "custom", label: t("tasks_view.create.custom") },
                    ]}
                  />
                  {recurringMode === "daily" && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">
                        {t("tasks_view.create.at")}
                      </span>
                      <input
                        type="time"
                        className={cn(inputCls, "w-32")}
                        value={dailyTime}
                        onChange={(e) => setDailyTime(e.target.value)}
                      />
                    </div>
                  )}
                  {recurringMode === "custom" && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">
                        {t("tasks_view.create.every")}
                      </span>
                      <input
                        type="number"
                        min={1}
                        className={cn(inputCls, "w-24")}
                        value={customValue}
                        onChange={(e) => setCustomValue(Math.max(1, Number(e.target.value)))}
                      />
                      <Segmented
                        size="sm"
                        value={customUnit}
                        onChange={setCustomUnit}
                        options={[
                          { id: "minutes", label: t("tasks_view.create.minutes") },
                          { id: "hours", label: t("tasks_view.create.hours") },
                        ]}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
            )}

            {showAgentConfig && (
              <Field label={t("tasks_view.create.prompt_label")}>
                <textarea
                  className={cn(inputCls, "min-h-[96px] resize-y leading-relaxed")}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t("tasks_view.create.prompt_placeholder")}
                  maxLength={16384}
                />
              </Field>
            )}

            {showAgentConfig && (
            <>
            {/* Plugins */}
            <div className="space-y-2.5 rounded-xl border border-border/70 bg-background/30 p-4">
              <div className="flex items-center gap-2">
                <Plug className="h-3.5 w-3.5 text-primary" />
                <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {t("tasks_view.create.plugins_label")}
                </span>
              </div>
              {pluginsLoading ? (
                <p className="text-xs text-muted-foreground">{t("tasks_view.create.plugins_loading")}</p>
              ) : plugins.length === 0 ? (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-400/80" />
                  {t("tasks_view.create.plugins_empty")}
                </p>
              ) : (
                <div className="space-y-1.5">
                  {plugins.map((p) => {
                    const on = p.id in grants;
                    return (
                      <div
                        key={p.id}
                        className={cn(
                          "flex items-center justify-between rounded-lg border px-3 py-2 transition-colors",
                          on ? "border-primary/40 bg-primary/5" : "border-border/60",
                        )}
                      >
                        <div className="flex items-center gap-2.5">
                          <Switch checked={on} onCheckedChange={() => togglePlugin(p.id)} />
                          <span className="text-sm">{p.name || p.id}</span>
                        </div>
                        {on && (
                          <Segmented
                            size="sm"
                            value={grants[p.id]}
                            onChange={(s) => setScope(p.id, s)}
                            options={SCOPES.map((s) => ({
                              id: s,
                              label: t(`tasks_view.create.scope_${s}`),
                            }))}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {hasElevatedGrant && (
                <p className="flex items-start gap-1.5 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-[11px] leading-relaxed text-amber-200/90">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400/80" />
                  {t("tasks_view.create.unattended_hint")}
                </p>
              )}
            </div>

            {/* Model tier */}
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {t("tasks_view.create.model_label")}
              </span>
              <Segmented
                size="sm"
                value={modelTier}
                onChange={setModelTier}
                options={TIERS.map((tier) => ({
                  id: tier,
                  label: t(`tasks_view.create.tier_${tier}`),
                }))}
              />
            </div>
            </>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-4">
          {createMut.isError && (
            <span className="mr-auto text-xs text-destructive">
              {t("tasks_view.create.save_error")}
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t("tasks_view.create.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={!valid || createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t("tasks_view.create.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

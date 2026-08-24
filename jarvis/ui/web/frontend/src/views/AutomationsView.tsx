/**
 * The Automations section — everything Jarvis does on its own, in one place.
 *
 * Four tabs over one content column: the user's recurring automations, the
 * one-off schedules waiting to fire, the run history, and the catalogue of
 * ready-made automations. The split is what the section was missing: a
 * waiting one-shot used to be filed under "Runs", so schedules had no home
 * and nobody found them, and the catalogue printed one sparse grid per
 * category down an unbounded-width page.
 *
 * The layout is the section design the rest of the app converged on: the
 * section header bar, a centred content column, a row of headline numbers,
 * a chip rail of tabs, and panels holding tables — the same shapes as Spend,
 * Local models and the extensions section.
 *
 * Degrades honestly against an older backend: when the catalogue route does
 * not exist yet (404 — it goes live with the next restart) the tab says so
 * inline, and a failed run/pause/delete call becomes a notice, not a crash.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  LayoutGrid,
  Plus,
  RefreshCw,
  Timer,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  IconButton,
  Panel,
  PanelHeader,
  SegmentedFilter,
  SoftButton,
  StatTile,
} from "@/components/extensions/primitives";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  ApiError,
  useDeleteTask,
  useRunNow,
  useSetEnabled,
  useTasks,
  useTemplates,
} from "@/hooks/useAutomations";
import { TaskCreateDialog } from "./tasks/TaskCreateDialog";
import type { TaskDraft } from "./tasks/taskSpec";
import { AutomationsPanel } from "./automations/AutomationsPanel";
import { CataloguePanel } from "./automations/CataloguePanel";
import { RunsPanel, RUN_FILTERS } from "./automations/RunsPanel";
import { SchedulesPanel } from "./automations/SchedulesPanel";
import { TemplateAddDialog } from "./automations/TemplateAddDialog";
import {
  automationStats,
  formatDelta,
  selectAutomations,
  selectRuns,
  selectSchedules,
  templateKeyOf,
  type AutomationTemplate,
  type RunFilter,
} from "./automations/automationsModel";
import { useTick } from "./automations/shared";

type Tab = "automations" | "schedules" | "runs" | "catalogue";

interface Notice {
  text: string;
  kind: "info" | "error";
}

/** The create dialog opened as "a schedule" starts on the one-off branch. */
const SCHEDULE_DRAFT: Partial<TaskDraft> = { triggerMode: "schedule", scheduleMode: "once" };

export function AutomationsView() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("automations");
  const [createDraft, setCreateDraft] = useState<Partial<TaskDraft> | null>(null);
  const [adding, setAdding] = useState<AutomationTemplate | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [runFilter, setRunFilter] = useState<RunFilter>("all");
  const [notice, setNotice] = useState<Notice | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showNotice = useCallback((text: string, kind: "info" | "error" = "info") => {
    setNotice({ text, kind });
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), kind === "error" ? 8000 : 4000);
  }, []);
  useEffect(
    () => () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
    },
    [],
  );

  const tasksQuery = useTasks();
  const templatesQuery = useTemplates();
  const tasks = useMemo(() => tasksQuery.data?.tasks ?? [], [tasksQuery.data]);
  const automations = useMemo(() => selectAutomations(tasks), [tasks]);
  const schedules = useMemo(() => selectSchedules(tasks), [tasks]);
  const runsCount = useMemo(() => selectRuns(tasks).length, [tasks]);
  const stats = useMemo(() => automationStats(tasks), [tasks]);

  const templates = useMemo(
    () => templatesQuery.data?.templates ?? [],
    [templatesQuery.data],
  );
  const templatesByKey = useMemo(
    () => new Map(templates.map((tpl) => [tpl.key, tpl])),
    [templates],
  );
  // template key → the user's (active) task created from it
  const installedByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const task of automations) {
      const key = templateKeyOf(task);
      if (key && !map.has(key)) map.set(key, task.id);
    }
    return map;
  }, [automations]);

  const runNow = useRunNow();
  const setEnabled = useSetEnabled();
  const deleteTask = useDeleteTask();

  const describeError = useCallback(
    (err: unknown, fallbackKey: string): string => {
      if (err instanceof ApiError) {
        if (err.status === 404 || err.status === 405) return t("automations_view.route_unavailable");
        if (err.status === 409) return t("automations_view.already_running");
      }
      return `${t(fallbackKey)} (${(err as Error)?.message ?? "?"})`;
    },
    [t],
  );

  const handleRunNow = useCallback(
    (id: string) =>
      runNow.mutate(id, {
        onSuccess: () => showNotice(t("automations_view.run_started")),
        onError: (err) => showNotice(describeError(err, "automations_view.run_error"), "error"),
      }),
    [runNow, showNotice, describeError, t],
  );
  const handleSetEnabled = useCallback(
    (id: string, enabled: boolean) =>
      setEnabled.mutate(
        { id, enabled },
        {
          onSuccess: () =>
            showNotice(t(enabled ? "automations_view.resumed" : "automations_view.paused_notice")),
          onError: (err) => showNotice(describeError(err, "automations_view.toggle_error"), "error"),
        },
      ),
    [setEnabled, showNotice, describeError, t],
  );
  const handleDelete = useCallback(
    (id: string, active: boolean) =>
      deleteTask.mutate(
        { id, active },
        {
          onSuccess: () => showNotice(t("automations_view.deleted")),
          onError: (err) => showNotice(describeError(err, "automations_view.delete_error"), "error"),
        },
      ),
    [deleteTask, showNotice, describeError, t],
  );

  const scrollToTask = useCallback((taskId: string) => {
    setTab("automations");
    setHighlightId(taskId);
    // The row exists already (installed) — give React a frame to switch tabs.
    requestAnimationFrame(() => {
      document
        .getElementById(`automation-${taskId}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    setTimeout(() => setHighlightId((cur) => (cur === taskId ? null : cur)), 2500);
  }, []);

  const handleAdded = useCallback(
    (taskId: string, template: AutomationTemplate) => {
      setAdding(null);
      showNotice(fill(t("automations_view.added_notice"), { title: template.name }));
      // The list refetches on invalidation; scroll once it has rendered.
      setTimeout(() => scrollToTask(taskId), 400);
    },
    [showNotice, scrollToTask, t],
  );

  const catalogueUnavailable = templatesQuery.data?.unavailable === true;
  const openCreate = useCallback(() => setCreateDraft({}), []);
  const openScheduleCreate = useCallback(() => setCreateDraft(SCHEDULE_DRAFT), []);
  const openCatalogue = useCallback(() => setTab("catalogue"), []);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Workflow className="h-4 w-4 text-primary" />}
        title={t("automations_view.title")}
        subtitle={t("automations_view.subtitle")}
        right={
          <div className="flex items-center gap-1.5">
            <IconButton
              label={t("automations_view.refresh")}
              onClick={() => {
                tasksQuery.refetch();
                templatesQuery.refetch();
              }}
              disabled={tasksQuery.isRefetching}
            >
              <RefreshCw className={cn("h-4 w-4", tasksQuery.isRefetching && "animate-spin")} />
            </IconButton>
            <SoftButton primary onClick={openCreate}>
              <Plus className="h-3.5 w-3.5" />
              {t("automations_view.new_button")}
            </SoftButton>
          </div>
        }
      />
      {createDraft && (
        <TaskCreateDialog initialDraft={createDraft} onClose={() => setCreateDraft(null)} />
      )}
      {adding && (
        <TemplateAddDialog template={adding} onClose={() => setAdding(null)} onAdded={handleAdded} />
      )}

      <ScrollArea className="flex-1">
        <div className="mx-auto flex max-w-[1180px] flex-col gap-4 px-6 py-6">
          {notice && (
            <div
              role="status"
              className={cn(
                "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm",
                notice.kind === "error"
                  ? "border-destructive/50 bg-destructive/5 text-destructive"
                  : "border-primary/40 bg-primary/5 text-primary",
              )}
            >
              <span className="min-w-0 flex-1 truncate">{notice.text}</span>
              <button
                type="button"
                onClick={() => setNotice(null)}
                aria-label={t("common.close")}
                className="shrink-0 opacity-70 transition-opacity hover:opacity-100"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {tasksQuery.error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {t("tasks_view.load_error")}: {(tasksQuery.error as Error).message}
            </div>
          )}

          {/* Headline numbers — what is armed, what happens next, what broke. */}
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              icon={<Zap className="h-4 w-4" />}
              label={t("automations_view.stat_active")}
              value={stats.active}
              hint={
                stats.paused > 0
                  ? fill(t("automations_view.stat_active_paused"), { n: stats.paused })
                  : t("automations_view.stat_active_hint")
              }
              tone="primary"
              loading={tasksQuery.isLoading}
            />
            <StatTile
              icon={<Timer className="h-4 w-4" />}
              label={t("automations_view.stat_next")}
              value={<NextRunValue dueNs={stats.nextDueNs} />}
              hint={stats.nextTitle || t("automations_view.stat_next_hint")}
              loading={tasksQuery.isLoading}
            />
            <StatTile
              icon={<CalendarClock className="h-4 w-4" />}
              label={t("automations_view.stat_schedules")}
              value={stats.schedules}
              hint={t("automations_view.stat_schedules_hint")}
              loading={tasksQuery.isLoading}
            />
            <StatTile
              icon={<AlertTriangle className="h-4 w-4" />}
              label={t("automations_view.stat_problems")}
              value={stats.problems}
              hint={stats.problemTitle || t("automations_view.stat_problems_hint")}
              tone={stats.problems > 0 ? "danger" : "success"}
              loading={tasksQuery.isLoading}
            />
          </div>

          <SegmentedFilter<Tab>
            label={t("automations_view.tabs_label")}
            value={tab}
            onChange={setTab}
            options={[
              { id: "automations", label: t("automations_view.tab_automations"), count: automations.length },
              { id: "schedules", label: t("automations_view.tab_schedules"), count: schedules.length },
              { id: "runs", label: t("automations_view.tab_runs"), count: runsCount },
              { id: "catalogue", label: t("automations_view.tab_catalogue"), count: templates.length },
            ]}
          />

          {tab === "automations" && (
            <Panel>
              <div className="px-4 pt-4">
                <PanelHeader
                  title={t("automations_view.yours_heading")}
                  subtitle={t("automations_view.yours_subtitle")}
                  actions={
                    <SoftButton onClick={openCatalogue}>
                      <LayoutGrid className="h-3.5 w-3.5" />
                      {t("automations_view.browse_catalogue")}
                    </SoftButton>
                  }
                />
              </div>
              <div className="mt-3">
                <AutomationsPanel
                  automations={automations}
                  templatesByKey={templatesByKey}
                  highlightId={highlightId}
                  onRunNow={handleRunNow}
                  onSetEnabled={handleSetEnabled}
                  onDelete={handleDelete}
                  onCreate={openCreate}
                  onBrowseCatalogue={openCatalogue}
                  busy={{
                    runId: runNow.isPending ? runNow.variables : undefined,
                    toggleId: setEnabled.isPending ? setEnabled.variables?.id : undefined,
                    deleteId: deleteTask.isPending ? deleteTask.variables?.id : undefined,
                  }}
                  loading={tasksQuery.isLoading}
                />
              </div>
            </Panel>
          )}

          {tab === "schedules" && (
            <Panel>
              <div className="px-4 pt-4">
                <PanelHeader
                  title={t("automations_view.schedules_heading")}
                  subtitle={t("automations_view.schedules_subtitle")}
                  actions={
                    <SoftButton primary onClick={openScheduleCreate}>
                      <Plus className="h-3.5 w-3.5" />
                      {t("automations_view.new_schedule")}
                    </SoftButton>
                  }
                />
              </div>
              <div className="mt-3">
                <SchedulesPanel
                  schedules={schedules}
                  onRunNow={handleRunNow}
                  onDelete={handleDelete}
                  onCreate={openScheduleCreate}
                  busy={{
                    runId: runNow.isPending ? runNow.variables : undefined,
                    deleteId: deleteTask.isPending ? deleteTask.variables?.id : undefined,
                  }}
                  loading={tasksQuery.isLoading}
                />
              </div>
            </Panel>
          )}

          {tab === "runs" && (
            <Panel>
              <div className="px-4 pt-4">
                <PanelHeader
                  title={t("automations_view.runs_heading")}
                  subtitle={t("automations_view.runs_subtitle")}
                  actions={
                    <SegmentedFilter<RunFilter>
                      label={t("automations_view.runs_filter_label")}
                      value={runFilter}
                      onChange={setRunFilter}
                      options={RUN_FILTERS.map((f) => ({
                        id: f,
                        label: t(`automations_view.runs_filter.${f}`),
                      }))}
                    />
                  }
                />
              </div>
              <div className="mt-3">
                <RunsPanel tasks={tasks} filter={runFilter} onNotice={showNotice} />
              </div>
            </Panel>
          )}

          {tab === "catalogue" && (
            <div className="space-y-3">
              <PanelHeader
                title={t("automations_view.catalogue_heading")}
                subtitle={t("automations_view.catalogue_hint")}
              />
              {catalogueUnavailable ? (
                <Panel className="p-6 text-center text-sm text-muted-foreground">
                  {t("automations_view.catalogue_unavailable")}
                </Panel>
              ) : templatesQuery.isLoading ? (
                <p className="text-sm text-muted-foreground">
                  {t("automations_view.catalogue_loading")}
                </p>
              ) : templatesQuery.error ? (
                <Panel className="p-4 text-sm text-destructive">
                  {t("automations_view.catalogue_error")}:{" "}
                  {(templatesQuery.error as Error).message}
                </Panel>
              ) : templates.length === 0 ? (
                <Panel className="p-6 text-center text-sm text-muted-foreground">
                  {t("automations_view.catalogue_empty")}
                </Panel>
              ) : (
                <CataloguePanel
                  templates={templates}
                  installedByKey={installedByKey}
                  onAdd={setAdding}
                  onShowInstalled={scrollToTask}
                  onCreateCustom={openCreate}
                />
              )}
            </div>
          )}

          {tasksQuery.isLoading && (
            <p className="text-sm text-muted-foreground">{t("tasks_view.loading")}</p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

/** The countdown as a tile value — "3h", "7min", "due now". Ticks each second. */
function NextRunValue({ dueNs }: { dueNs: number | null }) {
  const t = useT();
  useTick();
  if (dueNs == null) return <>—</>;
  const delta = dueNs - Date.now() * 1e6;
  if (delta <= 0) return <>{t("tasks_view.due_now")}</>;
  return <>{formatDelta(delta)}</>;
}

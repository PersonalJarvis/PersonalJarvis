/**
 * The Automations section: the user's recurring automations, the catalogue
 * of ready-made ones (grouped by category), and a Runs tab with the history.
 *
 * Degrades honestly against an older backend: when the catalogue route does
 * not exist yet (404 — it goes live with the next restart) the section says
 * so inline, and a failed run/pause/delete call becomes a notice instead of
 * a crash.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { History, Plus, RefreshCw, Workflow, X } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
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
import { AutomationCard } from "./automations/AutomationCard";
import { CatalogueCard } from "./automations/CatalogueCard";
import { TemplateAddDialog } from "./automations/TemplateAddDialog";
import {
  groupTemplates,
  selectAutomations,
  selectRuns,
  templateKeyOf,
  type AutomationTemplate,
  type TaskSummary,
} from "./automations/automationsModel";
import { SectionLabel } from "./automations/shared";
import { RunsTab } from "./TasksView";

type Tab = "automations" | "runs";

interface Notice {
  text: string;
  kind: "info" | "error";
}

export function AutomationsView() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("automations");
  const [showCreate, setShowCreate] = useState(false);
  const [adding, setAdding] = useState<AutomationTemplate | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showNotice = useCallback((text: string, kind: "info" | "error" = "info") => {
    setNotice({ text, kind });
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), kind === "error" ? 8000 : 4000);
  }, []);
  useEffect(() => () => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
  }, []);

  const tasksQuery = useTasks();
  const templatesQuery = useTemplates();
  const tasks = useMemo(() => tasksQuery.data?.tasks ?? [], [tasksQuery.data]);
  const automations = useMemo(() => selectAutomations(tasks), [tasks]);
  const runsCount = useMemo(() => selectRuns(tasks).length, [tasks]);

  const templates = templatesQuery.data?.templates ?? [];
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
  const groups = useMemo(() => groupTemplates(templates), [templates]);

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
    // The card exists already (installed) — give React a frame to switch tabs.
    requestAnimationFrame(() => {
      document.getElementById(`automation-${taskId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
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

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Workflow className="h-4 w-4 text-primary" />}
        title={t("automations_view.title")}
        subtitle={t("automations_view.subtitle")}
        right={
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                tasksQuery.refetch();
                templatesQuery.refetch();
              }}
              disabled={tasksQuery.isRefetching}
              aria-label={t("automations_view.refresh")}
              title={t("automations_view.refresh")}
            >
              <RefreshCw className={cn("h-4 w-4", tasksQuery.isRefetching && "animate-spin")} />
            </Button>
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="mr-1 h-4 w-4" />
              {t("automations_view.new_button")}
            </Button>
          </div>
        }
      />
      {showCreate && <TaskCreateDialog onClose={() => setShowCreate(false)} />}
      {adding && (
        <TemplateAddDialog template={adding} onClose={() => setAdding(null)} onAdded={handleAdded} />
      )}

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border px-6">
        <TabButton active={tab === "automations"} onClick={() => setTab("automations")} icon={Workflow}>
          {t("automations_view.tab_automations")}
          <Count n={automations.length} />
        </TabButton>
        <TabButton active={tab === "runs"} onClick={() => setTab("runs")} icon={History}>
          {t("automations_view.tab_runs")}
          <Count n={runsCount} />
        </TabButton>
        {notice && (
          <div
            role="status"
            className={cn(
              "ml-auto flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs",
              notice.kind === "error"
                ? "border-destructive/50 text-destructive"
                : "border-primary/40 text-primary",
            )}
          >
            <span className="max-w-md truncate">{notice.text}</span>
            <button type="button" onClick={() => setNotice(null)} aria-label={t("common.close")}>
              <X className="h-3 w-3" />
            </button>
          </div>
        )}
      </div>

      <ScrollArea className="flex-1">
        <div className="p-6">
          {tasksQuery.isLoading && (
            <div className="text-sm text-muted-foreground">{t("tasks_view.loading")}</div>
          )}
          {tasksQuery.error && (
            <div className="mb-4 rounded-lg border border-destructive/40 p-4 text-sm text-destructive">
              {t("tasks_view.load_error")}: {(tasksQuery.error as Error).message}
            </div>
          )}

          {tab === "automations" ? (
            <div className="space-y-8">
              <section aria-labelledby="your-automations">
                <div className="mb-3 flex items-baseline justify-between">
                  <h3 id="your-automations" className="text-sm font-semibold">
                    {t("automations_view.yours_heading")}
                  </h3>
                  <span className="text-[11px] text-muted-foreground">
                    {automations.length} {t("tasks_view.entries")}
                  </span>
                </div>
                {!tasksQuery.isLoading && automations.length === 0 ? (
                  <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border/60 p-8 text-center">
                    <Workflow className="h-7 w-7 text-muted-foreground/50" />
                    <p className="text-sm text-muted-foreground">{t("automations_view.yours_empty")}</p>
                    <p className="max-w-xl text-[11px] leading-relaxed text-muted-foreground/70">
                      {t("automations_view.yours_empty_hint")}
                    </p>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {automations.map((task: TaskSummary) => {
                      const key = templateKeyOf(task);
                      return (
                        <AutomationCard
                          key={task.id}
                          task={task}
                          template={key ? templatesByKey.get(key) : undefined}
                          highlighted={highlightId === task.id}
                          onRunNow={handleRunNow}
                          onSetEnabled={handleSetEnabled}
                          onDelete={handleDelete}
                          busy={{
                            run: runNow.isPending && runNow.variables === task.id,
                            toggle: setEnabled.isPending && setEnabled.variables?.id === task.id,
                            delete: deleteTask.isPending && deleteTask.variables?.id === task.id,
                          }}
                        />
                      );
                    })}
                  </div>
                )}
              </section>

              <section aria-labelledby="catalogue">
                <div className="mb-3 flex items-baseline justify-between">
                  <h3 id="catalogue" className="text-sm font-semibold">
                    {t("automations_view.catalogue_heading")}
                  </h3>
                  <span className="text-[11px] text-muted-foreground">
                    {t("automations_view.catalogue_hint")}
                  </span>
                </div>
                {catalogueUnavailable ? (
                  <div className="rounded-2xl border border-dashed border-border/60 p-6 text-center text-sm text-muted-foreground">
                    {t("automations_view.catalogue_unavailable")}
                  </div>
                ) : templatesQuery.isLoading ? (
                  <div className="text-sm text-muted-foreground">{t("automations_view.catalogue_loading")}</div>
                ) : templatesQuery.error ? (
                  <div className="rounded-lg border border-destructive/40 p-4 text-sm text-destructive">
                    {t("automations_view.catalogue_error")}: {(templatesQuery.error as Error).message}
                  </div>
                ) : groups.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-border/60 p-6 text-center text-sm text-muted-foreground">
                    {t("automations_view.catalogue_empty")}
                  </div>
                ) : (
                  <div className="space-y-6">
                    {groups.map((group) => (
                      <div key={group.category}>
                        <SectionLabel className="mb-2">
                          {t(`automations_view.category.${group.category}`)}
                        </SectionLabel>
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                          {group.templates.map((tpl) => (
                            <CatalogueCard
                              key={tpl.key}
                              template={tpl}
                              installedTaskId={installedByKey.get(tpl.key)}
                              onAdd={setAdding}
                              onShowInstalled={scrollToTask}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          ) : (
            <RunsTab tasks={tasks} onNotice={showNotice} />
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Workflow;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm transition-colors",
        active
          ? "border-primary font-medium text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      <Icon className={cn("h-3.5 w-3.5", active ? "text-primary" : "text-muted-foreground")} />
      {children}
    </button>
  );
}

function Count({ n }: { n: number }) {
  if (!n) return null;
  return (
    <span className="rounded-full border border-border px-1.5 text-[10px] font-mono leading-4 text-muted-foreground">
      {n}
    </span>
  );
}

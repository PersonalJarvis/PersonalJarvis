/**
 * Phase-6 mission control view.
 *
 * 4-pane CSS grid (not resizable for the MVP):
 *  - Top bar: counter + connection status + global kill button
 *  - Left:    MissionTree (react-arborist)
 *  - Center:  PtyTerminal (lazy) + EventTimeline
 *  - Right:   Tabs: Verdicts / Reasoning / Plan
 *
 * useMissionWebSocket() is called exactly once on mount and shares its
 * connection, via `share: true`, with any hypothetical further subscribers.
 */
import { Suspense, lazy, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Cable,
  CableCar,
  CircleSlash,
  Cpu,
  ListChecks,
  Map as MapIcon,
  MessageSquareText,
  ShieldAlert,
  Target,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { ViewHeader } from "@/views/ChatsView";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { fetchMissions, fetchMissionDetail } from "@/components/missions/api";
import { EventTimeline } from "@/components/missions/EventTimeline";
import { GlobalKillButton } from "@/components/missions/GlobalKillButton";
import { MissionTree } from "@/components/missions/MissionTree";
import { JarvisAgentPanel } from "@/components/missions/JarvisAgentPanel";
import {
  ToolApprovalPanel,
  useMissionToolApprovals,
} from "@/components/missions/ToolApprovalPanel";
import { VerdictPanel } from "@/components/missions/VerdictPanel";
import {
  selectActiveCount,
  useMissionsStore,
} from "@/components/missions/store";
import { useMissionWebSocket } from "@/components/missions/useMissionWebSocket";
import { useT } from "@/i18n";
import type { MissionPlanReady } from "@/types/missions";

const PtyTerminal = lazy(() =>
  import("@/components/missions/PtyTerminal").then((m) => ({
    default: m.PtyTerminal,
  })),
);

export function MissionsView() {
  const t = useT();
  useMissionWebSocket();

  const setMissions = useMissionsStore((s) => s.setMissions);
  const setMissionDetail = useMissionsStore((s) => s.setMissionDetail);
  const selectedMissionId = useMissionsStore((s) => s.selectedMissionId);
  const selectedWorkerId = useMissionsStore((s) => s.selectedWorkerId);
  const connected = useMissionsStore((s) => s.connected);
  const totalCount = useMissionsStore(useShallow((s) => Object.keys(s.missions).length));
  const activeCount = useMissionsStore(useShallow(selectActiveCount));
  const toolApprovalsQuery = useMissionToolApprovals(selectedMissionId);
  const pendingApprovalCount =
    toolApprovalsQuery.data?.approvals.filter(
      (approval) => approval.expires_at_ns / 1_000_000 > Date.now(),
    ).length ?? 0;

  const listQuery = useQuery({
    queryKey: ["missions"],
    queryFn: fetchMissions,
    refetchInterval: 10_000,
  });

  useEffect(() => {
    if (listQuery.data) setMissions(listQuery.data.missions);
  }, [listQuery.data, setMissions]);

  const detailQuery = useQuery({
    queryKey: ["missions", "detail", selectedMissionId],
    queryFn: () => fetchMissionDetail(selectedMissionId as string),
    enabled: !!selectedMissionId,
    staleTime: 5_000,
  });

  useEffect(() => {
    if (detailQuery.data && selectedMissionId) {
      setMissionDetail(
        selectedMissionId,
        detailQuery.data.events,
        detailQuery.data.verdicts,
        detailQuery.data.worker_snapshots,
      );
    }
  }, [detailQuery.data, selectedMissionId, setMissionDetail]);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Target className="h-4 w-4 text-primary" />}
        title={t("missions_view.title")}
        subtitle={t("missions_view.subtitle")}
        right={
          <div className="flex items-center gap-3">
            <ConnectionBadge connected={connected} />
            <Badge variant="outline" className="font-mono text-[10px]">
              {totalCount} total
            </Badge>
            <Badge
              variant={activeCount > 0 ? "default" : "outline"}
              className="font-mono text-[10px]"
            >
              {activeCount} {t("missions_view.active")}
            </Badge>
            <GlobalKillButton />
          </div>
        }
      />

      <div className="grid flex-1 grid-cols-[280px_1fr_320px] overflow-hidden">
        {/* Left pane */}
        <div className="flex h-full flex-col overflow-hidden border-r border-border bg-card/20">
          <div className="border-b border-border px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("missions_view.tree_label")}
          </div>
          <div className="flex-1 overflow-hidden">
            <MissionTree />
          </div>
        </div>

        {/* Center pane */}
        <div className="grid h-full grid-rows-[1fr_240px] overflow-hidden">
          <div className="overflow-hidden border-b border-border bg-background/20 p-3">
            {selectedWorkerId ? (
              <Suspense fallback={<TerminalFallback />}>
                <PtyTerminal
                  key={selectedWorkerId}
                  workerId={selectedWorkerId}
                />
              </Suspense>
            ) : (
              <SelectionPlaceholder hasMission={!!selectedMissionId} />
            )}
          </div>
          <div className="overflow-hidden bg-card/20">
            <div className="border-b border-border px-3 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("missions_view.timeline_label")}
            </div>
            <div className="h-[calc(100%-28px)]">
              <EventTimeline />
            </div>
          </div>
        </div>

        {/* Right pane */}
        <div className="flex h-full flex-col overflow-hidden border-l border-border bg-card/20">
          <Tabs defaultValue="verdicts" className="flex h-full flex-col">
            <div className="border-b border-border px-2 py-2">
              <TabsList className="grid w-full grid-cols-5">
                <TabsTrigger value="verdicts" className="gap-1.5">
                  <ListChecks className="h-3.5 w-3.5" />
                  Verdicts
                </TabsTrigger>
                <TabsTrigger value="reasoning" className="gap-1.5">
                  <MessageSquareText className="h-3.5 w-3.5" />
                  Reasoning
                </TabsTrigger>
                <TabsTrigger value="plan" className="gap-1.5">
                  <MapIcon className="h-3.5 w-3.5" />
                  Plan
                </TabsTrigger>
                <TabsTrigger value="openclaw" className="gap-1.5">
                  <Cpu className="h-3.5 w-3.5" />
                  Jarvis-Agent
                </TabsTrigger>
                <TabsTrigger
                  value="approvals"
                  className="relative gap-1.5"
                  aria-label={
                    pendingApprovalCount > 0
                      ? `${t("mission_tool_approvals.tab_label")}: ${pendingApprovalCount} ${t("mission_tool_approvals.pending")}`
                      : t("mission_tool_approvals.tab_label")
                  }
                >
                  <ShieldAlert className="h-3.5 w-3.5" />
                  {t("mission_tool_approvals.tab_label")}
                  {pendingApprovalCount > 0 ? (
                    <span className="absolute -right-1 -top-1 flex min-h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 font-mono text-[9px] leading-none text-destructive-foreground">
                      {pendingApprovalCount}
                    </span>
                  ) : null}
                </TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="verdicts" className="m-0 flex-1 overflow-hidden">
              <VerdictPanel />
            </TabsContent>
            <TabsContent value="reasoning" className="m-0 flex-1 overflow-hidden">
              <ReasoningPanel />
            </TabsContent>
            <TabsContent value="plan" className="m-0 flex-1 overflow-hidden">
              <PlanPanel />
            </TabsContent>
            <TabsContent value="openclaw" className="m-0 flex-1 overflow-hidden">
              <JarvisAgentPanel />
            </TabsContent>
            <TabsContent value="approvals" className="m-0 flex-1 overflow-hidden">
              <ToolApprovalPanel
                missionId={selectedMissionId}
                query={toolApprovalsQuery}
              />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}

function ConnectionBadge({ connected }: { connected: boolean }) {
  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] uppercase tracking-wider",
        connected
          ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
          : "border-amber-400/40 bg-amber-400/10 text-amber-300",
      )}
    >
      {connected ? (
        <>
          <Wifi className="h-3 w-3" /> live
        </>
      ) : (
        <>
          <WifiOff className="h-3 w-3" /> offline
        </>
      )}
    </span>
  );
}

function SelectionPlaceholder({ hasMission }: { hasMission: boolean }) {
  const t = useT();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border/60 bg-background/20 text-center text-sm text-muted-foreground">
      {hasMission ? (
        <>
          <CableCar className="h-8 w-8 text-muted-foreground/40" />
          <p>{t("missions_view.select_worker_hint")}</p>
        </>
      ) : (
        <>
          <Cable className="h-8 w-8 text-muted-foreground/40" />
          <p>{t("missions_view.select_mission_hint")}</p>
        </>
      )}
    </div>
  );
}

function TerminalFallback() {
  const t = useT();
  return (
    <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
      {t("missions_view.loading_terminal")}
    </div>
  );
}

function ReasoningPanel() {
  const t = useT();
  const events = useMissionsStore(
    useShallow((s) => {
      if (!s.selectedMissionId) return [];
      return (s.eventsByMission[s.selectedMissionId] ?? []).filter(
        (e) =>
          e.payload.event_type === "WorkerProgress" ||
          e.payload.event_type === "WorkerCorrectionRequired",
      );
    }),
  );

  if (events.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
        <CircleSlash className="h-7 w-7 text-muted-foreground/40" />
        <p>{t("missions_view.no_reasoning_notes")}</p>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <ul className="space-y-2 p-3">
        {events.map((env, idx) => {
          if (env.payload.event_type === "WorkerProgress") {
            const p = env.payload;
            return (
              <li
                key={`${env.event_id}-${idx}`}
                className="rounded border border-border/60 bg-card/30 p-2 text-xs"
              >
                <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-muted-foreground">
                  <span>w{p.worker_id.slice(0, 8)}</span>
                  {p.pct !== null && (
                    <span className="font-mono">{Math.round(p.pct * 100)}%</span>
                  )}
                </div>
                {p.note && <p className="mt-1 text-foreground/90">{p.note}</p>}
              </li>
            );
          }
          if (env.payload.event_type === "WorkerCorrectionRequired") {
            const p = env.payload;
            return (
              <li
                key={`${env.event_id}-${idx}`}
                className="rounded border border-amber-400/40 bg-amber-400/10 p-2 text-xs"
              >
                <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-amber-300">
                  <span>iter #{p.iteration} → {p.next_model}</span>
                  <span className="font-mono">w{p.worker_id.slice(0, 8)}</span>
                </div>
                <p className="mt-1 text-foreground/90">{p.correction_instruction}</p>
              </li>
            );
          }
          return null;
        })}
      </ul>
    </ScrollArea>
  );
}

function PlanPanel() {
  const t = useT();
  const planEnv = useMissionsStore(
    useShallow((s) => {
      if (!s.selectedMissionId) return null;
      const events = s.eventsByMission[s.selectedMissionId] ?? [];
      for (let i = events.length - 1; i >= 0; i--) {
        if (events[i].payload.event_type === "MissionPlanReady") {
          return events[i].payload as MissionPlanReady;
        }
      }
      return null;
    }),
  );

  if (!planEnv) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
        <MapIcon className="h-7 w-7 text-muted-foreground/40" />
        <p>{t("missions_view.no_plan_published")}</p>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-3 p-3">
        <div className="rounded border border-border/60 bg-card/30 p-2 text-xs">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("missions_view.expected_output")}
          </div>
          <p className="mt-1 text-foreground/90">
            {planEnv.expected_output || t("missions_view.not_specified")}
          </p>
        </div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("missions_view.steps")} ({planEnv.n_workers} {t("missions_view.workers")})
        </div>
        <ol className="space-y-2">
          {planEnv.plan.map((step, idx) => (
            <li
              key={idx}
              className="rounded border border-border/60 bg-card/30 p-2 text-xs"
            >
              <div className="text-[10px] uppercase tracking-wider text-primary">
                Step {idx + 1}
              </div>
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-foreground/80">
                {JSON.stringify(step, null, 2)}
              </pre>
            </li>
          ))}
        </ol>
      </div>
    </ScrollArea>
  );
}

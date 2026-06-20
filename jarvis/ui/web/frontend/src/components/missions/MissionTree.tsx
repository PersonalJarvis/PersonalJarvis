/**
 * Linke Pane: Tree der Missions + spawned Workers.
 *
 * react-arborist erwartet ein flat-array mit `children`-Property pro Node.
 * Wir bauen den Tree on-the-fly aus dem Store: jede MissionSummary wird ein
 * Top-Level-Node, jeder eindeutige worker_id aus dem zugehoerigen Event-Stream
 * landet als Child-Node.
 */
import { useMemo } from "react";
import { Tree, type NodeRendererProps } from "react-arborist";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cpu,
  Loader2,
  RotateCcw,
  Search,
  Skull,
  Target,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  MISSION_STATE_BADGE,
  type EventEnvelope,
  type MissionStateBadgeMeta,
  type MissionSummary,
  type WorkerSpawned,
} from "@/types/missions";
import { selectMissionList, useMissionsStore } from "./store";

const ICON_MAP: Record<MissionStateBadgeMeta["iconName"], LucideIcon> = {
  Loader2,
  CheckCircle2,
  XCircle,
  Skull,
  AlertTriangle,
  Clock,
  Search,
  RotateCcw,
};

interface TreeNode {
  id: string;
  name: string;
  kind: "mission" | "worker";
  mission?: MissionSummary;
  workerId?: string;
  workerCli?: string;
  children?: TreeNode[];
}

function buildTree(
  missions: MissionSummary[],
  eventsByMission: Record<string, EventEnvelope[]>,
): TreeNode[] {
  return missions.map((m): TreeNode => {
    const events = eventsByMission[m.id] ?? [];
    const seen = new Map<string, WorkerSpawned>();
    for (const env of events) {
      if (env.payload.event_type === "WorkerSpawned") {
        const ws = env.payload as WorkerSpawned;
        if (!seen.has(ws.worker_id)) seen.set(ws.worker_id, ws);
      }
    }
    const children: TreeNode[] = Array.from(seen.values()).map((ws) => ({
      id: `${m.id}::${ws.worker_id}`,
      name: ws.worker_id.slice(0, 8),
      kind: "worker",
      workerId: ws.worker_id,
      workerCli: ws.cli,
    }));
    return {
      id: m.id,
      name: shortPrompt(m.prompt),
      kind: "mission",
      mission: m,
      children: children.length ? children : undefined,
    };
  });
}

function shortPrompt(p: string): string {
  if (!p) return "(ohne Prompt)";
  return p.length > 48 ? p.slice(0, 47) + "…" : p;
}

export function MissionTree() {
  const { missions, eventsByMission } = useMissionsStore(
    useShallow((s) => ({
      missions: selectMissionList(s),
      eventsByMission: s.eventsByMission,
    })),
  );
  const selectedMissionId = useMissionsStore((s) => s.selectedMissionId);
  const selectedWorkerId = useMissionsStore((s) => s.selectedWorkerId);
  const selectMission = useMissionsStore((s) => s.selectMission);
  const selectWorker = useMissionsStore((s) => s.selectWorker);

  const data = useMemo(
    () => buildTree(missions, eventsByMission),
    [missions, eventsByMission],
  );

  if (data.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-xs text-muted-foreground">
        <Target className="h-7 w-7 text-muted-foreground/50" />
        <p>Noch keine Missions. Hauptjarvis dispatched gleich…</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-hidden">
      <Tree<TreeNode>
        data={data}
        openByDefault={true}
        width="100%"
        height={1000}
        rowHeight={32}
        indent={16}
        onSelect={(nodes) => {
          const node = nodes[0];
          if (!node) return;
          if (node.data.kind === "mission") {
            selectMission(node.data.id);
          } else if (node.data.kind === "worker" && node.data.mission === undefined) {
            // Worker-Node ist Child der Mission — Mission-ID extrahieren aus tree-id
            const missionId = node.data.id.split("::")[0];
            selectMission(missionId);
            selectWorker(node.data.workerId ?? null);
          }
        }}
        selection={
          selectedWorkerId
            ? `${selectedMissionId}::${selectedWorkerId}`
            : selectedMissionId ?? undefined
        }
      >
        {NodeRenderer}
      </Tree>
    </div>
  );
}

function NodeRenderer({ node, style, dragHandle }: NodeRendererProps<TreeNode>) {
  const t = useT();
  const data = node.data;
  const isMission = data.kind === "mission";
  const meta: MissionStateBadgeMeta | null =
    isMission && data.mission ? MISSION_STATE_BADGE[data.mission.state] : null;
  const Icon = meta ? ICON_MAP[meta.iconName] : Cpu;

  return (
    <div
      ref={dragHandle}
      style={style}
      className={cn(
        "group flex h-full cursor-pointer items-center gap-2 rounded-md px-2 text-xs transition-colors",
        node.isSelected
          ? "bg-primary/15 text-foreground"
          : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
      )}
      onClick={(e) => {
        if (data.children?.length) {
          e.stopPropagation();
          node.toggle();
          node.select();
        } else {
          node.select();
        }
      }}
    >
      {data.children?.length ? (
        <span
          className="text-muted-foreground/60"
          aria-hidden
          onClick={(e) => {
            e.stopPropagation();
            node.toggle();
          }}
        >
          {node.isOpen ? "▾" : "▸"}
        </span>
      ) : (
        <span className="w-3" aria-hidden />
      )}
      <Icon
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          isMission && data.mission?.state === "RUNNING" && "animate-spin",
          isMission && data.mission?.state === "CRITIQUING" && "animate-pulse",
        )}
      />
      <span className="flex-1 truncate">
        {isMission ? data.name : `${data.workerCli ?? "?"} · ${data.name}`}
      </span>
      {isMission && meta && (
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[9px] font-mono uppercase tracking-wider",
            meta.className,
          )}
        >
          {t(meta.labelKey)}
        </span>
      )}
    </div>
  );
}

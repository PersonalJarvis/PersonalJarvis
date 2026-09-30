/**
 * The fixed agents rail beside the stage (MASTERPLAN §4.1): the lead as a
 * compact centered master above the team — swatch and name in a small box —
 * then a search field and one row per remaining agent, plus the "+" that
 * opens the creator. Names open the chat; avatars open a compact profile
 * dialog without switching the conversation.
 *
 * The layout follows the Chef Bot reference: the master is centered and
 * larger, the team stays a compact list below the search. The rail is app
 * chrome: Ink & Paper tokens, both modes. The world beside it carries its
 * own branding; nothing here leaks into the viewport.
 *
 * It sits on either edge. Beside the island it is the RIGHT rail with its own
 * fixed width; inside the agent card it is the LEFT eighth and takes its width
 * from the grid cell — same rows, same sizes, only the divider swaps sides.
 *
 * Reordering is press-and-drag: pressing the left mouse button anywhere on a
 * row and moving it files the agent before or after the row under the
 * pointer. A small movement threshold keeps plain clicks (open chat /
 * profile) working; the drop position shows as a line above or below the
 * target row. HTML5 drag events and Alt + Arrow keys stay as fallbacks.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Eye, Loader2, Plus, Search, UsersRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { societyDisplayName } from "@/lib/societyDisplayName";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import type { SocietyChatGroup } from "@/lib/societyChatGroups";

import { AgentSwatch } from "../AgentSwatch";
import type { AgentRunState, SocietyAgent } from "../data";
import { AgentRosterActions } from "./AgentRosterActions";
import { GroupRosterActions } from "./GroupRosterActions";
import { useRosterUnread } from "./useRosterUnread";
import { ChatGroupDialog } from "../chat/ChatGroupDialog";

const HIDDEN_AGENTS_KEY = "society.roster.hidden-agent-ids";
const ORDER_KEY = "society.roster.order";
/** How far the pointer must travel before a press becomes a drag. */
const PRESS_DRAG_THRESHOLD_PX = 6;
/** A deliberate pause over another row creates a team; a quick drop reorders. */
const GROUP_HOLD_MS = 750;
type GroupDropTarget = { kind: "agent" | "group"; id: string };

function readHiddenAgents(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(HIDDEN_AGENTS_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

/**
 * The person's own row order, newest drag last. Unknown ids (retired agents,
 * a fresh install) are ignored when sorting; agents missing from the list
 * keep their default place at the end until the next drag persists them.
 */
function readRosterOrder(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

const AgentProfileDialog = lazy(() => import("../card/AgentProfileDialog").then((module) => ({ default: module.AgentProfileDialog })));

const STATE_DOT: Record<AgentRunState, string> = {
  idle: "bg-muted-foreground/50",
  working: "bg-success",
  waiting: "bg-warning",
  paused: "bg-muted-foreground/30",
};

export interface RosterRailProps {
  agents: SocietyAgent[];
  groups?: SocietyChatGroup[];
  activeGroupId?: string | null;
  onOpenGroup?: (groupId: string) => void;
  onGroupAgents?: (sourceId: string, targetId: string) => void;
  onAddAgentToGroup?: (agentId: string, groupId: string) => void;
  loading: boolean;
  /** True while rows come from the sample roster rather than society.db. */
  sample: boolean;
  activeAgentId: string | null;
  onOpen: (agentId: string) => void;
  onCreate: () => void;
  /** Which edge the rail sits on; decides which side carries the divider. */
  side?: "left" | "right";
  /** Replaces the fixed width when the rail is a grid cell rather than a flex sibling. */
  className?: string;
  /** Sits above the title — the way back to the rest of the app. */
  header?: ReactNode;
  /** Team actions stay visible below the scrolling roster. */
  footer?: ReactNode;
}

export function RosterRail({
  agents,
  groups = [],
  activeGroupId = null,
  onOpenGroup,
  onGroupAgents,
  onAddAgentToGroup,
  loading,
  sample,
  activeAgentId,
  onOpen,
  onCreate,
  side = "right",
  className,
  header,
  footer,
}: RosterRailProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [hiddenIds, setHiddenIds] = useState(readHiddenAgents);
  const [orderIds, setOrderIds] = useState(readRosterOrder);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const [dropAfter, setDropAfter] = useState(false);
  const [groupHover, setGroupHover] = useState<GroupDropTarget | null>(null);
  const [groupReady, setGroupReady] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [menu, setMenu] = useState<{ agentId: string; x: number; y: number } | null>(null);
  const [groupMenu, setGroupMenu] = useState<{ groupId: string; x: number; y: number } | null>(null);
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const profile = agents.find((agent) => agent.agentId === profileId);
  const menuAgent = agents.find((agent) => agent.agentId === menu?.agentId);
  const menuGroup = groups.find((group) => group.group_id === groupMenu?.groupId);
  const editingGroup = groups.find((group) => group.group_id === editingGroupId);
  const unread = useRosterUnread(agents, activeAgentId);
  const assistantName = useEventStore((state) => state.assistantName);

  const setHidden = useCallback((agentId: string, hidden: boolean) => {
    setHiddenIds((current) => {
      const next = hidden ? [...new Set([...current, agentId])] : current.filter((id) => id !== agentId);
      localStorage.setItem(HIDDEN_AGENTS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);
  const closeMenu = useCallback(() => setMenu(null), []);
  const openMenu = (event: MouseEvent, agentId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setGroupMenu(null);
    setMenu({ agentId, x: event.clientX, y: event.clientY });
  };
  const openGroupMenu = (event: MouseEvent, groupId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setMenu(null);
    setGroupMenu({ groupId, x: event.clientX, y: event.clientY });
  };

  const lead = useMemo(() => agents.find((a) => a.tier === "lead") ?? null, [agents]);
  const groupedIds = useMemo(() => new Set(groups.flatMap((group) => group.members)), [groups]);
  const visibleGroups = groups.filter((group) => `${group.name} ${group.members.map((id) => agents.find((agent) => agent.agentId === id)?.name ?? "").join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));

  const { leadVisible, rows } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = agents.filter((a) => {
      if (!showHidden && hiddenIds.includes(a.agentId)) return false;
      if (!q) return true;
      return `${a.name} ${societyDisplayName(a, assistantName)} ${a.title}`.toLowerCase().includes(q);
    },);
    const masterVisible = lead ? filtered.some((a) => a.agentId === lead.agentId) : false;
    // Orchestrators, then specialists; stable within a tier. The lead lives
    // in its own centered hero above and never repeats in the list.
    const rank = { lead: 0, orchestrator: 1, specialist: 2 } as const;
    const rest = filtered
      .filter((a) => a.agentId !== lead?.agentId && !groupedIds.has(a.agentId));
    // A drag persists the full visible order, so from then on the person's
    // own arrangement wins over the tier grouping. Agents the list never saw
    // (freshly created, or a retired id still stored) settle at the end in
    // tier order until the next drag files them in.
    const position = new Map(orderIds.map((id, index) => [id, index] as const));
    const custom = rest.some((a) => position.has(a.agentId));
    rest.sort((a, b) => {
      if (custom) {
        const ai = position.get(a.agentId);
        const bi = position.get(b.agentId);
        if (ai !== undefined && bi !== undefined) return ai - bi;
        if (ai !== undefined) return -1;
        if (bi !== undefined) return 1;
      }
      return rank[a.tier] - rank[b.tier];
    });
    return { leadVisible: masterVisible, rows: rest };
  }, [agents, assistantName, groupedIds, hiddenIds, lead, orderIds, query, showHidden]);

  // While searching, the list is a filtered excerpt — dragging there would
  // file agents by where they happen to sit in the excerpt, so rows stay put.
  const reorderable = query.trim() === "";

  /**
   * File `fromId` directly before `toId`, or directly after it when `after`
   * is set. Unlike the native-drop path this can also land behind the last
   * row, which is what moving an agent down needs.
   */
  const moveToPosition = useCallback((fromId: string, toId: string, after: boolean) => {
    if (fromId === toId) return;
    setOrderIds((current) => {
      const base = rows.map((a) => a.agentId);
      const known = new Set(base);
      for (const id of current) if (!known.has(id)) base.push(id);
      const without = base.filter((id) => id !== fromId);
      const at = without.indexOf(toId);
      if (at === -1) {
        const next = [...without, fromId];
        try {
          localStorage.setItem(ORDER_KEY, JSON.stringify(next));
        } catch {
          // Private mode: the order holds for this window.
        }
        return next;
      }
      const insert = after ? at + 1 : at;
      const next = [...without.slice(0, insert), fromId, ...without.slice(insert)];
      try {
        localStorage.setItem(ORDER_KEY, JSON.stringify(next));
      } catch {
        // Private mode: the order holds for this window.
      }
      return next;
    });
  }, [rows]);

  /** Keyboard twin of the drag: Alt + Arrow Up / Down steps the row. */
  const moveStep = useCallback((agentId: string, delta: -1 | 1) => {
    setOrderIds((current) => {
      const base = rows.map((a) => a.agentId);
      const known = new Set(base);
      for (const id of current) if (!known.has(id)) base.push(id);
      const at = base.indexOf(agentId);
      const swap = at + delta;
      if (at === -1 || swap < 0 || swap >= base.length) return current;
      const next = [...base];
      [next[at], next[swap]] = [next[swap], next[at]];
      try {
        localStorage.setItem(ORDER_KEY, JSON.stringify(next));
      } catch {
        // Private mode: the order holds for this window.
      }
      return next;
    });
  }, [rows]);

  const onRowKeyDown = (event: KeyboardEvent, agentId: string) => {
    if (!reorderable || !event.altKey) return;
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      moveStep(agentId, event.key === "ArrowUp" ? -1 : 1);
    }
  };

  // --- Press-and-drag: the whole row is the handle. -----------------------
  // One pointer gesture owns both actions. Native HTML drag competes with
  // pointermove in WebView and can cancel the gesture before release.
  // A quick drop reorders; holding over a row or group makes a team.
  const listRef = useRef<HTMLUListElement | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  const pendingRef = useRef<{ id: string; startX: number; startY: number; pointerId: number } | null>(null);
  const pointerDragRef = useRef(false);
  const suppressClickRef = useRef(false);
  const dragIdRef = useRef<string | null>(null);
  const dropIdRef = useRef<string | null>(null);
  const dropAfterRef = useRef(false);
  const groupHoverRef = useRef<GroupDropTarget | null>(null);
  const groupReadyRef = useRef(false);
  const holdTimerRef = useRef<number | null>(null);
  const reorderableRef = useRef(reorderable);
  const moveToRef = useRef(moveToPosition);
  const groupAgentsRef = useRef(onGroupAgents);
  const addToGroupRef = useRef(onAddAgentToGroup);
  dragIdRef.current = dragId;
  dropIdRef.current = dropId;
  dropAfterRef.current = dropAfter;
  reorderableRef.current = reorderable;
  moveToRef.current = moveToPosition;
  groupAgentsRef.current = onGroupAgents;
  addToGroupRef.current = onAddAgentToGroup;

  useEffect(() => {
    const rowElements = () => {
      const list = listRef.current;
      if (!list) return [] as HTMLElement[];
      return [...list.querySelectorAll("[data-agent-id]")] as HTMLElement[];
    };
    const hit = (element: HTMLElement, x: number, y: number) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && x >= rect.left && x <= rect.right
        && y >= rect.top && y <= rect.bottom;
    };
    const groupTargetAt = (x: number, y: number, sourceId: string): GroupDropTarget | null => {
      if (groupAgentsRef.current) {
        const agent = rowElements().find((element) =>
          element.dataset.agentId !== sourceId && hit(element, x, y));
        if (agent?.dataset.agentId) return { kind: "agent", id: agent.dataset.agentId };
      }
      if (addToGroupRef.current) {
        const group = [...(railRef.current?.querySelectorAll("[data-group-id]") ?? [])]
          .find((element) => hit(element as HTMLElement, x, y)) as HTMLElement | undefined;
        if (group?.dataset.groupId) return { kind: "group", id: group.dataset.groupId };
      }
      return null;
    };
    const clearHold = () => {
      if (holdTimerRef.current !== null) window.clearTimeout(holdTimerRef.current);
      holdTimerRef.current = null;
      groupHoverRef.current = null;
      groupReadyRef.current = false;
      setGroupHover(null);
      setGroupReady(false);
    };
    const updateHold = (target: GroupDropTarget | null) => {
      const previous = groupHoverRef.current;
      if (previous?.kind === target?.kind && previous?.id === target?.id) return;
      clearHold();
      if (!target) return;
      groupHoverRef.current = target;
      setGroupHover(target);
      holdTimerRef.current = window.setTimeout(() => {
        groupReadyRef.current = true;
        setGroupReady(true);
      }, GROUP_HOLD_MS);
    };
    const onMove = (event: PointerEvent) => {
      if (!reorderableRef.current) return;
      let sourceId = pointerDragRef.current ? dragIdRef.current : null;
      if (!sourceId) {
        const pending = pendingRef.current;
        if (!pending) return;
        if (event.pointerId !== pending.pointerId) return;
        const moved = Math.max(Math.abs(event.clientX - pending.startX), Math.abs(event.clientY - pending.startY));
        if (moved < PRESS_DRAG_THRESHOLD_PX) return;
        pointerDragRef.current = true;
        sourceId = pending.id;
        setDragId(pending.id);
        setDropId(null);
        setDropAfter(false);
      }
      if (!sourceId) return;
      {
        const elements = rowElements();
        const sourceEl = elements.find((el) => el.getAttribute("data-agent-id") === sourceId);
        if (sourceEl) {
          const rect = sourceEl.getBoundingClientRect();
          if (rect.height > 0 && event.clientY >= rect.top && event.clientY <= rect.bottom) {
            updateHold(null);
            if (dropIdRef.current !== null) {
              setDropId(null);
              setDropAfter(false);
            }
            return;
          }
        }
        const groupTarget = groupTargetAt(event.clientX, event.clientY, sourceId);
        updateHold(groupTarget);
        if (groupTarget?.kind === "group") {
          if (dropIdRef.current !== null) setDropId(null);
          if (event.cancelable) event.preventDefault();
          return;
        }
        let target: { id: string; after: boolean } | null = null;
        for (const el of elements) {
          const id = el.getAttribute("data-agent-id");
          if (!id || id === sourceId) continue;
          const rect = el.getBoundingClientRect();
          if (rect.height === 0) continue;
          if (event.clientY < rect.top + rect.height / 2) {
            target = { id, after: false };
            break;
          }
        }
        if (!target) {
          const others = elements.filter((el) => el.getAttribute("data-agent-id") !== sourceId);
          const last = others[others.length - 1];
          const lastId = last?.getAttribute("data-agent-id");
          if (lastId) target = { id: lastId, after: true };
        }
        if (target) {
          if (dropIdRef.current !== target.id || dropAfterRef.current !== target.after) {
            setDropId(target.id);
            setDropAfter(target.after);
          }
        } else if (dropIdRef.current !== null) {
          setDropId(null);
          setDropAfter(false);
        }
        if (event.cancelable) event.preventDefault();
        return;
      }
    };
    const finish = (commit: boolean, event?: PointerEvent) => {
      if (pointerDragRef.current && dragIdRef.current) {
        const from = dragIdRef.current;
        const toId = dropIdRef.current;
        const after = dropAfterRef.current;
        const held = groupReadyRef.current ? groupHoverRef.current : null;
        const releasedOn = event ? groupTargetAt(event.clientX, event.clientY, from) : null;
        pendingRef.current = null;
        pointerDragRef.current = false;
        setDragId(null);
        setDropId(null);
        setDropAfter(false);
        clearHold();
        if (commit && held && releasedOn?.kind === held.kind && releasedOn.id === held.id) {
          if (held.kind === "agent") groupAgentsRef.current?.(from, held.id);
          else addToGroupRef.current?.(from, held.id);
        } else if (commit && releasedOn?.kind !== "group" && toId && toId !== from) {
          moveToRef.current(from, toId, after);
        }
        suppressClickRef.current = true;
        window.setTimeout(() => {
          suppressClickRef.current = false;
        }, 0);
      } else {
        pendingRef.current = null;
        clearHold();
      }
    };
    const onUp = (event: PointerEvent) => finish(true, event);
    const onCancel = () => finish(false);
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") finish(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("keydown", onKey);
      if (holdTimerRef.current !== null) window.clearTimeout(holdTimerRef.current);
    };
  }, []);

  const onRowPointerDown = (event: ReactPointerEvent, agentId: string) => {
    if (!reorderable) return;
    if (event.pointerType === "touch") return;
    if (event.button !== undefined && event.button !== 0) return;
    pendingRef.current = { id: agentId, startX: event.clientX, startY: event.clientY, pointerId: event.pointerId };
  };

  const onRowClickCapture = (event: MouseEvent) => {
    if (suppressClickRef.current) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const openAgent = (agentId: string) => {
    if (suppressClickRef.current) return;
    onOpen(agentId);
  };

  const openProfile = (agentId: string) => {
    if (suppressClickRef.current) return;
    setProfileId(agentId);
  };

  const hiddenCount = agents.filter((agent) => hiddenIds.includes(agent.agentId)).length;

  return (
    <aside
      ref={railRef}
      data-testid="society-roster-rail"
      className={cn(
        "flex h-full min-h-0 flex-col border-border bg-sidebar",
        side === "left" ? "border-r border-border" : "border-l border-border",
        className ?? "w-[300px] shrink-0",
      )}
    >
      {header}
      <div className="flex items-center justify-between gap-2 px-3 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">
            {t("society.roster.title")}
          </h2>
          {sample ? (
            <Badge variant="outline" className="text-xs">
              {t("society.sample_badge")}
            </Badge>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
        {!sample && onOpenGroup && <Button size="sm" variant="secondary" className="h-8 px-2" onClick={() => setCreatingGroup(true)} data-testid="society-create-group-button" aria-label={t("society.groups.create")} title={t("society.groups.create")}>
          <UsersRound className="h-4 w-4" aria-hidden />
        </Button>}
        <Button
          size="sm"
          variant="secondary"
          className="h-8 gap-1 px-2.5"
          onClick={onCreate}
          data-testid="society-create-button"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("society.roster.create")}
        </Button>
        </div>
      </div>
      <label className="relative mx-3 mt-3 block">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("society.roster.search")}
          aria-label={t("society.roster.search")}
          className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
        />
      </label>
      {hiddenCount > 0 && <button type="button" onClick={() => setShowHidden((value) => !value)}
        className="mx-3 mt-2 flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Eye className="h-3.5 w-3.5" aria-hidden />
        {t(showHidden ? "society.roster.hide_hidden" : "society.roster.show_hidden").replace("{0}", String(hiddenCount))}
      </button>}
      <ScrollArea className="mt-2 min-h-0 flex-1">
        {lead && leadVisible ? (
          <div className="flex justify-center px-2 pb-2">
            <div
              data-testid="society-lead-hero"
              onContextMenu={(event) => openMenu(event, lead.agentId)}
              className={cn(
                "flex flex-col items-center gap-1.5 rounded-xl bg-secondary/50 px-5 py-3 text-center transition-colors hover:bg-secondary/80",
                lead.agentId === activeAgentId && "bg-secondary",
              )}
            >
              <button type="button" onClick={() => openProfile(lead.agentId)} aria-label={t("society.profile_card.open").replace("{0}", societyDisplayName(lead, assistantName))} className="relative rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <AgentSwatch agent={lead} size={56} />
                {lead.state === "working" ? (
                  <span
                    role="status"
                    aria-label={t("society.roster.thinking")}
                    title={t("society.roster.thinking")}
                    className="absolute -right-1 -top-1 grid h-5 w-5 place-items-center rounded-full bg-sidebar ring-2 ring-sidebar"
                  >
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
                  </span>
                ) : unread.has(lead.agentId) ? (
                  <span
                    aria-label={t("society.roster.unread")}
                    title={t("society.roster.unread")}
                    className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full bg-sky-400 ring-2 ring-sidebar"
                  />
                ) : null}
              </button>
              <button type="button" onClick={() => openAgent(lead.agentId)} aria-current={lead.agentId === activeAgentId ? "true" : undefined} className="flex max-w-full items-center justify-center gap-1.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="truncate text-sm font-medium text-foreground">{societyDisplayName(lead, assistantName)}</span>
                <Badge variant="secondary" className="shrink-0 px-1.5 py-0 text-xs">
                  {t("society.tier.lead")}
                </Badge>
              </button>
            </div>
          </div>
        ) : null}
        {leadVisible ? (
          <div className="mx-3 mb-1 border-t border-border/60" aria-hidden />
        ) : null}
        {visibleGroups.length > 0 && <ul className="flex flex-col gap-0.5 px-2 pb-2">
          {visibleGroups.map((group) => <li key={group.group_id}>
            <button type="button" data-group-id={group.group_id} onClick={() => onOpenGroup?.(group.group_id)} onContextMenu={(event) => openGroupMenu(event, group.group_id)} aria-current={activeGroupId === group.group_id ? "true" : undefined}
              className={cn("flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", activeGroupId === group.group_id && "bg-secondary", groupHover?.kind === "group" && groupHover.id === group.group_id && (groupReady ? "ring-2 ring-primary" : "ring-1 ring-border-strong"))}
              data-testid={`society-group-${group.group_id}`}>
              <span className="flex w-12 shrink-0 items-center justify-center">
                {group.members.slice(0, 3).map((id, index) => {
                  const member = agents.find((agent) => agent.agentId === id);
                  return member ? <span key={id} className={cn("rounded-full ring-2 ring-sidebar", index > 0 && "-ml-3")}><AgentSwatch agent={member} size={26} /></span> : null;
                })}
              </span>
              <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-foreground">{group.name}</span>
                <span className="block truncate text-xs text-muted-foreground">{groupHover?.kind === "group" && groupHover.id === group.group_id
                  ? t(groupReady ? "society.groups.release_to_group" : "society.groups.hold_to_group")
                  : group.members.map((id) => agents.find((agent) => agent.agentId === id)?.name ?? id).join(", ")}</span>
              </span>
            </button>
          </li>)}
        </ul>}
        <ul ref={listRef} className="flex flex-col gap-0.5 px-2 pb-3">
          {loading && !leadVisible && rows.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.loading")}</li>
          ) : null}
          {!loading && !leadVisible && rows.length === 0 && visibleGroups.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.empty")}</li>
          ) : null}
          {rows.map((agent) => {
            const isDragging = dragId === agent.agentId;
            const isDropTarget = dropId === agent.agentId && dragId !== agent.agentId;
            return (
            <li key={agent.agentId} className="relative">
              {isDropTarget && !dropAfter && !groupReady ? (
                <div aria-hidden className="pointer-events-none absolute inset-x-2 top-0 z-10 h-0.5 -translate-y-1/2 rounded-full bg-sky-400" />
              ) : null}
              <div
                data-agent-id={agent.agentId}
                onPointerDown={(event) => onRowPointerDown(event, agent.agentId)}
                onClickCapture={onRowClickCapture}
                onKeyDown={(event) => onRowKeyDown(event, agent.agentId)}
                onContextMenu={(event) => openMenu(event, agent.agentId)}
                title={reorderable ? `${t("society.roster.reorder")} · ${t("society.roster.reorder_keys")}` : undefined}
                aria-keyshortcuts={reorderable ? "Alt+ArrowUp Alt+ArrowDown" : undefined}
                className={cn(
                  "flex w-full select-none items-center rounded-md px-2 text-left transition-colors hover:bg-secondary",
                  reorderable ? "cursor-grab" : null,
                  isDragging ? "cursor-grabbing opacity-40" : null,
                  agent.agentId === activeAgentId && "bg-secondary",
                  hiddenIds.includes(agent.agentId) && "opacity-60",
                  isDropTarget && "bg-secondary ring-1 ring-inset ring-border-strong",
                  groupHover?.kind === "agent" && groupHover.id === agent.agentId && groupReady && "ring-2 ring-primary",
                )}
              >
                <button type="button" onClick={() => openProfile(agent.agentId)} aria-label={t("society.profile_card.open").replace("{0}", agent.name)} className="shrink-0 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <AgentSwatch agent={agent} size={48} />
                </button>
                <button type="button" onClick={() => openAgent(agent.agentId)} aria-current={agent.agentId === activeAgentId ? "true" : undefined} className="flex min-w-0 flex-1 select-none items-center gap-2.5 rounded-md py-2 pl-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-sm font-medium text-foreground">{agent.name}</span>
                    {agent.tier === "lead" ? (
                      <Badge variant="secondary" className="px-1.5 py-0 text-xs">
                        {t("society.tier.lead")}
                      </Badge>
                    ) : null}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">{agent.title}</span>
                </span>
                <RowStatus agent={agent} hasUnread={unread.has(agent.agentId)} />
                </button>
              </div>
              {groupHover?.kind === "agent" && groupHover.id === agent.agentId && (
                <span data-testid="society-group-drop-hint" className="pointer-events-none absolute right-2 top-1 rounded bg-primary px-1.5 py-0.5 text-[10px] text-primary-foreground">
                  {t(groupReady ? "society.groups.release_to_group" : "society.groups.hold_to_group")}
                </span>
              )}
              {isDropTarget && dropAfter && !groupReady ? (
                <div aria-hidden className="pointer-events-none absolute inset-x-2 bottom-0 z-10 h-0.5 translate-y-1/2 rounded-full bg-sky-400" />
              ) : null}
            </li>
            );
          })}
        </ul>
      </ScrollArea>
      {footer}
      {menu && menuAgent && <AgentRosterActions key={menu.agentId} agent={menuAgent} roster={agents} sample={sample}
        hidden={hiddenIds.includes(menu.agentId)} x={menu.x} y={menu.y} onVisibilityChange={setHidden} onDismiss={closeMenu} />}
      {groupMenu && menuGroup && <GroupRosterActions key={groupMenu.groupId} group={menuGroup} x={groupMenu.x} y={groupMenu.y}
        onEdit={() => setEditingGroupId(menuGroup.group_id)} onDismiss={() => setGroupMenu(null)} />}
      {profile && <Suspense fallback={null}><AgentProfileDialog key={profile.agentId} agent={profile} sample={sample} onClose={() => setProfileId(null)} /></Suspense>}
      {creatingGroup && <ChatGroupDialog agents={agents} onClose={() => setCreatingGroup(false)} onSaved={(id) => onOpenGroup?.(id)} />}
      {editingGroup && <ChatGroupDialog group={editingGroup} agents={agents} onClose={() => setEditingGroupId(null)} onSaved={(id) => onOpenGroup?.(id)} />}
    </aside>
  );
}

/**
 * The three states the roster dot can be in: thinking (a loading spinner
 * while `state` is working), fresh results (green until the row is opened),
 * or the plain backend state (grey idle, amber waiting, faint paused).
 */
function RowStatus({ agent, hasUnread }: { agent: SocietyAgent; hasUnread: boolean }) {
  const t = useT();
  if (agent.state === "working") {
    const label = t("society.roster.thinking");
    return (
      <span
        role="status"
        aria-label={label}
        title={label}
        className="grid h-4 w-4 shrink-0 place-items-center"
      >
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden />
      </span>
    );
  }
  if (hasUnread) {
    const label = t("society.roster.unread");
    return (
      <span
        className="h-2 w-2 shrink-0 rounded-full bg-sky-400"
        title={label}
        aria-label={label}
      />
    );
  }
  return (
    <span
      className={cn("h-2 w-2 shrink-0 rounded-full", STATE_DOT[agent.state])}
      title={t(`society.state.${agent.state}`)}
      aria-label={t(`society.state.${agent.state}`)}
    />
  );
}

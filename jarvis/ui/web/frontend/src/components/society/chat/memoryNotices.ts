import type { NoticeItem, TimelineItem } from "@/components/agentchat/reduce";

const NO_NOTICES: NoticeItem[] = [];

export interface FoldedTimeline {
  /** The timeline without the memory receipts that moved into a turn. */
  items: TimelineItem[];
  /** Turn id → the memory receipts drawn inside that turn's trace. */
  memoryByTurn: Map<string, NoticeItem[]>;
}

/**
 * A memory receipt belongs to the work of the turn before it, never below
 * the reply (maintainer, 2026-10-03). Background reviews post the receipt as
 * its own notice, often after the reply has landed, so each `memory_updated`
 * notice moves into the nearest earlier turn and is drawn in that turn's
 * trace above its answer. A receipt with no earlier turn stays where it is.
 */
export function foldMemoryNotices(items: TimelineItem[]): FoldedTimeline {
  const memoryByTurn = new Map<string, NoticeItem[]>();
  if (!items.some((item) => item.type === "notice" && item.kind === "memory_updated")) {
    return { items, memoryByTurn };
  }
  const kept: TimelineItem[] = [];
  let lastTurnId: string | null = null;
  for (const item of items) {
    if (item.type === "turn") lastTurnId = item.id;
    if (item.type === "notice" && item.kind === "memory_updated" && lastTurnId !== null) {
      memoryByTurn.set(lastTurnId, [...(memoryByTurn.get(lastTurnId) ?? NO_NOTICES), item]);
      continue;
    }
    kept.push(item);
  }
  return { items: kept, memoryByTurn };
}

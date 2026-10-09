import { lazy, Suspense, useState } from "react";
import { NotedLine } from "@/components/agentchat/MessengerTurn";
import type { NoticeItem } from "@/components/agentchat/reduce";
import { useT } from "@/i18n";

const MemoryFileViewer = lazy(() => import("./MemoryFileViewer").then(module => ({ default: module.MemoryFileViewer })));

/**
 * The agent's own memory file a "Memory updated" receipt points at, or null
 * when the path leaves the agent's folder. The receipt draws no row of its
 * own: the turn's "Noted" quill line opens it.
 */
export function memoryNoticePath(item: NoticeItem): string | null {
  const path = typeof item.data.path === "string" ? item.data.path : "";
  if (!path.startsWith(`society/${item.agentId}/`) || path.split("/").includes("..")) return null;
  return path;
}

/** "Memory updated · USER.md": what a "Noted" line opens, as its tooltip. */
export function memoryNoticeTitle(t: ReturnType<typeof useT>, path: string): string {
  return `${t("society.chat.memory_updated")} · ${path.split("/").at(-1)}`;
}

/** The memory file of a receipt, with the change it made when the receipt carries one. */
export function MemoryNoticeViewer({ item, onClose }: { item: NoticeItem; onClose: () => void }) {
  const path = memoryNoticePath(item);
  if (!path) return null;
  const before = typeof item.data.before === "string" ? item.data.before : undefined;
  const after = typeof item.data.after === "string" ? item.data.after : undefined;
  const patch = Array.isArray(item.data.markdown_diff) && item.data.markdown_diff.every(line => typeof line === "string") ? item.data.markdown_diff as string[] : undefined;
  return <Suspense fallback={null}><MemoryFileViewer path={path} before={before} after={after} patch={patch} onClose={onClose} /></Suspense>;
}

/** A memory receipt with no turn before it: a lone "Noted" quill line that opens the file. */
export function MemoryUpdateNotice({ item }: { item: NoticeItem }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const path = memoryNoticePath(item);
  if (!path) return null;
  return <>
    <NotedLine link={{ key: item.id, title: memoryNoticeTitle(t, path), onOpen: () => setOpen(true) }} />
    {open && <MemoryNoticeViewer item={item} onClose={() => setOpen(false)} />}
  </>;
}

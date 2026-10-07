import { lazy, Suspense, useState } from "react";
import { ChevronRight, FilePenLine, FileText } from "lucide-react";
import type { NoticeItem } from "@/components/agentchat/reduce";
import { useT } from "@/i18n";

const MemoryFileViewer = lazy(() => import("./MemoryFileViewer").then(module => ({ default: module.MemoryFileViewer })));

/**
 * The "Memory updated" receipt. Inside a turn (`inTrace`) it is one quiet
 * step of the reasoning trace, drawn like a tool row; standalone (no turn
 * before it) it keeps the centred chip.
 */
export function MemoryUpdateNotice({ item, inTrace = false }: { item: NoticeItem; inTrace?: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const path = typeof item.data.path === "string" ? item.data.path : "";
  const before = typeof item.data.before === "string" ? item.data.before : undefined;
  const after = typeof item.data.after === "string" ? item.data.after : undefined;
  const patch = Array.isArray(item.data.markdown_diff) && item.data.markdown_diff.every(line => typeof line === "string") ? item.data.markdown_diff as string[] : undefined;
  if (!path.startsWith(`society/${item.agentId}/`) || path.split("/").includes("..")) return null;
  const label = `${t("society.chat.memory_updated")} · ${path.split("/").at(-1)}`;
  return <>
    {inTrace ? (
      <button type="button" onClick={() => setOpen(true)} data-trace-memory={item.id} className="group/trace flex w-full min-w-0 items-start gap-3 rounded-md py-1 text-left text-sm leading-6 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span aria-hidden className="trace-node"><FilePenLine className="h-3.5 w-3.5 shrink-0" /></span>
        <span className="min-w-0 flex-1 truncate text-foreground-secondary">{label}</span>
        <ChevronRight aria-hidden className="mt-[5px] h-3.5 w-3.5 shrink-0 opacity-40 transition group-hover/trace:opacity-90" />
      </button>
    ) : (
      <button type="button" onClick={() => setOpen(true)} className="mx-auto my-3 flex max-w-[min(100%,36rem)] select-none items-center gap-2 rounded-lg bg-secondary px-3 py-2 text-left text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <FileText size={14} aria-hidden className="shrink-0" />
        <span className="truncate">{label}</span>
        <ChevronRight size={14} aria-hidden className="shrink-0 text-muted-foreground" />
      </button>
    )}
    {open && <Suspense fallback={null}><MemoryFileViewer path={path} before={before} after={after} patch={patch} onClose={() => setOpen(false)} /></Suspense>}
  </>;
}

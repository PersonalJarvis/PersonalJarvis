import { lazy, Suspense, useState } from "react";
import { ChevronRight, FileText } from "lucide-react";
import type { NoticeItem } from "@/components/agentchat/reduce";
import { useT } from "@/i18n";

const MemoryFileViewer = lazy(() => import("./MemoryFileViewer").then(module => ({ default: module.MemoryFileViewer })));

export function MemoryUpdateNotice({ item }: { item: NoticeItem }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const path = typeof item.data.path === "string" ? item.data.path : "";
  const before = typeof item.data.before === "string" ? item.data.before : undefined;
  const after = typeof item.data.after === "string" ? item.data.after : undefined;
  const patch = Array.isArray(item.data.markdown_diff) && item.data.markdown_diff.every(line => typeof line === "string") ? item.data.markdown_diff as string[] : undefined;
  if (!path.startsWith(`society/${item.agentId}/`) || path.split("/").includes("..")) return null;
  return <>
    <button type="button" onClick={() => setOpen(true)} className="mx-auto my-3 flex max-w-[min(100%,36rem)] select-none items-center gap-2 rounded-lg bg-secondary px-3 py-2 text-left text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <FileText size={14} aria-hidden className="shrink-0" />
      <span className="truncate">{t("society.chat.memory_updated")} · {path.split("/").at(-1)}</span>
      <ChevronRight size={14} aria-hidden className="shrink-0 text-muted-foreground" />
    </button>
    {open && <Suspense fallback={null}><MemoryFileViewer path={path} before={before} after={after} patch={patch} onClose={() => setOpen(false)} /></Suspense>}
  </>;
}

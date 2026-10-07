import { ChevronRight, Code2 } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { openCodingThread } from "./openCodingThread";

/**
 * A coding thread this agent started, or news from one: one quiet line that
 * opens the thread in the Agentic IDE, where the whole exchange is visible.
 */
export function CodingThreadActivity({ label, threadId, failed = false }: {
  label: string; threadId: string; failed?: boolean;
}) {
  const t = useT();
  return <button type="button" onClick={() => openCodingThread(threadId)} title={t("society.chat.coding_thread_open")}
    data-testid="coding-thread-activity"
    className={cn("mx-auto my-3 flex max-w-[min(100%,36rem)] items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", failed && "text-destructive")}>
    <Code2 aria-hidden className="h-3.5 w-3.5 shrink-0" />
    <span className="min-w-0 truncate">{label}</span>
    <span className="shrink-0 underline underline-offset-4">{t("society.chat.coding_thread_open")}</span>
    <ChevronRight aria-hidden className="h-3 w-3 shrink-0" />
  </button>;
}

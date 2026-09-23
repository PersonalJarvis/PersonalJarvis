import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { FileText, X } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useT } from "@/i18n";
import { MemoryChanges, MemoryMarkdown } from "./MemoryDocument";

interface MemoryFileResponse { path: string; content: string; updated_ms: number }

/** Historical write receipts stay distinct from the current file on disk. */
export function MemoryFileViewer({ path, before, after, patch, onClose }: {
  path: string; before?: string; after?: string; patch?: string[]; onClose: () => void;
}) {
  const t = useT();
  const [file, setFile] = useState<MemoryFileResponse | null>(null);
  const [error, setError] = useState(false);
  const opener = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const hasChange = before !== undefined && after !== undefined;
  useEffect(() => {
    const abort = new AbortController();
    setFile(null);
    setError(false);
    void fetch(`/api/society/memory/file?path=${encodeURIComponent(path)}`, { signal: abort.signal })
      .then(async response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<MemoryFileResponse>; })
      .then(data => { if (!abort.signal.aborted) setFile(data); })
      .catch(() => { if (!abort.signal.aborted) setError(true); });
    return () => abort.abort();
  }, [path]);
  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/60 backdrop-blur-sm" />
      <Dialog.Content onCloseAutoFocus={event => { event.preventDefault(); opener.current?.focus(); }} className="fixed left-1/2 top-1/2 z-50 flex h-[min(82dvh,780px)] w-[min(800px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-popover text-foreground shadow-float outline-none">
        <header className="flex shrink-0 items-center gap-3 border-b border-border p-4">
          <FileText size={18} aria-hidden className="shrink-0" />
          <div className="min-w-0 flex-1"><Dialog.Title className="truncate text-sm font-semibold">{path.split("/").at(-1)}</Dialog.Title><Dialog.Description className="mt-1 truncate font-mono text-xs text-muted-foreground">{path}</Dialog.Description></div>
          <Dialog.Close asChild><button type="button" aria-label={t("society.world.drawer_close")} className="rounded-md p-2 hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring"><X size={16} aria-hidden /></button></Dialog.Close>
        </header>
        <Tabs defaultValue={hasChange ? "changes" : "file"} className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-4 mt-3 w-fit shrink-0">
            {hasChange && <TabsTrigger value="changes">{t("society.chat.memory_changes")}</TabsTrigger>}
            <TabsTrigger value="file">{t("society.chat.memory_current")}</TabsTrigger>
            <TabsTrigger value="raw">{t("society.chat.memory_raw")}</TabsTrigger>
          </TabsList>
          {hasChange && <TabsContent value="changes" className="min-h-0 flex-1 overflow-auto p-4">
            <p className="mb-4 text-xs text-muted-foreground">{t("society.chat.memory_change_legend")}</p>
            <MemoryChanges before={before!} after={after!} patch={patch} />
          </TabsContent>}
          <TabsContent value="file" className="min-h-0 flex-1 overflow-auto p-5">
            {error ? <p role="alert" className="text-sm text-destructive">{t("society.chat.memory_file_failed")}</p> : file ? <MemoryMarkdown text={file.content} /> : <p role="status" className="text-sm text-muted-foreground">{t("society.chat.memory_file_loading")}</p>}
          </TabsContent>
          <TabsContent value="raw" className="min-h-0 flex-1 overflow-auto p-5">
            {error ? <p role="alert" className="text-sm text-destructive">{t("society.chat.memory_file_failed")}</p> : file ? <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-6">{file.content}</pre> : <p role="status" className="text-sm text-muted-foreground">{t("society.chat.memory_file_loading")}</p>}
          </TabsContent>
        </Tabs>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

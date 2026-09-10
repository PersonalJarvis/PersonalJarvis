import { lazy, Suspense, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { FolderOpen, X } from "lucide-react";
import { useT } from "@/i18n";
import { AgentMark } from "@/components/agentic/AgentMark";
import type { AgentStatus } from "@/lib/agenticIdeApi";

const FolderPicker = lazy(() => import("@/components/agentic/FolderPicker").then((module) => ({ default: module.FolderPicker })));

export function CodingProjectChoice({ agents, folder, onFolder }: {
  agents: AgentStatus[];
  folder: string;
  onFolder: (path: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [candidate, setCandidate] = useState<string | null>(null);
  return (
    <div className="mt-2 rounded-lg border border-border bg-secondary/40 p-2" data-testid="coding-project-choice">
      <div role="status" aria-live="polite" className="mb-2 flex flex-wrap items-center gap-2 text-xs text-foreground">
        <span className="text-muted-foreground">{t("society.chat.coding_selected")}</span>
        {agents.map((agent) => <span key={agent.name} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2 py-1 font-medium">
          <AgentMark agent={agent.name} label={agent.display_name} logoUrl={agent.logo_url || undefined} size="sm" variant="plain" />
          {agent.display_name}
        </span>)}
      </div>
      <div className="flex items-center gap-2">
        <input
          aria-label={t("society.chat.coding_project")}
          placeholder={t("society.chat.coding_project_hint")}
          value={folder}
          onChange={(event) => onFolder(event.target.value)}
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground"
        />
        <button type="button" onClick={() => { setCandidate(folder || null); setOpen(true); }}
          className="flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs hover:bg-secondary">
          <FolderOpen className="h-3.5 w-3.5" aria-hidden />{t("society.chat.coding_choose_folder")}
        </button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("society.chat.coding_project_optional")}</p>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[90] bg-[rgb(var(--scrim-rgb)/0.4)]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[91] flex max-h-[85vh] w-[min(680px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-popover p-4 text-foreground shadow-float">
            <Dialog.Title className="pr-8 text-sm font-semibold">{t("society.chat.coding_choose_folder")}</Dialog.Title>
            <Dialog.Description className="mt-1 text-xs text-muted-foreground">{t("society.chat.coding_project_optional")}</Dialog.Description>
            <Dialog.Close aria-label={t("society.chat.coding_close")} className="absolute right-3 top-3 rounded p-1 hover:bg-secondary"><X className="h-4 w-4" /></Dialog.Close>
            <div className="my-3 min-h-0 overflow-auto">
              <Suspense fallback={<p className="text-xs text-muted-foreground">{t("society.chat.mention_loading")}</p>}>
                {open && <FolderPicker selected={candidate} onSelect={setCandidate} />}
              </Suspense>
            </div>
            <button type="button" disabled={!candidate} onClick={() => { if (candidate) onFolder(candidate); setOpen(false); }}
              className="self-end rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-40">
              {t("society.chat.coding_use_folder")}
            </button>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

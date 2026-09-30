/**
 * A new workspace, chosen at the spawn point: which folder, and what the tab
 * is called. Only the CHOICE is made here. The workspace itself is opened by
 * the launch, with the new agents already in it, so a spawn point closed
 * halfway leaves no empty workspace and no half-registered project behind.
 */
import { lazy, Suspense, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useT } from "@/i18n";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { BrandedSelect } from "@/components/ui/select";

const FolderPicker = lazy(() => import("@/components/agentic/FolderPicker").then((module) => ({ default: module.FolderPicker })));

/** Where the new workspace opens: a connected project, or a folder that is not one yet. */
export interface NewWorkspaceTarget {
  path: string;
  label: string;
  projectId?: string;
}

const PICK = "__pick__";

const samePath = (a: string, b: string) =>
  a.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() === b.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

/** A picked folder as a target: the connected project it already is, if any. Pure. */
export function folderTarget(path: string, projects: readonly IdeProject[]): NewWorkspaceTarget {
  const project = projects.find((p) => samePath(p.path, path));
  if (project) return { path: project.path, label: project.name, projectId: project.id };
  return { path, label: path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path };
}

export function NewWorkspaceFields({ projects, target, onTarget, name, onName, disabled }: {
  projects: readonly IdeProject[];
  target: NewWorkspaceTarget | null;
  onTarget: (target: NewWorkspaceTarget) => void;
  name: string;
  onName: (name: string) => void;
  disabled: boolean;
}) {
  const t = useT();
  const [picking, setPicking] = useState(false);
  const [candidate, setCandidate] = useState<string | null>(null);
  const value = target ? target.projectId ?? `folder:${target.path}` : "";

  return (
    <>
      <div className="office-mc-row" title={target?.path}>
        <span>{t("society.office.mission_ws_folder")}</span>
        <BrandedSelect value={value} disabled={disabled} ariaLabel={t("society.office.mission_ws_folder")}
          placeholder={t("society.office.mission_ws_pick")} className="office-mc-select px-2 py-1.5 text-xs"
          options={[
            ...projects.map((p) => ({ value: p.id, label: p.name, searchText: p.path })),
            ...(target && !target.projectId ? [{ value, label: target.label, searchText: target.path }] : []),
            { value: PICK, label: t("society.office.mission_ws_choose") },
          ]}
          onValueChange={(next) => {
            // Picking "choose a folder" leaves the select on what it showed
            // before, so closing the window without a choice changes nothing.
            if (next === PICK) { setCandidate(target?.path ?? null); setPicking(true); return; }
            const project = projects.find((p) => p.id === next);
            if (project) onTarget({ path: project.path, label: project.name, projectId: project.id });
          }} />
      </div>
      <label className="office-mc-row">
        <span>{t("society.office.mission_ws_name")}</span>
        <input className="office-mc-input" value={name} maxLength={80} disabled={disabled}
          placeholder={target?.label ?? ""} onChange={(e) => onName(e.target.value)} />
      </label>

      <Dialog.Root open={picking} onOpenChange={setPicking}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[90] bg-[rgb(var(--scrim-rgb)/0.4)]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[91] flex max-h-[85vh] w-[min(680px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-popover p-4 text-foreground shadow-float">
            <Dialog.Title className="pr-8 text-sm font-semibold">{t("society.office.mission_ws_choose_title")}</Dialog.Title>
            <Dialog.Description className="mt-1 text-xs text-muted-foreground">{t("society.office.mission_ws_choose_hint")}</Dialog.Description>
            <Dialog.Close aria-label={t("society.office.mission_ws_close")} className="absolute right-3 top-3 rounded p-1 hover:bg-secondary"><X className="h-4 w-4" /></Dialog.Close>
            <div className="my-3 min-h-0 overflow-auto">
              <Suspense fallback={<p className="text-xs text-muted-foreground">{t("society.office.mission_loading")}</p>}>
                {picking && <FolderPicker selected={candidate} onSelect={setCandidate} />}
              </Suspense>
            </div>
            <button type="button" disabled={!candidate}
              onClick={() => { if (candidate) onTarget(folderTarget(candidate, projects)); setPicking(false); }}
              className="self-end rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-40">
              {t("society.office.mission_ws_use")}
            </button>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}

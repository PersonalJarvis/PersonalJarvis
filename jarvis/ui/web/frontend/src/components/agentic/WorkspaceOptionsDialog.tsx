import * as Dialog from "@radix-ui/react-dialog";
import { Columns2, GitBranch, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { PaneStyle } from "./terminalThemes";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  count: number;
  busy: boolean;
  canAdd: boolean;
  onAdd: () => void;
  onBalance: () => void;
  onRename: () => void;
  onClose: () => void;
  /** Opens the workspace's Git panel. */
  onGit: () => void;
  appearance: "light" | "dark" | null;
  onAppearance: (appearance: "light" | "dark" | null) => void;
  /** Square tiles with a slim title row, or rounded cards. */
  paneStyle?: PaneStyle;
  onPaneStyle?: (style: PaneStyle) => void;
}

const PANE_STYLES: { id: PaneStyle; labelKey: string; hintKey: string }[] = [
  { id: "minimal", labelKey: "ide_panes.options.style_minimal", hintKey: "ide_panes.options.style_minimal_hint" },
  { id: "classic", labelKey: "ide_panes.options.style_classic", hintKey: "ide_panes.options.style_classic_hint" },
];

/** Workspace controls live off-canvas so the terminal area needs no toolbar. */
export function WorkspaceOptionsDialog(props: Props) {
  const t = useT();
  const icon = "flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35";
  const choose = (action: () => void) => { props.onOpenChange(false); action(); };
  return <Dialog.Root open={props.open} onOpenChange={props.onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm" />
      <Dialog.Content className="fixed left-1/2 top-1/2 z-[90] w-[min(420px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 space-y-5 rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0"><Dialog.Title className="text-lg font-semibold">{t("ide_panes.options.title")}</Dialog.Title>
            <Dialog.Description className="mt-1 truncate text-sm text-muted-foreground">{props.workspace}</Dialog.Description></div>
          <Dialog.Close aria-label={t("ide_panes.options.close")} className={icon}><X className="h-4 w-4" /></Dialog.Close>
        </div>
        <section aria-label={t("ide_panes.options.arrangement")} className="space-y-2">
          <button type="button" disabled={props.busy || props.count < 2} onClick={() => choose(props.onBalance)}
            className="flex w-full items-center gap-2 rounded-lg border border-border px-3 py-2.5 text-sm hover:bg-muted disabled:opacity-40"><Columns2 className="h-4 w-4" />{t("ide_panes.options.balance")}</button>
          <p className="text-xs leading-relaxed text-muted-foreground">{t("ide_panes.options.arrangement_hint")}</p>
        </section>
        {props.onPaneStyle && <section aria-label={t("ide_panes.options.style")}>
          <p className="mb-2 text-sm font-medium">{t("ide_panes.options.style")}</p>
          <div className="flex gap-2">
            {PANE_STYLES.map((style) => <button type="button" key={style.id} aria-pressed={props.paneStyle === style.id}
              onClick={() => props.onPaneStyle?.(style.id)} className={cn("flex flex-1 flex-col items-start gap-0.5 rounded-lg border px-3 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", props.paneStyle === style.id ? "border-foreground/35 bg-muted" : "border-border text-muted-foreground hover:bg-muted")}>
              <span className="font-medium">{t(style.labelKey)}</span>
              <span className="text-xs text-muted-foreground">{t(style.hintKey)}</span>
            </button>)}
          </div>
        </section>}
        <section aria-label={t("ide_panes.options.appearance")}>
          <p className="mb-2 text-sm font-medium">{t("ide_panes.options.appearance")}</p>
          <div className="flex gap-2">
            {([null, "dark", "light"] as const).map((appearance) => <button type="button" key={appearance ?? "auto"}
              aria-label={t(appearance ? `ide_panes.options.appearance_${appearance}_aria` : "ide_panes.options.appearance_auto_aria")} aria-pressed={props.appearance === appearance}
              onClick={() => props.onAppearance(appearance)} className={cn("flex-1 rounded-lg border px-2 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", props.appearance === appearance ? "border-foreground/35 bg-muted" : "border-border text-muted-foreground hover:bg-muted")}>
              {appearance === null ? t("ide_panes.options.appearance_auto") : appearance === "dark" ? t("ide_panes.options.appearance_dark") : t("ide_panes.options.appearance_light")}</button>)}
          </div>
        </section>
        <div className="border-t border-border pt-2">
          <button type="button" disabled={props.busy || !props.canAdd} onClick={() => choose(props.onAdd)} className="block w-full rounded-lg px-2 py-2.5 text-left text-sm hover:bg-muted disabled:opacity-40">{t("ide_panes.options.add_agent")}</button>
          <button type="button" disabled={props.busy} onClick={() => choose(props.onGit)} className="flex w-full items-center gap-2 rounded-lg px-2 py-2.5 text-left text-sm hover:bg-muted disabled:opacity-40"><GitBranch className="h-4 w-4 text-muted-foreground" />{t("ide_panes.options.git")}</button>
          <button type="button" disabled={props.busy} onClick={() => choose(props.onRename)} className="block w-full rounded-lg px-2 py-2.5 text-left text-sm hover:bg-muted disabled:opacity-40">{t("ide_hotkeys.help.rename_workspace")}</button>
          <button type="button" disabled={props.busy} onClick={() => choose(props.onClose)} className="block w-full rounded-lg px-2 py-2.5 text-left text-sm text-destructive hover:bg-muted disabled:opacity-40">{t("agentic_grid.close_workspace.confirm")}</button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

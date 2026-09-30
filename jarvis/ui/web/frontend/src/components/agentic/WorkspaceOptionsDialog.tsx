import * as Dialog from "@radix-ui/react-dialog";
import { Columns2, GitBranch, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PaneStyle } from "./terminalThemes";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  count: number;
  /** The server's per-workspace pane limit (`max_terminals`). */
  maxPanes: number;
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

const PANE_STYLES: { id: PaneStyle; label: string; hint: string }[] = [
  { id: "minimal", label: "Minimal", hint: "Square frames, slim title row" },
  { id: "classic", label: "Classic", hint: "Rounded cards, taller title bar" },
];

/** Workspace controls live off-canvas so the terminal area needs no toolbar. */
export function WorkspaceOptionsDialog(props: Props) {
  const icon = "flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35";
  const choose = (action: () => void) => { props.onOpenChange(false); action(); };
  return <Dialog.Root open={props.open} onOpenChange={props.onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm" />
      <Dialog.Content className="fixed left-1/2 top-1/2 z-[90] w-[min(420px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 space-y-5 rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0"><Dialog.Title className="text-lg font-semibold">Workspace options</Dialog.Title>
            <Dialog.Description className="mt-1 truncate text-sm text-muted-foreground">{props.workspace}</Dialog.Description></div>
          <Dialog.Close aria-label="Close workspace options" className={icon}><X className="h-4 w-4" /></Dialog.Close>
        </div>
        <section aria-label="Terminal arrangement" className="space-y-2">
          <button type="button" disabled={props.busy || props.count < 2} onClick={() => choose(props.onBalance)}
            className="flex w-full items-center gap-2 rounded-lg border border-border px-3 py-2.5 text-sm hover:bg-muted disabled:opacity-40"><Columns2 className="h-4 w-4" />Balance layout</button>
          <p className="text-xs leading-relaxed text-muted-foreground">Drag a terminal title to an edge to place it beside, above or below another terminal. Drop in the center to swap positions.</p>
        </section>
        {props.onPaneStyle && <section aria-label="Terminal style">
          <p className="mb-2 text-sm font-medium">Terminal style</p>
          <div className="flex gap-2">
            {PANE_STYLES.map((style) => <button type="button" key={style.id} aria-pressed={props.paneStyle === style.id}
              onClick={() => props.onPaneStyle?.(style.id)} className={cn("flex flex-1 flex-col items-start gap-0.5 rounded-lg border px-3 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", props.paneStyle === style.id ? "border-foreground/35 bg-muted" : "border-border text-muted-foreground hover:bg-muted")}>
              <span className="font-medium">{style.label}</span>
              <span className="text-xs text-muted-foreground">{style.hint}</span>
            </button>)}
          </div>
        </section>}
        <section aria-label="Terminal appearance">
          <p className="mb-2 text-sm font-medium">Terminal appearance</p>
          <div className="flex gap-2">
            {([null, "dark", "light"] as const).map((appearance) => <button type="button" key={appearance ?? "auto"}
              aria-label={appearance ? `${appearance} terminals` : "Match app appearance"} aria-pressed={props.appearance === appearance}
              onClick={() => props.onAppearance(appearance)} className={cn("flex-1 rounded-lg border px-2 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", props.appearance === appearance ? "border-foreground/35 bg-muted" : "border-border text-muted-foreground hover:bg-muted")}>
              {appearance === null ? "Match app" : appearance === "dark" ? "Dark" : "Light"}</button>)}
          </div>
        </section>
        <div className="border-t border-border pt-2">
          <button type="button" disabled={props.busy || !props.canAdd || props.count >= props.maxPanes} onClick={() => choose(props.onAdd)} className="block w-full rounded-lg px-2 py-2.5 text-left text-sm hover:bg-muted disabled:opacity-40">Add coding agent</button>
          <button type="button" disabled={props.busy} onClick={() => choose(props.onGit)} className="flex w-full items-center gap-2 rounded-lg px-2 py-2.5 text-left text-sm hover:bg-muted disabled:opacity-40"><GitBranch className="h-4 w-4 text-muted-foreground" />Git: branches, commits, worktrees</button>
          <button type="button" disabled={props.busy} onClick={() => choose(props.onRename)} className="block w-full rounded-lg px-2 py-2.5 text-left text-sm hover:bg-muted disabled:opacity-40">Rename workspace</button>
          <button type="button" disabled={props.busy} onClick={() => choose(props.onClose)} className="block w-full rounded-lg px-2 py-2.5 text-left text-sm text-destructive hover:bg-muted disabled:opacity-40">Close workspace</button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

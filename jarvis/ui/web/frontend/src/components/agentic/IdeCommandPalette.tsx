/**
 * The Agentic IDE's command palette: every IDE action in one searchable list,
 * each row showing the keys that reach it without the palette.
 *
 * Opened from the search field in the window caption or its chord (Ctrl+Shift+P,
 * ⌘⇧P on a Mac, rebindable under Settings → Keyboard shortcuts). The rows come
 * from `buildIdeCommands`; picking one closes the palette first, so a command
 * that opens a dialog or acts on the selected pane gets the keyboard back.
 *
 * Radix Dialog owns Escape, the focus trap and handing focus back to the pane
 * the user came from.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import {
  ArrowRightLeft, Building2, Columns2, CopyPlus, CornerDownLeft, FolderPlus, GitBranch, GitFork, LayoutGrid,
  Maximize2, MessagesSquare, Mic, PanelRight, Pencil, Plus, Rows2, Search, Settings2, SquareTerminal, X,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { AgentMark } from "./AgentMark";
import {
  IDE_COMMAND_GROUPS, IDE_COMMAND_GROUP_LABEL, ideCommandMatches,
  type IdeCommand, type IdeCommandRun,
} from "./ideCommands";

const ROW = cn(
  "group flex h-10 cursor-default select-none items-center gap-3 rounded-lg px-2.5 text-sm",
  "text-foreground outline-none",
  "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
);

const TILE = cn(
  "grid h-6 w-6 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground",
  "group-data-[selected=true]:bg-white/20 group-data-[selected=true]:text-accent-foreground",
);

const GROUP = cn(
  "[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2.5",
  "[&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium",
  "[&_[cmdk-group-heading]]:text-muted-foreground",
);

const CAP = cn(
  "inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-border bg-muted px-1",
  "font-mono text-[11px] font-medium leading-none text-foreground",
  "group-data-[selected=true]:border-white/30 group-data-[selected=true]:bg-white/15 group-data-[selected=true]:text-accent-foreground",
);

/** The glyph a row wears, by what it does. */
function iconFor(command: IdeCommand): LucideIcon {
  const run = command.run;
  if (run.type === "face") return run.face === "grid" ? LayoutGrid : run.face === "threads" ? MessagesSquare : Building2;
  if (run.type === "panel-tab" || run.type === "panel-toggle") return PanelRight;
  switch (run.action.kind) {
    case "agent-picker": return Plus;
    case "split": return run.action.direction === "right" ? Columns2 : Rows2;
    case "maximize-pane": return Maximize2;
    case "balance": return LayoutGrid;
    case "fork-pane": return GitFork;
    case "close-pane":
    case "close-workspace": return X;
    case "new-workspace": return CopyPlus;
    case "new-worktree-workspace":
    case "git-panel": return GitBranch;
    case "workspace-options": return Settings2;
    case "rename-workspace": return Pencil;
    case "connect-project": return FolderPlus;
    case "workspace-step":
    case "workspace-index": return ArrowRightLeft;
    case "toggle-voice": return Mic;
    default: return SquareTerminal;
  }
}

/** "Ctrl B · W · N": one cap group per key press, a dot between presses. */
function KeySequence({ keys }: { keys: string[][] }) {
  if (keys.length === 0) return null;
  return <span className="ml-auto inline-flex shrink-0 items-center gap-1.5 pl-3" aria-hidden>
    {keys.map((press, index) => <span key={index} className="inline-flex items-center gap-1">
      {index > 0 && <span className="text-[10px] text-muted-foreground group-data-[selected=true]:text-accent-foreground/70">·</span>}
      {press.map((key) => <kbd key={key} className={CAP}>{key}</kbd>)}
    </span>)}
  </span>;
}

/** The keys as one sentence for a screen reader, which skips the caps. */
function spokenKeys(keys: string[][]): string {
  return keys.map((press) => press.join("+")).join(", then ");
}

export function IdeCommandPalette({ open, onOpenChange, commands, onRun }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: readonly IdeCommand[];
  onRun: (run: IdeCommandRun) => void;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const shown = useMemo(() => commands.filter((command) => ideCommandMatches(command, query)), [commands, query]);

  const close = () => {
    onOpenChange(false);
    setQuery("");
  };
  const pick = (command: IdeCommand) => {
    close();
    // After the dialog has handed focus back, so a pane command finds its pane
    // and a dialog the command opens does not fight this one for focus.
    window.setTimeout(() => onRun(command.run), 0);
  };

  return <Dialog.Root open={open} onOpenChange={(next) => { if (!next) setQuery(""); onOpenChange(next); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80]" />
      <Dialog.Content
        data-testid="ide-command-palette"
        aria-describedby={undefined}
        className={cn(
          "fixed left-1/2 top-12 z-[90] w-[min(640px,calc(100vw-2rem))] -translate-x-1/2",
          "overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-float",
          "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-top-1",
          "motion-reduce:animate-none",
        )}
      >
        <Dialog.Title className="sr-only">IDE commands</Dialog.Title>
        <Command shouldFilter={false} loop label="IDE commands" value={selected} onValueChange={setSelected}>
          <div className="flex items-center gap-2.5 border-b border-border px-3.5">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <Command.Input
              autoFocus
              value={query}
              onValueChange={(value) => { setQuery(value); setSelected(""); }}
              placeholder="Type a command — split, git, new workspace, Claude …"
              data-testid="ide-command-palette-input"
              className="h-12 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
          </div>
          <Command.List className="max-h-[min(28rem,60dvh)] overflow-y-auto p-1.5 scrollbar-jarvis">
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">No command matches.</Command.Empty>
            {IDE_COMMAND_GROUPS.map((group) => {
              const rows = shown.filter((command) => command.group === group);
              if (rows.length === 0) return null;
              return <Command.Group key={group} heading={IDE_COMMAND_GROUP_LABEL[group]} className={GROUP}>
                {rows.map((command) => {
                  const Icon = iconFor(command);
                  return <Command.Item key={command.id} value={command.id} onSelect={() => pick(command)}
                    data-testid={`ide-command-${command.id}`} className={ROW}
                    aria-label={command.keys.length ? `${command.label} (${spokenKeys(command.keys)})` : command.label}>
                    {command.agent
                      ? <AgentMark agent={command.agent} label={command.label} size="sm" variant="plain" />
                      : <span className={TILE}><Icon className="h-3.5 w-3.5" aria-hidden /></span>}
                    <span className="min-w-0 truncate">{command.label}</span>
                    <KeySequence keys={command.keys} />
                  </Command.Item>;
                })}
              </Command.Group>;
            })}
          </Command.List>
          <div className="flex items-center justify-end gap-4 border-t border-border px-3.5 py-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans"><CornerDownLeft className="h-3 w-3" aria-hidden /></kbd>
              run
            </span>
            <span className="inline-flex items-center gap-1.5">
              <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">esc</kbd>
              close
            </span>
          </div>
        </Command>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

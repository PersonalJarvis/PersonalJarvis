import { useCallback, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { Plus, SquareTerminal, X } from "lucide-react";
import { WorkspaceTerminal } from "@/components/workspace/WorkspaceTerminal";
import { cn } from "@/lib/utils";

/** One shell in the drawer: plain, or started to run a project action. */
export interface DrawerTerminal {
  id: string;
  title: string;
  folder: string;
  /** Typed once when the shell is ready (an action's command plus Enter). */
  input?: string;
}

const MIN_HEIGHT = 140;

/**
 * A terminal drawer under the conversation: shells in the thread's own
 * folder, one tab each. Project actions open here in a tab of their own. The
 * top edge drags to resize; the height is the caller's to keep.
 *
 * Every shell stays mounted while the person moves between threads — a dev
 * server keeps running — but only the open thread's folder has its tabs shown.
 */
export function ThreadTerminalDrawer({
  terminals,
  folder,
  open,
  active,
  height,
  onSelect,
  onAdd,
  onCloseTab,
  onClose,
  onResize,
}: {
  terminals: DrawerTerminal[];
  /** The open thread's folder: only its shells are shown. */
  folder: string;
  open: boolean;
  active: string;
  height: number;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onCloseTab: (id: string) => void;
  onClose: () => void;
  onResize: (height: number) => void;
}) {
  const drag = useRef<{ startY: number; startHeight: number } | null>(null);
  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    drag.current = { startY: event.clientY, startHeight: height };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [height]);
  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const next = drag.current.startHeight + (drag.current.startY - event.clientY);
    onResize(Math.max(MIN_HEIGHT, Math.min(next, Math.round(window.innerHeight * 0.75))));
  }, [onResize]);
  const onPointerUp = useCallback(() => { drag.current = null; }, []);
  const shown = terminals.filter((terminal) => terminal.folder === folder);
  const current = shown.some((terminal) => terminal.id === active) ? active : shown[shown.length - 1]?.id ?? "";

  return <section aria-label="Terminal" data-testid="thread-terminal-drawer" style={{ height }}
    className={cn("relative shrink-0 flex-col border-t border-border bg-background", open && shown.length > 0 ? "flex" : "hidden")}>
    <div role="separator" aria-orientation="horizontal" aria-label="Resize the terminal"
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      className="absolute inset-x-0 -top-1 z-10 h-2 cursor-row-resize" />
    <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
      <div role="tablist" aria-label="Terminals" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto scrollbar-none">
        {shown.map((terminal) => <div key={terminal.id} role="tab" aria-selected={terminal.id === current}
          className={cn("group/tab flex h-7 shrink-0 items-center gap-1.5 rounded-md pl-2 pr-1 text-xs transition-colors",
            terminal.id === current ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground")}>
          <button type="button" onClick={() => onSelect(terminal.id)} className="flex items-center gap-1.5 focus-visible:outline-none">
            <SquareTerminal aria-hidden className="h-3.5 w-3.5" /><span className="max-w-[160px] truncate">{terminal.title}</span>
          </button>
          <button type="button" aria-label={`Close ${terminal.title}`} onClick={() => onCloseTab(terminal.id)}
            className="rounded p-0.5 opacity-60 hover:bg-background hover:opacity-100"><X className="h-3 w-3" /></button>
        </div>)}
        <button type="button" aria-label="New terminal" title="New terminal" onClick={onAdd}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"><Plus className="h-3.5 w-3.5" /></button>
      </div>
      <button type="button" aria-label="Hide terminal" onClick={onClose}
        className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"><X className="h-4 w-4" /></button>
    </div>
    <div className="relative min-h-0 flex-1">
      {terminals.map((terminal) => <div key={terminal.id} hidden={terminal.id !== current} className="absolute inset-0 p-1">
        <WorkspaceTerminal paneKey={terminal.id} agentName="shell" folder={terminal.folder} initialInput={terminal.input}
          title={terminal.title} bare active={open && terminal.id === current} />
      </div>)}
    </div>
  </section>;
}

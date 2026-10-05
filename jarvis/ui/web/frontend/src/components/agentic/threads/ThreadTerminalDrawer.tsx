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
 */
export function ThreadTerminalDrawer({
  terminals,
  active,
  height,
  onSelect,
  onAdd,
  onCloseTab,
  onClose,
  onResize,
}: {
  terminals: DrawerTerminal[];
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

  return <section aria-label="Terminal" data-testid="thread-terminal-drawer" style={{ height }}
    className="relative flex shrink-0 flex-col border-t border-border bg-background">
    <div role="separator" aria-orientation="horizontal" aria-label="Resize the terminal"
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      className="absolute inset-x-0 -top-1 z-10 h-2 cursor-row-resize" />
    <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
      <div role="tablist" aria-label="Terminals" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto scrollbar-none">
        {terminals.map((terminal) => <div key={terminal.id} role="tab" aria-selected={terminal.id === active}
          className={cn("group/tab flex h-7 shrink-0 items-center gap-1.5 rounded-md pl-2 pr-1 text-xs transition-colors",
            terminal.id === active ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground")}>
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
      {terminals.map((terminal) => <div key={terminal.id} hidden={terminal.id !== active} className="absolute inset-0 p-1">
        <WorkspaceTerminal paneKey={terminal.id} agentName="shell" folder={terminal.folder} initialInput={terminal.input}
          title={terminal.title} bare active={terminal.id === active} />
      </div>)}
    </div>
  </section>;
}

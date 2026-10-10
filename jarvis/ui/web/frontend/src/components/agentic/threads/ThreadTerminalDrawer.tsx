import { useCallback, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { Plus, SquareTerminal, X } from "lucide-react";
import { WorkspaceTerminal } from "@/components/workspace/WorkspaceTerminal";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";
import { MAX_FOLDER_SHELLS, MIN_DRAWER_HEIGHT, drawerShown, shellsIn, useThreadTerminalsStore } from "@/store/threadTerminals";

/**
 * The thread layout's terminal drawer: plain shells in the open thread's
 * folder under the conversation, one tab each. The window caption's terminal
 * toggle pulls it up and down; the top edge drags to resize.
 *
 * Every shell stays mounted while the person moves between threads and back to
 * the grid — a dev server keeps running — but only the open thread's folder
 * has its tabs shown. Closing a tab ends that shell.
 */
export function ThreadTerminalDrawer({ onScreen }: { onScreen: boolean }) {
  const t = useT();
  const shells = useThreadTerminalsStore((state) => state.shells);
  const folder = useThreadTerminalsStore((state) => state.folder);
  const activeByFolder = useThreadTerminalsStore((state) => state.active);
  const height = useThreadTerminalsStore((state) => state.height);
  const shown = useThreadTerminalsStore(drawerShown);
  const addShell = useThreadTerminalsStore((state) => state.addShell);
  const closeShell = useThreadTerminalsStore((state) => state.closeShell);
  const select = useThreadTerminalsStore((state) => state.select);
  const setHeight = useThreadTerminalsStore((state) => state.setHeight);
  const drag = useRef<{ startY: number; startHeight: number } | null>(null);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { startY: event.clientY, startHeight: height };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [height]);
  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const next = drag.current.startHeight + (drag.current.startY - event.clientY);
    setHeight(Math.max(MIN_DRAWER_HEIGHT, Math.min(next, Math.round(window.innerHeight * 0.75))));
  }, [setHeight]);
  const onPointerUp = useCallback(() => { drag.current = null; }, []);

  if (shells.length === 0) return null;
  const here = shellsIn(shells, folder);
  const current = here.some((shell) => shell.id === activeByFolder[folder]) ? activeByFolder[folder] : here[here.length - 1]?.id ?? "";
  // Never taller than most of the window, even when it shrank since the drag.
  const drawerHeight = `min(${height}px, 75vh)`;

  return <section aria-label={t("ide_threads.terminal")} data-testid="thread-terminal-drawer" style={{ height: drawerHeight }}
    className={cn("relative shrink-0 flex-col border-t border-border bg-background", shown ? "flex" : "hidden")}>
    <div role="separator" aria-orientation="horizontal" aria-label={t("ide_threads.resize_terminal")}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      className="absolute inset-x-0 -top-1 z-10 h-2 cursor-row-resize" />
    <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
      <div role="tablist" aria-label={t("ide_threads.terminals")} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
        {here.map((shell) => {
          const title = fill(t("ide_threads.terminal_n"), { number: shell.number });
          return <div key={shell.id} role="tab" aria-selected={shell.id === current}
            className={cn("group/tab flex h-7 shrink-0 items-center gap-1.5 rounded-md pl-2 pr-1 text-xs transition-colors",
              shell.id === current ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground")}>
            <button type="button" onClick={() => select(shell.id)} className="flex items-center gap-1.5 focus-visible:outline-none">
              <SquareTerminal aria-hidden className="h-3.5 w-3.5" /><span className="max-w-[160px] truncate">{title}</span>
            </button>
            <button type="button" aria-label={fill(t("ide_threads.close_named"), { target: title })} title={t("ide_threads.close_terminal")} onClick={() => closeShell(shell.id)}
              data-testid={`thread-terminal-close-${shell.number}`}
              className="rounded p-0.5 opacity-60 hover:bg-background hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><X className="h-3 w-3" /></button>
          </div>;
        })}
        <button type="button" aria-label={t("ide_threads.new_terminal")} title={t("ide_threads.new_terminal")} onClick={addShell}
          disabled={here.length >= MAX_FOLDER_SHELLS} data-testid="thread-terminal-new"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"><Plus className="h-3.5 w-3.5" /></button>
      </div>
    </div>
    <div className="relative min-h-0 flex-1">
      {shells.map((shell) => <div key={shell.id} hidden={!shown || shell.id !== current} className="absolute inset-0 p-1">
        <WorkspaceTerminal paneKey={shell.id} agentName="shell" folder={shell.folder}
          title={fill(t("ide_threads.terminal_n"), { number: shell.number })} bare active={onScreen && shown && shell.id === current} />
      </div>)}
    </div>
  </section>;
}

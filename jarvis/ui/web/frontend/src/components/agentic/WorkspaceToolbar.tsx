import { useEffect, useRef, useState } from "react";
import { Mic, MoreHorizontal, Plus, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  project?: string;
  workspace?: string;
  folder?: string;
  count: number;
  busy: boolean;
  canAdd: boolean;
  onAdd: () => void;
  onRename: () => void;
  onClose: () => void;
  fontSize: number;
  onFontSize: (size: number) => void;
  appearance: "light" | "dark" | null;
  onAppearance: (appearance: "light" | "dark" | null) => void;
  columns: number;
  onColumns: (columns: number) => void;
  voiceOpen: boolean;
  onVoice: () => void;
}

/** Keep context short; display preferences live behind one explicit control. */
export function WorkspaceToolbar(props: Props) {
  const [open, setOpen] = useState(false);
  // Keep the user's preference, but mark the column count this workspace can
  // actually render. Smaller groups must not advertise empty columns.
  const selectedColumns = props.columns === 0 ? 0 : Math.max(Math.ceil(props.count / 2), Math.min(props.count, 4, props.columns));
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  useEffect(() => { setOpen(false); }, [props.workspace, props.folder]);
  const icon = "flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35";
  const option = (active: boolean) => cn("rounded-md border px-2.5 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-30", active ? "border-foreground/35 bg-muted text-foreground" : "border-border text-muted-foreground hover:bg-muted");

  return <header className="flex h-10 shrink-0 items-center justify-between gap-3 px-3" data-testid="workspace-toolbar">
    <div className="flex min-w-0 items-center gap-2 text-sm" title={props.folder}>
      {props.project && props.workspace && props.project !== props.workspace && <>
        <span className="truncate text-muted-foreground">{props.project}</span><span className="text-muted-foreground/50">/</span>
      </>}
      <span className="truncate font-medium">{props.workspace ?? props.project ?? "Projects"}</span>
    </div>
    <div className="flex shrink-0 items-center gap-1">
      {props.workspace && <>
        <button type="button" aria-label="Add coding agent" title="Add coding agent" className={icon}
          disabled={props.busy || !props.canAdd || props.count >= 8} onClick={props.onAdd}><Plus className="h-4 w-4" /></button>
        <div ref={root} className="relative">
          <button ref={trigger} type="button" aria-label="Workspace options" title="Workspace options" aria-expanded={open} aria-haspopup="dialog"
            className={icon} onClick={() => setOpen((value) => !value)}><MoreHorizontal className="h-4 w-4" /></button>
          {open && <div ref={panel} tabIndex={-1} role="dialog" aria-label="Workspace options"
            className="absolute right-0 top-full z-40 mt-1 w-64 space-y-4 rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-xl outline-none">
            <section aria-label="Grid columns">
              <p className="mb-2 text-xs font-medium text-muted-foreground">Columns</p>
              <div className="flex gap-1">
                {[0, 1, 2, 3, 4].map((count) => <button type="button" key={count} aria-label={count ? `${count} columns` : "Automatic columns"}
                  aria-pressed={selectedColumns === count} disabled={count > 0 && (count * 2 < props.count || count > props.count)}
                  onClick={() => props.onColumns(count)} className={option(selectedColumns === count)}>{count || "Auto"}</button>)}
              </div>
            </section>
            <section aria-label="Terminal text size" className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-muted-foreground">Text size</span>
              <div className="flex items-center gap-2">
                <button type="button" aria-label="Decrease terminal text size" disabled={props.fontSize <= 9}
                  onClick={() => props.onFontSize(props.fontSize - 1)} className={icon}><Minus className="h-3.5 w-3.5" /></button>
                <span className="w-5 text-center text-xs tabular-nums">{props.fontSize}</span>
                <button type="button" aria-label="Increase terminal text size" disabled={props.fontSize >= 22}
                  onClick={() => props.onFontSize(props.fontSize + 1)} className={icon}><Plus className="h-3.5 w-3.5" /></button>
              </div>
            </section>
            <section aria-label="Terminal appearance">
              <p className="mb-2 text-xs font-medium text-muted-foreground">Terminal appearance</p>
              <div className="flex gap-1">
                {([null, "dark", "light"] as const).map((appearance) => <button type="button" key={appearance ?? "auto"}
                  aria-label={appearance ? `${appearance} terminals` : "Match app appearance"} aria-pressed={props.appearance === appearance}
                  onClick={() => props.onAppearance(appearance)} className={option(props.appearance === appearance)}>{appearance === null ? "Match app" : appearance === "dark" ? "Dark" : "Light"}</button>)}
              </div>
            </section>
            <div className="-mx-1 border-t border-border pt-2">
              <button type="button" disabled={props.busy} onClick={() => { setOpen(false); props.onRename(); }} className="block w-full rounded-md px-2 py-2 text-left text-xs hover:bg-muted disabled:opacity-40">Rename workspace</button>
              <button type="button" disabled={props.busy} onClick={() => { setOpen(false); props.onClose(); }} className="block w-full rounded-md px-2 py-2 text-left text-xs text-destructive hover:bg-muted disabled:opacity-40">Close workspace</button>
            </div>
          </div>}
        </div>
      </>}
      <button type="button" aria-label="Jarvis Live" title="Jarvis Live" aria-pressed={props.voiceOpen} onClick={props.onVoice}
        className={cn(icon, props.voiceOpen && "bg-muted text-foreground")}><Mic className="h-4 w-4" /></button>
    </div>
  </header>;
}

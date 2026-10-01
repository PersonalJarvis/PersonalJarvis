import { useEffect, useMemo, useRef, useState } from "react";
import { Keyboard } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  assignAgentKeys, hotkeyHints, isLeaderChord, resolveHotkey,
  type HotkeyAgent, type IdeHotkeyAction, type IdeHotkeyStep,
} from "./ideHotkeys";

interface Props {
  /** Off while a dialog owns the keyboard or the IDE is not on screen. */
  enabled: boolean;
  /** The coding agents a new pane can run, in the order the picker lists them. */
  agents: readonly HotkeyAgent[];
  /** The selected pane: the one pane commands act on, and the anchor of a split. */
  pane: string;
  onAction: (action: IdeHotkeyAction) => void;
  onRenamePane: (name: string) => void;
  /** Ctrl+B twice: type one Ctrl+B into the selected pane. */
  onPassThrough: () => void;
}

type View = IdeHotkeyStep | { menu: "rename" };

const LEADER = ["Ctrl", "B"];

function Cap({ children }: { children: React.ReactNode }) {
  return <kbd className="inline-flex min-w-[1.5rem] items-center justify-center rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.12)]">{children}</kbd>;
}

/**
 * The IDE's key menu: Ctrl+B, then the keys it lists; Ctrl+B twice types a
 * Ctrl+B into the pane. It listens on the window in the capture phase, so the chord is taken
 * before a focused terminal can hand it to the agent running there; while the
 * menu is open every key goes to the menu and none reaches a pane.
 */
export function IdeHotkeyMenu({ enabled, agents, pane, onAction, onRenamePane, onPassThrough }: Props) {
  const [view, setView] = useState<View | null>(null);
  const [draft, setDraft] = useState("");
  const keyed = useMemo(() => assignAgentKeys(agents), [agents]);
  const latest = useRef({ view, keyed, pane, onAction, onPassThrough, enabled });
  latest.current = { view, keyed, pane, onAction, onPassThrough, enabled };
  // The element that had the keyboard before the menu opened gets it back.
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => { if (!enabled) setView(null); }, [enabled]);

  useEffect(() => {
    const close = (restore: boolean) => {
      setView(null);
      const target = returnFocus.current;
      returnFocus.current = null;
      if (restore && target?.isConnected) target.focus();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const { view: current, keyed: agentKeys, enabled: on } = latest.current;
      if (!on || event.isComposing) return;
      if (isLeaderChord(event)) {
        event.preventDefault();
        event.stopPropagation();
        if (current) {
          close(true);
          // Twice in a row: the agent in the pane wanted its own Ctrl+B.
          if (current.menu === "root") latest.current.onPassThrough();
          return;
        }
        returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setView({ menu: "root" });
        return;
      }
      if (!current) return;
      // The rename field types like any field; only Escape leaves it.
      if (current.menu === "rename") {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
        return;
      }
      const outcome = resolveHotkey(current, event, agentKeys);
      if (outcome.type === "ignore") return;
      event.preventDefault();
      event.stopPropagation();
      if (outcome.type === "close") { close(true); return; }
      if (outcome.type === "step") { setView(outcome.step); return; }
      if (outcome.action.kind === "rename-pane") {
        if (!latest.current.pane) { close(true); return; }
        setDraft(latest.current.pane);
        setView({ menu: "rename" });
        return;
      }
      // Hand the keyboard back first: an action that opens a dialog or focuses
      // another pane then moves it on from there.
      close(true);
      latest.current.onAction(outcome.action);
    };
    // A click anywhere else means the user moved on.
    const onPointerDown = (event: PointerEvent) => {
      if (!latest.current.view) return;
      if (event.target instanceof Element && event.target.closest("[data-ide-hotkey-menu]")) return;
      close(false);
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, []);

  if (!view) return null;

  const crumbs = view.menu === "workspace" ? ["W"] : view.menu === "direction"
    ? [keyed.find((agent) => agent.name === view.agent)?.key.toUpperCase() ?? "?"] : [];

  return <div data-ide-hotkey-menu role="dialog" aria-label="IDE shortcuts"
    className="pointer-events-auto fixed bottom-6 left-1/2 z-[90] w-[min(46rem,calc(100vw-2rem))] -translate-x-1/2 rounded-2xl border border-border bg-popover p-4 text-popover-foreground shadow-2xl">
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Keyboard className="h-4 w-4" aria-hidden="true" />
        <span className="inline-flex items-center gap-1">{LEADER.map((key) => <Cap key={key}>{key}</Cap>)}</span>
        {crumbs.map((key) => <span key={key} className="inline-flex items-center gap-1"><span aria-hidden="true">›</span><Cap>{key}</Cap></span>)}
        {pane && view.menu !== "workspace" && <span className="truncate">· {pane}</span>}
      </div>
      <span className="text-[11px] text-muted-foreground"><Cap>Esc</Cap> close{view.menu !== "root" && <> · <Cap>⌫</Cap> back</>}</span>
    </div>
    {view.menu === "rename" ? <form className="flex items-center gap-2" onSubmit={(event) => {
      event.preventDefault();
      const name = draft.trim();
      setView(null);
      if (returnFocus.current?.isConnected) returnFocus.current.focus();
      returnFocus.current = null;
      if (name && name !== pane) onRenamePane(name);
    }}>
      <label className="flex min-w-0 flex-1 items-center gap-2 text-xs text-muted-foreground">Rename {pane}
        <input autoFocus value={draft} maxLength={40} onChange={(event) => setDraft(event.target.value)} onFocus={(event) => event.currentTarget.select()}
          className="h-9 min-w-0 flex-1 rounded-lg border border-input bg-background px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/30" />
      </label>
      <button type="submit" className="h-9 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground">Save</button>
    </form>
      : <div className={cn("grid gap-x-6 gap-y-3", view.menu === "root" ? "sm:grid-cols-3" : "grid-cols-1")}>
        {hotkeyHints(view, keyed).map((group) => <section key={group.title} className="min-w-0">
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{group.title}</h3>
          <ul className={cn("space-y-1", view.menu !== "root" && "grid gap-1 space-y-0 sm:grid-cols-2")}>
            {group.hints.map((hint) => <li key={hint.label} className="flex items-center gap-2 text-sm">
              <span className="inline-flex shrink-0 items-center gap-1">{hint.keys.map((key) => <Cap key={key}>{key}</Cap>)}</span>
              <span className="truncate">{hint.label}</span>
            </li>)}
          </ul>
        </section>)}
      </div>}
  </div>;
}

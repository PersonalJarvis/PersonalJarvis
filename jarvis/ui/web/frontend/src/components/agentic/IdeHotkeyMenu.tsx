import { useEffect, useMemo, useRef, useState } from "react";
import { appZoomCaps } from "@/lib/appZoom";
import { appChord, useAppChordSettings } from "@/store/appChordSettings";
import {
  STICKY_ACTIONS, assignAgentKeys, hotkeyHints, isLeaderChord, modeBar, resolveHotkey,
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

function Cap({ children }: { children: React.ReactNode }) {
  return <kbd className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-border bg-muted px-1 font-mono text-[11px] font-medium leading-none text-foreground">{children}</kbd>;
}

/**
 * The IDE's persistent keyboard shortcut mode.
 *
 * Ctrl+B turns it on — one press, nothing to hold — and a bar at the bottom
 * says so and names the keys. It stays on across moves, splits and workspace
 * switches until Esc; Ctrl+B again types one Ctrl+B into the pane and leaves.
 * The listener sits on the window in the capture phase, so while the mode is
 * on no key reaches a terminal, and the chord itself never does.
 *
 * The bar stays mounted for the whole mode and only its contents change, so
 * moving between steps never makes it blink.
 */
export function IdeHotkeyMenu({ enabled, agents, pane, onAction, onRenamePane, onPassThrough }: Props) {
  const [view, setView] = useState<View | null>(null);
  const [draft, setDraft] = useState("");
  const leader = useAppChordSettings((state) => state.bindings.ide_menu);
  const keyed = useMemo(() => assignAgentKeys(agents), [agents]);
  const latest = useRef({ view, keyed, pane, onAction, onPassThrough, enabled });
  latest.current = { view, keyed, pane, onAction, onPassThrough, enabled };
  // The pane being renamed gets the keyboard back after the field closes.
  const renameReturn = useRef<HTMLElement | null>(null);

  useEffect(() => { if (!enabled) setView(null); }, [enabled]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const { view: current, keyed: agentKeys, enabled: on } = latest.current;
      if (!on || event.isComposing) return;
      // Read per keystroke: a leader changed in Settings applies at once.
      if (isLeaderChord(event, appChord("ide_menu"))) {
        event.preventDefault();
        event.stopPropagation();
        if (!current) { setView({ menu: "root" }); return; }
        // Pressed again while on: the agent in the pane wanted its own Ctrl+B.
        setView(null);
        latest.current.onPassThrough();
        return;
      }
      if (!current) return;
      if (current.menu === "rename") {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setView({ menu: "root" }); }
        return; // the field types like any field
      }
      const outcome = resolveHotkey(current, event, agentKeys);
      if (outcome.type === "ignore") return;
      if (outcome.type === "release") { setView(null); return; }
      event.preventDefault();
      event.stopPropagation();
      if (outcome.type === "unknown") return;
      if (outcome.type === "close") { setView(null); return; }
      if (outcome.type === "step") { setView(outcome.step); return; }
      if (outcome.action.kind === "rename-pane") {
        if (!latest.current.pane) return;
        renameReturn.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setDraft(latest.current.pane);
        setView({ menu: "rename" });
        return;
      }
      // Moves, splits and switches keep the mode on; anything that opens a
      // dialog or ends the pane hands the keyboard back first.
      setView(STICKY_ACTIONS.has(outcome.action.kind) ? { menu: "root" } : null);
      latest.current.onAction(outcome.action);
    };
    // A click elsewhere means the user went back to the mouse.
    const onPointerDown = (event: PointerEvent) => {
      if (!latest.current.view) return;
      if (event.target instanceof Element && event.target.closest("[data-ide-hotkey-menu]")) return;
      setView(null);
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, []);

  if (!view) return null;

  const leaderCaps = leader ? appZoomCaps(leader) : [];

  const step: IdeHotkeyStep = view.menu === "rename" ? { menu: "root" } : view;
  const bar = modeBar(step, keyed, leaderCaps);
  const submitRename = (event: React.FormEvent) => {
    event.preventDefault();
    const name = draft.trim();
    setView({ menu: "root" });
    if (renameReturn.current?.isConnected) renameReturn.current.focus();
    renameReturn.current = null;
    if (name && name !== pane) onRenamePane(name);
  };

  return <div data-ide-hotkey-menu role="dialog" aria-label="IDE shortcuts"
    className="pointer-events-none fixed inset-x-0 bottom-4 z-[90] flex flex-col items-center gap-2 px-4">
    {view.menu === "help" && <section aria-label="All keys"
      className="pointer-events-auto w-full max-w-3xl rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-xl motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-150">
      <h2 className="mb-3 text-sm font-semibold">All keys</h2>
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-3">
        {hotkeyHints({ menu: "root" }, keyed, leaderCaps).map((group) => <section key={group.title} className="min-w-0">
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{group.title}</h3>
          <ul className="space-y-1.5">
            {group.hints.map((hint) => <li key={hint.label} className="flex items-center gap-2 text-sm">
              <span className="inline-flex shrink-0 items-center gap-1">{hint.keys.map((key) => <Cap key={key}>{key}</Cap>)}</span>
              <span className="truncate text-muted-foreground">{hint.label}</span>
            </li>)}
          </ul>
        </section>)}
      </div>
    </section>}
    <div className="pointer-events-auto flex h-10 max-w-full items-center gap-3 overflow-hidden rounded-xl border border-border bg-popover pl-1.5 pr-2 text-xs text-popover-foreground shadow-lg motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-150">
      <span className="flex h-7 shrink-0 items-center gap-1.5 rounded-lg bg-primary px-2.5 text-[11px] font-semibold tracking-wide text-primary-foreground">
        <span className="h-1.5 w-1.5 rounded-full bg-primary-foreground" aria-hidden="true" />{view.menu === "rename" ? "RENAME" : bar.badge}
      </span>
      {pane && step.menu !== "workspace" && <span className="max-w-[10rem] shrink-0 truncate font-medium">{pane}</span>}
      {view.menu === "rename" ? <form className="flex items-center gap-2" onSubmit={submitRename}>
        <input autoFocus aria-label={`Rename ${pane}`} value={draft} maxLength={40} onChange={(event) => setDraft(event.target.value)}
          onFocus={(event) => event.currentTarget.select()}
          className="h-7 w-48 rounded-md border border-input bg-background px-2 text-xs text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/30" />
        <button type="submit" className="h-7 rounded-md bg-primary px-2.5 text-xs font-medium text-primary-foreground">Save</button>
      </form>
        : <ul className="flex min-w-0 items-center gap-3 overflow-hidden whitespace-nowrap">
          {bar.hints.map((hint) => <li key={`${hint.keys.join("+")}:${hint.label}`} className="inline-flex shrink-0 items-center gap-1">
            {hint.keys.map((key) => <Cap key={key}>{key}</Cap>)}<span className="text-muted-foreground">{hint.label}</span>
          </li>)}
        </ul>}
      <span className="ml-auto inline-flex shrink-0 items-center gap-1 border-l border-border pl-3">
        <Cap>Esc</Cap><span className="text-muted-foreground">{view.menu === "rename" ? "cancel" : "exit"}</span>
      </span>
    </div>
  </div>;
}

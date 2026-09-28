import { useEffect, useId, useLayoutEffect, useRef, useState, type ComponentType, type CSSProperties, type MouseEvent, type PointerEvent, type SVGProps } from "react";
import { createPortal } from "react-dom";
import { Check, Maximize2, Minimize2, MoreHorizontal, Plus, X } from "lucide-react";
import { AgentMark } from "./AgentMark";
import { usePaneTitle } from "@/store/paneRecaps";
import { PromptHistoryButton } from "./PromptHistoryButton";
import { SplitAboveIcon, SplitBelowIcon, SplitLeftIcon, SplitRightIcon } from "./splitIcons";
import { PANE_BRAND, PANE_CHROME, themeFor, type PaneEdgeState, type TerminalAppearance } from "./terminalThemes";

export type PaneSplitDirection = "right" | "down" | "left" | "above";

interface Props {
  name: string;
  workspaceId?: string;
  promptCount?: number;
  agent: string;
  agentLogoUrl?: string;
  displayName: string;
  status: PaneEdgeState;
  appearance: TerminalAppearance;
  arranging?: boolean;
  maximized?: boolean;
  addDisabled?: boolean;
  onArrangeStart?: (event: PointerEvent) => void;
  onActivate?: () => void;
  onToggleMaximize?: () => void;
  onAdd?: (direction: PaneSplitDirection) => void;
  onClose?: () => void;
  onRename?: (name: string) => Promise<boolean>;
  onOpenConversation?: () => void;
  onOpenChat?: () => void;
  onRestart?: () => void;
}

type MenuIcon = ComponentType<SVGProps<SVGSVGElement>>;
interface MenuItem { label: string; run: () => void; Icon?: MenuIcon; separated?: boolean }

const SPLIT_ITEMS: { direction: PaneSplitDirection; label: string; Icon: MenuIcon }[] = [
  { direction: "right", label: "Split right", Icon: SplitRightIcon },
  { direction: "down", label: "Split down", Icon: SplitBelowIcon },
  { direction: "left", label: "Split left", Icon: SplitLeftIcon },
  { direction: "above", label: "Split up", Icon: SplitAboveIcon },
];

const ACTION_CLASS = "flex h-7 w-7 shrink-0 items-center justify-center rounded text-[color:var(--pane-ink-muted)] hover:bg-[color:var(--pane-chip)] hover:text-[color:var(--pane-ink)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--pane-ink)] disabled:opacity-35";

/** Quiet workspace chrome; terminal rendering and connection state stay in AgenticTerminal. */
export function WorkspaceTerminalHeader({
  name, workspaceId, promptCount = 0, agent, agentLogoUrl, displayName, status, appearance, arranging = false,
  maximized = false, addDisabled = false, onArrangeStart, onActivate, onToggleMaximize,
  onAdd, onClose, onRename, onOpenConversation, onOpenChat, onRestart,
}: Props) {
  const brand = PANE_BRAND[appearance];
  // The pane's goal in a few words, in place of its call-sign; the call-sign
  // stays reachable in the tooltip, the accessible name and the rename field.
  const title = usePaneTitle(workspaceId, name);
  const chrome = PANE_CHROME[appearance];
  const theme = themeFor(appearance);
  const menuId = useId();
  const dragHintId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number } | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [renameError, setRenameError] = useState("");
  const stopped = status === "exited" || status === "error";
  const menuOpen = menuPosition !== null;
  const variables = {
    "--pane-ink": brand.ink,
    "--pane-ink-muted": brand.inkMuted,
    "--pane-chip": brand.chip,
    color: brand.ink,
  } as CSSProperties;

  useEffect(() => {
    if (!menuOpen) return;
    menuRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const outside = (event: globalThis.PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target) && !moreRef.current?.contains(event.target)) setMenuPosition(null);
    };
    const dismiss = () => setMenuPosition(null);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [menuOpen]);

  // Keep the opened menu inside the window once its real height is known.
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menuPosition || !menu) return;
    const top = Math.max(8, Math.min(menuPosition.top, window.innerHeight - menu.offsetHeight - 8));
    const left = Math.max(8, Math.min(menuPosition.left, window.innerWidth - menu.offsetWidth - 8));
    if (top !== menuPosition.top || left !== menuPosition.left) setMenuPosition({ left, top });
  }, [menuPosition]);

  const toggleMenu = () => {
    const rect = moreRef.current?.getBoundingClientRect();
    if (!rect) return;
    setMenuPosition((current) => current ? null : { left: rect.right - 200, top: rect.bottom + 4 });
  };
  // Right-clicking the title bar opens the same menu at the cursor. Stopped
  // here so the app-wide Cut/Copy/Paste menu does not open on top of it.
  const openMenuAt = (event: MouseEvent) => {
    if (event.shiftKey || draft !== null) return;
    event.preventDefault();
    event.stopPropagation();
    onActivate?.();
    setMenuPosition({ left: event.clientX, top: event.clientY });
  };
  const choose = (action: () => void) => { setMenuPosition(null); action(); };
  const commitRename = async () => {
    const wanted = draft?.trim();
    if (!wanted || wanted === name) { setDraft(null); return; }
    setSaving(true);
    setRenameError("");
    try { if (await onRename?.(wanted) !== false) setDraft(null); }
    catch (error) { setRenameError(error instanceof Error ? error.message : "The name could not be saved."); }
    finally { setSaving(false); }
  };

  return <>
    <header data-testid={`workspace-terminal-header-${name}`}
      className="relative flex h-9 min-h-9 shrink-0 select-none items-center gap-1 border-b pl-2.5 pr-1"
      style={{ ...variables, borderColor: chrome.border, background: chrome.shell, touchAction: onArrangeStart ? "none" : undefined }}
      onContextMenu={openMenuAt}
      onPointerDown={(event) => {
        if (event.button !== 0 || (event.target as HTMLElement).closest("[data-header-control]")) return;
        setMenuPosition(null);
        onArrangeStart?.(event);
      }}>
      {draft === null ? <button type="button" data-ide-drag-handle="true" data-testid={`pane-move-${name}`}
        // Pointer selection is handled by the pane's mousedown. Keyboard
        // activation selects too, while retaining focus for Alt+Arrow moves.
        onClick={(event) => { if (event.detail === 0) onActivate?.(); }}
        aria-label={onArrangeStart ? `Move ${name}` : name}
        aria-describedby={onArrangeStart ? dragHintId : undefined}
        aria-keyshortcuts={onArrangeStart ? "Alt+ArrowLeft Alt+ArrowRight Alt+ArrowUp Alt+ArrowDown" : undefined}
        className={`flex h-full min-w-0 flex-1 items-center gap-2 rounded text-left text-sm font-medium focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--pane-ink)] ${onArrangeStart ? arranging ? "cursor-grabbing" : "cursor-grab" : "cursor-default"}`}>
        <span role="img" aria-label={`${name}: ${status}`} className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: status === "live" ? theme.green : status === "error" ? theme.red : status === "connecting" ? theme.yellow : brand.inkFaint }} />
        <AgentMark agent={agent} label={displayName} logoUrl={agentLogoUrl} variant="plain" size="sm"
          className="!text-[color:var(--pane-ink)] [&>.bg-foreground]:!bg-[color:var(--pane-ink)]" />
        <span data-testid={`pane-title-${name}`} title={title ? `${title} (${name})` : name} className="truncate">{title || name}</span>
      </button> : <form data-header-control="true" className="flex min-w-0 flex-1 items-center gap-1"
        onSubmit={(event) => { event.preventDefault(); void commitRename(); }}>
        <input autoFocus aria-label={`Name for ${name}`} value={draft} maxLength={40} disabled={saving}
          aria-invalid={Boolean(renameError)}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setDraft(null); setRenameError(""); } }}
          className="min-w-0 flex-1 rounded border px-1.5 py-0.5 text-sm outline-none"
          style={{ borderColor: chrome.border, background: chrome.float, color: brand.ink }} />
        <button type="submit" aria-label="Save name" disabled={saving} className={ACTION_CLASS}><Check className="h-3.5 w-3.5" /></button>
        <button type="button" aria-label="Cancel rename" disabled={saving} onClick={() => { setDraft(null); setRenameError(""); }} className={ACTION_CLASS}><X className="h-3.5 w-3.5" /></button>
      </form>}
      <span id={dragHintId} className="sr-only">Drag to reorder, or focus this title and press Alt with an arrow key.</span>
      <div data-header-control="true" className="flex shrink-0 items-center gap-0.5">
        <button ref={moreRef} type="button" aria-label={`More actions for ${name}`} aria-haspopup="menu" aria-expanded={menuOpen}
          aria-controls={menuOpen ? menuId : undefined} onClick={toggleMenu}
          onKeyDown={(event) => { if (event.key === "ArrowDown") { event.preventDefault(); toggleMenu(); } }}
          className={ACTION_CLASS}><MoreHorizontal className="h-4 w-4" /></button>
        <button type="button" aria-label={`${maximized ? "Restore" : "Maximize"} ${name}`} disabled={!onToggleMaximize}
          onClick={onToggleMaximize} className={ACTION_CLASS}>{maximized ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}</button>
        <button type="button" aria-label={`Add agent beside ${name}`} disabled={addDisabled || !onAdd}
          onClick={() => onAdd?.("right")} className={ACTION_CLASS}><Plus className="h-4 w-4" /></button>
        <button type="button" aria-label={`Close ${name}`} disabled={!onClose} onClick={onClose} className={ACTION_CLASS}><X className="h-4 w-4" /></button>
      </div>
    </header>
    {renameError && <p role="alert" className="px-2 py-1 text-xs" style={{ color: theme.red }}>{renameError}</p>}
    {createPortal(<div ref={menuRef} id={menuId} role="menu" hidden={!menuOpen} aria-label={`Actions for ${name}`}
      className="fixed z-[90] w-[200px] rounded-lg border p-1 shadow-lg"
      style={{ ...variables, ...(menuPosition ?? { left: 0, top: 0 }), borderColor: chrome.border, background: chrome.float }}
      onPointerDown={(event) => event.stopPropagation()}
      onBlur={(event) => {
        if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== moreRef.current) setMenuPosition(null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMenuPosition(null); moreRef.current?.focus(); return; }
        if (event.key === "Tab") { setMenuPosition(null); moreRef.current?.focus(); return; }
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? []);
        const at = items.indexOf(document.activeElement as HTMLButtonElement);
        const index = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (at + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[index]?.focus();
      }}>
      {([
        onRename && { label: "Rename", run: () => { setDraft(name); setRenameError(""); } },
        ...(onAdd && !addDisabled ? SPLIT_ITEMS.map((item, index) => ({
          label: `${item.label}…`, Icon: item.Icon, separated: index === 0, run: () => onAdd(item.direction),
        })) : []),
        onToggleMaximize && { label: maximized ? "Restore size" : "Maximize", separated: true,
          Icon: maximized ? Minimize2 : Maximize2, run: onToggleMaximize },
        onOpenConversation && { label: "Conversation history", run: onOpenConversation },
        onOpenChat && { label: "Open as chat", run: onOpenChat },
        stopped && onRestart && { label: "Restart agent", run: onRestart },
      ] as (MenuItem | false | undefined | null)[]).filter((item): item is MenuItem => Boolean(item)).map((item) =>
        <button type="button" role="menuitem" key={item.label} onClick={() => choose(item.run)}
          className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs hover:bg-[color:var(--pane-chip)] focus:bg-[color:var(--pane-chip)] focus:outline-none ${item.separated ? "mt-1 border-t border-[color:var(--pane-chip)] pt-2" : ""}`}>
          {item.Icon && <item.Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}{item.label}
        </button>,
      )}
      <PromptHistoryButton terminal={name} workspaceId={workspaceId} count={promptCount} triggerMode="menu-item"
        onOpen={() => setMenuPosition(null)} restoreFocus={() => moreRef.current?.focus()} />
      {onClose && <button type="button" role="menuitem" onClick={() => choose(onClose)}
        className="mt-1 flex w-full items-center gap-2 rounded border-t border-[color:var(--pane-chip)] px-2.5 py-2 pt-2 text-left text-xs hover:bg-[color:var(--pane-chip)] focus:bg-[color:var(--pane-chip)] focus:outline-none">
        <X className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />Close pane</button>}
    </div>, document.body)}
  </>;
}

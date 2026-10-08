import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** One thing the side panel can open: a registry tab or a plain terminal. */
export interface SurfaceAction {
  id: string;
  label: string;
  icon: LucideIcon;
  shortcut: string;
  available: boolean;
  /** Why it is greyed out, shown as the row's tooltip. */
  reason?: string;
  /** True when the tab is already open (the "+" menu ticks it). */
  isOpen?: boolean;
  run: () => void;
}

/** Open overlays that must keep their own keys while the launcher is showing. */
const BLOCKING_LAYERS = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [aria-modal="true"]';

/**
 * True while an overlay is actually showing. Several menus stay mounted while
 * closed (hidden, zero size), so presence alone would mute the letters for good.
 */
function overlayShowing(): boolean {
  return [...document.querySelectorAll(BLOCKING_LAYERS)].some((node) => node.getClientRects().length > 0);
}

type ShortcutEvent = Pick<KeyboardEvent, "altKey" | "ctrlKey" | "defaultPrevented" | "isComposing" | "key" | "metaKey">;

/** The available action a bare letter press picks, or null (modifiers and IME input never pick). */
export function surfaceActionForKey(actions: readonly SurfaceAction[], event: ShortcutEvent): SurfaceAction | null {
  if (event.defaultPrevented || event.isComposing) return null;
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  const key = event.key.toLowerCase();
  return actions.find((action) => action.available && action.shortcut.toLowerCase() === key) ?? null;
}

/**
 * A focused editable is where the reader's next keystrokes belong, empty or
 * not: a terminal (xterm types through a textarea), the composer, a field.
 * Letters typed there must never open a surface instead.
 */
export function targetsTypingContext(target: EventTarget | null): boolean {
  return target instanceof Element
    && target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])') !== null;
}

export function SurfaceKbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-[1.25rem] shrink-0 items-center justify-center rounded border border-border bg-muted px-1 font-mono text-[11px] font-medium leading-none text-muted-foreground">
      {children}
    </kbd>
  );
}

/**
 * What an empty side panel shows: every surface it can open, one per row,
 * each with its letter. Keyboard first: while the launcher is on screen a
 * bare letter opens its surface from anywhere outside a typing context, and
 * arrows plus Enter work while the list has focus. Surfaces that cannot open
 * right now stay listed, greyed, with the reason as a tooltip.
 */
export function SurfaceLauncher({ actions, keysEnabled }: { actions: readonly SurfaceAction[]; keysEnabled: boolean }) {
  const t = useT();
  // -1: no highlight until the pointer or an arrow key picks a row.
  const [highlight, setHighlight] = useState(-1);
  const available = actions.filter((action) => action.available);
  const index = available.length === 0 ? -1 : Math.min(highlight, available.length - 1);

  const latest = useRef(available);
  latest.current = available;
  useEffect(() => {
    if (!keysEnabled) return;
    // Capture phase, so an app-level handler cannot swallow the letter first.
    const onKey = (event: KeyboardEvent) => {
      const action = surfaceActionForKey(latest.current, event);
      if (!action || targetsTypingContext(event.target)) return;
      if (overlayShowing()) return;
      event.preventDefault();
      event.stopPropagation();
      action.run();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [keysEnabled]);

  // Take focus on arrival so arrows work at once, unless the reader is typing
  // somewhere: a panel restored open at startup must not steal the composer.
  const focusOnMount = useCallback((node: HTMLDivElement | null) => {
    if (node && !targetsTypingContext(document.activeElement)) node.focus({ preventScroll: true });
  }, []);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.metaKey || event.ctrlKey || event.altKey || available.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlight((index + 1) % available.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight(index <= 0 ? available.length - 1 : index - 1);
    } else if (event.key === "Enter" && event.target === event.currentTarget && index >= 0) {
      event.preventDefault();
      available[index].run();
    }
  };

  return (
    <div
      ref={focusOnMount}
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label={t("ide_side_panel.launcher.title")}
      data-testid="ide-side-panel-launcher"
      className="flex h-full min-h-0 items-center justify-center overflow-y-auto px-6 outline-none"
    >
      <div className="w-full max-w-xs py-6">
        <h3 className="mb-3 text-center text-sm font-medium text-foreground">{t("ide_side_panel.launcher.title")}</h3>
        <div className="flex flex-col gap-0.5">
          {actions.map((action) => {
            const Icon = action.icon;
            const lit = index >= 0 && available[index] === action;
            return (
              <button
                key={action.id}
                type="button"
                tabIndex={-1}
                // aria-disabled, not disabled: a disabled button shows no tooltip.
                aria-disabled={!action.available}
                title={action.available ? undefined : action.reason}
                aria-keyshortcuts={action.shortcut}
                data-testid={`ide-side-panel-launch-${action.id}`}
                onClick={() => { if (action.available) action.run(); }}
                onMouseEnter={() => action.available && setHighlight(available.indexOf(action))}
                onMouseLeave={() => setHighlight((current) => (current === available.indexOf(action) ? -1 : current))}
                className={cn(
                  "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-sm text-foreground transition-colors",
                  action.available ? "hover:bg-secondary" : "cursor-default opacity-50",
                  lit && "bg-secondary",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{action.label}</span>
                <SurfaceKbd>{action.shortcut}</SurfaceKbd>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

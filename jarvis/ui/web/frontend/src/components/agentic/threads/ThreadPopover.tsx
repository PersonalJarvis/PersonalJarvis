import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/**
 * A small floating panel anchored to a button — the thread view's menus
 * (Open in, Git actions, project actions, a thread's row menu).
 *
 * Portalled to `body` so a scroll column never clips it, placed below the
 * anchor (above when there is no room) and kept inside the viewport. Escape
 * and a press outside close it; focus returns to the anchor.
 */
export function ThreadPopover({
  anchor,
  open,
  onClose,
  align = "start",
  side = "bottom",
  width = 260,
  className,
  label,
  children,
}: {
  anchor: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  align?: "start" | "end";
  side?: "top" | "bottom";
  width?: number;
  className?: string;
  /** Accessible name of the panel. */
  label: string;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number; maxHeight: number } | null>(null);

  const measure = useCallback(() => {
    const host = anchor.current;
    if (!host) return;
    const rect = host.getBoundingClientRect();
    const margin = 8;
    const height = panel.current?.offsetHeight ?? 0;
    const below = window.innerHeight - rect.bottom - margin;
    const above = rect.top - margin;
    const wantTop = side === "top" ? above > Math.min(height, 240) || above > below : below < Math.min(height, 240) && above > below;
    const left = align === "end" ? rect.right - width : rect.left;
    const clampedLeft = Math.max(margin, Math.min(left, window.innerWidth - width - margin));
    const room = Math.max(120, wantTop ? above - 4 : below - 4);
    const top = wantTop ? Math.max(margin, rect.top - 4 - Math.min(height, room)) : rect.bottom + 4;
    setPlace({ left: clampedLeft, top, maxHeight: room });
  }, [anchor, align, side, width]);

  useLayoutEffect(() => {
    if (!open) { setPlace(null); return; }
    measure();
    // A second pass once the panel has its real height.
    const frame = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(frame);
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panel.current?.contains(target) || anchor.current?.contains(target)) return;
      onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
      anchor.current?.focus();
    };
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", measure);
    };
  }, [open, onClose, anchor, measure]);

  if (!open) return null;
  return createPortal(
    <div ref={panel} role="dialog" aria-label={label}
      style={{ left: place?.left ?? -9999, top: place?.top ?? -9999, width, maxHeight: place?.maxHeight }}
      className={cn("fixed z-[90] overflow-y-auto rounded-xl border border-border bg-popover p-1 text-sm text-popover-foreground shadow-float scrollbar-jarvis", className)}>
      {children}
    </div>,
    document.body,
  );
}

/** One row of a popover menu: icon, words, an optional quiet hint. */
export function ThreadMenuItem({
  icon,
  label,
  hint,
  onSelect,
  disabled = false,
  danger = false,
  selected = false,
}: {
  icon?: ReactNode;
  label: ReactNode;
  hint?: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
  selected?: boolean;
}) {
  return <button type="button" role="menuitem" disabled={disabled} onClick={onSelect}
    className={cn(
      "flex min-h-8 w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-45",
      danger ? "text-destructive hover:bg-destructive/10" : "hover:bg-secondary",
      selected && "bg-secondary text-foreground-strong",
    )}>
    {icon && <span className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground">{icon}</span>}
    <span className="min-w-0 flex-1 truncate">{label}</span>
    {hint && <span className="shrink-0 text-xs text-muted-foreground">{hint}</span>}
  </button>;
}

/** A quiet group heading inside a popover. */
export function ThreadMenuHeading({ children }: { children: ReactNode }) {
  return <div className="px-2 pb-1 pt-2 text-xs font-medium text-muted-foreground">{children}</div>;
}

export function ThreadMenuSeparator() {
  return <div role="separator" className="my-1 h-px bg-border" />;
}

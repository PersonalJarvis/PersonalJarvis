import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SkillMenuItem {
  id: string;
  label: string;
  icon: LucideIcon;
  onSelect: () => void;
  tone?: "danger";
  /** Shown at the right edge: a shortcut or a state. */
  hint?: ReactNode;
  /** Keep the menu open after this item (a two-step delete). */
  keepOpen?: boolean;
}

const WIDTH = 216;

/**
 * A small action menu anchored to a button, drawn in a portal.
 *
 * The skill list scrolls inside a clipped panel, so a menu positioned inside
 * it would be cut off at the panel's edge; drawn at the document root with
 * fixed coordinates it is never clipped, and it opens upward when there is
 * no room below.
 */
export function SkillMenu({ anchor, open, onClose, items, label }: {
  anchor: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  items: SkillMenuItem[];
  label: string;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !anchor.current) {
      setPlace(null);
      return;
    }
    const rect = anchor.current.getBoundingClientRect();
    const height = menu.current?.offsetHeight ?? items.length * 34 + 8;
    const below = rect.bottom + 6 + height <= window.innerHeight - 8;
    setPlace({
      top: below ? rect.bottom + 6 : Math.max(8, rect.top - 6 - height),
      left: Math.min(Math.max(8, rect.right - WIDTH), window.innerWidth - WIDTH - 8),
    });
  }, [open, anchor, items.length]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (menu.current?.contains(target) || anchor.current?.contains(target)) return;
      onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    const onScroll = () => onClose();
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, onClose, anchor]);

  useEffect(() => {
    if (open && place) menu.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open, place]);

  if (!open) return null;
  return createPortal(
    <div
      ref={menu}
      role="menu"
      aria-label={label}
      style={{ top: place?.top ?? -9999, left: place?.left ?? -9999, width: WIDTH }}
      className="fixed z-[95] rounded-xl border border-border bg-popover p-1 shadow-float animate-in fade-in-0 zoom-in-95 duration-100 motion-reduce:animate-none"
      onKeyDown={(event) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        event.preventDefault();
        const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const next = buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length];
        next?.focus();
      }}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          data-testid={`skill-menu-${item.id}`}
          onClick={() => {
            item.onSelect();
            if (!item.keepOpen) onClose();
          }}
          className={cn(
            "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm outline-none transition-colors",
            "focus-visible:bg-secondary hover:bg-secondary",
            item.tone === "danger" ? "text-destructive" : "text-foreground",
          )}
        >
          <item.icon className={cn("h-4 w-4 shrink-0", item.tone === "danger" ? "text-destructive" : "text-muted-foreground")} aria-hidden />
          <span className="min-w-0 flex-1 truncate">{item.label}</span>
          {item.hint && <span className="shrink-0 text-xs text-muted-foreground">{item.hint}</span>}
        </button>
      ))}
    </div>,
    document.body,
  );
}

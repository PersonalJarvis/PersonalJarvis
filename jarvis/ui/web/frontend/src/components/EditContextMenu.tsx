import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Clipboard, Copy, Scissors, TextSelect } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { robustCopy, robustPaste } from "@/lib/clipboard";
import {
  capabilitiesFor,
  captureEditSnapshot,
  deleteSelection,
  pasteInto,
  selectAll,
  type EditCapabilities,
  type EditSnapshot,
} from "@/lib/editActions";
import { cn } from "@/lib/utils";

/**
 * App-wide right-click menu offering Cut / Copy / Paste / Select all.
 *
 * The desktop shell embeds the UI in a WebView whose own context menu is
 * switched off, so without this component right-clicking does nothing at all —
 * anywhere in the app. That leaves mouse-driven pasting impossible, which is
 * how most people paste. Mounted once in the app shell so it covers every
 * view, including the Agentic IDE terminals.
 *
 * Shift+right-click is deliberately left alone: in a real browser that still
 * opens the native menu, keeping the developer escape hatch intact.
 */

const MENU_WIDTH = 210;
const VIEWPORT_MARGIN = 8;

interface MenuState {
  x: number;
  y: number;
  snapshot: EditSnapshot;
  caps: EditCapabilities;
}

export function EditContextMenu() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => setMenu(null), []);

  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      // Escape hatch: let the browser's own menu through.
      if (event.shiftKey) return;
      const snapshot = captureEditSnapshot(event.target);
      const caps = capabilitiesFor(snapshot);
      // Nothing useful to offer (e.g. a right-click on empty chrome with no
      // selection) — stay out of the way rather than show a dead menu.
      if (!caps.canCut && !caps.canCopy && !caps.canPaste && !caps.canSelectAll) {
        return;
      }
      event.preventDefault();
      setMenu({ x: event.clientX, y: event.clientY, snapshot, caps });
    };

    document.addEventListener("contextmenu", onContextMenu);
    return () => document.removeEventListener("contextmenu", onContextMenu);
  }, []);

  useEffect(() => {
    if (!menu) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) close();
    };
    // A scroll or a resize moves the anchor out from under the menu.
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", close);
    window.addEventListener("blur", close);
    document.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("blur", close);
      document.removeEventListener("scroll", close, true);
    };
  }, [menu, close]);

  // Keep the menu inside the window: near the right or bottom edge it flips
  // back over the cursor instead of being clipped.
  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!node || !menu) return;
    const { width, height } = node.getBoundingClientRect();
    const maxX = window.innerWidth - width - VIEWPORT_MARGIN;
    const maxY = window.innerHeight - height - VIEWPORT_MARGIN;
    node.style.left = `${Math.max(VIEWPORT_MARGIN, Math.min(menu.x, maxX))}px`;
    node.style.top = `${Math.max(VIEWPORT_MARGIN, Math.min(menu.y, maxY))}px`;
    node.style.visibility = "visible";
    // Focus the menu so Escape and the arrow keys work without a mouse.
    node.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, [menu]);

  const run = useCallback(
    async (action: "cut" | "copy" | "paste" | "selectAll") => {
      if (!menu) return;
      const { snapshot } = menu;
      close();

      if (action === "selectAll") {
        selectAll(snapshot);
        return;
      }
      if (action === "copy" || action === "cut") {
        const copied = await robustCopy(snapshot.selectedText);
        if (!copied) {
          pushToast("error", t("edit_menu.copy_failed"));
          return;
        }
        if (action === "cut") deleteSelection(snapshot);
        return;
      }
      // Paste.
      const text = await robustPaste();
      if (text === null) {
        pushToast("error", t("edit_menu.paste_unavailable"));
        return;
      }
      if (!text) return; // nothing on the clipboard — not an error
      if (!pasteInto(snapshot, text)) {
        pushToast("error", t("edit_menu.paste_failed"));
      }
    },
    [menu, close, pushToast, t],
  );

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        "button:not([disabled])",
      ) ?? [],
    );
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next = (current + step + items.length) % items.length;
    items[next]?.focus();
  };

  if (!menu) return null;
  const { caps } = menu;

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label={t("edit_menu.label")}
      onKeyDown={onMenuKeyDown}
      // Positioned in the layout effect above, once its real size is known.
      style={{ width: MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-md border border-border bg-background py-1 shadow-lg"
    >
      <MenuItem
        icon={Scissors}
        label={t("edit_menu.cut")}
        shortcut="Ctrl+X"
        disabled={!caps.canCut}
        onSelect={() => void run("cut")}
      />
      <MenuItem
        icon={Copy}
        label={t("edit_menu.copy")}
        shortcut="Ctrl+C"
        disabled={!caps.canCopy}
        onSelect={() => void run("copy")}
      />
      <MenuItem
        icon={Clipboard}
        label={t("edit_menu.paste")}
        shortcut="Ctrl+V"
        disabled={!caps.canPaste}
        onSelect={() => void run("paste")}
      />
      <MenuItem
        icon={TextSelect}
        label={t("edit_menu.select_all")}
        shortcut="Ctrl+A"
        disabled={!caps.canSelectAll}
        onSelect={() => void run("selectAll")}
      />
    </div>
  );
}

function MenuItem({
  icon: Icon,
  label,
  shortcut,
  disabled,
  onSelect,
}: {
  icon: typeof Copy;
  label: string;
  shortcut: string;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      // Without this the click moves focus off the field before the action
      // runs, and the caret — the place the text has to land — is gone.
      onMouseDown={(e) => e.preventDefault()}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm",
        disabled
          ? "cursor-default text-muted-foreground/40"
          : "text-foreground hover:bg-primary/10 focus:bg-primary/10 focus:outline-none",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="flex-1">{label}</span>
      <span className="text-[10px] tracking-wider text-muted-foreground">
        {shortcut}
      </span>
    </button>
  );
}

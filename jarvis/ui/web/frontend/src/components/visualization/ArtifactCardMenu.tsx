import {
  useEffect,
  useLayoutEffect,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Download, Eye, ExternalLink, FolderOpen, Trash2 } from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { rowTitle, type RailRow } from "@/components/visualization/galleryModel";

/**
 * The right-click menu on an Artifacts card, and the question it asks before
 * anything is deleted.
 *
 * The desktop WebView has no native context menu, and the app-wide
 * Cut/Copy/Paste menu lives on `document`, so the card stops its own
 * right-click and opens this one instead. What it offers follows the card:
 * an artifact can be opened, opened in the browser, saved and shown in its
 * folder; a run without one can be opened and shown in its folder; anything
 * finished can be deleted. A card whose run is still working cannot be
 * deleted — its worker may be writing into that very folder — so the item is
 * there but disabled, with the reason on hover.
 */

const MENU_WIDTH = 220;
const VIEWPORT_MARGIN = 8;

export interface ArtifactMenuState {
  row: RailRow;
  x: number;
  y: number;
}

/** True while the card's run may still write into its folder. */
export function rowIsWorking(row: RailRow): boolean {
  if (row.kind === "build") return true;
  return row.run?.status === "running";
}

export function ArtifactCardMenu({
  row,
  x,
  y,
  canReveal,
  onDismiss,
  onOpen,
  onOpenExternal,
  onDownload,
  onReveal,
  onDelete,
}: {
  row: RailRow;
  x: number;
  y: number;
  /** Desktop only — a headless host has no file manager to open. */
  canReveal: boolean;
  onDismiss: () => void;
  onOpen: () => void;
  onOpenExternal: () => void;
  onDownload: () => void;
  onReveal: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const isVisual = row.kind === "visual";
  const working = rowIsWorking(row);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onDismiss();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onDismiss();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onDismiss);
    window.addEventListener("blur", onDismiss);
    document.addEventListener("scroll", onDismiss, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onDismiss);
      window.removeEventListener("blur", onDismiss);
      document.removeEventListener("scroll", onDismiss, true);
    };
  }, [onDismiss]);

  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!node) return;
    const { width, height } = node.getBoundingClientRect();
    const maxX = window.innerWidth - width - VIEWPORT_MARGIN;
    const maxY = window.innerHeight - height - VIEWPORT_MARGIN;
    node.style.left = `${Math.max(VIEWPORT_MARGIN, Math.min(x, maxX))}px`;
    node.style.top = `${Math.max(VIEWPORT_MARGIN, Math.min(y, maxY))}px`;
    node.style.visibility = "visible";
    node.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, [x, y]);

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [],
    );
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(current + step + items.length) % items.length]?.focus();
  };

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={t("visualization.menu_aria")}
      data-testid="artifact-card-menu"
      onKeyDown={onMenuKeyDown}
      onContextMenu={(event) => event.preventDefault()}
      style={{ width: MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-lg border border-border bg-popover py-1 shadow-float"
    >
      <MenuItem icon={<Eye />} testId="artifact-menu-open" onClick={onOpen}>
        {t("visualization.menu_open")}
      </MenuItem>
      {isVisual && (
        <MenuItem icon={<ExternalLink />} testId="artifact-menu-open-external" onClick={onOpenExternal}>
          {t("visualization.open_external")}
        </MenuItem>
      )}
      {isVisual && (
        <MenuItem icon={<Download />} testId="artifact-menu-download" onClick={onDownload}>
          {t("visualization.download")}
        </MenuItem>
      )}
      {canReveal && row.kind !== "build" && (
        <MenuItem icon={<FolderOpen />} testId="artifact-menu-reveal" onClick={onReveal}>
          {t("visualization.reveal")}
        </MenuItem>
      )}
      <div role="separator" className="my-1 h-px bg-border" />
      <MenuItem
        icon={<Trash2 />}
        testId="artifact-menu-delete"
        onClick={onDelete}
        disabled={working}
        title={working ? t("visualization.delete_running") : undefined}
        destructive
      >
        {t("visualization.delete")}
      </MenuItem>
    </div>,
    document.body,
  );
}

function MenuItem({
  icon,
  testId,
  onClick,
  disabled = false,
  title,
  destructive = false,
  children,
}: {
  icon: ReactNode;
  testId: string;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  destructive?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm transition-colors",
        "focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50",
        "[&_svg]:h-4 [&_svg]:w-4 [&_svg]:shrink-0",
        destructive
          ? "text-destructive hover:bg-destructive/10 focus-visible:bg-destructive/10"
          : "text-foreground hover:bg-secondary focus-visible:bg-secondary [&_svg]:text-muted-foreground",
      )}
    >
      <span aria-hidden className="contents">
        {icon}
      </span>
      {children}
    </button>
  );
}

/** "Delete this?" — the one step between a right-click and a gone folder. */
export function ConfirmDeleteArtifact({
  row,
  busy,
  onCancel,
  onConfirm,
}: {
  row: RailRow;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  const title = fill(t("visualization.delete_title"), { name: rowTitle(row) });
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="artifact-confirm-delete"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 p-6 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-lg bg-popover p-5 shadow-float">
        <h3 className="font-display text-base font-semibold [overflow-wrap:anywhere]">{title}</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          {t(row.kind === "visual" ? "visualization.delete_body_file" : "visualization.delete_body_run")}
        </p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            className="rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            autoFocus
            disabled={busy}
            onClick={onCancel}
          >
            {t("visualization.delete_cancel")}
          </button>
          <button
            type="button"
            data-testid="artifact-confirm-delete-confirm"
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            disabled={busy}
            onClick={onConfirm}
          >
            {t("visualization.delete_confirm")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

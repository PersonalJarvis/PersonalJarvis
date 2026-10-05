import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GripVertical, Loader2, PenLine, Trash2 } from "lucide-react";

import { setWorkspaceEntryDrag } from "@/components/agentic/explorerDrag";
import { Button } from "@/components/ui/button";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useLocaleChunk, useT } from "@/i18n";
import {
  appshotLibraryFileName,
  appshotLibraryImageUrl,
  clearAppshotLibrary,
  deleteAppshotLibraryItem,
  fetchAppshotLibrary,
  openAppshotLibraryItem,
  type AppshotLibraryItem,
} from "@/lib/appshotApi";
import { canNativeDrag, startNativeFileDrag } from "@/lib/nativeDrag";
import { cn } from "@/lib/utils";
import { useAppshotEditor } from "@/store/appshotEditor";
import { useEventStore } from "@/store/events";

/**
 * The gallery on the Appshots page: every appshot the user took and every
 * edit they saved, newest first (`jarvis.appshot.library`).
 *
 * A tile is a drag source for the app's own fields: it carries the picture's
 * real path the way a row from the workspace explorer does, so the chat
 * composer attaches it and a terminal pane hands it to its agent — the drop
 * targets need no appshot-specific code. Inside the desktop shell the grip
 * starts a native file drag instead, which reaches any other app as well.
 *
 * The gallery sits in the Settings dialog, which covers the chat and the
 * terminals — the very fields a picture is dragged into. So while a tile is
 * being dragged the dialog steps aside (`html[data-appshot-drag]`, see
 * LIFT_DIALOG_CSS) and comes back the moment the drag ends.
 */

type Filter = "all" | "edited";

/** Rows shown while the gallery is folded; "Show more" opens the rest. */
const ROWS = 2;
/** Tiles each further "Show more" adds; the grid stays light with 500 kept. */
const PAGE = 48;
/** Columns assumed until the grid has been measured (and where it cannot be). */
const FALLBACK_COLUMNS = 6;

/** How many tiles fit in one row of the auto-filled grid right now. */
function useGridColumns(grid: HTMLUListElement | null): number {
  const [columns, setColumns] = useState(FALLBACK_COLUMNS);
  useEffect(() => {
    if (!grid) return;
    const measure = () => {
      const tracks = getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean).length;
      if (tracks > 0) setColumns(tracks);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    return () => observer.disconnect();
  }, [grid]);
  return columns;
}

const tileKey = (item: AppshotLibraryItem) => `${item.id}:${item.variant}`;

/**
 * While a tile is dragged: the Settings dialog and its dim fade out and let
 * the drag through to the app behind. Radix keeps `pointer-events: none` on
 * the body while a modal is open, which would hide every drop target, so the
 * body is opened up for exactly as long as the drag lasts. The dim is the
 * element right before the dialog — only that one: the dialog is a child of
 * <body>, so a looser sibling match would hide the whole app (#root).
 */
const LIFT_DIALOG_CSS = `
html[data-appshot-drag] body { pointer-events: auto !important; }
html[data-appshot-drag] [data-testid="settings-hub-dialog"],
html[data-appshot-drag] [data-state]:has(+ [data-testid="settings-hub-dialog"]) {
  opacity: 0;
  pointer-events: none !important;
  transition: opacity 120ms ease-out;
}`;

function liftDialog(lifted: boolean): void {
  const root = document.documentElement;
  if (lifted) root.dataset.appshotDrag = "1";
  else delete root.dataset.appshotDrag;
}

function when(item: AppshotLibraryItem): string {
  return new Date((item.edited_at || item.taken_at) * 1000).toLocaleString([], {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function Tile({
  item,
  busy,
  onOpen,
  onDelete,
}: {
  item: AppshotLibraryItem;
  busy: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const imageRef = useRef<HTMLImageElement | null>(null);
  const where = item.app_name || item.label || t("appshots.preview_front_window");
  const edited = item.variant === "edited";
  const nativeDrag = canNativeDrag();

  const onDragStart = (event: React.DragEvent) => {
    // The path, a file URI and the path as text — what the panes, the chat
    // composer and other text fields read (explorerDrag.ts). DownloadURL lets
    // a browser drop the picture itself onto the desktop or into a folder.
    const dt = event.dataTransfer;
    const lastSlash = Math.max(item.path.lastIndexOf("/"), item.path.lastIndexOf("\\"));
    setWorkspaceEntryDrag(dt, {
      root: item.path.slice(0, lastSlash),
      path: item.path.slice(lastSlash + 1),
    });
    const absoluteUrl = new URL(appshotLibraryImageUrl(item), window.location.href).toString();
    dt.setData("DownloadURL", `${item.mime}:${appshotLibraryFileName(item)}:${absoluteUrl}`);
    if (imageRef.current) dt.setDragImage(imageRef.current, 24, 24);
    // Not in this tick: hiding the drag source before the browser has taken
    // its picture cancels the drag in Chromium.
    window.setTimeout(() => liftDialog(true), 0);
  };

  return (
    <li
      draggable
      onDragStart={onDragStart}
      onDragEnd={() => liftDialog(false)}
      data-testid="appshot-library-tile"
      data-variant={item.variant}
      className="group relative flex cursor-grab flex-col overflow-hidden rounded-lg border border-border bg-background active:cursor-grabbing"
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={busy}
        aria-label={`${t("appshot_editor.library_edit")}: ${where}`}
        className="flex aspect-[16/10] w-full items-center justify-center overflow-hidden bg-secondary/60 p-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-border-strong"
      >
        <img
          ref={imageRef}
          src={appshotLibraryImageUrl(item, true)}
          alt={t("appshots.preview_alt").replace("{0}", where)}
          loading="lazy"
          draggable={false}
          className="max-h-full max-w-full rounded-sm object-contain shadow-sm ring-1 ring-border transition-opacity group-hover:opacity-90"
        />
      </button>
      {edited && (
        <span className="pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-accent-foreground shadow-sm">
          <PenLine className="h-3 w-3" aria-hidden />
          {t("appshot_editor.library_edited_badge")}
        </span>
      )}
      <div className="absolute right-1.5 top-1.5 flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        {nativeDrag && (
          <QuickTooltip content={t("appshot_editor.library_drag_out")} side="top">
            <span
              role="button"
              tabIndex={-1}
              aria-label={t("appshot_editor.library_drag_out")}
              data-testid="appshot-library-drag-out"
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.stopPropagation();
                startNativeFileDrag(item.path);
              }}
              className="inline-flex h-7 w-7 cursor-grab items-center justify-center rounded-md border border-border bg-card text-muted-foreground shadow-sm hover:text-foreground"
            >
              <GripVertical className="h-3.5 w-3.5" aria-hidden />
            </span>
          </QuickTooltip>
        )}
        <QuickTooltip
          content={t(
            edited
              ? "appshot_editor.library_delete_edit_hint"
              : "appshot_editor.library_delete_original_hint",
          )}
          side="top"
        >
          <button
            type="button"
            onClick={onDelete}
            disabled={busy}
            aria-label={t("appshot_editor.library_delete")}
            data-testid="appshot-library-delete"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border bg-card text-muted-foreground shadow-sm hover:text-destructive focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
          </button>
        </QuickTooltip>
      </div>
      <div className="min-w-0 px-3 py-2">
        <p className="truncate text-sm font-medium text-foreground">{where}</p>
        <p className="truncate text-xs text-muted-foreground">
          {[when(item), `${item.width} × ${item.height}`].join(" · ")}
        </p>
      </div>
    </li>
  );
}

export function AppshotLibrary({
  enabled,
  refreshKey,
}: {
  /** `[appshot].library`; `undefined` while the settings load or on an older backend. */
  enabled: boolean | undefined;
  /** Changes whenever a new appshot was taken or an edit replaced one. */
  refreshKey: string;
}) {
  const t = useT();
  // The gallery's strings live in the editor's locale chunk.
  const ready = useLocaleChunk("appshot_editor");
  const pushToast = useEventStore((s) => s.pushToast);
  const openEditor = useAppshotEditor((s) => s.open);
  const [items, setItems] = useState<AppshotLibraryItem[] | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  // Folded: exactly ROWS full rows. Unfolded: `limit` tiles, PAGE more per click.
  const [expanded, setExpanded] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [grid, setGrid] = useState<HTMLUListElement | null>(null);
  const columns = useGridColumns(grid);
  const [busyKey, setBusyKey] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const confirmTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await fetchAppshotLibrary();
      setItems(body.items);
      setError("");
    } catch (failure) {
      setError((failure as Error).message);
      setItems((current) => current ?? []);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // The editor's own window saves while this page waits behind it; coming
  // back to the app shows the edit.
  useEffect(() => {
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  useEffect(
    () => () => {
      if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
    },
    [],
  );

  // Backstops for a drag whose `dragend` never reaches the tile (the drop
  // landed in another window, or the tile re-rendered): any drop or drag end
  // on the page, any pointer or key event afterwards (none fire during a
  // drag), and leaving the window all bring the dialog back. Unmounting too.
  useEffect(() => {
    const settle = () => {
      if (document.documentElement.dataset.appshotDrag) liftDialog(false);
    };
    const events = ["drop", "dragend", "pointermove", "pointerdown", "keydown", "blur"] as const;
    for (const name of events) window.addEventListener(name, settle, true);
    return () => {
      for (const name of events) window.removeEventListener(name, settle, true);
      liftDialog(false);
    };
  }, []);

  const editedCount = useMemo(
    () => (items ?? []).filter((item) => item.variant === "edited").length,
    [items],
  );
  const shown = useMemo(
    () => (items ?? []).filter((item) => filter === "all" || item.variant === "edited"),
    [items, filter],
  );

  const open = useCallback(
    async (item: AppshotLibraryItem) => {
      setBusyKey(tileKey(item));
      try {
        const result = await openAppshotLibraryItem(item);
        if (!result.window) openEditor(result.id);
      } catch (failure) {
        pushToast(
          "error",
          t("appshot_editor.library_open_failed").replace("{0}", (failure as Error).message),
        );
        void load();
      } finally {
        setBusyKey("");
      }
    },
    [load, openEditor, pushToast, t],
  );

  const remove = useCallback(
    async (item: AppshotLibraryItem) => {
      setBusyKey(tileKey(item));
      try {
        await deleteAppshotLibraryItem(item);
      } catch (failure) {
        pushToast("error", (failure as Error).message);
      } finally {
        setBusyKey("");
        void load();
      }
    },
    [load, pushToast],
  );

  const clearAll = useCallback(async () => {
    if (!confirmClear) {
      // Two presses, no dialog: the first one only arms the button.
      setConfirmClear(true);
      confirmTimer.current = window.setTimeout(() => setConfirmClear(false), 4000);
      return;
    }
    if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
    setConfirmClear(false);
    setClearing(true);
    try {
      await clearAppshotLibrary();
      pushToast("success", t("appshot_editor.library_deleted_all"));
    } catch (failure) {
      pushToast("error", (failure as Error).message);
    } finally {
      setClearing(false);
      void load();
    }
  }, [confirmClear, load, pushToast, t]);

  const total = items?.length ?? 0;
  const empty = items !== null && shown.length === 0;
  const folded = columns * ROWS;
  const visible = expanded ? Math.max(limit, folded) : folded;

  return (
    <section
      data-testid="appshot-library"
      className="mt-5 rounded-xl border border-border bg-card p-5"
      aria-labelledby="appshot-library-title"
    >
      <style>{LIFT_DIALOG_CSS}</style>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-[min(100%,16rem)] flex-1">
          <p id="appshot-library-title" className="text-base font-medium text-foreground">
            {t("appshot_editor.library_title")}
            {total > 0 && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                {t("appshot_editor.library_count").replace("{0}", String(total))}
              </span>
            )}
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {enabled === false
              ? t("appshot_editor.library_off")
              : t("appshot_editor.library_subtitle")}
          </p>
        </div>
        {total > 0 && (
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <div role="tablist" className="flex items-center rounded-lg bg-secondary p-0.5">
              {(["all", "edited"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={filter === value}
                  data-testid={`appshot-library-filter-${value}`}
                  onClick={() => {
                    setFilter(value);
                    setExpanded(false);
                    setLimit(PAGE);
                  }}
                  className={cn(
                    "h-7 rounded-md px-3 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                    filter === value
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {value === "all"
                    ? t("appshot_editor.library_filter_all")
                    : `${t("appshot_editor.library_filter_edited")} ${editedCount}`}
                </button>
              ))}
            </div>
            <Button
              type="button"
              variant={confirmClear ? "destructive" : "ghost"}
              size="sm"
              disabled={clearing}
              onClick={() => void clearAll()}
              data-testid="appshot-library-clear"
            >
              {clearing ? <Loader2 className="animate-spin" aria-hidden /> : <Trash2 aria-hidden />}
              {confirmClear
                ? t("appshot_editor.library_delete_all_confirm")
                : t("appshot_editor.library_delete_all")}
            </Button>
          </div>
        )}
      </div>

      {error && (
        <p className="mt-3 text-sm text-destructive" role="alert">
          {t("appshot_editor.library_load_failed").replace("{0}", error)}
        </p>
      )}

      {items === null || !ready ? (
        <div className="flex h-32 items-center justify-center" role="status" aria-busy="true">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
        </div>
      ) : empty ? (
        !error && (
          <p className="mt-4 rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            {filter === "edited"
              ? t("appshot_editor.library_empty_edited")
              : t("appshot_editor.library_empty")}
          </p>
        )
      ) : (
        <>
          <ul ref={setGrid} className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(min(100%,12rem),1fr))] gap-3">
            {shown.slice(0, visible).map((item) => (
              <Tile
                key={tileKey(item)}
                item={item}
                busy={busyKey === tileKey(item)}
                onOpen={() => void open(item)}
                onDelete={() => void remove(item)}
              />
            ))}
          </ul>
          <div className="mt-3 flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">{t("appshot_editor.library_drag_hint")}</p>
            <div className="flex shrink-0 items-center gap-2">
              {expanded && shown.length > folded && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setExpanded(false);
                    setLimit(PAGE);
                    grid?.scrollIntoView?.({ block: "nearest" });
                  }}
                  data-testid="appshot-library-less"
                >
                  {t("appshot_editor.library_show_less")}
                </Button>
              )}
              {shown.length > visible && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    if (expanded) setLimit((value) => value + PAGE);
                    else {
                      setExpanded(true);
                      setLimit(Math.max(PAGE, folded));
                    }
                  }}
                  data-testid="appshot-library-more"
                >
                  {t("appshot_editor.library_show_more")}
                </Button>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

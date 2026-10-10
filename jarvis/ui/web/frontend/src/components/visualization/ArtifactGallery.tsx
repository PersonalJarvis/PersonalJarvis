import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  FileImage,
  FileText,
  Files,
  Globe,
  LayoutGrid,
  Loader2,
  PenTool,
  Search,
  Workflow,
} from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import { useThemeValue } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import { endMissionDrag, startMissionDrag } from "@/lib/missionDnd";
import { openExternalUrl } from "@/lib/openExternal";
import { useEventStore } from "@/store/events";
import {
  DeleteOutputError,
  artifactDownloadUrl,
  deleteOutput,
  revealArtifact,
  revealOutput,
  useOutputsCapabilities,
  type OutputStatus,
} from "@/hooks/useOutputs";
import { artifactPageUrl, type VisualArtifact, type VisualKind } from "@/hooks/useVisualArtifacts";
import {
  RAIL_FILTERS,
  relativeWhen,
  rowRequest,
  rowTitle,
  rowWhen,
  type GalleryGroup,
  type GallerySort,
  type RailFilter,
  type RailRow,
} from "@/components/visualization/galleryModel";
import {
  ArtifactCardMenu,
  ConfirmDeleteArtifact,
  rowIsWorking,
  type ArtifactMenuState,
} from "@/components/visualization/ArtifactCardMenu";

/**
 * The Artifacts library — every artifact as a card with the artifact itself
 * drawn on it, the way a gallery of finished work should look.
 *
 * A page's card frames the live page (scaled down, scripts running, network
 * shut — the same sandbox as the stage), a picture shows the picture, a PDF
 * its first page, and a run that drew nothing gets a composed text cover
 * from its own answer. Previews mount only while their card is near the
 * viewport, so a library of a hundred dashboards costs what a screenful
 * costs — and a WebGL map scrolled out of view gives its context back
 * (AP-32).
 */

/** What a card's status dot means — the run vocabulary, one language. */
const STATUS_DOT: Record<OutputStatus, string> = {
  success: "bg-success",
  error: "bg-destructive",
  running: "bg-success animate-pulse",
  cancelled: "bg-warning",
  unknown: "bg-muted-foreground",
};

const KIND_ICON: Record<VisualKind, typeof Globe> = {
  page: Globe,
  image: FileImage,
  vector: PenTool,
  document: FileText,
};

const FILTER_ICON: Record<RailFilter, typeof Globe> = {
  all: LayoutGrid,
  pages: Globe,
  images: FileImage,
  documents: FileText,
  outputs: Workflow,
};

/** The width a page is laid out at before it is scaled onto its card. */
const PAGE_VIRTUAL_WIDTH = 1280;
const DOCUMENT_VIRTUAL_WIDTH = 900;
/** CSS px a framed page may spend on its own vertical scrollbar. */
const SCROLLBAR_ALLOWANCE = 24;

/* ------------------------------------------------------------------------- */

/** The library's left rail: the categories, each with its count. */
export function GalleryCategoryRail({
  filter,
  counts,
  loading,
  onChange,
}: {
  filter: RailFilter;
  counts: Record<RailFilter, number>;
  /** No answer yet — counts are left out rather than shown as zeros. */
  loading: boolean;
  onChange: (next: RailFilter) => void;
}) {
  const t = useT();
  return (
    <nav
      aria-label={t("visualization.rail_filter")}
      className="flex w-56 shrink-0 flex-col gap-0.5 border-r border-border bg-sidebar p-3"
      data-testid="visualization-filter"
    >
      <p className="px-2 pb-2 pt-1 text-sm font-medium text-foreground-faint">
        {t("visualization.nav_library")}
      </p>
      {RAIL_FILTERS.map((id) => {
        const Icon = FILTER_ICON[id];
        const active = filter === id;
        return (
          <button
            key={id}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(id)}
            data-testid={`visualization-filter-${id}`}
            className={cn(
              "flex h-9 items-center gap-2.5 rounded-md px-2 text-left text-base transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "jarvis-nav-active bg-secondary font-medium text-foreground-strong"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 truncate">{t(`visualization.filter_${id}`)}</span>
            {!loading && (
              <span className="text-sm tabular-nums text-foreground-faint">{counts[id]}</span>
            )}
          </button>
        );
      })}
    </nav>
  );
}

/** Search box and sort order — the library's own toolbar. */
export function GalleryControls({
  query,
  onQuery,
  sort,
  onSort,
}: {
  query: string;
  onQuery: (next: string) => void;
  sort: GallerySort;
  onSort: (next: GallerySort) => void;
}) {
  const t = useT();
  return (
    <div className="flex items-center gap-2">
      <div className="relative w-64">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-faint"
          aria-hidden
        />
        <Input
          type="search"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder={t("visualization.search_placeholder")}
          aria-label={t("visualization.search_placeholder")}
          data-testid="visualization-search"
          className="pl-9"
        />
      </div>
      <BrandedSelect
        value={sort}
        onValueChange={(value) => onSort(value as GallerySort)}
        ariaLabel={t("visualization.sort_label")}
        testId="visualization-sort"
        className="w-44"
        options={[
          { value: "newest", label: t("visualization.sort_newest") },
          { value: "oldest", label: t("visualization.sort_oldest") },
          { value: "name", label: t("visualization.sort_name") },
        ]}
      />
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/** The grid of cards, under its date headings. */
export function ArtifactGallery({
  groups,
  loading,
  searching,
  footer,
  onOpen,
}: {
  groups: GalleryGroup[];
  loading: boolean;
  /** A search is narrowing the library — an empty result is "no match", not "nothing". */
  searching: boolean;
  footer?: ReactNode;
  onOpen: (row: RailRow) => void;
}) {
  const t = useT();
  const empty = groups.every((group) => group.rows.length === 0);
  const actions = useCardActions();

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="w-full px-8 pb-10 pt-2" data-testid="visualization-artifacts">
        {loading && empty ? (
          <CardGrid>
            {Array.from({ length: 6 }, (_, index) => (
              <SkeletonCard key={index} />
            ))}
          </CardGrid>
        ) : empty ? (
          <div className="flex min-h-80 items-center justify-center" data-testid="visualization-no-matches">
            <EmptyState
              icon={<Search aria-hidden />}
              title={t(searching ? "visualization.no_matches_title" : "visualization.category_empty_title")}
              description={t(
                searching ? "visualization.no_matches_body" : "visualization.category_empty_body",
              )}
            />
          </div>
        ) : (
          groups.map((group) => (
            <section key={group.id} className="pt-6" aria-label={t(`visualization.group_${group.id}`)}>
              <h2 className="pb-3 text-lg font-semibold text-foreground-strong">
                {t(`visualization.group_${group.id}`)}
              </h2>
              <CardGrid>
                {group.rows.map((row) => (
                  <ArtifactCard
                    key={row.key}
                    row={row}
                    onOpen={onOpen}
                    onMenu={actions.openMenu}
                    onDeleteKey={actions.askDelete}
                  />
                ))}
              </CardGrid>
            </section>
          ))
        )}
        {footer}
      </div>
      {actions.menu && (
        <ArtifactCardMenu
          row={actions.menu.row}
          x={actions.menu.x}
          y={actions.menu.y}
          canReveal={actions.canReveal}
          onDismiss={actions.closeMenu}
          onOpen={() => actions.run(onOpen)}
          onOpenExternal={() => actions.run(actions.openExternal)}
          onDownload={() => actions.run(actions.download)}
          onReveal={() => actions.run(actions.reveal)}
          onDelete={() => actions.run(actions.askDelete)}
        />
      )}
      {actions.pendingDelete && (
        <ConfirmDeleteArtifact
          row={actions.pendingDelete}
          busy={actions.deleting}
          onCancel={actions.cancelDelete}
          onConfirm={() => void actions.confirmDelete()}
        />
      )}
    </ScrollArea>
  );
}

/**
 * What a card's right-click menu does. One menu and one pending delete for
 * the whole gallery — the card only reports where it was clicked.
 */
function useCardActions() {
  const t = useT();
  const theme = useThemeValue();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const capabilities = useOutputsCapabilities();
  const [menu, setMenu] = useState<ArtifactMenuState | null>(null);
  const [pendingDelete, setPendingDelete] = useState<RailRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  const openMenu = useCallback((row: RailRow, x: number, y: number) => setMenu({ row, x, y }), []);
  const closeMenu = useCallback(() => setMenu(null), []);

  /** Close the menu, then act on the row it was opened for. */
  const run = useCallback(
    (action: (row: RailRow) => void) => {
      if (!menu) return;
      setMenu(null);
      action(menu.row);
    },
    [menu],
  );

  const openExternal = useCallback(
    (row: RailRow) => {
      if (row.kind !== "visual") return;
      const { visual } = row;
      const url =
        visual.kind === "page" ? `${artifactPageUrl(visual.slug, visual.path)}?theme=${theme}` : visual.url;
      void openExternalUrl(`${window.location.origin}${url}`);
    },
    [theme],
  );

  const download = useCallback((row: RailRow) => {
    if (row.kind !== "visual") return;
    const link = document.createElement("a");
    link.href = artifactDownloadUrl(row.visual.slug, row.visual.path);
    link.download = row.visual.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }, []);

  const reveal = useCallback(
    (row: RailRow) => {
      const request =
        row.kind === "visual"
          ? revealArtifact(row.visual.slug, row.visual.path)
          : revealOutput(row.run.slug);
      request.catch(() => pushToast("error", t("visualization.reveal_failed")));
    },
    [pushToast, t],
  );

  const askDelete = useCallback((row: RailRow) => {
    if (!rowIsWorking(row)) setPendingDelete(row);
  }, []);
  const cancelDelete = useCallback(() => {
    if (!deleting) setPendingDelete(null);
  }, [deleting]);

  const confirmDelete = useCallback(async () => {
    const row = pendingDelete;
    if (!row || row.kind === "build") return;
    const slug = row.kind === "visual" ? row.visual.slug : row.run.slug;
    setDeleting(true);
    try {
      await deleteOutput(slug, row.kind === "visual" ? row.visual.path : null);
      setPendingDelete(null);
      pushToast("success", t("visualization.deleted"));
    } catch (error) {
      const running = error instanceof DeleteOutputError && error.status === 409;
      pushToast("error", t(running ? "visualization.delete_running" : "visualization.delete_failed"));
    } finally {
      setDeleting(false);
      void queryClient.invalidateQueries({ queryKey: ["outputs"] });
      void queryClient.invalidateQueries({ queryKey: ["output-artifacts", slug] });
    }
  }, [pendingDelete, pushToast, queryClient, t]);

  return {
    menu,
    openMenu,
    closeMenu,
    run,
    canReveal: capabilities.data?.native_file_actions === true,
    openExternal,
    download,
    reveal,
    pendingDelete,
    deleting,
    askDelete,
    cancelDelete,
    confirmDelete,
  };
}

function CardGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(20rem,1fr))] gap-5">{children}</div>
  );
}

function SkeletonCard() {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card" aria-hidden>
      <div className="aspect-[16/10] animate-pulse bg-foreground/10" />
      <div className="flex items-center gap-3 p-3">
        <div className="h-9 w-9 shrink-0 animate-pulse rounded-lg bg-foreground/10" />
        <div className="flex-1 space-y-2">
          <div className="h-3.5 w-2/3 animate-pulse rounded bg-foreground/15" />
          <div className="h-3 w-1/3 animate-pulse rounded bg-foreground/10" />
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/** The kind word under a card's title. */
function kindLabel(row: RailRow, t: (key: string) => string): string {
  if (row.kind === "build") return t("visualization.building");
  if (row.kind === "run") return t("visualization.output_kind");
  return t(`visualization.kind_${row.visual.kind}`);
}

const ROW_TESTID: Record<RailRow["kind"], string> = {
  build: "visualization-building-row",
  visual: "visualization-artifact-row",
  run: "visualization-run-row",
};

function ArtifactCard({
  row,
  onOpen,
  onMenu,
  onDeleteKey,
}: {
  row: RailRow;
  onOpen: (row: RailRow) => void;
  onMenu: (row: RailRow, x: number, y: number) => void;
  onDeleteKey: (row: RailRow) => void;
}) {
  const t = useT();
  const language = useRunLocale();
  const title = rowTitle(row);
  const request = rowRequest(row);
  const status: OutputStatus =
    row.kind === "visual" ? (row.visual.status ?? "unknown") : (row.run.status ?? "unknown");
  const KindIcon =
    row.kind === "visual" ? KIND_ICON[row.visual.kind] : row.kind === "build" ? Loader2 : Workflow;
  // Every card can be dragged onto the Jarvis dock — the run is what the dock
  // takes, whichever of its artifacts the card happens to show.
  const dragRun = row.run;
  const dragProps = dragRun
    ? {
        draggable: true,
        onDragStart: (e: DragEvent) => startMissionDrag(e, dragRun),
        onDragEnd: endMissionDrag,
      }
    : {};

  const onContextMenu = (event: ReactMouseEvent<HTMLButtonElement>) => {
    // The app-wide Cut/Copy/Paste menu lives on document; a card offers its
    // own actions instead. The menu key reports no pointer, so the menu then
    // opens at the card's corner.
    event.preventDefault();
    event.stopPropagation();
    if (event.clientX === 0 && event.clientY === 0) {
      const box = event.currentTarget.getBoundingClientRect();
      onMenu(row, box.left + 16, box.top + 16);
    } else {
      onMenu(row, event.clientX, event.clientY);
    }
  };

  return (
    <button
      type="button"
      onClick={() => onOpen(row)}
      onContextMenu={onContextMenu}
      onKeyDown={(event) => {
        if (event.key === "Delete" && !event.altKey && !event.ctrlKey && !event.metaKey) {
          event.preventDefault();
          onDeleteKey(row);
        }
      }}
      data-testid={ROW_TESTID[row.kind]}
      data-kind={row.kind === "visual" ? row.visual.kind : undefined}
      data-status={row.kind === "run" ? status : undefined}
      className={cn(
        "group flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card text-left",
        "transition-[border-color,box-shadow] duration-150 hover:border-border-strong hover:shadow-lg",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
      {...dragProps}
    >
      <div className="relative aspect-[16/10] w-full overflow-hidden border-b border-border bg-background">
        <CardCover row={row} />
        {status !== "success" && status !== "unknown" && row.kind !== "build" && (
          <span className="absolute bottom-2 right-2 inline-flex items-center gap-1.5 rounded-md border border-border bg-popover/90 px-2 py-0.5 text-sm text-foreground backdrop-blur">
            <span className={cn("h-1.5 w-1.5 rounded-full", STATUS_DOT[status])} aria-hidden />
            {t(`visualization.status_word_${status}`)}
          </span>
        )}
      </div>
      <div className="flex min-w-0 items-center gap-3 p-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
          <KindIcon className={cn("h-4 w-4", row.kind === "build" && "animate-spin")} aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-base font-semibold text-foreground-strong">{title}</span>
          <span className="block truncate text-sm text-muted-foreground">
            {[kindLabel(row, t), relativeWhen(rowWhen(row), Date.now(), language)]
              .filter(Boolean)
              .join(" · ")}
          </span>
          {row.kind !== "run" && request && request !== title && (
            <span className="mt-0.5 block truncate text-sm text-foreground-faint">{request}</span>
          )}
        </span>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------------- */

/** What fills the top of a card: the artifact itself, or a composed cover. */
function CardCover({ row }: { row: RailRow }) {
  const theme = useThemeValue();
  if (row.kind === "build") return <BuildingCover />;
  if (row.kind === "run") {
    return (
      <TextCover
        title={rowTitle(row)}
        body={row.run.summary?.trim() || row.run.terminal_reason || row.run.error || ""}
        files={row.run.artifact_count ?? 0}
      />
    );
  }
  const { visual } = row;
  if (visual.kind === "image" || visual.kind === "vector") return <ImageCover visual={visual} />;
  if (visual.kind === "page") {
    return (
      <ScaledFrame
        src={`${artifactPageUrl(visual.slug, visual.path)}?theme=${theme}`}
        title={visual.title}
        virtualWidth={PAGE_VIRTUAL_WIDTH}
        // allow-scripts WITHOUT allow-same-origin, exactly as on the stage.
        sandbox="allow-scripts"
      />
    );
  }
  return (
    <ScaledFrame
      src={`${visual.url}#toolbar=0&navpanes=0&scrollbar=0&view=FitH`}
      title={visual.title}
      virtualWidth={DOCUMENT_VIRTUAL_WIDTH}
      // Empty sandbox: the browser's own PDF viewer still draws the file.
      sandbox=""
      white
    />
  );
}

/**
 * True while the element is on, or about to scroll onto, the screen. Without
 * an IntersectionObserver (a test DOM) nothing is ever mounted — a cover then
 * stays its quiet placeholder, never a dozen live frames.
 */
function useNearViewport(ref: RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => setNear(entries.some((entry) => entry.isIntersecting)),
      { rootMargin: "400px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return near;
}

/** The element's size, kept current. */
function useBoxSize(ref: RefObject<HTMLElement | null>): { width: number; height: number } {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/**
 * A page laid out at desktop width and scaled onto the card — the dashboard
 * looks like the dashboard, not like its mobile layout. Inert: no pointer,
 * no focus, hidden from assistive tech (the card button carries the name).
 */
function ScaledFrame({
  src,
  title,
  virtualWidth,
  sandbox,
  white = false,
}: {
  src: string;
  title: string;
  virtualWidth: number;
  sandbox: string;
  white?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const near = useNearViewport(box);
  const { width, height } = useBoxSize(box);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => setLoaded(false), [src]);
  const scale = width > 0 ? width / virtualWidth : 0;

  return (
    <div ref={box} className="absolute inset-0" aria-hidden>
      {!loaded && <div className="absolute inset-0 animate-pulse bg-foreground/5" />}
      {near && scale > 0 && (
        <iframe
          key={src}
          src={src}
          title={title}
          tabIndex={-1}
          sandbox={sandbox}
          loading="lazy"
          onLoad={() => setLoaded(true)}
          data-testid="artifact-card-frame"
          className={cn(
            "pointer-events-none absolute left-0 top-0 origin-top-left border-0 transition-opacity duration-300",
            white ? "bg-white" : "bg-background",
            loaded ? "opacity-100" : "opacity-0",
          )}
          style={{
            // A little wider than the box shows: the page's own scrollbar
            // falls outside the card instead of striping its edge.
            width: virtualWidth + SCROLLBAR_ALLOWANCE,
            height: height / scale,
            transform: `scale(${scale})`,
          }}
        />
      )}
    </div>
  );
}

function ImageCover({ visual }: { visual: VisualArtifact }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <IconCover icon={<FileImage className="h-8 w-8" aria-hidden />} />;
  return (
    <img
      src={visual.url}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={cn(
        "absolute inset-0 h-full w-full",
        visual.kind === "vector" ? "object-contain p-6" : "object-cover object-top",
      )}
    />
  );
}

function IconCover({ icon }: { icon: ReactNode }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center text-foreground-faint">{icon}</div>
  );
}

/** A run that drew nothing: its own answer, set like the first page of a document. */
function TextCover({ title, body, files }: { title: string; body: string; files: number }) {
  const t = useT();
  return (
    <div className="absolute inset-0 flex flex-col gap-2 bg-sidebar px-5 pb-4 pt-5">
      <p className="line-clamp-2 shrink-0 text-base font-semibold leading-snug text-foreground-strong">{title}</p>
      {body && (
        <p className="min-h-0 flex-1 overflow-hidden text-sm leading-relaxed text-muted-foreground [mask-image:linear-gradient(to_bottom,black_60%,transparent)]">
          {body}
        </p>
      )}
      {files > 0 && (
        <p className="mt-auto inline-flex shrink-0 items-center gap-1.5 text-sm text-foreground-faint">
          <Files className="h-3.5 w-3.5" aria-hidden />
          {files === 1 ? t("visualization.file_one") : t("visualization.files_count").replace("{0}", String(files))}
        </p>
      )}
    </div>
  );
}

/** A page being written: a page-shaped placeholder that says so. */
function BuildingCover() {
  const t = useT();
  return (
    <div className="absolute inset-0 flex flex-col gap-3 bg-sidebar p-5">
      <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/15" />
      <div className="h-3 w-3/4 animate-pulse rounded bg-foreground/10" />
      <div className="grid flex-1 grid-cols-3 gap-2 pt-1">
        <div className="animate-pulse rounded-md bg-foreground/10" />
        <div className="animate-pulse rounded-md bg-foreground/10" />
        <div className="animate-pulse rounded-md bg-foreground/10" />
      </div>
      <p className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        {t("visualization.building")}
      </p>
    </div>
  );
}

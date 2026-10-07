import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Code2,
  Download,
  ExternalLink,
  Eye,
  Files,
  FolderOpen,
  Loader2,
  RefreshCw,
  Shapes,
  ShieldCheck,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { OutputPreview } from "@/components/visualization/OutputPreview";
import { RunGraphPanel } from "@/components/visualization/RunGraphPanel";
import {
  RunActions,
  RunFiles,
  RunStatusBadge,
  deliverableFiles,
} from "@/components/visualization/RunPanels";
import {
  ArtifactGallery,
  GalleryCategoryRail,
  GalleryControls,
} from "@/components/visualization/ArtifactGallery";
import {
  buildRailRows,
  countByFilter,
  filterRailRows,
  groupRailRows,
  parseArtifactUtterance,
  runTitle,
  runWhen,
  searchRailRows,
  sortRailRows,
  type GallerySort,
  type RailFilter,
  type RailRow,
} from "@/components/visualization/galleryModel";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import { artifactKind, isTextKind } from "@/lib/artifactKind";
import { CapacityDecisionPanel } from "@/components/missions/CapacityDecisionPanel";
import { cleanRequest } from "@/lib/runRequest";
import { useEventStore } from "@/store/events";
import { openExternalUrl } from "@/lib/openExternal";
import {
  artifactDownloadUrl,
  artifactOpenUrl,
  revealArtifact,
  useArtifactFile,
  useArtifactsForOutput,
  useOutputsCapabilities,
  useOutputsList,
  type ArtifactSummary,
  type OutputSummary,
} from "@/hooks/useOutputs";
import {
  artifactPageUrl,
  missionMapUrl,
  toVisuals,
  useVisualArtifacts,
  visualId,
  type VisualArtifact,
} from "@/hooks/useVisualArtifacts";

export { buildRailRows, filterRailRows, parseArtifactUtterance };
export type { RailFilter };

/**
 * The Artifacts section — everything a run produced, as a library first and
 * a stage second.
 *
 * An artifact is the thing the user asked to LOOK AT: the dashboard, the
 * report, the diagram a background agent wrote as one self-contained HTML
 * file (`create_artifact`), or any image/PDF a worker left behind. Since
 * 2026-10-01 the section opens on a GALLERY: every artifact as a card with
 * the artifact itself drawn on it (a live, scaled page; the picture; a PDF's
 * first page), grouped by day, narrowed by category (pages, images,
 * documents, outputs) and by search, sorted newest, oldest or by name. A
 * click opens the STAGE: the page full-size in its sandbox, the source one
 * tab away, every file of the run behind "Files", and the n8n-style run
 * graph behind "Run"; back, previous and next walk the same list.
 *
 * Every other run lands here too (the Outputs section folded in on
 * 2026-08-23): a run that drew no page or picture is an "Output" card with
 * its own answer as the cover, and its stage composes a page from what it
 * left behind (`OutputPreview`).
 *
 * It owns no data: runs come from `/api/outputs`, files from the artifact
 * listing (`useVisualArtifacts`, `useArtifactsForOutput`), a page's source
 * from `/raw`. A run that is still building its artifact shows as a
 * "building…" card the gallery follows until the page lands — the listings
 * of running runs poll, nothing else does.
 *
 * Detachable (`DETACHABLE_VIEWS` in jarvis/ui/desktop_app.py): an artifact is
 * the thing people put on a second monitor.
 */

type StageMode = "preview" | "code" | "files" | "run";

/**
 * What is open: an artifact (`path`) or a whole run (`null`), "latest" for
 * whatever leads the library (another surface asked for the newest), or
 * nothing — the gallery.
 */
type Selection = { slug: string; path: string | null } | "latest" | null;

/** What the stage shows: a run (always), and its artifact when it has one. */
interface StageTarget {
  run: OutputSummary | null;
  visual: VisualArtifact | null;
  /** The run is a `create_artifact` build still writing its page. */
  building: boolean;
}

const FILTER_KEY = "jarvis.artifacts.rail-filter";
const SORT_KEY = "jarvis.artifacts.sort";

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const stored = window.localStorage.getItem(key);
    if (stored !== null && (allowed as readonly string[]).includes(stored)) return stored as T;
  } catch {
    // Storage can be unavailable (private window, blocked site data) — the default then.
  }
  return fallback;
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // A remembered category or order is a convenience, never a requirement.
  }
}

const FILTERS: readonly RailFilter[] = ["all", "pages", "images", "documents", "outputs"];
const SORTS: readonly GallerySort[] = ["newest", "oldest", "name"];

/** The stage's timestamp — what tells two same-named artifacts apart. */
function formatWhen(seconds: number): string {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The card's key for what the stage shows. */
function stageKey(target: StageTarget): string | null {
  if (target.building && target.run) return `build:${target.run.slug}`;
  if (target.visual) return visualId(target.visual);
  if (target.run) return `run:${target.run.slug}`;
  return null;
}

function selectionOf(row: RailRow): { slug: string; path: string | null } {
  return row.kind === "visual"
    ? { slug: row.visual.slug, path: row.visual.path }
    : { slug: row.run.slug, path: null };
}

/** A key press that belongs to a text field, not to the gallery's navigation. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

export function VisualizationView() {
  const t = useT();
  const outputs = useOutputsList();
  const runs = useMemo(() => outputs.data ?? [], [outputs.data]);
  const gallery = useVisualArtifacts();
  const visuals = gallery.visuals;

  /* Runs still writing their artifact — recognised by the brief's lead line,
   * so an unrelated running mission does not pose as a page in the making. */
  const building = useMemo(
    () =>
      runs.filter(
        (run) => run.status === "running" && parseArtifactUtterance(run.utterance) !== null,
      ),
    [runs],
  );

  const allRows = useMemo(() => buildRailRows(runs, visuals, building), [runs, visuals, building]);
  const counts = useMemo(() => countByFilter(allRows), [allRows]);

  const [filter, setFilterState] = useState<RailFilter>(() => readStored(FILTER_KEY, FILTERS, "all"));
  const setFilter = useCallback((next: RailFilter) => {
    setFilterState(next);
    store(FILTER_KEY, next);
  }, []);
  const [sort, setSortState] = useState<GallerySort>(() => readStored(SORT_KEY, SORTS, "newest"));
  const setSort = useCallback((next: GallerySort) => {
    setSortState(next);
    store(SORT_KEY, next);
  }, []);
  const [query, setQuery] = useState("");

  /* The library as shown: category, then search, then order. The stage's
   * previous / next walk exactly this list. */
  const rows = useMemo(
    () => sortRailRows(searchRailRows(filterRailRows(allRows, filter), query), sort),
    [allRows, filter, query, sort],
  );
  const groups = useMemo(() => groupRailRows(rows, sort, Date.now()), [rows, sort]);
  /* The order the cards appear in on screen — groups pull work in progress
   * to the top, so this is not always `rows`. */
  const ordered = useMemo(() => groups.flatMap((group) => group.rows), [groups]);

  /* A `?run=<slug>` in the URL opens that run's newest artifact — what makes a
   * detached window or a pasted link open on the page it talks about. Read
   * once at mount; clicks own it after. */
  const [selection, setSelection] = useState<Selection>(() => {
    const slug = new URLSearchParams(window.location.search).get("run");
    return slug ? { slug, path: null } : null;
  });

  /*
   * Another surface asked for something to be staged ("show visuals" on the
   * agent strip, the `create_artifact` tool via NavigateSidebar). A target
   * names a `visualId` (slug::path) or "latest"; see VisualStageRequest.
   */
  const visualStage = useEventStore((s) => s.visualStage);
  useEffect(() => {
    if (visualStage === null) return;
    if (visualStage.target === "latest") {
      setSelection("latest");
      return;
    }
    const separator = visualStage.target.indexOf("::");
    if (separator > 0) {
      setSelection({
        slug: visualStage.target.slice(0, separator),
        path: visualStage.target.slice(separator + 2),
      });
    }
  }, [visualStage]);

  /*
   * What the stage shows. The card the user opened — an artifact, or a run
   * (its newest artifact once it has one, the run itself otherwise). For
   * "latest", whatever leads the whole library: the newest build in
   * progress, else the newest thing there is.
   */
  const target: StageTarget | null = useMemo(() => {
    if (selection === null) return null;
    const bySlug = (slug: string) => runs.find((r) => r.slug === slug) ?? null;
    if (selection === "latest") {
      const first = allRows[0];
      if (!first) return { run: null, visual: null, building: false };
      if (first.kind === "build") return { run: first.run, visual: null, building: true };
      if (first.kind === "visual") return { run: first.run, visual: first.visual, building: false };
      return { run: first.run, visual: null, building: false };
    }
    if (selection.path !== null) {
      const visual = visuals.find((v) => v.slug === selection.slug && v.path === selection.path);
      if (visual) return { run: bySlug(visual.slug), visual, building: false };
    }
    const run = bySlug(selection.slug);
    const visual = visuals.find((v) => v.slug === selection.slug) ?? null;
    if (run === null && visual === null) return { run: null, visual: null, building: false };
    const isBuilding = run !== null && building.includes(run) && visual === null;
    return { run, visual, building: isBuilding };
  }, [selection, runs, visuals, building, allRows]);

  const refetch = useCallback(() => {
    void outputs.refetch();
    gallery.refetch();
  }, [outputs, gallery]);

  const open = useCallback((row: RailRow) => setSelection(selectionOf(row)), []);
  const back = useCallback(() => setSelection(null), []);

  /* Previous / next: the stage's place in the library as currently shown.
   * Opened from elsewhere (a link, "latest") and not in the current view,
   * the stage simply has no neighbours. */
  const activeKey = target ? stageKey(target) : null;
  const position = activeKey ? ordered.findIndex((row) => row.key === activeKey) : -1;
  const step = useCallback(
    (delta: number) => {
      if (position < 0) return;
      const next = ordered[position + delta];
      if (next) setSelection(selectionOf(next));
    },
    [ordered, position],
  );

  /* Escape goes back to the library, ←/→ walk it — unless a text field has
   * the keys. A framed page keeps its own keys (its events never reach us). */
  const staged = target !== null;
  useEffect(() => {
    if (!staged) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isTypingTarget(event.target)) return;
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "Escape") back();
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [staged, back, step]);

  const loading = outputs.isLoading || gallery.isLoading;
  const error = outputs.isError || gallery.isError;

  if (target !== null) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {target.building && target.run !== null ? (
            <>
              <StageNav
                onBack={back}
                position={position}
                total={ordered.length}
                onStep={step}
              />
              <RunCapacityDecision run={target.run} />
              <BuildingStage run={target.run} />
            </>
          ) : target.run === null && target.visual === null ? (
            <>
              <StageNav onBack={back} position={-1} total={0} onStep={step} />
              <EmptyStage loading={loading} error={error} />
            </>
          ) : (
            <Stage
              key={target.run?.slug ?? target.visual?.slug}
              run={target.run}
              visual={target.visual}
              onJumpToRun={(slug) => setSelection({ slug, path: null })}
              nav={
                <StageNav
                  onBack={back}
                  position={position}
                  total={ordered.length}
                  onStep={step}
                />
              }
            />
          )}
        </section>
      </div>
    );
  }

  const nothingAtAll = !loading && allRows.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Shapes aria-hidden />}
        title={t("visualization.title")}
        subtitle={t("visualization.subtitle")}
        right={
          <div className="flex items-center gap-2">
            {!nothingAtAll && (
              <GalleryControls query={query} onQuery={setQuery} sort={sort} onSort={setSort} />
            )}
            <Button
              variant="outline"
              onClick={refetch}
              disabled={loading}
              title={t("visualization.refresh")}
              aria-label={t("visualization.refresh")}
              data-testid="visualization-refresh"
            >
              {loading ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <RefreshCw aria-hidden />
              )}
            </Button>
          </div>
        }
      />

      <div className="flex min-h-0 flex-1">
        <GalleryCategoryRail
          filter={filter}
          counts={counts}
          loading={loading && allRows.length === 0}
          onChange={setFilter}
        />
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {nothingAtAll ? (
            <EmptyStage loading={false} error={error} />
          ) : (
            <ArtifactGallery
              groups={groups}
              loading={loading}
              searching={query.trim().length > 0}
              onOpen={open}
              footer={
                gallery.skippedRuns > 0 ? (
                  <p className="pt-8 text-sm text-foreground-faint">
                    {t("visualization.older_not_scanned").replace(
                      "{0}",
                      String(gallery.scannedRuns),
                    )}
                  </p>
                ) : null
              }
            />
          )}
        </section>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/** Back to the library, and the stage's place in it with previous / next. */
function StageNav({
  onBack,
  position,
  total,
  onStep,
}: {
  onBack: () => void;
  /** Index in the library as shown, or -1 when the open item is not in it. */
  position: number;
  total: number;
  onStep: (delta: number) => void;
}) {
  const t = useT();
  return (
    <div className="flex shrink-0 items-center gap-1 px-4 pt-3">
      <Button variant="ghost" size="sm" onClick={onBack} data-testid="visualization-back">
        <ArrowLeft className="mr-1.5 h-4 w-4" aria-hidden />
        {t("visualization.back")}
      </Button>
      {position >= 0 && total > 1 && (
        <div className="ml-auto flex items-center gap-1">
          <span className="px-2 text-sm tabular-nums text-foreground-faint">
            {t("visualization.position")
              .replace("{0}", String(position + 1))
              .replace("{1}", String(total))}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onStep(-1)}
            disabled={position === 0}
            title={t("visualization.previous")}
            aria-label={t("visualization.previous")}
            data-testid="visualization-previous"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onStep(1)}
            disabled={position >= total - 1}
            title={t("visualization.next")}
            aria-label={t("visualization.next")}
            data-testid="visualization-next"
          >
            <ChevronRight className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * The stage for one run: its artifact under Preview / Code when it drew one,
 * the composed output page under Preview and the primary file's source under
 * Code when it did not; every file under Files, the graph under Run. Keyed
 * by run in the caller, so a new run opens on Preview.
 *
 * A run outside the rail's scan window arrives without artifacts in hand; its
 * listing is read here (same cache entry the rail's scan fills) and the newest
 * page or picture in it goes on stage — an older dashboard is one click away
 * instead of "not scanned".
 */
function Stage({
  run,
  visual: pickedVisual,
  onJumpToRun,
  nav,
}: {
  run: OutputSummary | null;
  visual: VisualArtifact | null;
  onJumpToRun: (slug: string) => void;
  /** Back to the library and previous / next, above the toolbar. */
  nav: ReactNode;
}) {
  const slug = run?.slug ?? pickedVisual?.slug ?? null;
  const listing = useArtifactsForOutput(run !== null ? slug : null);
  const visual: VisualArtifact | null = useMemo(() => {
    if (pickedVisual) return pickedVisual;
    if (run === null) return null;
    const found = toVisuals(run, listing.data?.files ?? []);
    found.sort((a, b) => b.mtime - a.mtime);
    return found[0] ?? null;
  }, [pickedVisual, run, listing.data]);
  // The output's primary file — what Code shows and the toolbar's file
  // actions act on when the run drew no page: the first text deliverable.
  const primary: ArtifactSummary | null = useMemo(() => {
    if (visual !== null) return null;
    return primaryTextFile(listing.data?.files ?? []);
  }, [visual, listing.data]);

  const [mode, setMode] = useState<StageMode>("preview");
  // "Open in Files" on the output page lands the reader on that file.
  const [filesPath, setFilesPath] = useState<string | null>(null);
  const currentId = visual ? visualId(visual) : null;
  // A new artifact opens on its page, whatever tab the previous one was on; a
  // run that turns out to have one (its listing just arrived) moves to it too.
  useEffect(() => {
    setMode("preview");
  }, [currentId]);
  const openFile = useCallback((path: string) => {
    setFilesPath(path);
    setMode("files");
  }, []);

  return (
    <>
      {nav}
      {run && <RunCapacityDecision run={run} />}
      <ArtifactToolbar
        run={run}
        visual={visual}
        primary={primary}
        mode={mode}
        onMode={setMode}
        onJumpToRun={onJumpToRun}
      />
      <div className="min-h-0 flex-1" data-testid="visualization-stage">
        {mode === "preview" && visual && <ArtifactStage visual={visual} />}
        {mode === "preview" && !visual && run && (
          <OutputPreview key={run.slug} run={run} onOpenFile={openFile} />
        )}
        {mode === "code" && visual && <ArtifactSource slug={visual.slug} path={visual.path} />}
        {mode === "code" && !visual && run && primary && (
          <ArtifactSource slug={run.slug} path={primary.path} />
        )}
        {mode === "files" && run && <RunFiles run={run} initialPath={filesPath} />}
        {mode === "run" && run && <RunGraphPanel key={run.slug} run={run} />}
      </div>
    </>
  );
}

/** The `/view` page follows the app's theme like an artifact page does. */
function withTheme(url: string | null, theme: string): string | null {
  if (url === null) return null;
  return url.includes("?") ? url : `${url}?theme=${theme}`;
}

/** The first deliverable whose bytes are text — the output's "source". */
function primaryTextFile(files: ArtifactSummary[]): ArtifactSummary | null {
  return (
    deliverableFiles(files).find((f) => isTextKind(artifactKind(f.path, f.is_text))) ?? null
  );
}

function ArtifactToolbar({
  run,
  visual,
  primary,
  mode,
  onMode,
  onJumpToRun,
}: {
  run: OutputSummary | null;
  visual: VisualArtifact | null;
  /** The output's primary text file when the run drew no page. */
  primary: ArtifactSummary | null;
  mode: StageMode;
  onMode: (mode: StageMode) => void;
  onJumpToRun: (slug: string) => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const capabilities = useOutputsCapabilities();
  const parsed = parseArtifactUtterance(visual?.utterance ?? run?.utterance);
  const slug = visual?.slug ?? run?.slug ?? "";
  // The file the toolbar's Download / Reveal / Open act on: the artifact, or
  // the output's primary file.
  const file: { slug: string; path: string } | null = visual
    ? { slug: visual.slug, path: visual.path }
    : run && primary
      ? { slug: run.slug, path: primary.path }
      : null;

  const onReveal = useCallback(async () => {
    if (!file) return;
    try {
      await revealArtifact(file.slug, file.path);
    } catch {
      pushToast("error", t("visualization.reveal_failed"));
    }
  }, [file, pushToast, t]);

  const theme = useThemeValue();
  const externalUrl = visual
    ? visual.kind === "page"
      ? `${artifactPageUrl(visual.slug, visual.path)}?theme=${theme}`
      : visual.url
    : file
      ? withTheme(artifactOpenUrl(file.slug, file.path), theme)
      : null;

  const title = visual?.title ?? (run ? runTitle(run) : "");
  const caption = [
    visual ? formatWhen(visual.mtime) : run ? formatWhen(runWhen(run)) : "",
    visual ? formatSize(visual.size) : "",
    run && typeof run.duration_s === "number" ? `${run.duration_s.toFixed(1)} s` : "",
    parsed?.request ||
      (visual ? cleanRequest(visual.utterance) || visual.name : run ? cleanRequest(run.utterance) : ""),
  ]
    .filter(Boolean)
    .join(" · ");

  // Every run gets the same four tabs; Code is absent only when there is no
  // source to show (a picture, a run that left no text file).
  const tabs: Array<{ id: StageMode; label: string; Icon: typeof Eye; show: boolean }> = [
    {
      id: "preview",
      label: t("visualization.tab_preview"),
      Icon: Eye,
      show: visual !== null || run !== null,
    },
    {
      id: "code",
      label: t("visualization.tab_code"),
      Icon: Code2,
      show: visual !== null ? visual.kind === "page" || visual.kind === "vector" : primary !== null,
    },
    { id: "files", label: t("visualization.tab_files"), Icon: Files, show: run !== null },
    { id: "run", label: t("visualization.tab_run"), Icon: Workflow, show: run !== null },
  ];

  return (
    <div className="flex shrink-0 items-end gap-4 border-b border-border px-6 pt-1">
      <div className="min-w-0 flex-1 pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <p
            className="truncate text-xl font-semibold text-foreground-strong"
            data-testid="visualization-title"
          >
            {title}
          </p>
          {run && <RunStatusBadge run={run} />}
          {run && <RunActions run={run} onJumpToRun={onJumpToRun} />}
        </div>
        <p className="mt-0.5 truncate text-sm text-muted-foreground">{caption}</p>
      </div>

      <div
        role="tablist"
        aria-label={t("visualization.stage_tabs")}
        className="-mb-px flex shrink-0 items-center gap-5"
      >
        {tabs
          .filter((tab) => tab.show)
          .map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={mode === id}
              onClick={() => onMode(id)}
              data-testid={`visualization-tab-${id}`}
              className={cn(
                "relative inline-flex h-10 items-center gap-1.5 border-b-2 text-base font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                mode === id
                  ? "border-accent text-foreground-strong"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </button>
          ))}
      </div>

      <div className="flex shrink-0 items-center gap-1 pb-3">
        {mode === "run" && slug && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void openExternalUrl(`${window.location.origin}${missionMapUrl(slug)}`)}
            title={t("visualization.open_map_page_hint")}
            data-testid="visualization-open-map"
          >
            <Workflow className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("visualization.open_map_page")}
          </Button>
        )}
        {externalUrl && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void openExternalUrl(`${window.location.origin}${externalUrl}`)}
            title={t("visualization.open_external_hint")}
            data-testid="visualization-open-external"
          >
            <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("visualization.open_external")}
          </Button>
        )}
        {file && (
          <Button variant="ghost" size="sm" asChild>
            <a
              href={artifactDownloadUrl(file.slug, file.path)}
              download
              title={t("visualization.download")}
              aria-label={t("visualization.download")}
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
            </a>
          </Button>
        )}
        {/* Desktop only: a headless host has no file manager to open, so the
            button is absent rather than dead. */}
        {file && capabilities.data?.native_file_actions && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void onReveal()}
            title={t("visualization.reveal")}
            aria-label={t("visualization.reveal")}
          >
            <FolderOpen className="h-3.5 w-3.5" aria-hidden />
          </Button>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * The artifact on stage, full-size.
 *
 * A PAGE is framed from `/page` — served with scripts allowed and every
 * network path shut — inside `sandbox="allow-scripts"` WITHOUT
 * `allow-same-origin`: its JavaScript runs in an opaque origin that cannot
 * reach the app's cookies, storage or API (the Claude-artifact model). Raster
 * and vector go through `<img>`, which executes nothing. A PDF renders in the
 * browser's own viewer inside an empty sandbox.
 */
function ArtifactStage({ visual }: { visual: VisualArtifact }) {
  const t = useT();
  /* The page follows the APP's theme, not the OS's: the artifact brief has every
   * page stamp `data-theme` from this query (design_guide.THEME_BOOTSTRAP_JS),
   * so a light app shows a light artifact even on a dark-mode machine. */
  const theme = useThemeValue();
  const [failed, setFailed] = useState(false);
  const id = visualId(visual);
  // A new file must not inherit the previous file's failure verdict.
  useEffect(() => setFailed(false), [id]);

  if (failed) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <p className="max-w-sm text-base text-muted-foreground">
          {t("visualization.render_failed")}
        </p>
      </div>
    );
  }

  if (visual.kind === "image" || visual.kind === "vector") {
    return (
      <div className="flex h-full items-center justify-center overflow-auto p-6">
        <img
          src={visual.url}
          alt={visual.title}
          data-testid="visualization-image"
          onError={() => setFailed(true)}
          className="max-h-full max-w-full rounded-md border border-border bg-background/40 object-contain"
        />
      </div>
    );
  }

  if (visual.kind === "page") {
    return (
      <div className="flex h-full flex-col">
        <iframe
          key={`${id}:${theme}`}
          src={`${artifactPageUrl(visual.slug, visual.path)}?theme=${theme}`}
          title={visual.title}
          data-testid="visualization-frame"
          onError={() => setFailed(true)}
          // allow-scripts WITHOUT allow-same-origin: the page's JS runs in an
          // opaque origin. No forms, no popups, no navigation of the app.
          sandbox="allow-scripts"
          className="min-h-0 w-full flex-1 border-0 bg-background"
        />
        <p className="flex shrink-0 items-center gap-1.5 border-t border-border px-4 py-1.5 text-sm text-muted-foreground">
          <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden />
          {t("visualization.page_sandbox_note")}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <iframe
        key={id}
        src={visual.url}
        title={visual.title}
        data-testid="visualization-frame"
        onError={() => setFailed(true)}
        // Empty sandbox: the browser's own PDF viewer is not the document's
        // script context, so the file still renders.
        sandbox=""
        className="min-h-0 w-full flex-1 border-0 bg-white"
      />
      <p className="flex shrink-0 items-center gap-1.5 border-t border-border px-4 py-1.5 text-sm text-muted-foreground">
        <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden />
        {t("visualization.sandbox_note")}
      </p>
    </div>
  );
}

/** The page's source, read through `/raw` — what "Code" shows. */
function ArtifactSource({ slug, path }: { slug: string; path: string }) {
  const t = useT();
  const file = useArtifactFile(slug, path);
  if (file.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }
  if (file.isError || !file.data) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <p className="text-base text-muted-foreground">{t("visualization.source_failed")}</p>
      </div>
    );
  }
  return (
    <pre
      className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-sm leading-relaxed text-foreground/90"
      data-testid="visualization-source"
    >
      {file.data.text}
      {file.data.truncated ? `\n…` : ""}
    </pre>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * A mission parked in WAITING_CAPACITY asks here: wait for its own
 * subscription, approve paid API use for this one mission, or cancel. Nothing
 * is billed without that explicit approval.
 */
function RunCapacityDecision({ run }: { run: OutputSummary }) {
  if (!run.waiting_capacity || !run.mission_id) return null;
  return <CapacityDecisionPanel missionId={run.mission_id} state="WAITING_CAPACITY" />;
}

function BuildingStage({ run }: { run: OutputSummary }) {
  const t = useT();
  const parsed = parseArtifactUtterance(run.utterance);
  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center justify-center p-8"
      data-testid="visualization-building"
    >
      <EmptyState
        icon={<Loader2 className="animate-spin" aria-hidden />}
        title={t("visualization.building_title").replace("{0}", parsed?.title ?? "")}
        description={t("visualization.building_body")}
      />
      {parsed?.request && (
        <p className="max-w-md truncate text-sm text-foreground-faint">{parsed.request}</p>
      )}
    </div>
  );
}

function EmptyStage({ loading, error }: { loading: boolean; error: boolean }) {
  const t = useT();
  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center justify-center p-8"
      data-testid="visualization-empty"
    >
      <EmptyState
        icon={
          loading && !error ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Shapes aria-hidden />
          )
        }
        title={t(error ? "visualization.error_title" : "visualization.empty_title")}
        description={t(error ? "visualization.error_body" : "visualization.empty_body")}
      />
    </div>
  );
}

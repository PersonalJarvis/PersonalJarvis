import { useCallback, useEffect, useMemo, useState, type DragEvent } from "react";
import {
  Code2,
  Download,
  ExternalLink,
  Eye,
  FileImage,
  FileText,
  Files,
  FolderOpen,
  Globe,
  Loader2,
  RefreshCw,
  Shapes,
  ShieldCheck,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { RunGraphPanel } from "@/components/visualization/RunGraphPanel";
import {
  RunActions,
  RunFiles,
  RunStatusBadge,
} from "@/components/visualization/RunPanels";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { openExternalUrl } from "@/lib/openExternal";
import { endMissionDrag, startMissionDrag } from "@/lib/missionDnd";
import {
  artifactDownloadUrl,
  revealArtifact,
  useArtifactFile,
  useArtifactsForOutput,
  useOutputsCapabilities,
  useOutputsList,
  type OutputStatus,
  type OutputSummary,
} from "@/hooks/useOutputs";
import {
  artifactPageUrl,
  missionMapUrl,
  toVisuals,
  useVisualArtifacts,
  visualId,
  type VisualArtifact,
  type VisualKind,
} from "@/hooks/useVisualArtifacts";

/**
 * The Artifacts section — everything a run produced, with the artifact itself
 * on stage.
 *
 * An artifact is the thing the user asked to LOOK AT: the dashboard, the
 * report, the diagram a background agent wrote as one self-contained HTML
 * file (`create_artifact`), or any image/PDF a worker left behind. It is
 * shown the way Claude shows an artifact: the page fills the stage, its own
 * scripts run inside a sandbox, the source is one tab away, every file of
 * the run is behind "Files", and "how did this come to be" — the n8n-style
 * run graph — behind "Run".
 *
 * Since 2026-08-23 this section is ALSO where every other run lands. The
 * Outputs section that used to list them is gone: a run that produced no
 * page or picture — a research answer, a refactor, a failed build — shows as
 * a run row in the same rail, with its status, its summary or the reason it
 * ended, its files, and the controls it always had (hold-to-abort, Continue,
 * Restart, the GitHub link). One place for what Jarvis and its agents made,
 * not two.
 *
 * It owns no data: runs come from `/api/outputs`, files from the artifact
 * listing (`useVisualArtifacts`, `useArtifactsForOutput`), a page's source
 * from `/raw`. A run that is still building its artifact shows as a
 * "building…" row the rail follows until the page lands — the listings of
 * running runs poll, nothing else does.
 *
 * Detachable (`DETACHABLE_VIEWS` in jarvis/ui/desktop_app.py): an artifact is
 * the thing people put on a second monitor.
 */

/** What a row's status dot means — the run vocabulary, one language. */
const RUN_DOT: Record<OutputStatus, string> = {
  success: "bg-emerald-400",
  error: "bg-destructive",
  running: "bg-primary animate-pulse",
  cancelled: "bg-amber-400",
  unknown: "bg-muted-foreground/50",
};

const KIND_ICON: Record<VisualKind, typeof Globe> = {
  page: Globe,
  image: FileImage,
  vector: FileImage,
  document: FileText,
};

type StageMode = "preview" | "code" | "files" | "run";

/** The rail's pick: an artifact (`path`) or a whole run (`null`). */
interface Selection {
  slug: string;
  path: string | null;
}

/** One rail row — a build in progress, an artifact, or a run without one. */
type RailRow =
  | { kind: "build"; run: OutputSummary; key: string }
  | { kind: "visual"; run: OutputSummary | null; visual: VisualArtifact; key: string }
  | { kind: "run"; run: OutputSummary; key: string };

/** What the stage shows: a run (always), and its artifact when it has one. */
interface StageTarget {
  run: OutputSummary | null;
  visual: VisualArtifact | null;
  /** The run is a `create_artifact` build still writing its page. */
  building: boolean;
}

/**
 * A `create_artifact` mission's prompt leads with `Artifact: <title>` and the
 * user's request right after (jarvis/artifacts/brief.py). The run list
 * already strips the quality lead, so the run's `utterance` starts with that
 * line — which is how a running build is recognised and labelled here.
 */
export function parseArtifactUtterance(
  utterance: string | undefined,
): { title: string; request: string } | null {
  const text = (utterance ?? "").trim();
  const match = /^Artifact:[ \t]*([^\n]+)/.exec(text);
  if (!match) return null;
  const rest = text.slice(match[0].length).trim();
  const request = rest.split(/\n\s*\n/)[0]?.trim() ?? "";
  return { title: match[1].trim(), request };
}

/** What a run is called when it has no page title of its own. */
function runTitle(run: OutputSummary): string {
  return parseArtifactUtterance(run.utterance)?.title || run.utterance?.trim() || run.slug;
}

/** The rail row's timestamp — what tells two same-named artifacts apart. */
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

/** The run's moment for ordering — when it ended, else when it began. */
function runWhen(run: OutputSummary): number {
  return run.completed_at ?? run.started_at ?? 0;
}

/**
 * The rail, in order: builds in progress, then running runs, then every
 * artifact and every artifact-less run newest first. A run with at least one
 * artifact is reached through its artifact rows (its other files sit behind
 * the stage's Files tab), so it gets no row of its own.
 */
export function buildRailRows(
  runs: OutputSummary[],
  visuals: VisualArtifact[],
  building: OutputSummary[],
): RailRow[] {
  const bySlug = new Map(runs.map((run) => [run.slug, run]));
  const visualSlugs = new Set(visuals.map((v) => v.slug));
  const buildSlugs = new Set(building.map((r) => r.slug));

  const rest: Array<{ row: RailRow; running: boolean; when: number }> = [];
  for (const visual of visuals) {
    rest.push({
      row: { kind: "visual", run: bySlug.get(visual.slug) ?? null, visual, key: visualId(visual) },
      running: visual.status === "running",
      when: visual.mtime,
    });
  }
  for (const run of runs) {
    if (visualSlugs.has(run.slug) || buildSlugs.has(run.slug)) continue;
    rest.push({
      row: { kind: "run", run, key: `run:${run.slug}` },
      running: run.status === "running",
      when: runWhen(run),
    });
  }
  rest.sort((a, b) => Number(b.running) - Number(a.running) || b.when - a.when);

  return [
    ...building.map((run): RailRow => ({ kind: "build", run, key: `build:${run.slug}` })),
    ...rest.map((entry) => entry.row),
  ];
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

  const rows = useMemo(() => buildRailRows(runs, visuals, building), [runs, visuals, building]);

  /* A `?run=<slug>` in the URL pre-selects that run's newest artifact — what
   * makes a detached window or a pasted link open on the page it talks about.
   * Read once at mount; clicks own it after. */
  const [selection, setSelection] = useState<Selection | null>(() => {
    const slug = new URLSearchParams(window.location.search).get("run");
    return slug ? { slug, path: null } : null;
  });
  /* Once the user picked a row, the newest artifact landing must not steal the
   * stage — an explicit choice is never fought (the selection stays until the
   * next click). A pick of a BUILDING run is the exception it resolves itself:
   * its page replaces the spinner when it lands. */
  const pick = useCallback((next: Selection) => setSelection(next), []);

  /*
   * Another surface asked for something to be staged ("show visuals" on the
   * agent strip, the `create_artifact` tool via NavigateSidebar). A target
   * names a `visualId` (slug::path) or "latest"; see VisualStageRequest.
   */
  const visualStage = useEventStore((s) => s.visualStage);
  useEffect(() => {
    if (visualStage === null) return;
    if (visualStage.target === "latest") {
      setSelection(null);
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
   * What the stage shows. The row the user picked — an artifact, or a run
   * (its newest artifact once it has one, the run itself otherwise). While
   * nothing was picked, the first rail row: the newest build in progress,
   * else the newest thing there is.
   */
  const target: StageTarget = useMemo(() => {
    const bySlug = (slug: string) => runs.find((r) => r.slug === slug) ?? null;
    const resolveRun = (slug: string): StageTarget | null => {
      const run = bySlug(slug);
      const visual = visuals.find((v) => v.slug === slug) ?? null;
      if (run === null && visual === null) return null;
      const isBuilding = run !== null && building.includes(run) && visual === null;
      return { run, visual, building: isBuilding };
    };
    if (selection !== null) {
      if (selection.path !== null) {
        const visual = visuals.find(
          (v) => v.slug === selection.slug && v.path === selection.path,
        );
        if (visual) return { run: bySlug(visual.slug), visual, building: false };
      }
      const resolved = resolveRun(selection.slug);
      if (resolved) return resolved;
    }
    const first = rows[0];
    if (!first) return { run: null, visual: null, building: false };
    if (first.kind === "build") return { run: first.run, visual: null, building: true };
    if (first.kind === "visual") return { run: first.run, visual: first.visual, building: false };
    return { run: first.run, visual: null, building: false };
  }, [selection, runs, visuals, building, rows]);

  const refetch = useCallback(() => {
    void outputs.refetch();
    gallery.refetch();
  }, [outputs, gallery]);

  const loading = outputs.isLoading || gallery.isLoading;
  const activeKey =
    target.building && target.run
      ? `build:${target.run.slug}`
      : target.visual
        ? visualId(target.visual)
        : target.run
          ? `run:${target.run.slug}`
          : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Shapes className="h-4 w-4 text-primary" aria-hidden />}
        title={t("visualization.title")}
        subtitle={t("visualization.subtitle")}
        right={
          <Button
            variant="outline"
            size="sm"
            onClick={refetch}
            disabled={loading}
            data-testid="visualization-refresh"
          >
            {loading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {t("visualization.refresh")}
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1">
        {/* Rail — builds in progress first, then every artifact and run, newest first. */}
        <aside className="flex w-72 shrink-0 flex-col border-r border-border">
          <p className="border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("visualization.rail")}
            {rows.length > 0 && (
              <span className="ml-1.5 normal-case tracking-normal text-muted-foreground/70">
                · {rows.length}
              </span>
            )}
          </p>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="space-y-1 p-2" data-testid="visualization-artifacts">
              {rows.map((row) => (
                <li key={row.key}>
                  <RailRowButton
                    row={row}
                    active={activeKey === row.key}
                    onPick={pick}
                  />
                </li>
              ))}
            </ul>
            {gallery.skippedRuns > 0 && (
              <p className="px-3 pb-3 text-[11px] text-muted-foreground/70">
                {t("visualization.older_not_scanned").replace(
                  "{0}",
                  String(gallery.scannedRuns),
                )}
              </p>
            )}
          </ScrollArea>
        </aside>

        {/* Stage — the artifact itself, full-size; the run around it. */}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {target.building && target.run !== null ? (
            <BuildingStage run={target.run} />
          ) : target.run === null && target.visual === null ? (
            <EmptyStage loading={loading} error={outputs.isError || gallery.isError} />
          ) : (
            <Stage
              key={target.run?.slug ?? target.visual?.slug}
              run={target.run}
              visual={target.visual}
              onJumpToRun={(slug) => pick({ slug, path: null })}
            />
          )}
        </section>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function RailRowButton({
  row,
  active,
  onPick,
}: {
  row: RailRow;
  active: boolean;
  onPick: (next: Selection) => void;
}) {
  const t = useT();
  const base = cn(
    "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors",
    active
      ? "bg-primary/15 text-foreground"
      : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
  );
  // Every row can be dragged onto the Jarvis dock — the run is what the dock
  // takes, whichever of its artifacts the row happens to show.
  const dragRun = row.run;
  const dragProps = dragRun
    ? {
        draggable: true,
        onDragStart: (e: DragEvent) => startMissionDrag(e, dragRun),
        onDragEnd: endMissionDrag,
      }
    : {};

  if (row.kind === "build") {
    const parsed = parseArtifactUtterance(row.run.utterance);
    return (
      <button
        type="button"
        onClick={() => onPick({ slug: row.run.slug, path: null })}
        aria-current={active}
        data-testid="visualization-building-row"
        className={base}
        {...dragProps}
      >
        <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-primary" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-foreground">
            {parsed?.title || t("visualization.building")}
          </span>
          <span className="block truncate">{t("visualization.building")}</span>
        </span>
      </button>
    );
  }

  if (row.kind === "visual") {
    const { visual } = row;
    const Icon = KIND_ICON[visual.kind];
    const parsed = parseArtifactUtterance(visual.utterance);
    return (
      <button
        type="button"
        onClick={() => onPick({ slug: visual.slug, path: visual.path })}
        aria-current={active}
        data-testid="visualization-artifact-row"
        data-kind={visual.kind}
        className={base}
        {...dragProps}
      >
        <span className="relative mt-0.5 shrink-0">
          <Icon className="h-3.5 w-3.5" aria-hidden />
          <span
            className={cn(
              "absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full",
              RUN_DOT[visual.status ?? "unknown"],
            )}
            aria-hidden
          />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-foreground">{visual.title}</span>
          <span className="block truncate">
            {[
              formatWhen(visual.mtime),
              parsed?.request || visual.utterance?.trim() || visual.name,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
      </button>
    );
  }

  // A run without a page or picture: the answer, the refactor, the failure.
  const { run } = row;
  const status = run.status ?? "unknown";
  const Icon = status === "running" ? Loader2 : (run.artifact_count ?? 0) > 0 ? FileText : Workflow;
  return (
    <button
      type="button"
      onClick={() => onPick({ slug: run.slug, path: null })}
      aria-current={active}
      data-testid="visualization-run-row"
      data-status={status}
      className={base}
      {...dragProps}
    >
      <span className="relative mt-0.5 shrink-0">
        <Icon
          className={cn("h-3.5 w-3.5", status === "running" && "animate-spin text-primary")}
          aria-hidden
        />
        {status !== "running" && (
          <span
            className={cn("absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full", RUN_DOT[status])}
            aria-hidden
          />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-foreground">{runTitle(run)}</span>
        <span className="block truncate">
          {[formatWhen(runWhen(run)), run.summary?.trim() || run.terminal_reason || status]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * The stage for one run: its artifact (when it has one) under Preview / Code,
 * every file under Files, the graph under Run. Keyed by run in the caller, so
 * a new run opens on its own default tab.
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
}: {
  run: OutputSummary | null;
  visual: VisualArtifact | null;
  onJumpToRun: (slug: string) => void;
}) {
  const slug = run?.slug ?? pickedVisual?.slug ?? null;
  const listing = useArtifactsForOutput(run !== null && pickedVisual === null ? slug : null);
  const visual: VisualArtifact | null = useMemo(() => {
    if (pickedVisual) return pickedVisual;
    if (run === null) return null;
    const found = toVisuals(run, listing.data?.files ?? []);
    found.sort((a, b) => b.mtime - a.mtime);
    return found[0] ?? null;
  }, [pickedVisual, run, listing.data]);

  const [mode, setMode] = useState<StageMode>(visual ? "preview" : "files");
  const currentId = visual ? visualId(visual) : null;
  // A new artifact opens on its page, whatever tab the previous one was on; a
  // run that turns out to have one (its listing just arrived) moves to it too.
  useEffect(() => {
    setMode(currentId ? "preview" : "files");
  }, [currentId]);

  return (
    <>
      <ArtifactToolbar
        run={run}
        visual={visual}
        mode={mode}
        onMode={setMode}
        onJumpToRun={onJumpToRun}
      />
      <div className="min-h-0 flex-1" data-testid="visualization-stage">
        {mode === "preview" && visual && <ArtifactStage visual={visual} />}
        {mode === "code" && visual && <ArtifactSource slug={visual.slug} path={visual.path} />}
        {mode === "files" && run && <RunFiles run={run} />}
        {mode === "run" && run && <RunGraphPanel key={run.slug} run={run} />}
      </div>
    </>
  );
}

function ArtifactToolbar({
  run,
  visual,
  mode,
  onMode,
  onJumpToRun,
}: {
  run: OutputSummary | null;
  visual: VisualArtifact | null;
  mode: StageMode;
  onMode: (mode: StageMode) => void;
  onJumpToRun: (slug: string) => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const capabilities = useOutputsCapabilities();
  const parsed = parseArtifactUtterance(visual?.utterance ?? run?.utterance);
  const slug = visual?.slug ?? run?.slug ?? "";

  const onReveal = useCallback(async () => {
    if (!visual) return;
    try {
      await revealArtifact(visual.slug, visual.path);
    } catch {
      pushToast("error", t("visualization.reveal_failed"));
    }
  }, [visual, pushToast, t]);

  const theme = useThemeValue();
  const externalUrl = visual
    ? visual.kind === "page"
      ? `${artifactPageUrl(visual.slug, visual.path)}?theme=${theme}`
      : visual.url
    : null;

  const title = visual?.title ?? (run ? runTitle(run) : "");
  const caption = [
    visual ? formatWhen(visual.mtime) : run ? formatWhen(runWhen(run)) : "",
    visual ? formatSize(visual.size) : "",
    run && typeof run.duration_s === "number" ? `${run.duration_s.toFixed(1)} s` : "",
    parsed?.request || (visual ? visual.utterance?.trim() || visual.name : ""),
  ]
    .filter(Boolean)
    .join(" · ");

  const tabs: Array<{ id: StageMode; label: string; Icon: typeof Eye; show: boolean }> = [
    { id: "preview", label: t("visualization.tab_preview"), Icon: Eye, show: visual !== null },
    {
      id: "code",
      label: t("visualization.tab_code"),
      Icon: Code2,
      show: visual !== null && (visual.kind === "page" || visual.kind === "vector"),
    },
    { id: "files", label: t("visualization.tab_files"), Icon: Files, show: run !== null },
    { id: "run", label: t("visualization.tab_run"), Icon: Workflow, show: run !== null },
  ];

  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate text-sm font-medium" data-testid="visualization-title">
            {title}
          </p>
          {run && <RunStatusBadge run={run} />}
          {run && <RunActions run={run} onJumpToRun={onJumpToRun} />}
        </div>
        <p className="truncate text-[11px] text-muted-foreground">{caption}</p>
      </div>

      <div
        role="tablist"
        aria-label={t("visualization.stage_tabs")}
        className="flex shrink-0 items-center rounded-md border border-border bg-secondary/40 p-0.5"
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
                "inline-flex items-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium transition-colors",
                mode === id
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {label}
            </button>
          ))}
      </div>

      <div className="flex shrink-0 items-center gap-1">
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
        {visual && externalUrl && (
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
        {visual && (
          <Button variant="ghost" size="sm" asChild>
            <a
              href={artifactDownloadUrl(visual.slug, visual.path)}
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
        {visual && capabilities.data?.native_file_actions && (
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
        <p className="max-w-sm text-xs text-muted-foreground">
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
        <p className="flex shrink-0 items-center gap-1.5 border-t border-border px-4 py-1.5 text-[11px] text-muted-foreground">
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
      <p className="flex shrink-0 items-center gap-1.5 border-t border-border px-4 py-1.5 text-[11px] text-muted-foreground">
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
        <p className="text-xs text-muted-foreground">{t("visualization.source_failed")}</p>
      </div>
    );
  }
  return (
    <pre
      className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-relaxed text-foreground/90"
      data-testid="visualization-source"
    >
      {file.data.text}
      {file.data.truncated ? `\n…` : ""}
    </pre>
  );
}

/* ------------------------------------------------------------------------- */

function BuildingStage({ run }: { run: OutputSummary }) {
  const t = useT();
  const parsed = parseArtifactUtterance(run.utterance);
  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-8 text-center"
      data-testid="visualization-building"
    >
      <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden />
      <p className="text-sm font-medium">
        {t("visualization.building_title").replace("{0}", parsed?.title ?? "")}
      </p>
      <p className="max-w-sm text-xs text-muted-foreground">{t("visualization.building_body")}</p>
      {parsed?.request && (
        <p className="max-w-md truncate text-[11px] text-muted-foreground/70">{parsed.request}</p>
      )}
    </div>
  );
}

function EmptyStage({ loading, error }: { loading: boolean; error: boolean }) {
  const t = useT();
  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-8 text-center"
      data-testid="visualization-empty"
    >
      {loading && !error ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
      ) : (
        <Shapes className="h-6 w-6 text-muted-foreground" aria-hidden />
      )}
      <p className="text-sm font-medium">
        {t(error ? "visualization.error_title" : "visualization.empty_title")}
      </p>
      <p className="max-w-sm text-xs text-muted-foreground">
        {t(error ? "visualization.error_body" : "visualization.empty_body")}
      </p>
    </div>
  );
}

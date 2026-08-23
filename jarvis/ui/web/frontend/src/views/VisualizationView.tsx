import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Code2,
  Download,
  ExternalLink,
  Eye,
  FileImage,
  FileText,
  FolderOpen,
  Frame,
  Globe,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { RunGraphPanel } from "@/components/visualization/RunGraphPanel";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { openExternalUrl } from "@/lib/openExternal";
import {
  artifactDownloadUrl,
  revealArtifact,
  useArtifactFile,
  useOutputsCapabilities,
  useOutputsList,
  type OutputStatus,
  type OutputSummary,
} from "@/hooks/useOutputs";
import {
  artifactPageUrl,
  missionMapUrl,
  useVisualArtifacts,
  visualId,
  type VisualArtifact,
  type VisualKind,
} from "@/hooks/useVisualArtifacts";

/**
 * The Artifacts section — every page and picture a run produced, full-size.
 *
 * An artifact is the thing the user asked to LOOK AT: the dashboard, the
 * report, the diagram a background agent wrote as one self-contained HTML
 * file (`create_artifact`), or any image/PDF a worker left behind. It is
 * shown the way Claude shows an artifact: the page fills the stage, its own
 * scripts run inside a sandbox, the source is one tab away, and "how did
 * this come to be" — the n8n-style run graph that used to BE this section —
 * sits behind a third tab instead of in front of the page.
 *
 * It owns no data: runs come from `/api/outputs`, files from the artifact
 * listing (`useVisualArtifacts`), a page's source from `/raw`. A run that is
 * still building its artifact shows as a "building…" row the rail follows
 * until the page lands — the list for a running run polls, nothing else does.
 *
 * Detachable (`DETACHABLE_VIEWS` in jarvis/ui/desktop_app.py): an artifact is
 * the thing people put on a second monitor.
 */

/** What a row's status dot means — the Outputs vocabulary, one language. */
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

type StageMode = "preview" | "code" | "run";

/** The rail's pick: an artifact (`path`) or a whole run still building (`null`). */
interface Selection {
  slug: string;
  path: string | null;
}

/**
 * A `create_artifact` mission's prompt leads with `Artifact: <title>` and the
 * user's request right after (jarvis/artifacts/brief.py). The Outputs list
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

  /* A `?run=<slug>` in the URL pre-selects that run's newest artifact — what
   * makes a detached window or a pasted link open on the page it talks about.
   * Read once at mount; clicks own it after. */
  const [selection, setSelection] = useState<Selection | null>(() => {
    const slug = new URLSearchParams(window.location.search).get("run");
    return slug ? { slug, path: null } : null;
  });
  /* Once the user picked a row, the newest artifact landing must not steal the
   * stage — an explicit choice is never fought. A pick of a BUILDING run is the
   * exception it resolves itself: its page replaces the spinner when it lands. */
  const userPicked = useRef(selection !== null);

  /*
   * Another surface asked for something to be staged ("show visuals" on the
   * agent strip, the `create_artifact` tool via NavigateSidebar). A target
   * names a `visualId` (slug::path) or "latest"; see VisualStageRequest.
   */
  const visualStage = useEventStore((s) => s.visualStage);
  useEffect(() => {
    if (visualStage === null) return;
    if (visualStage.target === "latest") {
      userPicked.current = false;
      setSelection(null);
      return;
    }
    const separator = visualStage.target.indexOf("::");
    if (separator > 0) {
      userPicked.current = true;
      setSelection({
        slug: visualStage.target.slice(0, separator),
        path: visualStage.target.slice(separator + 2),
      });
    }
  }, [visualStage]);

  /*
   * What the stage shows. In order: the row the user picked (an artifact, or
   * a run's newest artifact once it has one); while nothing was picked, the
   * newest build in progress; otherwise the newest artifact there is.
   */
  const currentBuild: OutputSummary | null = useMemo(() => {
    if (selection !== null) {
      if (selection.path !== null) return null;
      const hasPage = visuals.some((v) => v.slug === selection.slug);
      return hasPage ? null : (building.find((r) => r.slug === selection.slug) ?? null);
    }
    return building[0] ?? null;
  }, [selection, visuals, building]);

  const current: VisualArtifact | null = useMemo(() => {
    if (currentBuild !== null) return null;
    if (selection !== null) {
      const picked =
        selection.path !== null
          ? visuals.find((v) => v.slug === selection.slug && v.path === selection.path)
          : visuals.find((v) => v.slug === selection.slug);
      if (picked) return picked;
      if (userPicked.current) return visuals[0] ?? null;
    }
    return visuals[0] ?? null;
  }, [currentBuild, selection, visuals]);

  const currentRun: OutputSummary | null = useMemo(
    () => (current ? (runs.find((r) => r.slug === current.slug) ?? null) : null),
    [current, runs],
  );

  const [mode, setMode] = useState<StageMode>("preview");
  const currentId = current ? visualId(current) : null;
  // A new artifact opens on its page, whatever tab the previous one was on.
  useEffect(() => setMode("preview"), [currentId]);

  const refetch = useCallback(() => {
    void outputs.refetch();
    gallery.refetch();
  }, [outputs, gallery]);

  const loading = outputs.isLoading || gallery.isLoading;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Frame className="h-4 w-4 text-primary" aria-hidden />}
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
        {/* Rail — builds in progress first, then every artifact, newest first. */}
        <aside className="flex w-72 shrink-0 flex-col border-r border-border">
          <p className="border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("visualization.rail")}
            {visuals.length > 0 && (
              <span className="ml-1.5 normal-case tracking-normal text-muted-foreground/70">
                · {visuals.length}
              </span>
            )}
          </p>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="space-y-1 p-2" data-testid="visualization-artifacts">
              {building.map((run) => {
                const parsed = parseArtifactUtterance(run.utterance);
                const active = currentBuild?.slug === run.slug;
                return (
                  <li key={`build:${run.slug}`}>
                    <button
                      type="button"
                      onClick={() => {
                        userPicked.current = true;
                        setSelection({ slug: run.slug, path: null });
                      }}
                      aria-current={active}
                      data-testid="visualization-building-row"
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors",
                        active
                          ? "bg-primary/15 text-foreground"
                          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                      )}
                    >
                      <Loader2
                        className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-primary"
                        aria-hidden
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-foreground">
                          {parsed?.title || t("visualization.building")}
                        </span>
                        <span className="block truncate">{t("visualization.building")}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
              {visuals.map((visual) => {
                const Icon = KIND_ICON[visual.kind];
                const active = current !== null && visualId(current) === visualId(visual);
                const parsed = parseArtifactUtterance(visual.utterance);
                return (
                  <li key={visualId(visual)}>
                    <button
                      type="button"
                      onClick={() => {
                        userPicked.current = true;
                        setSelection({ slug: visual.slug, path: visual.path });
                      }}
                      aria-current={active}
                      data-testid="visualization-artifact-row"
                      data-kind={visual.kind}
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors",
                        active
                          ? "bg-primary/15 text-foreground"
                          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                      )}
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
                        <span className="block truncate font-medium text-foreground">
                          {visual.title}
                        </span>
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
                  </li>
                );
              })}
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

        {/* Stage — the artifact itself, full-size. */}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {currentBuild !== null ? (
            <BuildingStage run={currentBuild} />
          ) : current === null ? (
            <EmptyStage
              loading={loading}
              error={outputs.isError || gallery.isError}
            />
          ) : (
            <>
              <ArtifactToolbar
                visual={current}
                mode={mode}
                onMode={setMode}
                runAvailable={currentRun !== null}
              />
              <div className="min-h-0 flex-1" data-testid="visualization-stage">
                {mode === "preview" && <ArtifactStage visual={current} />}
                {mode === "code" && (
                  <ArtifactSource slug={current.slug} path={current.path} />
                )}
                {mode === "run" && currentRun !== null && (
                  <RunGraphPanel key={currentRun.slug} run={currentRun} />
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function ArtifactToolbar({
  visual,
  mode,
  onMode,
  runAvailable,
}: {
  visual: VisualArtifact;
  mode: StageMode;
  onMode: (mode: StageMode) => void;
  runAvailable: boolean;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const capabilities = useOutputsCapabilities();
  const parsed = parseArtifactUtterance(visual.utterance);

  const onReveal = useCallback(async () => {
    try {
      await revealArtifact(visual.slug, visual.path);
    } catch {
      pushToast("error", t("visualization.reveal_failed"));
    }
  }, [visual, pushToast, t]);

  const externalUrl =
    visual.kind === "page"
      ? artifactPageUrl(visual.slug, visual.path)
      : visual.url;

  const tabs: Array<{ id: StageMode; label: string; Icon: typeof Eye; show: boolean }> = [
    { id: "preview", label: t("visualization.tab_preview"), Icon: Eye, show: true },
    { id: "code", label: t("visualization.tab_code"), Icon: Code2, show: visual.kind === "page" || visual.kind === "vector" },
    { id: "run", label: t("visualization.tab_run"), Icon: Workflow, show: runAvailable },
  ];

  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" data-testid="visualization-title">
          {visual.title}
        </p>
        <p className="truncate text-[11px] text-muted-foreground">
          {[
            formatWhen(visual.mtime),
            formatSize(visual.size),
            parsed?.request || visual.utterance?.trim() || visual.name,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
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
        {mode === "run" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              void openExternalUrl(`${window.location.origin}${missionMapUrl(visual.slug)}`)
            }
            title={t("visualization.open_map_page_hint")}
            data-testid="visualization-open-map"
          >
            <Workflow className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("visualization.open_map_page")}
          </Button>
        )}
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
        {/* Desktop only: a headless host has no file manager to open, so the
            button is absent rather than dead. */}
        {capabilities.data?.native_file_actions && (
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
          key={id}
          src={artifactPageUrl(visual.slug, visual.path)}
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
        <Frame className="h-6 w-6 text-muted-foreground" aria-hidden />
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

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Download,
  ExternalLink,
  FileImage,
  FolderOpen,
  Frame,
  Loader2,
  Maximize2,
  RefreshCw,
  ShieldAlert,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { openExternalUrl } from "@/lib/openExternal";
import {
  artifactDownloadUrl,
  revealArtifact,
  useOutputsCapabilities,
} from "@/hooks/useOutputs";
import {
  DEFAULT_RUN_SCAN_LIMIT,
  useVisualArtifacts,
  visualId,
  type VisualArtifact,
} from "@/hooks/useVisualArtifacts";

/**
 * The Visualization section — everything a run DREW, at full size.
 *
 * The app could already tell you that a chart exists: Outputs lists it as a
 * filename in a directory, one row among the logs and the JSON. What it could
 * not do is show it to you. A picture reduced to its filename is the one
 * artifact class that loses all of its meaning in a list, so this section reads
 * the same run archive and puts the images, vector drawings and rendered pages
 * on a stage instead.
 *
 * It owns no data of its own — see `useVisualArtifacts`. That is deliberate: a
 * second store of "the visuals" would need its own writer, its own cleanup and
 * its own answer for the runs that existed before the feature. Reading the
 * archive means every run ever made is already in here.
 *
 * Detachable (`DETACHABLE_VIEWS` in jarvis/ui/desktop_app.py): a diagram is the
 * thing people put on a second monitor while they work in the first window.
 */

const ZOOM_STEPS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4] as const;

/** Human file size, rounded the way a caption wants it. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The run label a tile is filed under — the utterance, else the raw slug. */
function runLabel(artifact: VisualArtifact): string {
  return artifact.utterance?.trim() || artifact.slug;
}

export function VisualizationView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const capabilities = useOutputsCapabilities();
  const { visuals, scannedRuns, skippedRuns, isLoading, isError, refetch } =
    useVisualArtifacts();

  /*
   * The selection is held as an ID, not as the object: the list is refetched,
   * and a stored object would pin a stale copy of an artifact whose file has
   * since been rewritten by a re-run.
   */
  const [selectedId, setSelectedId] = useState<string | null>(null);

  /*
   * Another surface asked for something to be put on the stage.
   *
   * The request arrives through the store rather than as a prop, because the
   * asker is never this section's parent — it is the agent strip in a different
   * view, and that view is unmounted by the time this one renders. Each request
   * carries a counter, so the store value is a NEW object even when the target
   * is unchanged; that is what lets "show me the latest" work a second time
   * after the user has clicked around the gallery. See VisualStageRequest.
   */
  const visualStage = useEventStore((s) => s.visualStage);
  useEffect(() => {
    if (visualStage === null) return;
    // "latest" clears the pin instead of naming an id: the gallery is
    // newest-first and refetches, so an id resolved now would go stale as soon
    // as a newer picture lands, while "nothing selected" always falls through
    // to the newest one below.
    setSelectedId(visualStage.target === "latest" ? null : visualStage.target);
  }, [visualStage]);

  const selected = useMemo(() => {
    if (visuals.length === 0) return null;
    return visuals.find((v) => visualId(v) === selectedId) ?? visuals[0];
  }, [visuals, selectedId]);

  // "fit" is a mode, not a factor: it means "as large as the stage allows",
  // which no number can express while the window is resizable.
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [failed, setFailed] = useState(false);
  const selectedKey = selected ? visualId(selected) : null;
  // A new picture starts fitted and un-failed — carrying a 4× zoom from a
  // thumbnail-sized icon onto a full-page diagram shows a corner of it.
  useEffect(() => {
    setZoom("fit");
    setFailed(false);
  }, [selectedKey]);

  const stepZoom = useCallback((direction: 1 | -1) => {
    setZoom((current) => {
      const from = current === "fit" ? 1 : current;
      const index = ZOOM_STEPS.indexOf(from as (typeof ZOOM_STEPS)[number]);
      // A zoom that came from "fit" may sit between two steps; nearest-step
      // search keeps the buttons predictable in that case.
      const base =
        index >= 0
          ? index
          : ZOOM_STEPS.reduce(
              (best, step, i) =>
                Math.abs(step - from) < Math.abs(ZOOM_STEPS[best] - from) ? i : best,
              0,
            );
      const next = Math.min(ZOOM_STEPS.length - 1, Math.max(0, base + direction));
      return ZOOM_STEPS[next];
    });
  }, []);

  const onReveal = useCallback(
    async (artifact: VisualArtifact) => {
      try {
        await revealArtifact(artifact.slug, artifact.path);
      } catch {
        pushToast("error", t("visualization.reveal_failed"));
      }
    },
    [pushToast, t],
  );

  const zoomable = selected?.kind === "image" || selected?.kind === "vector";

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
            onClick={() => refetch()}
            disabled={isLoading}
            data-testid="visualization-refresh"
          >
            {isLoading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {t("visualization.refresh")}
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1">
        {/* Gallery rail — every visual found, newest first. */}
        <aside className="flex w-64 shrink-0 flex-col border-r border-border">
          <ScrollArea className="min-h-0 flex-1">
            <ul className="space-y-1 p-2" data-testid="visualization-gallery">
              {visuals.map((artifact) => {
                const id = visualId(artifact);
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(id)}
                      aria-current={id === selectedKey}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors",
                        id === selectedKey
                          ? "bg-primary/15 text-foreground"
                          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                      )}
                    >
                      <FileImage className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-foreground">
                          {artifact.name}
                        </span>
                        <span className="block truncate">{runLabel(artifact)}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </ScrollArea>
          {/* The scan window, stated rather than hidden: an older picture that
              is not in the list must read as "not looked at", never as gone. */}
          <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
            {t("visualization.scan_note")
              .replace("{0}", String(scannedRuns || DEFAULT_RUN_SCAN_LIMIT))
              .replace("{1}", String(skippedRuns))}
          </p>
        </aside>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {selected === null ? (
            <EmptyStage
              loading={isLoading}
              error={isError}
              title={t(isError ? "visualization.error_title" : "visualization.empty_title")}
              body={t(isError ? "visualization.error_body" : "visualization.empty_body")}
            />
          ) : (
            <>
              <div
                className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-4"
                data-testid="visualization-stage"
              >
                {failed ? (
                  <p className="text-sm text-muted-foreground">
                    {t("visualization.render_failed")}
                  </p>
                ) : (
                  <VisualFrame
                    artifact={selected}
                    zoom={zoom}
                    onError={() => setFailed(true)}
                  />
                )}
              </div>

              {/* Caption + actions. One row, because the stage is the point and
                  every pixel spent on chrome is a pixel off the picture. */}
              <footer className="flex shrink-0 items-center gap-3 border-t border-border px-4 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium">{selected.path}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {runLabel(selected)} · {formatSize(selected.size)}
                  </p>
                </div>

                {zoomable && (
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => stepZoom(-1)}
                      title={t("visualization.zoom_out")}
                      aria-label={t("visualization.zoom_out")}
                    >
                      <ZoomOut className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                    <span className="w-12 text-center text-[11px] tabular-nums text-muted-foreground">
                      {zoom === "fit" ? t("visualization.zoom_fit") : `${Math.round(zoom * 100)}%`}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => stepZoom(1)}
                      title={t("visualization.zoom_in")}
                      aria-label={t("visualization.zoom_in")}
                    >
                      <ZoomIn className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setZoom("fit")}
                      title={t("visualization.zoom_fit")}
                      aria-label={t("visualization.zoom_fit")}
                    >
                      <Maximize2 className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  </div>
                )}

                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      void openExternalUrl(`${window.location.origin}${selected.url}`)
                    }
                    title={t("visualization.open_external")}
                  >
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    {t("visualization.open_external")}
                  </Button>
                  <Button variant="ghost" size="sm" asChild>
                    <a
                      href={artifactDownloadUrl(selected.slug, selected.path)}
                      download
                      title={t("visualization.download")}
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden />
                    </a>
                  </Button>
                  {/* Desktop only: a headless host has no file manager to open,
                      so the button is absent rather than dead. */}
                  {capabilities.data?.native_file_actions && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void onReveal(selected)}
                      title={t("visualization.reveal")}
                    >
                      <FolderOpen className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  )}
                </div>
              </footer>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

/**
 * The picture itself.
 *
 * Raster and vector go through `<img>`, which cannot execute anything even when
 * the SVG carries a script. Pages and PDFs need a document frame, and those are
 * the two cases where the file's own content is being interpreted — the backend
 * already serves both under the no-script CSP (`VIEW_CSP`, outputs_routes.py),
 * and the frame is sandboxed on top so the preview stays inert even if that
 * header is ever loosened.
 */
function VisualFrame({
  artifact,
  zoom,
  onError,
}: {
  artifact: VisualArtifact;
  zoom: number | "fit";
  onError: () => void;
}) {
  const t = useT();
  // Natural width is measured rather than assumed, so a numeric zoom is a real
  // multiple of the file's own pixels and the stage gets scroll extents that
  // match what is drawn. `transform: scale()` would give neither. Height
  // follows from the aspect ratio and is left to the browser.
  const [naturalWidth, setNaturalWidth] = useState<number | null>(null);

  if (artifact.kind === "image" || artifact.kind === "vector") {
    const scaled =
      zoom !== "fit" && naturalWidth !== null
        ? { width: `${naturalWidth * zoom}px`, maxWidth: "none", maxHeight: "none" }
        : undefined;
    return (
      <img
        src={artifact.url}
        alt={artifact.name}
        data-testid="visualization-image"
        onLoad={(event) => setNaturalWidth(event.currentTarget.naturalWidth)}
        onError={onError}
        style={scaled}
        className={cn(
          "rounded-md bg-background/40 shadow-sm",
          zoom === "fit" && "max-h-full max-w-full object-contain",
        )}
      />
    );
  }

  return (
    <div className="flex h-full w-full min-w-0 flex-col gap-2">
      <iframe
        src={artifact.url}
        title={artifact.name}
        data-testid="visualization-frame"
        onError={onError}
        // Empty sandbox = opaque origin, no scripts, no forms, no navigation.
        // A PDF still renders: the browser's own viewer is not the framed
        // document's script context.
        sandbox=""
        className="min-h-0 flex-1 rounded-md border border-border bg-white"
      />
      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden />
        {t("visualization.sandbox_note")}
      </p>
    </div>
  );
}

function EmptyStage({
  loading,
  error,
  title,
  body,
}: {
  loading: boolean;
  error: boolean;
  title: string;
  body: string;
}) {
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
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-sm text-xs text-muted-foreground">{body}</p>
    </div>
  );
}

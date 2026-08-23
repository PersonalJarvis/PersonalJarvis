/**
 * The artifacts a run left behind — the data behind the Artifacts section.
 *
 * No new REST surface: `/api/outputs` already lists the runs and
 * `/api/outputs/{slug}/artifacts` already lists their files, so this hook is a
 * read over what the Outputs view reads too. What it adds is the SELECTION —
 * which of those files are things a person can look at — and a flat, newest-
 * first list across runs, because "show me what came out of this" is a question
 * about pages and pictures, not about directories.
 *
 * The scan is BOUNDED (`DEFAULT_RUN_SCAN_LIMIT`). One artifact listing per run
 * is one filesystem walk on the backend, and a machine with three hundred
 * archived runs would pay for all of them on every visit to the section. The
 * limit is stated in the UI rather than hidden, so a missing older picture reads
 * as "not scanned" instead of "not there".
 */
import { useQueries } from "@tanstack/react-query";

import {
  useOutputsList,
  type ArtifactSummary,
  type ArtifactsResponse,
  type OutputStatus,
  type OutputSummary,
} from "@/hooks/useOutputs";

/** How a visual is put on screen — one branch of the stage per kind. */
export type VisualKind = "image" | "vector" | "page" | "document";

/**
 * Extension → kind. Deliberately narrow: everything here must render inside the
 * desktop WebView without a plugin or a converter. A format that only *some*
 * hosts can draw would make the stage a coin flip, and a blank frame is a worse
 * answer than not listing the file at all (it still shows up in Outputs).
 */
const VISUAL_EXTENSIONS: ReadonlyArray<readonly [string, VisualKind]> = [
  [".png", "image"],
  [".jpg", "image"],
  [".jpeg", "image"],
  [".gif", "image"],
  [".webp", "image"],
  [".bmp", "image"],
  [".avif", "image"],
  [".svg", "vector"],
  [".html", "page"],
  [".htm", "page"],
  [".pdf", "document"],
];

/** The kind this filename renders as, or null when it is not a visual. */
export function classifyVisual(path: string): VisualKind | null {
  const lower = path.toLowerCase();
  for (const [extension, kind] of VISUAL_EXTENSIONS) {
    if (lower.endsWith(extension)) return kind;
  }
  return null;
}

/** One renderable artifact, already carrying the run it belongs to. */
export interface VisualArtifact {
  /** Run directory name — the `/api/outputs/{slug}` path segment. */
  slug: string;
  /** What the user asked for, when the run recorded it. */
  utterance?: string;
  /** The run's status — a page from a run that failed its review still shows. */
  status?: OutputStatus;
  /** Artifact path relative to the run directory. */
  path: string;
  /** Last path segment — the fallback label. */
  name: string;
  /**
   * What the rail calls it: a page's own `<title>` when the listing's preview
   * carries one, otherwise the filename. The title is what the user asked for
   * in their words ("Umsatz-Dashboard"), the filename is its slug.
   */
  title: string;
  kind: VisualKind;
  size: number;
  mtime: number;
  /** Ready-to-use `src` for an `<img>`/`<iframe>` (served inline, no scripts). */
  url: string;
}

/** Newest runs scanned for visuals. See the module docstring for the why. */
export const DEFAULT_RUN_SCAN_LIMIT = 10;

/**
 * Encode an artifact path segment by segment.
 *
 * Same rule as `useOutputs.encodeArtifactPath` (not exported there): `encodeURI`
 * leaves `#`, `?` and `&` raw, so a file called `chart#2.png` would be truncated
 * at the `#` and 404.
 */
function encodeArtifactPath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

/** The inline URL a visual is rendered from. */
export function visualUrl(slug: string, path: string): string {
  return `/api/outputs/${encodeURIComponent(slug)}/files/${encodeArtifactPath(
    path,
  )}/download?disposition=inline`;
}

/**
 * The ARTIFACT PAGE url of an HTML deliverable: served with scripts allowed
 * and every network path shut (`ARTIFACT_PAGE_CSP`, outputs_routes.py). The
 * stage frames it with `sandbox="allow-scripts"` and no same-origin, so the
 * page's own JavaScript runs — a dashboard filters, a chart draws — inside an
 * opaque origin that cannot reach the app. Non-HTML files keep `visualUrl`.
 */
export function artifactPageUrl(slug: string, path: string): string {
  return `/api/outputs/${encodeURIComponent(slug)}/files/${encodeArtifactPath(path)}/page`;
}

/** A stable identity for one artifact — the selection key and the React key. */
export function visualId(artifact: Pick<VisualArtifact, "slug" | "path">): string {
  return `${artifact.slug}::${artifact.path}`;
}

/** The server-rendered node-graph page for one run (no JS, brand-themed). */
export function missionMapUrl(slug: string): string {
  return `/api/outputs/${encodeURIComponent(slug)}/graph`;
}

const TITLE_RE = /<title[^>]*>([\s\S]*?)<\/title>/i;

/** A page's `<title>` from the listing's text preview, or null. */
export function pageTitleFromPreview(preview: string | null | undefined): string | null {
  if (!preview) return null;
  const match = TITLE_RE.exec(preview);
  if (!match) return null;
  const title = match[1].replace(/\s+/g, " ").trim();
  return title.length > 0 ? decodeEntities(title) : null;
}

/** The handful of entities a `<title>` realistically carries. */
function decodeEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'");
}

function toVisuals(run: OutputSummary, files: ArtifactSummary[]): VisualArtifact[] {
  const visuals: VisualArtifact[] = [];
  for (const file of files) {
    const kind = classifyVisual(file.path);
    if (kind === null) continue;
    const name = file.path.split("/").pop() ?? file.path;
    visuals.push({
      slug: run.slug,
      utterance: run.utterance,
      status: run.status,
      path: file.path,
      name,
      title: (kind === "page" ? pageTitleFromPreview(file.preview) : null) ?? name,
      kind,
      size: file.size,
      mtime: file.mtime,
      url: visualUrl(run.slug, file.path),
    });
  }
  return visuals;
}

export interface VisualArtifactsResult {
  /** Every visual found, newest file first. */
  visuals: VisualArtifact[];
  /** How many runs were actually looked at (≤ `limit`). */
  scannedRuns: number;
  /** Runs that exist but were outside the scan window. */
  skippedRuns: number;
  /** True while the run list or any artifact listing is still in flight. */
  isLoading: boolean;
  /** The run list could not be read at all — the honest "backend is down" case. */
  isError: boolean;
  refetch: () => void;
}

/**
 * Every visual artifact of the newest `limit` runs, newest file first.
 *
 * A run whose artifact listing fails contributes nothing instead of taking the
 * section down with it: half a gallery is still a gallery, and the failure is
 * almost always a run directory that was cleaned up between the two requests.
 */
export function useVisualArtifacts(
  limit: number = DEFAULT_RUN_SCAN_LIMIT,
): VisualArtifactsResult {
  const outputs = useOutputsList();
  const runs = outputs.data ?? [];
  // `/api/outputs` is already sorted newest-first; sorting again here would be
  // a second, weaker opinion about the same order.
  const scanned = runs.slice(0, limit);

  return useQueries({
    queries: scanned.map((run) => ({
      // Same key as `useArtifactsForOutput`, so the Outputs view and this
      // section share one cache entry rather than each fetching their own.
      queryKey: ["output-artifacts", run.slug],
      queryFn: async (): Promise<ArtifactsResponse> => {
        const response = await fetch(
          `/api/outputs/${encodeURIComponent(run.slug)}/artifacts`,
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      },
      // No poll for finished runs: a gallery that re-listed ten run
      // directories every few seconds would keep a disk busy for pages that
      // never change. A RUNNING run is the exception — its artifact is the
      // thing the user is waiting for, so that one listing follows the build
      // and the page appears the moment the worker archives it.
      staleTime: run.status === "running" ? 0 : 30_000,
      refetchInterval: run.status === "running" ? 4_000 : false,
    })),
    combine: (results) => {
      const visuals = results.flatMap((result, index) =>
        result.data ? toVisuals(scanned[index], result.data.files ?? []) : [],
      );
      visuals.sort((a, b) => b.mtime - a.mtime);
      return {
        visuals,
        scannedRuns: scanned.length,
        skippedRuns: Math.max(0, runs.length - scanned.length),
        isLoading: outputs.isLoading || results.some((r) => r.isLoading),
        isError: outputs.isError,
        refetch: () => {
          void outputs.refetch();
          for (const result of results) void result.refetch();
        },
      };
    },
  });
}

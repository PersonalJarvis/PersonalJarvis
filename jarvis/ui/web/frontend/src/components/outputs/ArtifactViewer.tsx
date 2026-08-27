/**
 * ArtifactViewer — the Outputs "Results" pane, laid out like a document reader.
 *
 * Left: a rail listing every deliverable the mission produced. Right: the
 * SELECTED file, shown the way a person wants to look at it — a Markdown
 * report as a typeset document, an HTML page as the running page, an image
 * as an image, code with syntax colours, a CSV as a table. The source text of
 * a rendered file stays one click away ("Source"), and the whole pane expands
 * to a full-window reader. Opening the file in a real app / the browser keeps
 * the existing opener model (chooser, remembered preference, reveal).
 *
 * Security model for HTML: the page is framed from the `/page` endpoint with
 * `sandbox="allow-scripts"` and WITHOUT `allow-same-origin`. Its own scripts
 * run (a chart draws, tabs switch) while the opaque origin plus the server's
 * CSP keep it away from the app's cookies, storage, API and the network.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Code2,
  Copy,
  ExternalLink,
  Eye,
  FileCode2,
  FileImage,
  FileQuestion,
  FileText,
  FolderOpen,
  Globe,
  Loader2,
  Maximize2,
  Table2,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";
import { useThemeValue } from "@/hooks/useTheme";
import {
  artifactKind,
  artifactLanguage,
  formatBytes,
  hasRenderedView,
  isTextKind,
  parseCsv,
  type ArtifactKind,
} from "@/lib/artifactKind";
import {
  artifactInlineUrl,
  artifactOpenUrl,
  artifactPageUrl,
  openArtifactWith,
  revealArtifact,
  useArtifactFile,
  useOpeners,
  usePreferredOpener,
  useSetPreferredOpener,
  type ArtifactSummary,
} from "@/hooks/useOutputs";
import { CodeBlock } from "@/components/docs/CodeBlock";
import { OpenWithDialog } from "@/components/OpenWithDialog";
import { MarkdownProse, resolveSiblingPath } from "@/components/outputs/MarkdownProse";

// Re-exported: the resolver moved to MarkdownProse when the output page
// started sharing the renderer; callers and tests keep importing it here.
export { resolveSiblingPath };

/** Above this many characters the source view skips Shiki — highlighting a
 *  megabyte of text would freeze the UI thread for seconds. */
const HIGHLIGHT_MAX_CHARS = 120_000;
/** Rows a CSV table draws before it stops — the rest is in the file. */
const CSV_MAX_ROWS = 500;

const ARCHIVED_DELIVERABLE_PREFIX = /^tasks\/[^/]+\/artifacts\/files\//;

/** Hide archive plumbing from the label while retaining the full path for APIs. */
export function deliverableDisplayPath(path: string): string {
  return path.replace(ARCHIVED_DELIVERABLE_PREFIX, "");
}

type ViewMode = "rendered" | "source";

export interface ArtifactViewerProps {
  slug: string;
  files: ArtifactSummary[];
  nativeActions: boolean;
  /** The file to open on first — how "Open in Files" on the output page lands on it. */
  initialPath?: string | null;
}

export function ArtifactViewer({ slug, files, nativeActions, initialPath }: ArtifactViewerProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(initialPath ?? null);
  const [fullscreen, setFullscreen] = useState(false);

  // The selection follows the list: first file by default, and a path that
  // vanished (a re-run replaced the deliverables) falls back to the first.
  const selected = useMemo(
    () => files.find((f) => f.path === selectedPath) ?? files[0] ?? null,
    [files, selectedPath],
  );

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const pane = (
    <div
      className={cn(
        "flex min-h-0 w-full",
        fullscreen ? "h-full" : "h-[min(72vh,760px)] min-h-[420px]",
      )}
    >
      <FileRail
        files={files}
        selectedPath={selected?.path ?? null}
        onSelect={(p) => setSelectedPath(p)}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <SelectedFile
            key={`${slug}:${selected.path}`}
            slug={slug}
            file={selected}
            files={files}
            nativeActions={nativeActions}
            fullscreen={fullscreen}
            onToggleFullscreen={() => setFullscreen((v) => !v)}
            onSelectSibling={(p) => setSelectedPath(p)}
          />
        ) : null}
      </div>
    </div>
  );

  if (fullscreen) {
    // Portalled to <body>: an ancestor with a transform (the detail pane's
    // scroll area) would otherwise pin a `fixed` overlay to itself.
    return (
      <>
        {/* Placeholder keeps the page layout stable while the reader is open. */}
        <div className="h-[min(72vh,760px)] min-h-[420px] rounded-xl border border-dashed border-border/60" />
        {createPortal(
          <div
            role="dialog"
            aria-modal="true"
            data-testid="artifact-reader-fullscreen"
            className="fixed inset-0 z-50 flex flex-col bg-background"
          >
            {pane}
          </div>,
          document.body,
        )}
      </>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-background/60">
      {pane}
    </div>
  );
}

// --- File rail -------------------------------------------------------------

function KindIcon({ kind, className }: { kind: ArtifactKind; className?: string }) {
  const cls = cn("h-3.5 w-3.5 shrink-0", className);
  switch (kind) {
    case "markdown":
    case "text":
      return <FileText className={cls} aria-hidden="true" />;
    case "html":
      return <Globe className={cls} aria-hidden="true" />;
    case "image":
      return <FileImage className={cls} aria-hidden="true" />;
    case "pdf":
      return <FileText className={cls} aria-hidden="true" />;
    case "code":
      return <FileCode2 className={cls} aria-hidden="true" />;
    case "csv":
      return <Table2 className={cls} aria-hidden="true" />;
    default:
      return <FileQuestion className={cls} aria-hidden="true" />;
  }
}

function FileRail({
  files,
  selectedPath,
  onSelect,
}: {
  files: ArtifactSummary[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  const t = useT();
  return (
    <nav
      aria-label={t("outputs_view.results")}
      className="flex w-56 shrink-0 flex-col overflow-y-auto border-r border-border/60 bg-card/30 py-2"
    >
      <ul className="flex flex-col gap-px px-1.5">
        {files.map((f) => {
          const display = deliverableDisplayPath(f.path);
          const slash = display.lastIndexOf("/");
          const dir = slash === -1 ? "" : display.slice(0, slash + 1);
          const name = slash === -1 ? display : display.slice(slash + 1);
          const kind = artifactKind(f.path, f.is_text);
          const isSelected = f.path === selectedPath;
          return (
            <li key={f.path}>
              <button
                type="button"
                onClick={() => onSelect(f.path)}
                aria-current={isSelected ? "true" : undefined}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] transition-colors",
                  isSelected
                    ? "bg-primary/10 text-foreground"
                    : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground",
                )}
              >
                <KindIcon
                  kind={kind}
                  className={isSelected ? "text-primary" : "text-muted-foreground/70"}
                />
                <span
                  className="flex min-w-0 flex-1 flex-col font-mono leading-tight"
                  data-testid="artifact-path"
                  title={f.path}
                >
                  {dir && (
                    <span className="truncate text-[10px] text-muted-foreground/60">{dir}</span>
                  )}
                  <span className="truncate">{name}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// --- Selected file: toolbar + body -----------------------------------------

function SelectedFile({
  slug,
  file,
  files,
  nativeActions,
  fullscreen,
  onToggleFullscreen,
  onSelectSibling,
}: {
  slug: string;
  file: ArtifactSummary;
  files: ArtifactSummary[];
  nativeActions: boolean;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  onSelectSibling: (path: string) => void;
}) {
  const t = useT();
  const kind = artifactKind(file.path, file.is_text);
  const [mode, setMode] = useState<ViewMode>("rendered");
  const [chooserOpen, setChooserOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const openers = useOpeners();
  const preferred = usePreferredOpener();
  const setPreferred = useSetPreferredOpener();

  // Text is needed for: a rendered Markdown/CSV document, every source view,
  // plain code/text files. A rendered HTML page and media never fetch it.
  const needsText =
    isTextKind(kind) && !(kind === "html" && mode === "rendered");
  const full = useArtifactFile(slug, needsText ? file.path : null);
  const text = full.data?.text ?? null;

  const openUrl = artifactOpenUrl(slug, file.path);
  const absoluteOpenUrl = openUrl
    ? new URL(openUrl, window.location.origin).toString()
    : null;

  // Open the artifact's render url in the user's real default browser. Routes
  // through openExternalUrl (the backend open-external bridge), NOT a bare
  // window.open — the embedded WebView2 desktop shell silently drops
  // window.open / target="_blank".
  const openInBrowser = () => {
    if (absoluteOpenUrl) void openExternalUrl(absoluteOpenUrl);
  };
  const openWithOpener = (opener: string) => {
    if (opener === "browser" && absoluteOpenUrl) {
      openInBrowser();
      return;
    }
    void openArtifactWith(slug, file.path, opener).catch(() => {});
  };
  const pickOpener = (opener: string, remember: boolean) => {
    openWithOpener(opener);
    if (remember) setPreferred.mutate(opener);
    setChooserOpen(false);
  };
  const handleOpen = () => {
    if (!nativeActions) {
      // Headless VPS / web: the UI is already a real browser tab, so the
      // render URL opens in the user's own browser.
      openInBrowser();
      return;
    }
    const pref = preferred.data ?? "";
    if (pref) openWithOpener(pref);
    else setChooserOpen(true); // first time: ask which app
  };

  const handleCopy = () => {
    if (text === null) return;
    void robustCopy(text).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const display = deliverableDisplayPath(file.path);
  const canToggle = hasRenderedView(kind);
  const iconBtn =
    "rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary/50 hover:text-foreground disabled:opacity-40";

  return (
    <>
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-border/60 bg-card/30 px-3">
        <KindIcon kind={kind} className="text-primary" />
        <span
          className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground"
          title={file.path}
        >
          {`${display}  ·  ${formatBytes(file.size)}`}
        </span>

        {canToggle && (
          <div
            role="group"
            aria-label={t("outputs_view.view_mode")}
            className="ml-2 flex shrink-0 items-center rounded-md border border-border/60 bg-background/60 p-0.5"
          >
            <ModeButton
              active={mode === "rendered"}
              onClick={() => setMode("rendered")}
              icon={<Eye className="h-3 w-3" aria-hidden="true" />}
              label={t("outputs_view.view_rendered")}
            />
            <ModeButton
              active={mode === "source"}
              onClick={() => setMode("source")}
              icon={<Code2 className="h-3 w-3" aria-hidden="true" />}
              label={t("outputs_view.view_source")}
            />
          </div>
        )}

        <div className="ml-1 flex shrink-0 items-center gap-0.5">
          {isTextKind(kind) && (
            <button
              type="button"
              title={t("outputs_view.copy_contents")}
              aria-label={t("outputs_view.copy_contents")}
              disabled={text === null}
              onClick={handleCopy}
              className={iconBtn}
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
              ) : (
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          )}
          {(nativeActions || openUrl) && (
            <button
              type="button"
              title={t("outputs_view.open_action")}
              onClick={handleOpen}
              className={iconBtn}
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
          {nativeActions && (
            <button
              type="button"
              title={t("outputs_view.open_with_change")}
              onClick={() => setChooserOpen(true)}
              className={iconBtn}
            >
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
          {nativeActions && (
            <button
              type="button"
              title={t("outputs_view.reveal_in_folder")}
              onClick={() => void revealArtifact(slug, file.path).catch(() => {})}
              className={iconBtn}
            >
              <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
          <button
            type="button"
            title={fullscreen ? t("outputs_view.exit_fullscreen") : t("outputs_view.fullscreen")}
            aria-label={fullscreen ? t("outputs_view.exit_fullscreen") : t("outputs_view.fullscreen")}
            onClick={onToggleFullscreen}
            className={iconBtn}
          >
            {fullscreen ? (
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        </div>
      </header>

      {chooserOpen && (
        <OpenWithDialog
          openers={openers.data ?? []}
          loading={openers.isLoading}
          onPick={pickOpener}
          onClose={() => setChooserOpen(false)}
        />
      )}

      <div className="relative min-h-0 flex-1 overflow-auto">
        <ArtifactBody
          slug={slug}
          file={file}
          files={files}
          kind={kind}
          mode={mode}
          text={text}
          loading={needsText && full.isLoading}
          error={needsText && full.isError ? String(full.error) : null}
          truncated={!!full.data?.truncated}
          onSelectSibling={onSelectSibling}
        />
      </div>
    </>
  );
}

function ModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
        active
          ? "bg-primary/15 text-foreground"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// --- Body: one renderer per kind -------------------------------------------

function ArtifactBody({
  slug,
  file,
  files,
  kind,
  mode,
  text,
  loading,
  error,
  truncated,
  onSelectSibling,
}: {
  slug: string;
  file: ArtifactSummary;
  files: ArtifactSummary[];
  kind: ArtifactKind;
  mode: ViewMode;
  text: string | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onSelectSibling: (path: string) => void;
}) {
  const t = useT();

  if (kind === "image") {
    return (
      <div className="flex min-h-full items-center justify-center p-6 [background-image:linear-gradient(45deg,hsl(var(--muted)/0.35)_25%,transparent_25%),linear-gradient(-45deg,hsl(var(--muted)/0.35)_25%,transparent_25%),linear-gradient(45deg,transparent_75%,hsl(var(--muted)/0.35)_75%),linear-gradient(-45deg,transparent_75%,hsl(var(--muted)/0.35)_75%)] [background-size:20px_20px] [background-position:0_0,0_10px,10px_-10px,-10px_0]">
        <img
          src={artifactInlineUrl(slug, file.path)}
          alt={deliverableDisplayPath(file.path)}
          className="max-h-full max-w-full rounded-md shadow-sm"
        />
      </div>
    );
  }

  if (kind === "pdf") {
    // A PDF renders through the browser's own viewer; that viewer is a plugin
    // and plugins never load inside a sandboxed frame, so this one is not
    // sandboxed — the bytes are same-origin and inert.
    return (
      <iframe
        title={deliverableDisplayPath(file.path)}
        src={artifactInlineUrl(slug, file.path)}
        className="h-full w-full border-0 bg-white"
      />
    );
  }

  if (kind === "html" && mode === "rendered") {
    return <HtmlPage slug={slug} path={file.path} />;
  }

  if (kind === "binary") {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <FileQuestion className="h-8 w-8 text-muted-foreground/60" aria-hidden="true" />
        <p className="text-xs text-muted-foreground">{t("outputs_view.binary_file_hint")}</p>
      </div>
    );
  }

  if (loading || (text === null && !error)) {
    return (
      <div className="flex items-center gap-2 p-4 text-[11px] text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        {t("outputs_view.loading_file")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-4 text-[11px] text-destructive">
        {t("common.error")}: {error}
      </div>
    );
  }

  const body = text ?? "";
  let content: ReactNode;
  if (kind === "markdown" && mode === "rendered") {
    content = (
      <MarkdownDocument
        slug={slug}
        path={file.path}
        files={files}
        text={body}
        onSelectSibling={onSelectSibling}
      />
    );
  } else if (kind === "csv" && mode === "rendered") {
    content = <CsvTable text={body} tab={file.path.toLowerCase().endsWith(".tsv")} />;
  } else {
    content = <SourceView path={file.path} text={body} />;
  }

  return (
    <>
      {content}
      {truncated && (
        <div className="border-t border-border/60 px-4 py-2 text-[10px] text-muted-foreground">
          {t("outputs_view.file_truncated")}
        </div>
      )}
    </>
  );
}

/**
 * The running HTML page. Framed from `/page` (scripts run, network shut) when
 * the backend serves it; an older backend without that route still shows the
 * page through the inline download (styles and data-URL images render, scripts
 * do not) instead of a blank 404 frame. Either way the frame never gets
 * `allow-same-origin`. The page follows the APP's theme, not the OS's, through
 * the same `?theme=` query the Artifacts stage appends. Drawn full-height in
 * the Files reader and, through `className`, as a block on the output page.
 */
export function HtmlPage({
  slug,
  path,
  className = "h-full w-full",
}: {
  slug: string;
  path: string;
  className?: string;
}) {
  const t = useT();
  const theme = useThemeValue();
  const pageUrl = artifactPageUrl(slug, path);
  const probe = useQuery<boolean>({
    queryKey: ["output-artifact-page-available", slug, path],
    queryFn: async () => {
      try {
        const r = await fetch(pageUrl, { method: "HEAD" });
        // 405 is a backend that HAS the route but predates HEAD support on it
        // (FastAPI registers GET alone): the page is served, so frame it. Only
        // a 404 — no route at all — earns the no-script inline fallback.
        return r.ok || r.status === 405;
      } catch {
        return false;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
  if (probe.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-[11px] text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        {t("outputs_view.loading_file")}
      </div>
    );
  }
  const src = probe.data ? `${pageUrl}?theme=${theme}` : artifactInlineUrl(slug, path);
  return (
    <iframe
      key={`${src}`}
      title={deliverableDisplayPath(path)}
      src={src}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      data-testid="artifact-html-page"
      className={cn("border-0 bg-white", className)}
    />
  );
}

/** Highlighted source for code, plain `<pre>` for text or anything huge. */
export function SourceView({ path, text }: { path: string; text: string }) {
  const language = artifactLanguage(path);
  if (text.length > HIGHLIGHT_MAX_CHARS || language === "txt") {
    return (
      <pre className="m-0 whitespace-pre-wrap break-words p-4 font-mono text-[12px] leading-relaxed text-foreground/90">
        {text}
      </pre>
    );
  }
  return (
    <div className="p-3 [&>div]:my-0">
      <CodeBlock language={language} code={text} />
    </div>
  );
}

/** A CSV/TSV as a table, capped at `CSV_MAX_ROWS` rows. */
export function CsvTable({ text, tab }: { text: string; tab: boolean }) {
  const t = useT();
  const rows = useMemo(() => parseCsv(text, tab ? "\t" : ","), [text, tab]);
  if (rows.length === 0) {
    return <div className="p-4 text-xs text-muted-foreground">{t("outputs_view.csv_empty")}</div>;
  }
  const [head, ...rest] = rows;
  const shown = rest.slice(0, CSV_MAX_ROWS);
  return (
    <div className="p-4">
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full text-[12px]">
          <thead className="bg-muted/40">
            <tr>
              {head.map((h, i) => (
                <th
                  key={i}
                  className="whitespace-nowrap px-3 py-1.5 text-left font-semibold text-foreground"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, ri) => (
              <tr key={ri} className="border-t border-border/60 odd:bg-background/40">
                {head.map((_, ci) => (
                  <td key={ci} className="whitespace-nowrap px-3 py-1 text-foreground/90">
                    {r[ci] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rest.length > shown.length && (
        <p className="mt-2 text-[10px] text-muted-foreground">
          {t("outputs_view.csv_more_rows").replace("{n}", String(rest.length - shown.length))}
        </p>
      )}
    </div>
  );
}

// --- Markdown document -----------------------------------------------------

/**
 * The reader's document: the shared renderer in a serif reading face with a
 * measure — a report reads as a report, while everything else in the app
 * stays in the UI sans.
 */
function MarkdownDocument({
  slug,
  path,
  files,
  text,
  onSelectSibling,
}: {
  slug: string;
  path: string;
  files: ArtifactSummary[];
  text: string;
  onSelectSibling: (path: string) => void;
}) {
  return (
    <MarkdownProse
      slug={slug}
      path={path}
      files={files}
      text={text}
      onSelectSibling={onSelectSibling}
      className={cn(
        "mx-auto max-w-3xl px-8 py-8 lg:px-12",
        "font-serif text-[15.5px] leading-7 prose-headings:font-serif",
        "prose-h1:text-3xl prose-h1:leading-tight prose-h2:mt-10 prose-h2:text-2xl prose-h3:text-xl",
      )}
    />
  );
}

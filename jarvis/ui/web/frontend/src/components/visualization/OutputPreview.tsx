/**
 * The output page — a run that produced no page or picture, shown the way an
 * artifact is shown: one composed page on the stage, not a file list.
 *
 * Every run gets the same treatment (2026-08-25): the artifacts a worker
 * wrote as HTML fill the stage as pages; everything else — a research answer,
 * a memo, a set of scripts, a failed build — is composed HERE from what the
 * run left behind, in the same design standard the artifact brief hands the
 * worker (`jarvis/artifacts/design_guide.py`: the app's tokens, an eyebrow
 * that says something true, a headline, the body in reading order, a
 * hairline where a section ends; no hero, no gradient, no decoration).
 *
 * Reading order, top to bottom:
 * 1. eyebrow (kind · when · duration · status), the headline, the request;
 * 2. why the run ended, when it ended early (failure, cancellation, review);
 * 3. the answer — the worker's final reply, or the run's summary;
 * 4. the deliverables — every file rendered in place: a Markdown report as
 *    a document, an HTML page running in its sandbox, a table as a table, a
 *    picture as a picture; a script, a patch or a data file as a card that
 *    says what it is (its own description, what it defines, which files a
 *    patch touches) with the source folded under it — the reader scrolling
 *    through sees what was made, and the code is one click away here and
 *    under Code / Files. Anything the page cannot draw is one click from
 *    Files.
 *
 * Data: the run itself (`OutputSummary`), the reconstructed plan for the
 * final answer (`/plan`, same cache the run graph reads), the file listing
 * (`/artifacts`, same cache the rail's scan fills) and each text file's
 * bytes (`/raw`). Nothing is fetched twice.
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  ChevronRight,
  FileCode2,
  FileImage,
  FileQuestion,
  FileText,
  Globe,
  Loader2,
  ShieldCheck,
  Table2,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { artifactKind, formatBytes, isTextKind, type ArtifactKind } from "@/lib/artifactKind";
import { codeDigest, type CodeDigest } from "@/lib/codeDigest";
import { cleanRequest, requestHeadline } from "@/lib/runRequest";
import {
  artifactInlineUrl,
  useArtifactFile,
  useArtifactsForOutput,
  usePlanForOutput,
  type ArtifactSummary,
  type OutputStatus,
  type OutputSummary,
} from "@/hooks/useOutputs";
import {
  CsvTable,
  HtmlPage,
  SourceView,
  deliverableDisplayPath,
} from "@/components/outputs/ArtifactViewer";
import { MarkdownProse, splitFrontMatter } from "@/components/outputs/MarkdownProse";
import { deliverableFiles, runNeedsReview } from "@/components/visualization/RunPanels";

/** Files drawn in place; the rest are listed and one click from Files. */
const MAX_INLINE_FILES = 12;
/** Above this a text file is not fetched for the page — Files still shows it. */
const MAX_INLINE_BYTES = 400_000;

/** The document's first `# Heading` — the title the worker gave its report. */
export function markdownTitle(text: string | null | undefined): string | null {
  if (!text) return null;
  const match = /^\s*#\s+(.+?)\s*#*\s*$/m.exec(splitFrontMatter(text).body.slice(0, 4000));
  if (!match) return null;
  const title = match[1].replace(/[*_`]/g, "").trim();
  return title.length > 0 ? title : null;
}

/** Status as a word plus a dot — the one place colour carries meaning here. */
const STATUS_DOT: Record<OutputStatus, string> = {
  success: "bg-muted-foreground",
  error: "bg-destructive",
  running: "bg-primary animate-pulse",
  cancelled: "bg-foreground",
  unknown: "bg-muted-foreground/50",
};

function formatWhen(seconds: number | undefined): string {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number | undefined): string {
  if (typeof seconds !== "number") return "";
  if (seconds < 90) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return `${minutes} min ${rest} s`;
}

/** The first file the page would draw as a document — its title names the run. */
function primaryMarkdown(files: ArtifactSummary[]): ArtifactSummary | null {
  return files.find((f) => artifactKind(f.path, f.is_text) === "markdown") ?? null;
}

export function OutputPreview({
  run,
  onOpenFile,
}: {
  run: OutputSummary;
  /** Switch the stage to Files with this file selected. */
  onOpenFile?: (path: string) => void;
}) {
  const t = useT();
  const listing = useArtifactsForOutput(run.slug);
  const plan = usePlanForOutput(run.slug);
  const lead = useMemo(
    () => primaryMarkdown(deliverableFiles(listing.data?.files ?? [])),
    [listing.data],
  );
  // The report leads the page; scripts, data and assets follow in the
  // reader's order (primary files first, then nested assets).
  const files = useMemo(() => {
    const ordered = deliverableFiles(listing.data?.files ?? []);
    return lead ? [lead, ...ordered.filter((f) => f.path !== lead.path)] : ordered;
  }, [listing.data, lead]);
  const leadText = useArtifactFile(run.slug, lead && lead.size <= MAX_INLINE_BYTES ? lead.path : null);

  const request = cleanRequest(run.utterance);
  const title = markdownTitle(leadText.data?.text) ?? requestHeadline(request);
  // The request repeats the headline when the headline IS the request.
  const showRequest = request.length > 0 && request !== title;

  // The answer is the worker's final reply. The run's summary is what the
  // voice read back — the reply itself (often cut short) or the stock "Done.
  // File X is in the folder…" — so it stands in only when no reply survived.
  const answer = plan.data?.final_answer?.trim() || run.summary?.trim() || "";

  const status = run.status ?? "unknown";
  const needsReview = runNeedsReview(run);
  const endedEarly = !!run.terminal_reason || needsReview || (!!run.error && status === "error");
  const loading = listing.isLoading || plan.isLoading;
  const inline = files.slice(0, MAX_INLINE_FILES);
  const rest = files.slice(MAX_INLINE_FILES);
  const nothing = !loading && files.length === 0 && answer.length === 0 && !endedEarly;

  return (
    <div className="h-full overflow-auto" data-testid="output-preview">
      <article className="mx-auto flex max-w-[1080px] flex-col gap-10 px-8 py-10 lg:px-12">
        <header className="flex flex-col gap-3">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            <span>{t("visualization.output_kind")}</span>
            {run.started_at || run.completed_at ? (
              <>
                <span aria-hidden>·</span>
                <span className="normal-case tracking-normal">
                  {formatWhen(run.completed_at ?? run.started_at)}
                </span>
              </>
            ) : null}
            {typeof run.duration_s === "number" && (
              <>
                <span aria-hidden>·</span>
                <span className="normal-case tracking-normal tabular-nums">
                  {formatDuration(run.duration_s)}
                </span>
              </>
            )}
            <span aria-hidden>·</span>
            <span className="inline-flex items-center gap-1.5" data-testid="output-preview-status">
              <span className={cn("h-1.5 w-1.5 rounded-full", STATUS_DOT[status])} aria-hidden />
              {needsReview ? t("outputs_view.needs_review") : t(`visualization.status_word_${status}`)}
            </span>
          </p>
          <h1
            className="text-balance text-[clamp(26px,3.2vw,34px)] font-semibold leading-tight tracking-tight text-foreground"
            data-testid="output-preview-title"
          >
            {title || run.slug}
          </h1>
          {showRequest && (
            <p
              className="max-w-[68ch] text-[15px] leading-relaxed text-muted-foreground"
              data-testid="output-preview-request"
            >
              {request}
            </p>
          )}
        </header>

        {endedEarly && (
          <section
            data-testid="output-preview-outcome"
            className={cn(
              "flex gap-3 rounded-xl border p-4",
              needsReview || status === "cancelled"
                ? "border-foreground/30 bg-foreground/5"
                : "border-destructive/30 bg-destructive/5",
            )}
          >
            <AlertTriangle
              className={cn(
                "mt-0.5 h-4 w-4 shrink-0",
                needsReview || status === "cancelled" ? "text-foreground" : "text-destructive",
              )}
              aria-hidden
            />
            <div className="flex min-w-0 flex-col gap-1">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {needsReview
                  ? t("outputs_view.needs_review")
                  : status === "cancelled"
                    ? t("outputs_view.cancellation_reason")
                    : t("outputs_view.failure_reason")}
              </p>
              {needsReview && (
                <p className="text-sm leading-relaxed text-foreground/90">
                  {t("outputs_view.needs_review_hint")}
                </p>
              )}
              {!needsReview && status === "error" && run.has_partial_output && (
                <p className="text-sm leading-relaxed text-foreground/90">
                  {t("outputs_view.partial_output_hint")}
                </p>
              )}
              {run.terminal_reason && (
                <p className="font-mono text-[12px] text-muted-foreground">{run.terminal_reason}</p>
              )}
              {run.error && !run.terminal_reason && (
                <pre className="whitespace-pre-wrap font-mono text-[12px] text-destructive/90">
                  {run.error}
                </pre>
              )}
            </div>
          </section>
        )}

        {loading && answer.length === 0 && files.length === 0 && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("outputs_view.loading_artifacts")}
          </p>
        )}

        {nothing && (
          <p className="text-sm text-muted-foreground" data-testid="output-preview-nothing">
            {t("visualization.preview_nothing")}
          </p>
        )}

        {answer.length > 0 && (
          <section className="flex flex-col gap-3" data-testid="output-preview-answer">
            <SectionEyebrow>{t("visualization.preview_answer")}</SectionEyebrow>
            <MarkdownProse
              slug={run.slug}
              path=""
              files={files}
              text={answer}
              onSelectSibling={onOpenFile}
              className="max-w-none text-[15px] leading-7 prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg"
              testId="output-preview-answer-body"
            />
          </section>
        )}

        {files.length > 0 && (
          <section className="flex flex-col gap-4" data-testid="output-preview-files">
            <SectionEyebrow>
              {t("visualization.preview_deliverables")}
              <span className="ml-2 font-normal normal-case tracking-normal text-muted-foreground/70">
                {files.length} {files.length === 1 ? t("outputs_view.file") : t("outputs_view.files")}
              </span>
            </SectionEyebrow>
            {inline.map((file) => (
              <FileSection
                key={file.path}
                slug={run.slug}
                file={file}
                files={files}
                onOpenFile={onOpenFile}
              />
            ))}
            {rest.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {t("visualization.preview_more_files").replace("{0}", String(rest.length))}
              </p>
            )}
          </section>
        )}
      </article>
    </div>
  );
}

/** A section's label — small caps, a hairline above, the way the standard marks a section. */
function SectionEyebrow({ children }: { children: ReactNode }) {
  return (
    <h2 className="border-t border-border pt-4 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
      {children}
    </h2>
  );
}

function KindIcon({ kind, className }: { kind: ArtifactKind; className?: string }) {
  const cls = cn("h-3.5 w-3.5 shrink-0", className);
  switch (kind) {
    case "markdown":
    case "text":
    case "pdf":
      return <FileText className={cls} aria-hidden />;
    case "html":
      return <Globe className={cls} aria-hidden />;
    case "image":
      return <FileImage className={cls} aria-hidden />;
    case "code":
      return <FileCode2 className={cls} aria-hidden />;
    case "csv":
      return <Table2 className={cls} aria-hidden />;
    default:
      return <FileQuestion className={cls} aria-hidden />;
  }
}

/**
 * One deliverable in place: a labelled block with the file's own rendering
 * inside. Text is fetched only for kinds the page draws from text; a page
 * (HTML) runs in its sandbox from the server's `/page` route; a binary is
 * named and left to Files, where the reader has the openers for it.
 */
function FileSection({
  slug,
  file,
  files,
  onOpenFile,
}: {
  slug: string;
  file: ArtifactSummary;
  files: ArtifactSummary[];
  onOpenFile?: (path: string) => void;
}) {
  const t = useT();
  const kind = artifactKind(file.path, file.is_text);
  const drawsFromText = isTextKind(kind) && kind !== "html";
  const tooLarge = file.size > MAX_INLINE_BYTES;
  const full = useArtifactFile(slug, drawsFromText && !tooLarge ? file.path : null);
  const display = deliverableDisplayPath(file.path);

  let body: ReactNode;
  if (kind === "image") {
    body = (
      <div className="flex justify-center p-4">
        <img
          src={artifactInlineUrl(slug, file.path)}
          alt={display}
          loading="lazy"
          className="max-h-[70vh] max-w-full rounded-md"
        />
      </div>
    );
  } else if (kind === "html") {
    body = (
      <div className="flex flex-col" data-testid="output-preview-page">
        <HtmlPage slug={slug} path={file.path} className="h-[min(70vh,720px)] w-full" />
        <p className="flex items-center gap-1.5 border-t border-border/60 px-4 py-1.5 text-[11px] text-muted-foreground">
          <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden />
          {t("visualization.page_sandbox_note")}
        </p>
      </div>
    );
  } else if (!drawsFromText || tooLarge) {
    body = (
      <p className="px-4 py-3 text-xs text-muted-foreground">
        {t(tooLarge ? "visualization.preview_too_large" : "visualization.preview_open_in_files_hint")}
      </p>
    );
  } else if (full.isLoading || (!full.data && !full.isError)) {
    body = (
      <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        {t("outputs_view.loading_file")}
      </p>
    );
  } else if (full.isError || !full.data) {
    body = (
      <p className="px-4 py-3 text-xs text-destructive">
        {t("common.error")}: {String(full.error ?? "")}
      </p>
    );
  } else if (kind === "markdown") {
    body = (
      <MarkdownProse
        slug={slug}
        path={file.path}
        files={files}
        text={full.data.text}
        onSelectSibling={onOpenFile}
        className="max-w-none px-6 py-5 text-[15px] leading-7 prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg"
        testId="output-preview-markdown"
      />
    );
  } else if (kind === "csv") {
    body = <CsvTable text={full.data.text} tab={file.path.toLowerCase().endsWith(".tsv")} />;
  } else {
    body = <CodeCard path={file.path} text={full.data.text} />;
  }

  return (
    <section
      className="overflow-hidden rounded-xl border border-border bg-card/40"
      data-testid="output-preview-file"
      data-kind={kind}
    >
      <header className="flex h-10 items-center gap-2 border-b border-border/60 bg-card/60 px-4">
        <KindIcon kind={kind} className="text-primary" />
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground" title={file.path}>
          {display}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {formatBytes(file.size)}
        </span>
        {onOpenFile && (
          <button
            type="button"
            onClick={() => onOpenFile(file.path)}
            className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-secondary/50 hover:text-foreground"
            title={t("visualization.preview_open_in_files")}
          >
            {t("visualization.preview_open_in_files")}
            <ArrowUpRight className="h-3 w-3" aria-hidden />
          </button>
        )}
      </header>
      {full.data?.truncated && (
        <p className="border-b border-border/60 px-4 py-1.5 text-[10px] text-muted-foreground">
          {t("outputs_view.file_truncated")}
        </p>
      )}
      {body}
    </section>
  );
}

/** A file this short is shown whole — folding twelve lines away would be theatre. */
const SHORT_FILE_LINES = 30;
/** Definitions, keys or touched files named on the card before "+N more". */
const MAX_NAMED = 12;

/**
 * A script, a patch or a data file as a card: what the file says about
 * itself first (`codeDigest`), the source folded beneath it — open by
 * default only when the file is short. The reader who wants every line has
 * the fold, and the Code and Files tabs.
 */
function CodeCard({ path, text }: { path: string; text: string }) {
  const t = useT();
  const digest = useMemo(() => codeDigest(path, text), [path, text]);
  const [open, setOpen] = useState(digest.lines <= SHORT_FILE_LINES);
  return (
    <div data-testid="output-preview-code-card" data-open={open ? "true" : "false"}>
      <div className="flex flex-col gap-2 px-4 py-3">
        {digest.description && (
          <p
            className="max-w-[72ch] text-[14px] leading-relaxed text-foreground/90"
            data-testid="output-preview-code-description"
          >
            {digest.description}
          </p>
        )}
        <DigestFacts digest={digest} />
      </div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 border-t border-border/60 px-4 py-2 text-left text-[11px] text-muted-foreground transition-colors hover:bg-secondary/40 hover:text-foreground"
      >
        <ChevronRight
          className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden
        />
        {open ? t("visualization.preview_hide_source") : t("visualization.preview_show_source")}
      </button>
      {open && (
        <div className="border-t border-border/60" data-testid="output-preview-code-source">
          <SourceView path={path} text={text} />
        </div>
      )}
    </div>
  );
}

/** The card's facts line(s): language · lines, then what the file defines or touches. */
function DigestFacts({ digest }: { digest: CodeDigest }) {
  const t = useT();
  const lines =
    digest.lines === 1
      ? t("visualization.preview_line_one")
      : t("visualization.preview_lines").replace("{0}", String(digest.lines));
  const named = <T,>(items: T[], draw: (item: T) => ReactNode) => (
    <>
      {items.slice(0, MAX_NAMED).map((item, i) => (
        <span key={i}>
          {i > 0 && <span className="text-muted-foreground/60">, </span>}
          {draw(item)}
        </span>
      ))}
      {items.length > MAX_NAMED && (
        <span className="text-muted-foreground"> +{items.length - MAX_NAMED}</span>
      )}
    </>
  );
  const added = digest.diff?.reduce((n, f) => n + f.added, 0) ?? 0;
  const removed = digest.diff?.reduce((n, f) => n + f.removed, 0) ?? 0;

  return (
    <div className="flex flex-col gap-1.5" data-testid="output-preview-code-facts">
      <p className="flex flex-wrap items-center gap-x-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        <span>{digest.language}</span>
        <span aria-hidden>·</span>
        <span className="normal-case tracking-normal tabular-nums">{lines}</span>
        {digest.diff && digest.diff.length > 0 && (
          <>
            <span aria-hidden>·</span>
            <span className="normal-case tracking-normal tabular-nums">
              {digest.diff.length === 1
                ? t("visualization.preview_file_changed_one")
                : t("visualization.preview_files_changed").replace("{0}", String(digest.diff.length))}
              <span className="ml-2 text-muted-foreground">+{added}</span>
              <span className="ml-1.5 text-destructive">−{removed}</span>
            </span>
          </>
        )}
        {digest.json && (
          <>
            <span aria-hidden>·</span>
            <span className="normal-case tracking-normal tabular-nums">
              {digest.json.kind === "object"
                ? t("visualization.preview_json_object").replace("{0}", String(digest.json.count))
                : t("visualization.preview_json_array").replace("{0}", String(digest.json.count))}
            </span>
          </>
        )}
      </p>
      {digest.symbols.length > 0 && (
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          <span className="mr-2 text-[11px] font-semibold uppercase tracking-[0.08em]">
            {t("visualization.preview_defines")}
          </span>
          {named(digest.symbols, (s) => (
            <code className="font-mono text-[12px] text-foreground/80">
              {s.kind === "function" ? `${s.name}()` : s.name}
            </code>
          ))}
        </p>
      )}
      {digest.json?.kind === "object" && digest.json.keys.length > 0 && (
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          {named(digest.json.keys, (k) => (
            <code className="font-mono text-[12px] text-foreground/80">{k}</code>
          ))}
        </p>
      )}
      {digest.diff && digest.diff.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-[12px]" data-testid="output-preview-diff-files">
          {digest.diff.slice(0, MAX_NAMED).map((f) => (
            <li key={f.path} className="flex items-baseline gap-3">
              <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground/80">
                {f.path}
              </code>
              <span className="shrink-0 font-mono text-[11px] tabular-nums">
                <span className="text-muted-foreground">+{f.added}</span>
                <span className="ml-1.5 text-destructive">−{f.removed}</span>
              </span>
            </li>
          ))}
          {digest.diff.length > MAX_NAMED && (
            <li className="text-[11px] text-muted-foreground">+{digest.diff.length - MAX_NAMED}</li>
          )}
        </ul>
      )}
    </div>
  );
}

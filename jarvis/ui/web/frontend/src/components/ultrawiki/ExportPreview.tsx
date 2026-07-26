/**
 * The export-import add-flow: get the file here, then look inside it.
 *
 * Two problems this solves, both of which the plain path field could not:
 *
 *   • The file is usually not on the machine running Jarvis. "Type the full
 *     path to your Takeout archive" is a non-starter on a headless server and
 *     unfriendly even on a desktop, so the upload control streams it over and
 *     fills the path field with wherever it landed.
 *   • "Approve & import everything" is a big button to press blind. The
 *     preview answers what "everything" IS — how many mails, events, chat
 *     days and table chunks were found, and how many files will be skipped as
 *     unrecognised — BEFORE the source is created.
 *
 * The preview is deliberately read-only and changes nothing: consent still
 * starts pending, and approval is still the one gate before a byte is read.
 * Where the backend says its own count is a lower bound (`truncated`), that
 * sentence is shown rather than smoothed over.
 */
import { useRef, useState } from "react";
import { FileUp, Loader2, ScanSearch } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT, useUiLanguage } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import {
  previewUltraWikiExport,
  uploadUltraWikiExport,
  type UltraWikiExportPreview,
} from "@/lib/ultrawikiApi";

/** One "3,214 mails" segment of the found-line. */
export interface ExportSummarySegment {
  key: string;
  label: string;
  count: number;
  exact: boolean;
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatBytes(bytes: number): string {
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = value >= 100 || unit === 0 ? Math.round(value) : Number(value.toFixed(1));
  return `${rounded} ${BYTE_UNITS[unit]}`;
}

/**
 * Thousands grouping follows the app's UI LANGUAGE, never the browser's or
 * the machine's locale — the trap `components/runs/format.ts` documents: on a
 * German-locale machine running the English UI, a bare `toLocaleString()`
 * renders 3214 as "3.214", which an English reader parses as three-point-two.
 */
function counter(language: string): (value: number) => string {
  const locale = localeForUiLanguage(language);
  return (value: number) => value.toLocaleString(locale);
}

/**
 * Fold the report into the segments the found-line renders.
 *
 * Formats that share a label are merged (an mbox and a loose `.eml` are both
 * "mails"): showing "3,214 mails - 1 mails" would read as a bug, not as
 * detail.
 */
export function summarizeExportPreview(
  report: UltraWikiExportPreview,
  t: (key: string) => string,
): ExportSummarySegment[] {
  const byLabel = new Map<string, ExportSummarySegment>();
  for (const [format, row] of Object.entries(report.formats ?? {})) {
    const items = Number(row?.items_estimate ?? 0);
    if (items <= 0) continue;
    const label = t(`ultrawiki.export.format_${format}`);
    const existing = byLabel.get(label);
    if (existing) {
      existing.count += items;
      existing.exact = existing.exact && row?.exact !== false;
      continue;
    }
    byLabel.set(label, {
      key: format,
      label,
      count: items,
      exact: row?.exact !== false,
    });
  }
  return [...byLabel.values()].sort((a, b) => b.count - a.count);
}

export function ExportPreview({
  path,
  onPathChange,
}: {
  path: string;
  onPathChange: (path: string) => void;
}): JSX.Element {
  const t = useT();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState("");
  const [uploaded, setUploaded] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [report, setReport] = useState<UltraWikiExportPreview | null>(null);
  const [error, setError] = useState("");

  const inputCls =
    "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/40";

  async function handleUpload(file: File) {
    setError("");
    setReport(null);
    setUploaded("");
    setUploading(file.name);
    try {
      const result = await uploadUltraWikiExport(file);
      onPathChange(result.path);
      setUploaded(t("ultrawiki.export.uploaded").replace("{0}", result.name));
    } catch (err) {
      setError(
        t("ultrawiki.export.upload_failed").replace(
          "{0}",
          (err as Error).message,
        ),
      );
    } finally {
      setUploading("");
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handlePreview() {
    const target = path.trim();
    if (!target) return;
    setError("");
    setPreviewing(true);
    try {
      setReport(await previewUltraWikiExport(target));
    } catch (err) {
      setReport(null);
      setError(
        t("ultrawiki.export.preview_failed").replace(
          "{0}",
          (err as Error).message,
        ),
      );
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <div className="space-y-2" data-testid="ultrawiki-export-panel">
      <label className="block">
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.export.path_label")}
        </span>
        <input
          type="text"
          value={path}
          onChange={(e) => {
            onPathChange(e.target.value);
            setReport(null);
          }}
          className={inputCls}
          data-testid="ultrawiki-export-path-input"
        />
        <span className="mt-1 block text-[11px] text-muted-foreground">
          {t("ultrawiki.export.path_hint")}
        </span>
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <label
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[11px] text-foreground hover:bg-secondary/30"
          data-testid="ultrawiki-export-upload-label"
        >
          <FileUp className="h-3.5 w-3.5" aria-hidden />
          {t("ultrawiki.export.upload_button")}
          <input
            ref={fileInput}
            type="file"
            className="sr-only"
            data-testid="ultrawiki-export-upload-input"
            aria-label={t("ultrawiki.export.upload_label")}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleUpload(file);
            }}
          />
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void handlePreview()}
          disabled={previewing || !path.trim()}
          data-testid="ultrawiki-export-preview"
        >
          {previewing ? (
            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <ScanSearch className="mr-1 h-3.5 w-3.5" aria-hidden />
          )}
          {t("ultrawiki.export.preview_button")}
        </Button>
        {uploading && (
          <span
            className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground"
            data-testid="ultrawiki-export-uploading"
          >
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("ultrawiki.export.uploading").replace("{0}", uploading)}
          </span>
        )}
        {!uploading && uploaded && (
          <span
            className="text-[11px] text-muted-foreground"
            data-testid="ultrawiki-export-uploaded"
          >
            {uploaded}
          </span>
        )}
      </div>

      {error && (
        <p
          className="text-[11px] text-destructive"
          role="alert"
          data-testid="ultrawiki-export-error"
        >
          {error}
        </p>
      )}

      {report && <ExportReport report={report} />}
    </div>
  );
}

function ExportReport({
  report,
}: {
  report: UltraWikiExportPreview;
}): JSX.Element {
  const t = useT();
  const count = counter(useUiLanguage());
  const segments = summarizeExportPreview(report, t);
  const extras: string[] = [];
  if (report.unknown_files > 0) {
    extras.push(
      t("ultrawiki.export.unknown_skipped").replace(
        "{0}",
        count(report.unknown_files),
      ),
    );
  }
  if (report.unreadable.length > 0) {
    extras.push(
      t("ultrawiki.export.unreadable").replace(
        "{0}",
        count(report.unreadable.length),
      ),
    );
  }
  if (report.archives.files > 0) {
    extras.push(
      t("ultrawiki.export.archives")
        .replace("{0}", count(report.archives.files))
        .replace("{1}", count(report.archives.entries)),
    );
  }
  extras.push(
    t("ultrawiki.export.total_size").replace("{0}", formatBytes(report.total_bytes)),
  );

  return (
    <div
      className="rounded-lg border border-border bg-card/40 p-2.5"
      data-testid="ultrawiki-export-report"
    >
      {segments.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {t("ultrawiki.export.nothing_found")}
        </p>
      ) : (
        <p className="text-xs text-foreground">
          <span className="text-muted-foreground">
            {t("ultrawiki.export.found")}{" "}
          </span>
          {segments.map((segment, index) => (
            <span key={segment.key} data-testid={`uw-export-count-${segment.key}`}>
              {index > 0 && <span className="text-muted-foreground"> · </span>}
              <span className="font-medium">
                {segment.exact ? "" : "~"}
                {count(segment.count)}
              </span>{" "}
              {segment.label}
            </span>
          ))}
        </p>
      )}
      <p className="mt-1 text-[11px] text-muted-foreground">
        {extras.join(" · ")}
      </p>
      {report.truncated && (
        <p
          className="mt-1 text-[11px] text-[#ffb84d]"
          data-testid="ultrawiki-export-truncated"
        >
          {t("ultrawiki.export.truncated")}
        </p>
      )}
      {report.notes.map((note) => (
        <p key={note} className="mt-1 text-[11px] text-muted-foreground">
          {note}
        </p>
      ))}
    </div>
  );
}

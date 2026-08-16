import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  FolderOpen,
  Link2,
  Loader2,
  ShieldAlert,
  Upload,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { UploadDropzone, useUploadInputs } from "@/components/UploadDropzone";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useImportSkill } from "@/hooks/useSkills";
import { collectDroppedFiles, formatBytes, type PickedFile } from "@/lib/filePicking";
import {
  inspectSkillUpload,
  uploadSkill,
  type SkillUploadReport,
} from "@/lib/skillUpload";

/**
 * Bringing your own skill in — the drop-a-folder flow.
 *
 * The shape follows what the public skill hubs settled on, for a good reason:
 * the files come first and the details come second. You drop a folder, the
 * dialog tells you what it found in it, and only then is there anything to
 * confirm. Nothing is typed that can be read.
 *
 * Two ways in, because we have two:
 *   - upload a folder or a .zip / .tar.gz from this machine,
 *   - fetch a single SKILL.md from a link.
 * The link route has existed in the backend for a long time and was never
 * wired into a screen; it is reachable from here now.
 *
 * What the preview must never do is understate the result. If the safety lint
 * bites, the skill lands as a draft rather than going live — so the preview
 * says "draft" up front instead of letting the owner discover it afterwards.
 */

type Mode = "choose" | "files" | "link";

export function SkillUploadDialog({
  open,
  onClose,
  onInstalled,
}: {
  open: boolean;
  onClose: () => void;
  onInstalled?: (name: string) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("choose");
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [report, setReport] = useState<SkillUploadReport | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [link, setLink] = useState("");
  const importFromLink = useImportSkill();

  const reset = useCallback(() => {
    setMode("choose");
    setPicked([]);
    setReport(null);
    setInspecting(false);
    setInstalling(false);
    setError(null);
    setDragging(false);
    setLink("");
  }, []);

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  const inspect = useCallback(
    async (files: PickedFile[]) => {
      setPicked(files);
      setReport(null);
      setError(null);
      if (files.length === 0) return;
      setInspecting(true);
      try {
        setReport(await inspectSkillUpload(files));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setInspecting(false);
      }
    },
    [],
  );

  const { inputs, openFolder, openArchive } = useUploadInputs(
    (files) => void inspect(files),
    "skill",
  );

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setDragging(false);
    setMode("files");
    void collectDroppedFiles(event.dataTransfer).then(inspect);
  };

  const install = async () => {
    if (picked.length === 0) return;
    setInstalling(true);
    setError(null);
    try {
      const detail = await uploadSkill<{ name: string }>(picked);
      await qc.invalidateQueries({ queryKey: ["skills"] });
      onInstalled?.(detail.name);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(false);
    }
  };

  const installFromLink = () => {
    if (!link.trim()) return;
    setError(null);
    importFromLink.mutate(link.trim(), {
      onSuccess: (detail) => {
        onInstalled?.(detail.name);
        onClose();
      },
      onError: (err) => setError(err instanceof Error ? err.message : String(err)),
    });
  };

  if (!open) return null;

  const blocker = describeBlocker(report, picked.length, t);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 p-6"
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="flex max-h-full w-[640px] flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
        <header className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
          <div>
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <Upload className="h-4 w-4 text-primary" />
              {t("skill_upload.title")}
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("skill_upload.subtitle")}
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("common.cancel")}>
            <X className="h-4 w-4" />
          </Button>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="px-6 py-5">
            {mode === "choose" && (
              <div className="grid gap-3">
                <SourceCard
                  icon={<FolderOpen className="h-5 w-5" />}
                  title={t("skill_upload.source_files_title")}
                  description={t("skill_upload.source_files_desc")}
                  action={t("skill_upload.source_files_action")}
                  onSelect={() => setMode("files")}
                />
                <SourceCard
                  icon={<Link2 className="h-5 w-5" />}
                  title={t("skill_upload.source_link_title")}
                  description={t("skill_upload.source_link_desc")}
                  action={t("skill_upload.source_link_action")}
                  onSelect={() => setMode("link")}
                />
              </div>
            )}

            {mode === "files" && (
              <div className="flex flex-col gap-4">
                <BackLink label={t("skill_upload.back")} onClick={reset} />

                {inputs}

                {picked.length === 0 ? (
                  <UploadDropzone
                    dragging={dragging}
                    title={t("skill_upload.drop_title")}
                    hint={t("skill_upload.drop_hint")}
                    folderLabel={t("skill_upload.choose_folder")}
                    archiveLabel={t("skill_upload.choose_archive")}
                    onChooseFolder={openFolder}
                    onChooseArchive={openArchive}
                  />
                ) : (
                  <UploadReport
                    report={report}
                    inspecting={inspecting}
                    fileCount={picked.length}
                    onReplace={openFolder}
                    onClear={() => void inspect([])}
                  />
                )}
              </div>
            )}

            {mode === "link" && (
              <div className="flex flex-col gap-4">
                <BackLink label={t("skill_upload.back")} onClick={reset} />
                <div className="flex flex-col gap-2">
                  <label htmlFor="skill-import-link" className="text-sm font-medium">
                    {t("skill_upload.link_label")}
                  </label>
                  <input
                    id="skill-import-link"
                    autoFocus
                    value={link}
                    onChange={(event) => setLink(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") installFromLink();
                    }}
                    placeholder="https://github.com/.../SKILL.md"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("skill_upload.link_hint")}
                  </p>
                </div>
              </div>
            )}

            {error && (
              <p className="mt-4 flex items-start gap-2 text-sm text-destructive" role="alert">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {error}
              </p>
            )}
          </div>
        </ScrollArea>

        {mode !== "choose" && (
          <footer className="flex items-center justify-between gap-4 border-t border-border px-6 py-4">
            <p className="min-w-0 text-xs text-muted-foreground">{blocker}</p>
            <div className="flex shrink-0 items-center gap-2">
              <Button size="sm" variant="ghost" onClick={onClose}>
                {t("common.cancel")}
              </Button>
              {mode === "files" ? (
                <Button
                  size="sm"
                  onClick={() => void install()}
                  disabled={!report?.ready || installing || inspecting}
                  className="gap-1.5"
                >
                  {installing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Check className="h-3.5 w-3.5" />
                  )}
                  {t("skill_upload.install")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  onClick={installFromLink}
                  disabled={!link.trim() || importFromLink.isPending}
                  className="gap-1.5"
                >
                  {importFromLink.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Check className="h-3.5 w-3.5" />
                  )}
                  {t("skill_upload.install")}
                </Button>
              )}
            </div>
          </footer>
        )}
      </div>
    </div>
  );
}

/**
 * Where the install stands, in one line.
 *
 * A short state, not the reason: when something blocks, the reason is already
 * on screen in red right above this line, and repeating it verbatim reads as
 * two separate problems.
 */
function describeBlocker(
  report: SkillUploadReport | null,
  fileCount: number,
  t: (key: string) => string,
): string {
  if (fileCount === 0) return t("skill_upload.blocker_no_files");
  if (!report) return "";
  if (report.problems.length > 0) return t("skill_upload.blocked");
  if (report.lint_findings.length > 0) return t("skill_upload.will_land_as_draft");
  return t("skill_upload.ready");
}

function BackLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-fit items-center gap-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-3 w-3" />
      {label}
    </button>
  );
}

function SourceCard({
  icon,
  title,
  description,
  action,
  onSelect,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="group flex items-start gap-4 rounded-lg border border-border bg-muted/20 p-4 text-left transition-colors hover:border-primary hover:bg-muted/40"
    >
      <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-border bg-card text-primary">
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold">{title}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{description}</span>
      </span>
      <span className="mt-2 flex shrink-0 items-center gap-1 text-xs font-medium text-muted-foreground transition-colors group-hover:text-foreground">
        {action}
        <ArrowRight className="h-3 w-3" />
      </span>
    </button>
  );
}

/** What the upload holds, once the server has looked at it. */
function UploadReport({
  report,
  inspecting,
  fileCount,
  onReplace,
  onClear,
}: {
  report: SkillUploadReport | null;
  inspecting: boolean;
  fileCount: number;
  onReplace: () => void;
  onClear: () => void;
}) {
  const t = useT();

  if (inspecting || !report) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/20 px-4 py-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {`${t("skill_upload.inspecting")} (${fileCount})`}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-4 rounded-lg border border-border bg-muted/20 px-4 py-3">
        <div className="min-w-0">
          {report.skill ? (
            <>
              <p className="truncate text-sm font-semibold">{report.skill.name}</p>
              <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                {report.skill.description}
              </p>
            </>
          ) : (
            <p className="text-sm font-medium text-muted-foreground">
              {t("skill_upload.no_skill_detected")}
            </p>
          )}
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            {`${report.files.length} ${t("skill_upload.files")} · ${formatBytes(report.total_bytes)}`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="outline" onClick={onReplace}>
            {t("skill_upload.replace")}
          </Button>
          <Button size="sm" variant="ghost" onClick={onClear}>
            {t("skill_upload.clear")}
          </Button>
        </div>
      </div>

      {report.files.length > 0 && (
        <div className="max-h-[180px] overflow-y-auto rounded-lg border border-border bg-background">
          <ul className="flex flex-col gap-0.5 p-2">
            {report.files.map((path) => (
              <li
                key={path}
                className="truncate rounded px-2 py-1 font-mono text-[11px] text-muted-foreground"
                title={path}
              >
                {path}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.problems.map((problem) => (
        <Notice key={problem} tone="error" icon={<AlertTriangle className="h-3.5 w-3.5" />}>
          {problem}
        </Notice>
      ))}

      {report.lint_findings.length > 0 && (
        <Notice tone="warn" icon={<ShieldAlert className="h-3.5 w-3.5" />}>
          <span className="font-medium">{t("skill_upload.lint_title")}</span>{" "}
          {t("skill_upload.lint_body")}
          <ul className="mt-1 list-disc pl-4">
            {report.lint_findings.slice(0, 4).map((finding) => (
              <li key={finding}>{finding}</li>
            ))}
          </ul>
        </Notice>
      )}

      {report.ignored.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          {`${t("skill_upload.ignored")} (${report.ignored.length}): ${report.ignored
            .slice(0, 3)
            .map((path) => path.split("/").pop())
            .join(", ")}${report.ignored.length > 3 ? ", …" : ""}`}
        </p>
      )}
    </div>
  );
}

function Notice({
  tone,
  icon,
  children,
}: {
  tone: "error" | "warn";
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
        // Amber rather than a token: the app has no warning colour, and its
        // brand accent IS amber — so the shade is spelled out for both
        // appearances instead of borrowing one that only reads in the dark.
        tone === "error"
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
      )}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

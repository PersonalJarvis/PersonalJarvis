import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Loader2, Plug, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { UploadDropzone, useUploadInputs } from "@/components/UploadDropzone";
import { useT } from "@/i18n";
import { collectDroppedFiles, formatBytes, type PickedFile } from "@/lib/filePicking";
import {
  inspectPluginUpload,
  uploadPlugin,
  type PluginUploadReport,
} from "@/lib/pluginUpload";

/**
 * Bringing your own plugin in.
 *
 * Shorter than the skill flow, because a plugin here is a *manifest* rather
 * than a code package: a `plugin.json`, optionally with an `mcp.json` beside
 * it. There is no folder of code to review, so the preview shows what the
 * catalog card will say — name, category, and above all which authentication
 * mode the plugin will ask you for.
 *
 * The one thing it must not do is imply a review that never happened. An
 * uploaded plugin carries no publisher and no source URL, because there is
 * nothing here to vouch for either — and the dialog says so before the yes.
 */
export function PluginUploadDialog({
  open,
  onClose,
  onInstalled,
}: {
  open: boolean;
  onClose: () => void;
  onInstalled?: (id: string) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [report, setReport] = useState<PluginUploadReport | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const reset = useCallback(() => {
    setPicked([]);
    setReport(null);
    setInspecting(false);
    setInstalling(false);
    setError(null);
    setDragging(false);
  }, []);

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  const inspect = useCallback(async (files: PickedFile[]) => {
    setPicked(files);
    setReport(null);
    setError(null);
    if (files.length === 0) return;
    setInspecting(true);
    try {
      setReport(await inspectPluginUpload(files));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInspecting(false);
    }
  }, []);

  const { inputs, openFolder, openArchive } = useUploadInputs(
    (files) => void inspect(files),
    "plugin",
  );

  const install = async () => {
    if (picked.length === 0) return;
    setInstalling(true);
    setError(null);
    try {
      const result = await uploadPlugin(picked);
      await qc.invalidateQueries({ queryKey: ["marketplace", "plugins"] });
      onInstalled?.(result.plugin.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(false);
    }
  };

  if (!open) return null;

  // A short state, not the reason. The reason is already on screen in red,
  // right above this line — repeating it verbatim reads as two problems.
  const blocker =
    picked.length === 0
      ? t("plugin_upload.blocker_no_files")
      : report
        ? report.problems.length > 0
          ? t("plugin_upload.blocked")
          : t("plugin_upload.ready")
        : "";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 p-6"
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        void collectDroppedFiles(event.dataTransfer).then(inspect);
      }}
    >
      <div className="flex max-h-full w-[640px] flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
        <header className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
          <div>
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <Upload className="h-4 w-4 text-primary" />
              {t("plugin_upload.title")}
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("plugin_upload.subtitle")}
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("common.cancel")}>
            <X className="h-4 w-4" />
          </Button>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-4 px-6 py-5">
            {inputs}

            {picked.length === 0 ? (
              <UploadDropzone
                dragging={dragging}
                title={t("plugin_upload.drop_title")}
                hint={t("plugin_upload.drop_hint")}
                folderLabel={t("plugin_upload.choose_folder")}
                archiveLabel={t("plugin_upload.choose_archive")}
                onChooseFolder={openFolder}
                onChooseArchive={openArchive}
              />
            ) : inspecting || !report ? (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/20 px-4 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("plugin_upload.inspecting")}
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between gap-4 rounded-lg border border-border bg-muted/20 px-4 py-3">
                  <div className="min-w-0">
                    {report.plugin ? (
                      <>
                        <p className="flex items-center gap-1.5 truncate text-sm font-semibold">
                          <Plug className="h-3.5 w-3.5 shrink-0 text-primary" />
                          {report.plugin.display_name}
                        </p>
                        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                          {report.plugin.description}
                        </p>
                        <p className="mt-1.5 text-[11px] text-muted-foreground">
                          {`${report.plugin.category} · ${t("plugin_upload.auth_mode")}: ${report.plugin.auth_mode}`}
                          {report.has_mcp ? ` · ${t("plugin_upload.with_mcp")}` : ""}
                        </p>
                      </>
                    ) : (
                      <p className="text-sm font-medium text-muted-foreground">
                        {t("plugin_upload.no_plugin_detected")}
                      </p>
                    )}
                    <p className="mt-1.5 text-[11px] text-muted-foreground">
                      {`${report.files.length} ${t("plugin_upload.files")} · ${formatBytes(report.total_bytes)}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button size="sm" variant="outline" onClick={openFolder}>
                      {t("plugin_upload.replace")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void inspect([])}>
                      {t("plugin_upload.clear")}
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
                  <p
                    key={problem}
                    role="alert"
                    className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
                  >
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {problem}
                  </p>
                ))}

                {report.ready && (
                  // Said before the yes, not discovered after it: nobody
                  // vouched for this entry, and the card will show no
                  // publisher because there is none to show.
                  <p className="rounded-md border border-foreground/40 bg-foreground/10 px-3 py-2 text-xs text-foreground">
                    {t("plugin_upload.unreviewed")}
                  </p>
                )}
              </>
            )}

            {error && (
              <p className="flex items-start gap-2 text-sm text-destructive" role="alert">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {error}
              </p>
            )}
          </div>
        </ScrollArea>

        <footer className="flex items-center justify-between gap-4 border-t border-border px-6 py-4">
          <p className="min-w-0 text-xs text-muted-foreground">{blocker}</p>
          <div className="flex shrink-0 items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t("common.cancel")}
            </Button>
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
              {t("plugin_upload.install")}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}

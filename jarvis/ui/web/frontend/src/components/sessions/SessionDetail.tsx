/**
 * Detail pane for a single voice session: header (aggregates) +
 * turn timeline + a global click-to-copy for the whole session.
 *
 * Loading/empty/error states are rendered inline — no modal.
 */
import {
  Code2,
  Copy,
  Download,
  FileCode2,
  FileJson,
  FileText,
  Loader2,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { OpenWithDialog } from "@/components/OpenWithDialog";
import { useEventStore } from "@/store/events";
import {
  buildSessionFilename,
  mimeFor,
  robustCopy,
  saveOrDownload,
} from "@/lib/clipboard";
import { useCapabilities } from "@/hooks/useCapabilities";
import {
  useOpeners,
  usePreferredOpener,
  useSetPreferredOpener,
} from "@/hooks/useOutputs";
import { useT } from "@/i18n";

import { fetchSessionExport, openSessionWith, sessionExportUrl } from "./api";
import { TurnCard } from "./TurnCard";
import type {
  SessionDetail as SessionDetailModel,
  VoiceSpokenLine,
} from "./types";

type ExportFormat = "markdown" | "plain" | "json";

const FORMAT_LABEL: Record<ExportFormat, string> = {
  markdown: "Markdown",
  plain: "Text",
  json: "JSON",
};

interface Props {
  detail: SessionDetailModel | undefined;
  loading: boolean;
  error: Error | null;
}

export function SessionDetail({ detail, loading, error }: Props) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  // Desktop shell? Then save straight to ~/Downloads (browser downloads are
  // silently dropped by pywebview); otherwise use the normal browser download.
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  // "Open in editor" — reuses the Outputs view's opener list + remembered
  // default (shared `[ui] preferred_opener`), so the chosen editor is the same
  // everywhere. The chooser tracks which format row triggered it.
  const openers = useOpeners();
  const preferred = usePreferredOpener();
  const setPreferred = useSetPreferredOpener();
  const [editorFormat, setEditorFormat] = useState<ExportFormat | null>(null);

  // Group the SpeechSpoken raw events under their turn so each TurnCard can
  // render the "Spoken output" track (every voiced non-reply phrase). Hook
  // runs unconditionally (before the early returns) per the rules of hooks.
  const spokenByTurn = useMemo(() => {
    const map = new Map<string, VoiceSpokenLine[]>();
    for (const e of detail?.events ?? []) {
      if (e.kind !== "SpeechSpoken") continue;
      const text = String((e.payload as { text?: unknown })?.text ?? "");
      if (!text.trim()) continue;
      // The technical diagnostic rides on the recorded event; it is carried on
      // the projection for parity with the payload but deliberately NOT rendered
      // in the transcript (TurnCard) — it is surfaced in the Run Inspector
      // instead (user request 2026-06-22, reversing the 2026-06-16 ask).
      const rawDetail = (e.payload as { detail?: unknown })?.detail;
      const detail =
        typeof rawDetail === "string" && rawDetail.trim()
          ? rawDetail
          : undefined;
      const line: VoiceSpokenLine = {
        turn_id: e.turn_id,
        ts_ms: e.ts_ms,
        text,
        spoken_kind: String(
          (e.payload as { spoken_kind?: unknown })?.spoken_kind ?? "other",
        ),
        detail,
      };
      const arr = map.get(e.turn_id ?? "") ?? [];
      arr.push(line);
      map.set(e.turn_id ?? "", arr);
    }
    for (const arr of map.values()) arr.sort((a, b) => a.ts_ms - b.ts_ms);
    return map;
  }, [detail]);

  const copyAs = useCallback(
    async (format: ExportFormat) => {
      if (!detail) return;
      try {
        const text = await fetchSessionExport(detail.session.id, format);
        const ok = await robustCopy(text);
        if (ok) {
          pushToast("success", `${t("session_detail.copied_as")} ${FORMAT_LABEL[format]}`);
        } else {
          pushToast("error", t("session_detail.copy_failed_clipboard"));
        }
      } catch (e) {
        pushToast(
          "error",
          e instanceof Error ? e.message : t("session_detail.copy_failed"),
        );
      }
    },
    [detail, pushToast],
  );

  const downloadAsFormat = useCallback(
    async (format: ExportFormat) => {
      if (!detail) return;
      try {
        const text = await fetchSessionExport(detail.session.id, format);
        // First user utterance as the filename slug — falls back to session_id.
        const preview =
          detail.turns.find((t) => t.user_text)?.user_text ?? "";
        const filename = buildSessionFilename(detail.session, preview, format);
        const savedPath = await saveOrDownload({
          filename,
          text,
          mime: mimeFor(format),
          native,
        });
        pushToast(
          "success",
          savedPath
            ? `${t("session_detail.saved_to_downloads")} ${savedPath}`
            : `${t("session_detail.downloaded_as")} ${filename}`,
          savedPath ? { filePath: savedPath, filename } : undefined,
        );
      } catch (e) {
        pushToast(
          "error",
          e instanceof Error ? e.message : t("session_detail.download_failed"),
        );
      }
    },
    [detail, pushToast, native],
  );

  // Launch the transcript in a local app (editor / default / browser).
  const launchInEditor = useCallback(
    async (format: ExportFormat, opener: string) => {
      if (!detail) return;
      try {
        const opened = await openSessionWith(detail.session.id, format, opener);
        pushToast(
          opened ? "success" : "error",
          opened
            ? t("session_detail.opened_in_editor")
            : t("session_detail.open_failed"),
        );
      } catch (e) {
        pushToast(
          "error",
          e instanceof Error ? e.message : t("session_detail.open_failed"),
        );
      }
    },
    [detail, pushToast, t],
  );

  const openInEditor = useCallback(
    (format: ExportFormat) => {
      if (!detail) return;
      if (!native) {
        // Headless VPS / browser: no local apps — open the export in a new tab.
        window.open(
          sessionExportUrl(detail.session.id, format),
          "_blank",
          "noopener,noreferrer",
        );
        return;
      }
      const pref = preferred.data ?? "";
      if (pref) {
        void launchInEditor(format, pref);
      } else {
        setEditorFormat(format); // first time: ask which app via the chooser
      }
    },
    [detail, native, preferred.data, launchInEditor],
  );

  const pickOpener = useCallback(
    (opener: string, remember: boolean) => {
      if (editorFormat) void launchInEditor(editorFormat, opener);
      if (remember) setPreferred.mutate(opener);
      setEditorFormat(null);
    },
    [editorFormat, launchInEditor, setPreferred],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        {t("session_detail.loading")}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <div className="font-semibold text-destructive">{t("session_detail.load_error")}</div>
          <div className="mt-1 text-muted-foreground">{error.message}</div>
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        {t("sessions.select_one")}
      </div>
    );
  }

  const { session, turns } = detail;
  const startedDt = new Date(session.started_ms);
  const endedDt = session.ended_ms ? new Date(session.ended_ms) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="shrink-0 border-b border-border bg-card/40 px-5 py-4 backdrop-blur">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-display text-lg font-semibold">
              {t("session_detail.title")}
            </div>
            <div className="font-mono text-xs text-muted-foreground">
              {startedDt.toLocaleString("de")}
              {endedDt && ` — ${endedDt.toLocaleTimeString("de")}`}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{session.turn_count} {t("session_detail.turns")}</Badge>
              <Badge variant="secondary">{session.language}</Badge>
              {session.hangup_reason && (
                <Badge variant="outline">{session.hangup_reason}</Badge>
              )}
              {session.providers_used.map((p) => (
                <Badge key={p} variant="outline" className="font-mono text-[10px]">
                  {p}
                </Badge>
              ))}
              {session.total_cost_usd > 0 && (
                <Badge variant="outline">
                  ${session.total_cost_usd.toFixed(4)}
                </Badge>
              )}
              {(session.total_tokens_in > 0 || session.total_tokens_out > 0) && (
                <Badge variant="outline" className="text-[10px]">
                  {session.total_tokens_in}+{session.total_tokens_out} tok
                </Badge>
              )}
            </div>
          </div>

          {/* Export actions: one row per format with copy + download */}
          <div className="flex shrink-0 flex-col gap-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Export
            </div>
            <ExportRow
              icon={<FileText className="h-3.5 w-3.5" />}
              label="Text"
              onCopy={() => copyAs("plain")}
              onDownload={() => downloadAsFormat("plain")}
              onOpenEditor={() => openInEditor("plain")}
              variant="primary"
            />
            <ExportRow
              icon={<FileCode2 className="h-3.5 w-3.5" />}
              label="Markdown"
              onCopy={() => copyAs("markdown")}
              onDownload={() => downloadAsFormat("markdown")}
              onOpenEditor={() => openInEditor("markdown")}
              variant="outline"
            />
            <ExportRow
              icon={<FileJson className="h-3.5 w-3.5" />}
              label="JSON"
              onCopy={() => copyAs("json")}
              onDownload={() => downloadAsFormat("json")}
              onOpenEditor={() => openInEditor("json")}
              variant="outline"
            />
          </div>
        </div>
      </div>

      {/* "Open with…" chooser — only on the desktop, where local apps exist.
          editorFormat carries which format row opened it. */}
      {editorFormat && (
        <OpenWithDialog
          openers={openers.data ?? []}
          loading={openers.isLoading}
          onPick={pickOpener}
          onClose={() => setEditorFormat(null)}
        />
      )}

      {/* Turns */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-5">
          {turns.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              {t("sessions.no_turns")}
              {t("session_detail.no_turns_suffix")}
            </div>
          ) : (
            turns.map((t) => (
              <TurnCard
                key={t.id}
                turn={t}
                spoken={spokenByTurn.get(t.id) ?? []}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}


interface ExportRowProps {
  icon: React.ReactNode;
  label: string;
  onCopy: () => void;
  onDownload: () => void;
  onOpenEditor: () => void;
  variant: "primary" | "outline";
}

/**
 * One row per export format: the format label (with icon) on the left,
 * three compact action buttons (copy / download / open in editor) on the right.
 */
function ExportRow({
  icon,
  label,
  onCopy,
  onDownload,
  onOpenEditor,
  variant,
}: ExportRowProps) {
  const t = useT();
  const labelClass =
    variant === "primary"
      ? "rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs font-medium text-primary"
      : "rounded-md border border-border bg-background/40 px-2 py-1 text-xs font-medium text-foreground/90";

  return (
    <div className="flex items-center gap-1.5">
      <div className={`${labelClass} flex flex-1 items-center gap-1.5`}>
        {icon}
        {label}
      </div>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onCopy}
        className="h-7 w-7 shrink-0 p-0"
        title={`${t("session_detail.copy_action")} ${label}`}
      >
        <Copy className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onDownload}
        className="h-7 w-7 shrink-0 p-0"
        title={`${t("session_detail.download_file_action")} ${label}`}
      >
        <Download className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onOpenEditor}
        className="h-7 w-7 shrink-0 p-0"
        title={`${t("session_detail.open_editor_action")} ${label}`}
      >
        <Code2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

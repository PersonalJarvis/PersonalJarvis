import {
  ChevronDown,
  Code2,
  Copy,
  Download,
  Loader2,
  MessagesSquare,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ActionMenu, type MenuAction } from "@/components/extensions/primitives";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
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
import { useT, useUiLanguage } from "@/i18n";

import { fetchSessionExport, openSessionWith, sessionExportUrl } from "./api";
import { hangupLabel } from "./SessionList";
import { TurnCard } from "./TurnCard";
import { VoiceModeBadge } from "./VoiceModeBadge";
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

const FORMATS: ExportFormat[] = ["plain", "markdown", "json"];

interface Props {
  detail: SessionDetailModel | undefined;
  loading: boolean;
  error: Error | null;
}

/**
 * One session, read like a conversation.
 *
 * The header is a title row (name, mode badge, when) with one muted line of
 * facts and a single Export menu — nine icon buttons in a column were a
 * toolbar nobody asked for. The turns below are a chat timeline capped at
 * the reading measure: no card around each turn, no box around each line.
 */
export function SessionDetail({ detail, loading, error }: Props) {
  const t = useT();
  const uiLanguage = useUiLanguage();
  const locale =
    uiLanguage === "de" ? "de-DE" : uiLanguage === "es" ? "es-ES" : "en-US";
  const pushToast = useEventStore((s) => s.pushToast);
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  const openers = useOpeners();
  const preferred = usePreferredOpener();
  const setPreferred = useSetPreferredOpener();
  const [editorFormat, setEditorFormat] = useState<ExportFormat | null>(null);

  const spokenByTurn = useMemo(() => {
    const map = new Map<string, VoiceSpokenLine[]>();
    for (const e of detail?.events ?? []) {
      if (e.kind !== "SpeechSpoken") continue;
      const text = String((e.payload as { text?: unknown })?.text ?? "");
      if (!text.trim()) continue;
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
    [detail, pushToast, t],
  );

  const downloadAsFormat = useCallback(
    async (format: ExportFormat) => {
      if (!detail) return;
      try {
        const text = await fetchSessionExport(detail.session.id, format);
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
    [detail, pushToast, native, t],
  );

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

  const exportActions = useMemo<MenuAction[]>(() => {
    const actions: MenuAction[] = [];
    for (const format of FORMATS) {
      const label = FORMAT_LABEL[format];
      actions.push({
        id: `copy-${format}`,
        label: `${t("session_detail.copy_action")} ${label}`,
        icon: <Copy className="h-4 w-4" />,
        onSelect: () => void copyAs(format),
        separatorAbove: format !== "plain",
      });
      actions.push({
        id: `download-${format}`,
        label: `${t("session_detail.download_file_action")} ${label}`,
        icon: <Download className="h-4 w-4" />,
        onSelect: () => void downloadAsFormat(format),
      });
      actions.push({
        id: `open-${format}`,
        label: `${t("session_detail.open_editor_action")} ${label}`,
        icon: <Code2 className="h-4 w-4" />,
        onSelect: () => openInEditor(format),
      });
    }
    return actions;
  }, [t, copyAs, downloadAsFormat, openInEditor]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-base text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("session_detail.loading")}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/20 bg-destructive/[0.08] p-4 text-base">
          <div className="font-medium text-destructive">{t("session_detail.load_error")}</div>
          <div className="mt-1 text-muted-foreground">{error.message}</div>
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState icon={<MessagesSquare />} title={t("sessions.select_one")} />
      </div>
    );
  }

  const { session, turns } = detail;
  const startedDt = new Date(session.started_ms);
  const endedDt = session.ended_ms ? new Date(session.ended_ms) : null;
  const facts = [
    `${session.turn_count} ${t("session_detail.turns")}`,
    session.language,
    session.hangup_reason ? hangupLabel(session.hangup_reason) : null,
    ...session.providers_used,
    session.total_cost_usd > 0 ? `$${session.total_cost_usd.toFixed(4)}` : null,
    session.total_tokens_in > 0 || session.total_tokens_out > 0
      ? `${formatTokens(session.total_tokens_in + session.total_tokens_out)} tok`
      : null,
  ].filter(Boolean) as string[];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-8 pb-4 pt-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-semibold text-foreground-strong">
                {t("session_detail.title")}
              </h2>
              <VoiceModeBadge mode={session.voice_mode} prominence="prominent" />
            </div>
            <p className="mt-1 text-base text-muted-foreground">
              {startedDt.toLocaleString(locale)}
              {endedDt && ` – ${endedDt.toLocaleTimeString(locale)}`}
            </p>
            <p className="mt-2 text-sm text-foreground-faint">{facts.join(" · ")}</p>
          </div>

          <ActionMenu
            label={t("session_detail.export_label")}
            actions={exportActions}
            trigger={({ open, toggle }) => (
              <Button
                variant="outline"
                onClick={toggle}
                aria-expanded={open}
                aria-haspopup="menu"
              >
                <Download />
                {t("session_detail.export_label")}
                <ChevronDown className={open ? "rotate-180" : undefined} />
              </Button>
            )}
          />
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

      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto w-full max-w-3xl space-y-8 px-8 py-6">
          {turns.length === 0 ? (
            <EmptyState
              icon={<MessagesSquare />}
              title={t("sessions.no_turns")}
              description={t("session_detail.no_turns_suffix")}
            />
          ) : (
            turns.map((t, index) => (
              <TurnCard
                key={t.id}
                turn={t}
                displayNumber={index + 1}
                spoken={spokenByTurn.get(t.id) ?? []}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

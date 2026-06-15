/**
 * Detail-Pane einer einzelnen Voice-Session: Header (Aggregate) +
 * Turn-Timeline + globaler Click-to-Copy fuer die ganze Session.
 *
 * Lade-/Empty-/Error-States werden inline gerendert — kein Modal.
 */
import {
  Copy,
  Download,
  FileCode2,
  FileJson,
  FileText,
  Loader2,
} from "lucide-react";
import { useCallback, useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useEventStore } from "@/store/events";
import {
  buildSessionFilename,
  downloadAs,
  mimeFor,
  robustCopy,
} from "@/lib/clipboard";
import { useT } from "@/i18n";

import { fetchSessionExport } from "./api";
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

  // Group the SpeechSpoken raw events under their turn so each TurnCard can
  // render the "Spoken output" track (every voiced non-reply phrase). Hook
  // runs unconditionally (before the early returns) per the rules of hooks.
  const spokenByTurn = useMemo(() => {
    const map = new Map<string, VoiceSpokenLine[]>();
    for (const e of detail?.events ?? []) {
      if (e.kind !== "SpeechSpoken") continue;
      const text = String((e.payload as { text?: unknown })?.text ?? "");
      if (!text.trim()) continue;
      const line: VoiceSpokenLine = {
        turn_id: e.turn_id,
        ts_ms: e.ts_ms,
        text,
        spoken_kind: String(
          (e.payload as { spoken_kind?: unknown })?.spoken_kind ?? "other",
        ),
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
          pushToast("success", `Session als ${FORMAT_LABEL[format]} kopiert`);
        } else {
          pushToast("error", "Kopieren fehlgeschlagen — Clipboard-Zugriff blockiert?");
        }
      } catch (e) {
        pushToast(
          "error",
          e instanceof Error ? e.message : "Kopieren fehlgeschlagen",
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
        // Erste User-Utterance als Filename-Slug — fallback auf session_id.
        const preview =
          detail.turns.find((t) => t.user_text)?.user_text ?? "";
        const filename = buildSessionFilename(detail.session, preview, format);
        downloadAs(filename, text, mimeFor(format));
        pushToast("success", `Heruntergeladen als ${filename}`);
      } catch (e) {
        pushToast(
          "error",
          e instanceof Error ? e.message : "Download fehlgeschlagen",
        );
      }
    },
    [detail, pushToast],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Lade Session…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <div className="font-semibold text-destructive">Fehler beim Laden</div>
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
              Voice-Session
            </div>
            <div className="font-mono text-xs text-muted-foreground">
              {startedDt.toLocaleString("de")}
              {endedDt && ` — ${endedDt.toLocaleTimeString("de")}`}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{session.turn_count} Turns</Badge>
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

          {/* Export-Actions: pro Format eine Reihe mit Kopieren + Herunterladen */}
          <div className="flex shrink-0 flex-col gap-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Export
            </div>
            <ExportRow
              icon={<FileText className="h-3.5 w-3.5" />}
              label="Text"
              onCopy={() => copyAs("plain")}
              onDownload={() => downloadAsFormat("plain")}
              variant="primary"
            />
            <ExportRow
              icon={<FileCode2 className="h-3.5 w-3.5" />}
              label="Markdown"
              onCopy={() => copyAs("markdown")}
              onDownload={() => downloadAsFormat("markdown")}
              variant="outline"
            />
            <ExportRow
              icon={<FileJson className="h-3.5 w-3.5" />}
              label="JSON"
              onCopy={() => copyAs("json")}
              onDownload={() => downloadAsFormat("json")}
              variant="outline"
            />
          </div>
        </div>
      </div>

      {/* Turns */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-5">
          {turns.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              {t("sessions.no_turns")}
              ohne Folge-Utterance.
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
  variant: "primary" | "outline";
}

/**
 * Eine Zeile pro Export-Format: links das Format-Label (mit Icon),
 * rechts zwei kompakte Action-Buttons (Kopieren / Herunterladen).
 */
function ExportRow({
  icon,
  label,
  onCopy,
  onDownload,
  variant,
}: ExportRowProps) {
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
        title={`${label} kopieren`}
      >
        <Copy className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onDownload}
        className="h-7 w-7 shrink-0 p-0"
        title={`${label} als Datei herunterladen`}
      >
        <Download className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

/**
 * A single voice turn — user block + brain meta + tools + Jarvis block.
 *
 * The click-to-copy button lets the user copy just the turn text
 * (without the session frame).
 */
import {
  Brain,
  Clock,
  Copy,
  Download,
  Hourglass,
  MessageSquareWarning,
  Mic2,
  Volume2,
  Wrench,
} from "lucide-react";
import { useCallback } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { robustCopy, saveOrDownload } from "@/lib/clipboard";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

import type { VoiceSpokenLine, VoiceTurnRow } from "./types";

// Human-readable label per SpeechSpoken spoken_kind. Mirror of
// jarvis/sessions/constants.py SPOKEN_KINDS — every kind needs an entry
// (parity: tests/unit/sessions/test_spoken_kind_parity.py). An unknown kind
// falls back to the kind string itself so it still renders.
export const SPOKEN_KIND_LABEL: Record<string, string> = {
  reply: "Reply",
  clarify: "Clarifying question",
  timeout: "Timeout notice",
  unavailable: "Brain unavailable",
  stt_unavailable: "Couldn't hear you",
  privacy: "Privacy",
  completion: "Background result",
  subagent: "Jarvis-Agent / Output",
  action_done: "Action confirmed",
  backchannel: "Backchannel",
  announcement: "Announcement",
  preamble: "Preamble",
  progress: "Progress update",
  withheld: "Answer withheld (safety)",
  other: "Spoken",
};

interface Props {
  turn: VoiceTurnRow;
  displayNumber?: number;
  spoken?: VoiceSpokenLine[];
}

export function TurnCard({ turn, displayNumber, spoken = [] }: Props) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const assistantName = useEventStore((s) => s.assistantName);
  // Desktop shell → save to ~/Downloads via the backend; browser → blob download.
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  const visibleTurnNumber = displayNumber ?? turn.idx + 1;
  const confirmedReplies = spoken.filter((line) => line.spoken_kind === "reply");
  const auxiliarySpoken = spoken.filter((line) => line.spoken_kind !== "reply");
  const audibleReply = confirmedReplies.length
    ? confirmedReplies.map((line) => line.text).join(" ")
    : turn.jarvis_text;
  // The Jarvis-Agents brand label is fixed: it names the system, not the
  // configured assistant, so it does not follow the assistant name.
  const kindLabel: Record<string, string> = {
    ...SPOKEN_KIND_LABEL,
    subagent: "Jarvis-Agent / Output",
  };

  const copyTurn = useCallback(async () => {
    const text = formatTurnPlain(turn, spoken, visibleTurnNumber);
    const ok = await robustCopy(text);
    pushToast(
      ok ? "success" : "error",
      ok ? `${t("turn_card.turn")} ${visibleTurnNumber} ${t("turn_card.copied")}` : t("turn_card.copy_failed"),
    );
  }, [turn, spoken, visibleTurnNumber, pushToast, t]);

  const downloadTurn = useCallback(async () => {
    const text = formatTurnPlain(turn, spoken);
    const stamp = new Date(turn.started_ms);
    const pad = (n: number): string => String(n).padStart(2, "0");
    const filename =
      `voice-turn-${stamp.getFullYear()}-${pad(stamp.getMonth() + 1)}-${pad(stamp.getDate())}` +
      `_${pad(stamp.getHours())}-${pad(stamp.getMinutes())}-${pad(stamp.getSeconds())}.txt`;
    const savedPath = await saveOrDownload({
      filename,
      text,
      mime: "text/plain;charset=utf-8",
      native,
    });
    pushToast(
      "success",
      savedPath
        ? `${t("turn_card.saved_to_downloads")} ${savedPath}`
        : `${t("turn_card.downloaded_as")} ${filename}`,
      savedPath ? { filePath: savedPath, filename } : undefined,
    );
  }, [turn, spoken, pushToast, t, native]);

  const startedAt = new Date(turn.started_ms).toLocaleTimeString("de", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <Card className="min-w-0 max-w-full bg-background/40">
      <CardContent className="min-w-0 space-y-3 p-4">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">Turn {visibleTurnNumber}</span>
            <span>·</span>
            <span>{startedAt}</span>
            {turn.latency_total_ms > 0 && (
              <>
                <span>·</span>
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {formatMs(turn.latency_total_ms)}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={copyTurn}
              className="h-7 px-2 text-xs"
              title={t("turn_card.copy_turn")}
            >
              <Copy className="mr-1 h-3 w-3" />
              {t("turn_card.copy")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={downloadTurn}
              className="h-7 w-7 p-0"
              title={t("turn_card.download_turn")}
            >
              <Download className="h-3 w-3" />
            </Button>
          </div>
        </div>

        {/* User */}
        {turn.user_text && (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-emerald-400">
              <Mic2 className="h-3 w-3" />
              User
              <Badge variant="secondary" className="ml-1 text-[9px]">
                {turn.user_lang}
              </Badge>
            </div>
            <div className="min-w-0 whitespace-pre-wrap break-words rounded-md border border-emerald-400/20 bg-emerald-400/5 p-2 text-sm [overflow-wrap:anywhere]">
              {turn.user_text}
            </div>
          </div>
        )}

        {/* Brain-Meta */}
        {(turn.tier ||
          turn.provider ||
          turn.tokens_in > 0 ||
          turn.cost_usd > 0) && (
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Brain className="h-3 w-3 text-primary" />
            {turn.tier && (
              <Badge variant="outline" className="text-[10px]">
                {turn.tier}
              </Badge>
            )}
            {turn.provider && (
              <Badge variant="outline" className="text-[10px]">
                {turn.provider}
              </Badge>
            )}
            {turn.model && (
              <Badge variant="outline" className="font-mono text-[10px]">
                {turn.model}
              </Badge>
            )}
            {(turn.tokens_in > 0 || turn.tokens_out > 0) && (
              <span className="text-muted-foreground">
                {turn.tokens_in}+{turn.tokens_out} tok
              </span>
            )}
            {turn.cost_usd > 0 && (
              <span className="text-muted-foreground">
                · ${turn.cost_usd.toFixed(4)}
              </span>
            )}
          </div>
        )}

        {/* Tools */}
        {turn.tool_calls.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Wrench className="h-3 w-3 text-amber-400" />
            <span className="text-muted-foreground">Tools:</span>
            {turn.tool_calls.map((tc) => (
              <Badge
                key={tc}
                variant="secondary"
                className="font-mono text-[10px]"
              >
                {tc}
              </Badge>
            ))}
          </div>
        )}

        {/* Jarvis */}
        {audibleReply && (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-primary">
              <Volume2 className="h-3 w-3" />
              {assistantName}
              <Badge variant="secondary" className="ml-1 text-[9px]">
                {turn.jarvis_lang}
              </Badge>
              {turn.voice_name && (
                <Badge
                  variant="outline"
                  className="ml-1 font-mono text-[9px] normal-case text-muted-foreground"
                  title={
                    turn.voice_provider
                      ? `Voice: ${turn.voice_name} (${turn.voice_provider})`
                      : `Voice: ${turn.voice_name}`
                  }
                >
                  {turn.voice_name}
                  {turn.voice_provider ? ` · ${turn.voice_provider}` : ""}
                </Badge>
              )}
              {turn.awaiting_confirmation && (
                <Badge
                  variant="outline"
                  className="ml-1 border-amber-400/40 text-[9px] text-amber-300"
                >
                  Awaiting confirmation
                </Badge>
              )}
            </div>
            <div className="min-w-0 whitespace-pre-wrap break-words rounded-md border border-primary/20 bg-primary/5 p-2 text-sm [overflow-wrap:anywhere]">
              {audibleReply}
            </div>
          </div>
        )}

        {/* Supplemental spoken output. Playback-confirmed normal replies render
            in the main assistant block above; status phrases and readbacks stay
            distinct here while preserving their audible order. */}
        {auxiliarySpoken.length > 0 && (
          <div className="space-y-1.5 border-t border-border/50 pt-2">
            <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-sky-300">
              <MessageSquareWarning className="h-3 w-3" />
              Spoken output
            </div>
            <div className="space-y-1">
              {auxiliarySpoken.map((s, i) => {
                // A spawned sub-agent / mission result gets its own colour so it
                // reads distinctly from a generic background completion and from
                // a normal reply: violet ("agent") vs. the sky tint of the rest.
                const isSubagent = s.spoken_kind === "subagent";
                return (
                  <div
                    key={`${s.ts_ms}-${i}`}
                    data-spoken-kind={s.spoken_kind}
                    className={
                      isSubagent
                        ? "flex items-start gap-2 rounded-md border border-violet-400/30 bg-violet-400/10 p-2 text-sm"
                        : "flex items-start gap-2 rounded-md border border-sky-400/20 bg-sky-400/5 p-2 text-sm"
                    }
                  >
                    <Badge
                      variant="secondary"
                      className={
                        isSubagent
                          ? "mt-0.5 shrink-0 border-violet-400/40 text-[9px] uppercase tracking-wide text-violet-200"
                          : "mt-0.5 shrink-0 text-[9px] uppercase tracking-wide"
                      }
                    >
                      {kindLabel[s.spoken_kind] ?? s.spoken_kind}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      {/* Only the spoken phrase belongs in the transcript. The
                          technical diagnostic (exit code + raw harness reason)
                          stays on the recorded SpeechSpoken event and is shown
                          in the Run Inspector — never here (user request
                          2026-06-22, reversing the 2026-06-16 ask). */}
                      <span className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                        {s.text}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Latency breakdown — how long Jarvis thought / spoke */}
        {(turn.think_ms > 0 || turn.speak_ms > 0) && (
          <div className="grid grid-cols-2 gap-2 border-t border-border/50 pt-2 text-[11px]">
            <div className="flex items-center gap-1.5">
              <Hourglass className="h-3 w-3 text-amber-300" />
              <span className="text-muted-foreground">{t("turn_card.thought")}</span>
              <span className="font-mono text-foreground/90">
                {turn.think_ms > 0 ? formatMs(turn.think_ms) : "—"}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <Volume2 className="h-3 w-3 text-primary" />
              <span className="text-muted-foreground">{t("turn_card.spoke")}</span>
              <span className="font-mono text-foreground/90">
                {turn.speak_ms > 0 ? formatMs(turn.speak_ms) : "—"}
              </span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// --- Helpers ---------------------------------------------------------

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatTurnPlain(
  turn: VoiceTurnRow,
  spoken: VoiceSpokenLine[] = [],
  displayNumber: number = turn.idx + 1,
): string {
  const lines: string[] = [];
  lines.push(`--- Turn ${displayNumber} ---`);
  if (turn.user_text) lines.push(`[USER]   ${turn.user_text}`);
  const meta: string[] = [];
  if (turn.tier) meta.push(`tier=${turn.tier}`);
  if (turn.provider) meta.push(`provider=${turn.provider}`);
  if (turn.model) meta.push(`model=${turn.model}`);
  if (turn.tokens_in || turn.tokens_out) {
    meta.push(`tokens=${turn.tokens_in}+${turn.tokens_out}`);
  }
  if (turn.cost_usd > 0) meta.push(`cost=$${turn.cost_usd.toFixed(4)}`);
  if (turn.latency_total_ms > 0) {
    meta.push(`latency=${formatMs(turn.latency_total_ms)}`);
  }
  if (turn.think_ms > 0) meta.push(`think=${formatMs(turn.think_ms)}`);
  if (turn.speak_ms > 0) meta.push(`speak=${formatMs(turn.speak_ms)}`);
  if (meta.length) lines.push(`[BRAIN]  ${meta.join(" ")}`);
  if (turn.tool_calls.length) {
    lines.push(`[TOOLS]  ${turn.tool_calls.join(", ")}`);
  }
  const jarvisLines: Array<{ ts_ms: number; lines: string[] }> = [];
  const hasConfirmedReply = spoken.some((line) => line.spoken_kind === "reply");
  if (turn.jarvis_text && !hasConfirmedReply) {
    const prefix = turn.awaiting_confirmation ? "(awaiting confirmation) " : "";
    jarvisLines.push({
      ts_ms: turn.ended_ms ?? Number.MAX_SAFE_INTEGER,
      lines: [`[JARVIS] ${prefix}${turn.jarvis_text}`],
    });
  }
  for (const s of spoken) {
    if (s.spoken_kind === "reply") {
      jarvisLines.push({ ts_ms: s.ts_ms, lines: [`[JARVIS] ${s.text}`] });
      continue;
    }
    const label = (SPOKEN_KIND_LABEL[s.spoken_kind] ?? s.spoken_kind).toUpperCase();
    // The technical detail is deliberately excluded from the transcript copy —
    // it lives in the Run Inspector, not in what was said (user request 2026-06-22).
    jarvisLines.push({ ts_ms: s.ts_ms, lines: [`[SPOKEN: ${label}] ${s.text}`] });
  }
  jarvisLines
    .sort((a, b) => a.ts_ms - b.ts_ms)
    .forEach((item) => lines.push(...item.lines));
  return lines.join("\n");
}

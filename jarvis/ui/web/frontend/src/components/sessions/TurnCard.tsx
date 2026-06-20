/**
 * Einzelner Voice-Turn — User-Block + Brain-Meta + Tools + Jarvis-Block.
 *
 * Per Click-to-Copy-Button kann der User den Turn-Text alleine kopieren
 * (ohne Session-Frame).
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
import { downloadAs, robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

import type { VoiceSpokenLine, VoiceTurnRow } from "./types";

// Human-readable label per SpeechSpoken spoken_kind. Mirror of
// jarvis/sessions/constants.py SPOKEN_KINDS — every kind needs an entry
// (parity: tests/unit/sessions/test_spoken_kind_parity.py). An unknown kind
// falls back to the kind string itself so it still renders.
export const SPOKEN_KIND_LABEL: Record<string, string> = {
  clarify: "Clarifying question",
  timeout: "Timeout notice",
  unavailable: "Brain unavailable",
  stt_unavailable: "Couldn't hear you",
  privacy: "Privacy",
  completion: "Background result",
  subagent: "Jarvis Sub-Agent / Output",
  action_done: "Action confirmed",
  backchannel: "Backchannel",
  announcement: "Announcement",
  preamble: "Preamble",
  progress: "Progress update",
  other: "Spoken",
};

interface Props {
  turn: VoiceTurnRow;
  spoken?: VoiceSpokenLine[];
}

export function TurnCard({ turn, spoken = [] }: Props) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const assistantName = useEventStore((s) => s.assistantName);
  // Override the subagent label so it follows the configured assistant name.
  const kindLabel: Record<string, string> = {
    ...SPOKEN_KIND_LABEL,
    subagent: `${assistantName} Sub-Agent / Output`,
  };

  const copyTurn = useCallback(async () => {
    const text = formatTurnPlain(turn, spoken);
    const ok = await robustCopy(text);
    pushToast(
      ok ? "success" : "error",
      ok ? `${t("turn_card.turn")} ${turn.idx + 1} ${t("turn_card.copied")}` : t("turn_card.copy_failed"),
    );
  }, [turn, spoken, pushToast, t]);

  const downloadTurn = useCallback(() => {
    const text = formatTurnPlain(turn, spoken);
    const stamp = new Date(turn.started_ms);
    const pad = (n: number): string => String(n).padStart(2, "0");
    const filename =
      `voice-turn-${stamp.getFullYear()}-${pad(stamp.getMonth() + 1)}-${pad(stamp.getDate())}` +
      `_${pad(stamp.getHours())}-${pad(stamp.getMinutes())}-${pad(stamp.getSeconds())}.txt`;
    downloadAs(filename, text, "text/plain;charset=utf-8");
    pushToast("success", `${t("turn_card.downloaded_as")} ${filename}`);
  }, [turn, spoken, pushToast, t]);

  const startedAt = new Date(turn.started_ms).toLocaleTimeString("de", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <Card className="bg-background/40">
      <CardContent className="space-y-3 p-4">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">Turn {turn.idx + 1}</span>
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
            <div className="rounded-md border border-emerald-400/20 bg-emerald-400/5 p-2 text-sm">
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
        {turn.jarvis_text && (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-primary">
              <Volume2 className="h-3 w-3" />
              {assistantName}
              <Badge variant="secondary" className="ml-1 text-[9px]">
                {turn.jarvis_lang}
              </Badge>
              {turn.awaiting_confirmation && (
                <Badge
                  variant="outline"
                  className="ml-1 border-amber-400/40 text-[9px] text-amber-300"
                >
                  Awaiting confirmation
                </Badge>
              )}
            </div>
            <div className="rounded-md border border-primary/20 bg-primary/5 p-2 text-sm">
              {turn.jarvis_text}
            </div>
          </div>
        )}

        {/* Spoken output — every phrase Jarvis VOICED that is not the normal
            reply (timeout/clarify/announcement/…). Without this the log only
            shows the conversational reply and hides what the user actually
            heard (user report 2026-06-15). */}
        {spoken.length > 0 && (
          <div className="space-y-1.5 border-t border-border/50 pt-2">
            <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-sky-300">
              <MessageSquareWarning className="h-3 w-3" />
              Spoken output
            </div>
            <div className="space-y-1">
              {spoken.map((s, i) => {
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
                      <span className="break-words">{s.text}</span>
                      {s.detail && (
                        <div className="mt-1 flex items-start gap-1.5 font-mono text-[11px] text-muted-foreground">
                          <span className="shrink-0 uppercase tracking-wide text-amber-400/80">
                            detail
                          </span>
                          <span className="min-w-0 flex-1 break-words">
                            {s.detail}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Latenz-Aufschluesselung — wie lang Jarvis nachgedacht / gesprochen hat */}
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
): string {
  const lines: string[] = [];
  lines.push(`--- Turn ${turn.idx + 1} ---`);
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
  if (turn.jarvis_text) {
    const prefix = turn.awaiting_confirmation ? "(awaiting confirmation) " : "";
    jarvisLines.push({
      ts_ms: turn.ended_ms ?? Number.MAX_SAFE_INTEGER,
      lines: [`[JARVIS] ${prefix}${turn.jarvis_text}`],
    });
  }
  for (const s of spoken) {
    const label = (SPOKEN_KIND_LABEL[s.spoken_kind] ?? s.spoken_kind).toUpperCase();
    const spokenLines = [`[SPOKEN: ${label}] ${s.text}`];
    if (s.detail) spokenLines.push(`[DETAIL] ${s.detail}`);
    jarvisLines.push({ ts_ms: s.ts_ms, lines: spokenLines });
  }
  jarvisLines
    .sort((a, b) => a.ts_ms - b.ts_ms)
    .forEach((item) => lines.push(...item.lines));
  return lines.join("\n");
}

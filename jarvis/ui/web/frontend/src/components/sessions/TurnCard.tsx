import { Copy, Download, Mic, Volume2, Wrench } from "lucide-react";
import { useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { agentBrand } from "@/lib/agentBrand";
import { robustCopy, saveOrDownload } from "@/lib/clipboard";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { VoiceSpokenLine, VoiceTurnRow } from "./types";

export const SPOKEN_KIND_LABEL: Record<string, string> = {
  reply: "Reply",
  clarify: "Clarifying question",
  timeout: "Timeout notice",
  unavailable: "Brain unavailable",
  stt_unavailable: "Couldn't hear you",
  privacy: "Privacy",
  completion: "Background result",
  subagent: "Agent / Output",
  action_done: "Action confirmed",
  backchannel: "Backchannel",
  announcement: "Announcement",
  preamble: "Preamble",
  progress: "Progress update",
  withheld: "Answer withheld (safety)",
  other: "Spoken",
};

export function spokenKindLabels(assistantName: string): Record<string, string> {
  return {
    ...SPOKEN_KIND_LABEL,
    subagent: `${agentBrand(assistantName)} / Output`,
  };
}

interface Props {
  turn: VoiceTurnRow;
  displayNumber?: number;
  spoken?: VoiceSpokenLine[];
}

const PROSE = "min-w-0 whitespace-pre-wrap break-words text-base leading-7 [overflow-wrap:anywhere]";

/**
 * One turn of a voice session, drawn like a chat: a quiet turn line (number,
 * time, latency, copy), the user's words as a bubble on the lift surface, the
 * assistant's reply on the card surface, and the facts of the exchange —
 * brain, tokens, cost, tools, how long it thought and spoke — as ONE muted
 * line underneath. No box inside a box.
 */
export function TurnCard({ turn, displayNumber, spoken = [] }: Props) {
  const t = useT();
  const [showRaw, setShowRaw] = useState(false);
  const rawUserText = turn.user_text ?? "";
  const polishedUserText = (turn.user_text_polished ?? "").trim();
  const polished =
    polishedUserText && polishedUserText !== rawUserText.trim()
      ? polishedUserText
      : null;
  const pushToast = useEventStore((s) => s.pushToast);
  const assistantName = useEventStore((s) => s.assistantName);
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  const visibleTurnNumber = displayNumber ?? turn.idx + 1;
  const confirmedReplies = spoken.filter((line) => line.spoken_kind === "reply");
  const auxiliarySpoken = spoken.filter((line) => line.spoken_kind !== "reply");
  const audibleReply = confirmedReplies.length
    ? confirmedReplies.map((line) => line.text).join(" ")
    : turn.jarvis_text;
  const kindLabel = spokenKindLabels(assistantName);

  const copyTurn = useCallback(async () => {
    const text = formatTurnPlain(turn, spoken, visibleTurnNumber, kindLabel);
    const ok = await robustCopy(text);
    pushToast(
      ok ? "success" : "error",
      ok ? `${t("turn_card.turn")} ${visibleTurnNumber} ${t("turn_card.copied")}` : t("turn_card.copy_failed"),
    );
  }, [turn, spoken, visibleTurnNumber, pushToast, t, kindLabel]);

  const downloadTurn = useCallback(async () => {
    const text = formatTurnPlain(turn, spoken, undefined, kindLabel);
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
  }, [turn, spoken, pushToast, t, native, kindLabel]);

  const startedAt = new Date(turn.started_ms).toLocaleTimeString("de", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  const facts: string[] = [];
  if (turn.provider) facts.push(turn.provider);
  if (turn.model) facts.push(turn.model);
  if (turn.tier) facts.push(turn.tier);
  if (turn.tokens_in > 0 || turn.tokens_out > 0)
    facts.push(`${turn.tokens_in}+${turn.tokens_out} tok`);
  if (turn.cost_usd > 0) facts.push(`$${turn.cost_usd.toFixed(4)}`);
  if (turn.think_ms > 0) facts.push(`${t("turn_card.thought")} ${formatMs(turn.think_ms)}`);
  if (turn.speak_ms > 0) facts.push(`${t("turn_card.spoke")} ${formatMs(turn.speak_ms)}`);

  return (
    <article className="group/turn min-w-0 max-w-full" data-testid="turn-card">
      {/* The turn line: quiet, with the actions appearing on hover. */}
      <div className="flex h-8 items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-foreground-faint">
          <span className="font-medium text-muted-foreground">Turn {visibleTurnNumber}</span>
          <span>·</span>
          <span className="tabular-nums">{startedAt}</span>
          {turn.latency_total_ms > 0 && (
            <>
              <span>·</span>
              <span className="tabular-nums">{formatMs(turn.latency_total_ms)}</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover/turn:opacity-100">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={copyTurn}
            title={t("turn_card.copy_turn")}
          >
            <Copy />
            {t("turn_card.copy")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={downloadTurn}
            title={t("turn_card.download_turn")}
            aria-label={t("turn_card.download_turn")}
          >
            <Download />
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        {/* The user: a bubble on the lift surface, right-aligned like a chat. */}
        {turn.user_text && (
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Mic aria-hidden className="h-3.5 w-3.5" />
              <span>User</span>
              <span className="text-foreground-faint">{turn.user_lang}</span>
              {polished && (
                <Badge variant="secondary" data-testid="turn-polished-badge">
                  {t("session_turn.polished")}
                </Badge>
              )}
            </div>
            <div className={cn(PROSE, "max-w-[85%] rounded-lg bg-secondary px-4 py-3 text-foreground")}>
              {polished ?? turn.user_text}
            </div>
            {polished && (
              <button
                type="button"
                onClick={() => setShowRaw((v) => !v)}
                className="rounded-sm text-sm text-muted-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                data-testid="turn-polished-toggle"
              >
                {showRaw
                  ? t("session_turn.hide_original")
                  : t("session_turn.show_original")}
              </button>
            )}
            {polished && showRaw && (
              <div
                className={cn(PROSE, "max-w-[85%] rounded-lg border border-dashed border-border px-4 py-3 text-muted-foreground")}
                data-testid="turn-raw-text"
              >
                {turn.user_text}
              </div>
            )}
          </div>
        )}

        {/* Tools the turn reached for. */}
        {turn.tool_calls.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
            <Wrench aria-hidden className="h-3.5 w-3.5" />
            <span>Tools:</span>
            {turn.tool_calls.map((tc) => (
              <Badge key={tc} variant="secondary" className="font-mono">
                {tc}
              </Badge>
            ))}
          </div>
        )}

        {/* The assistant: on the card surface, with its voice named beside it. */}
        {audibleReply && (
          <div className="flex flex-col items-start gap-1">
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Volume2 aria-hidden className="h-3.5 w-3.5" />
              <span className="font-medium text-foreground">{assistantName}</span>
              <span className="text-foreground-faint">{turn.jarvis_lang}</span>
              {turn.voice_name && (
                <span
                  className="text-foreground-faint"
                  title={
                    turn.voice_verified === false
                      ? `Requested voice (native audio is not a verified speaker): ${
                          turn.voice_provider
                            ? `${turn.voice_name} (${turn.voice_provider})`
                            : turn.voice_name
                        }`
                      : turn.voice_provider
                        ? `Voice: ${turn.voice_name} (${turn.voice_provider})`
                        : `Voice: ${turn.voice_name}`
                  }
                >
                  {[
                    turn.voice_name,
                    turn.voice_provider,
                    turn.voice_verified === false ? "requested" : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              )}
              {turn.awaiting_confirmation && (
                <Badge variant="warning">Awaiting confirmation</Badge>
              )}
            </div>
            <div className={cn(PROSE, "w-full rounded-lg border border-border bg-card p-4 text-foreground")}>
              {audibleReply}
            </div>
          </div>
        )}

        {/* Supplemental spoken output: status phrases and readbacks, in the
            order they were heard, each with its kind as a small badge. */}
        {auxiliarySpoken.length > 0 && (
          <div className="space-y-2" data-testid="turn-spoken-output">
            <div className="text-sm text-muted-foreground">Spoken output</div>
            {auxiliarySpoken.map((s, i) => (
              <div
                key={`${s.ts_ms}-${i}`}
                data-spoken-kind={s.spoken_kind}
                data-spoken-tone={s.spoken_kind === "subagent" ? "agent" : "status"}
                className={cn(
                  "flex items-start gap-3 rounded-lg border p-3",
                  s.spoken_kind === "subagent"
                    ? "border-accent/20 bg-accent-soft"
                    : "border-border bg-card",
                )}
              >
                <Badge
                  variant={s.spoken_kind === "subagent" ? "accent" : "secondary"}
                  className="mt-0.5 shrink-0"
                >
                  {kindLabel[s.spoken_kind] ?? s.spoken_kind}
                </Badge>
                <span className={cn(PROSE, "flex-1 text-foreground")}>{s.text}</span>
              </div>
            ))}
          </div>
        )}

        {/* The facts of the exchange, in one muted line. */}
        {facts.length > 0 && (
          <p className="text-sm text-foreground-faint" data-testid="turn-facts">
            {facts.join(" · ")}
          </p>
        )}
      </div>
    </article>
  );
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatTurnPlain(
  turn: VoiceTurnRow,
  spoken: VoiceSpokenLine[] = [],
  displayNumber: number = turn.idx + 1,
  kindLabel: Record<string, string> = spokenKindLabels(""),
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
    const label = (kindLabel[s.spoken_kind] ?? s.spoken_kind).toUpperCase();
    jarvisLines.push({ ts_ms: s.ts_ms, lines: [`[SPOKEN: ${label}] ${s.text}`] });
  }
  jarvisLines
    .sort((a, b) => a.ts_ms - b.ts_ms)
    .forEach((item) => lines.push(...item.lines));
  return lines.join("\n");
}

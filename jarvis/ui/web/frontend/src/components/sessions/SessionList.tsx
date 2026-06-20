/**
 * Sidebar-Liste der Voice-Sessions, neueste zuerst.
 *
 * Eine Zeile = eine Session-Card mit:
 *  - Datum + Zeit (relativ zu jetzt)
 *  - Dauer / Turn-Count
 *  - Erste User-Utterance als Preview
 *  - Hangup-Reason als Badge
 */
import { Clock, Loader2, Mic, MicOff } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { translate, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { SessionListItem } from "./types";

interface Props {
  sessions: SessionListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}

export function SessionList({ sessions, selectedId, onSelect, loading }: Props) {
  const t = useT();
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        {t("session_list.loading")}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
        <MicOff className="h-8 w-8 opacity-40" />
        <div className="font-medium">{t("session_list.empty_title")}</div>
        <div className="text-xs">
          {t("session_list.empty_hint")}
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <ul className="space-y-1 p-2">
        {sessions.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className={cn(
                "group w-full rounded-lg border border-transparent p-3 text-left text-sm transition-all",
                "hover:border-border hover:bg-background/60",
                s.id === selectedId &&
                  "border-primary/40 bg-background shadow-[inset_2px_0_0_hsl(var(--primary))]",
              )}
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Mic className="h-3 w-3" />
                  {formatRelative(s.started_ms)}
                </span>
                {s.ended_ms === null ? (
                  <Badge variant="default" className="animate-pulse">
                    {t("sessions.running")}
                  </Badge>
                ) : (
                  <Badge variant="secondary" className="text-[10px]">
                    {hangupLabel(s.hangup_reason)}
                  </Badge>
                )}
              </div>
              <div className="line-clamp-2 text-foreground/90">
                {s.preview || (
                  <span className="italic text-muted-foreground/70">
                    {t("session_list.no_user_text")}
                  </span>
                )}
              </div>
              <div className="mt-1.5 flex items-center gap-3 text-[10px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {formatDuration(s.duration_s)}
                </span>
                <span>· {s.turn_count} {t("session_list.turns")}</span>
                {s.total_cost_usd > 0 && (
                  <span>· ${s.total_cost_usd.toFixed(4)}</span>
                )}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </ScrollArea>
  );
}

// --- Helpers ---------------------------------------------------------

// Compose "vor 5 min" / "5 min ago" / "hace 5 min" from a localized prefix and
// suffix so the word order stays correct in every language. One side is empty
// per locale (de/es use a prefix, en uses the "ago" suffix). Joined with a
// single space and trimmed so the empty side leaves no double gap.
function ago(value: string): string {
  const prefix = translate("session_list.ago_prefix");
  const suffix = translate("session_list.ago_suffix");
  return [prefix, value, suffix].filter(Boolean).join(" ");
}

function formatRelative(ms: number): string {
  const diff = Date.now() - ms;
  if (diff < 60_000) return translate("session_list.just_now");
  if (diff < 3_600_000)
    return ago(`${Math.floor(diff / 60_000)} ${translate("session_list.unit_min")}`);
  if (diff < 86_400_000)
    return ago(`${Math.floor(diff / 3_600_000)} ${translate("session_list.unit_hour")}`);
  const d = new Date(ms);
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

function formatDuration(secs: number | null): string {
  if (secs === null) return translate("session_list.duration_running");
  if (secs < 60) return `${secs.toFixed(0)} ${translate("session_list.unit_sec")}`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} ${translate("session_list.unit_min")}`;
  const hours = Math.floor(mins / 60);
  return `${hours} ${translate("session_list.unit_hour")} ${mins % 60} ${translate(
    "session_list.unit_min",
  )}`;
}

function hangupLabel(reason: string): string {
  switch (reason) {
    case "voice_pattern":
      return translate("session_list.hangup_voice_pattern");
    case "hotkey":
      return translate("session_list.hangup_hotkey");
    case "idle_timeout":
      return translate("session_list.hangup_idle_timeout");
    case "shutdown":
      return translate("session_list.hangup_shutdown");
    case "error":
      return translate("session_list.hangup_error");
    case "turn_complete":
      return translate("session_list.hangup_turn_complete");
    default:
      return reason || "—";
  }
}

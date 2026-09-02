import { Loader2, Mic, MicOff } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { ScrollArea } from "@/components/ui/scroll-area";
import { translate, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { SessionListItem } from "./types";
import { VoiceModeBadge } from "./VoiceModeBadge";

interface Props {
  sessions: SessionListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}

/**
 * The session rail: one row per voice session — the first thing said as the
 * title, one muted meta line under it (when · how long · turns · cost), a
 * status dot for a live session. No pills: the mode and the hang-up reason
 * belong to the detail header, where there is room to read them.
 */
export function SessionList({ sessions, selectedId, onSelect, loading }: Props) {
  const t = useT();
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-base text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("session_list.loading")}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState
          icon={<MicOff />}
          title={t("session_list.empty_title")}
          description={t("session_list.empty_hint")}
        />
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <ul className="space-y-0.5 p-2">
        {sessions.map((s) => {
          const active = s.id === selectedId;
          const live = s.ended_ms === null;
          const meta = [
            formatRelative(s.started_ms),
            formatDuration(s.duration_s),
            `${s.turn_count} ${t("session_list.turns")}`,
            s.total_cost_usd > 0 ? `$${s.total_cost_usd.toFixed(2)}` : null,
          ].filter(Boolean);
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onSelect(s.id)}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "flex w-full items-start gap-3 rounded-md px-3 py-3 text-left transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                <span className="relative mt-1 shrink-0">
                  <Mic aria-hidden className="h-4 w-4" />
                  {live && (
                    <span
                      aria-label={t("sessions.running")}
                      className="absolute -right-0.5 -top-0.5 h-2 w-2 animate-pulse rounded-full bg-success ring-2 ring-sidebar"
                    />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base font-medium text-foreground">
                    {s.preview || (
                      <span className="font-normal italic text-muted-foreground">
                        {t("session_list.no_user_text")}
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block truncate text-sm text-muted-foreground">
                    {meta.join(" · ")}
                  </span>
                </span>
                <VoiceModeBadge mode={s.voice_mode} className="mt-0.5" />
              </button>
            </li>
          );
        })}
      </ul>
    </ScrollArea>
  );
}

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

export function hangupLabel(reason: string): string {
  switch (reason) {
    case "voice_pattern":
      return translate("session_list.hangup_voice_pattern");
    case "hotkey":
      return translate("session_list.hangup_hotkey");
    case "client_stop":
      return translate("session_list.hangup_client_stop");
    case "ws_closed":
      return translate("session_list.hangup_ws_closed");
    case "realtime_fallback":
      return translate("session_list.hangup_realtime_fallback");
    case "desktop_fallback":
      return translate("session_list.hangup_desktop_fallback");
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

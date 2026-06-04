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
import { cn } from "@/lib/utils";

import type { SessionListItem } from "./types";

interface Props {
  sessions: SessionListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}

export function SessionList({ sessions, selectedId, onSelect, loading }: Props) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Lade Transkripte…
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
        <MicOff className="h-8 w-8 opacity-40" />
        <div className="font-medium">Noch keine Voice-Sessions</div>
        <div className="text-xs">
          Sage <span className="font-mono">"Hey Jarvis"</span> — beim Auflegen
          erscheint die Session hier.
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
                    läuft
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
                    (kein User-Text aufgezeichnet)
                  </span>
                )}
              </div>
              <div className="mt-1.5 flex items-center gap-3 text-[10px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {formatDuration(s.duration_s)}
                </span>
                <span>· {s.turn_count} Turns</span>
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

function formatRelative(ms: number): string {
  const diff = Date.now() - ms;
  if (diff < 60_000) return "gerade eben";
  if (diff < 3_600_000) return `vor ${Math.floor(diff / 60_000)} min`;
  if (diff < 86_400_000) return `vor ${Math.floor(diff / 3_600_000)} h`;
  const d = new Date(ms);
  return d.toLocaleDateString("de", { day: "2-digit", month: "short" });
}

function formatDuration(secs: number | null): string {
  if (secs === null) return "läuft";
  if (secs < 60) return `${secs.toFixed(0)} s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  return `${hours} h ${mins % 60} min`;
}

function hangupLabel(reason: string): string {
  switch (reason) {
    case "voice_pattern":
      return "Auflegen";
    case "hotkey":
      return "Hotkey";
    case "idle_timeout":
      return "Timeout";
    case "shutdown":
      return "Shutdown";
    case "error":
      return "Fehler";
    case "turn_complete":
      return "Antwort fertig";
    default:
      return reason || "—";
  }
}

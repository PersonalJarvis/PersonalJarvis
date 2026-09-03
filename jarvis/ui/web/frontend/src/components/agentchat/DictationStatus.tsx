/**
 * "The microphone is on" — the one recording strip every composer shows while
 * dictation runs.
 *
 * Three composers grew three different answers to the same question: the front
 * page drew a grey strip with a green dot, the agent composer a blue one, and
 * the society chat nothing at all beyond a faint tint on a 32px button. Someone
 * who pressed the mic and said nothing yet had no way to tell whether the app
 * was listening. This is that one answer, shared.
 *
 * Everything it shows is real. `dictating` is the live session flag from the
 * event bus, and the clock counts from the moment that flag went true — there
 * is no audio level in the frontend, so nothing here pretends to be a level
 * meter. The pulse means "running", not "loud".
 *
 * Green, not red: a live microphone is a state, not a fault, and the palette
 * keeps `--destructive` for faults (see components/VoiceIndicator.tsx).
 */
import { useEffect, useRef, useState } from "react";

import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** "0:07", "1:42" — mm:ss with no leading hour nobody dictates for. */
function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export interface DictationStatusProps {
  /**
   * Ends the dictation. Given one, the strip carries its own Stop — the
   * fastest way out is then the thing that just appeared, not the small icon
   * the user has to find again.
   */
  onStop?: () => void;
  className?: string;
}

export function DictationStatus({ onStop, className }: DictationStatusProps) {
  const t = useT();
  const dictating = useEventStore((s) => s.dictating);
  const startedRef = useRef(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!dictating) {
      startedRef.current = 0;
      setElapsed(0);
      return;
    }
    startedRef.current = Date.now();
    setElapsed(0);
    const id = window.setInterval(() => setElapsed(Date.now() - startedRef.current), 500);
    return () => window.clearInterval(id);
  }, [dictating]);

  if (!dictating) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="dictation-status"
      className={cn(
        "flex items-center gap-2 rounded-md border border-success/30 bg-success/10 px-3 py-1.5 text-meta text-foreground",
        className,
      )}
    >
      <span className="relative flex h-2 w-2 shrink-0" aria-hidden>
        <span className="absolute inline-flex h-full w-full rounded-full bg-success/70 motion-safe:animate-ping" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
      </span>
      <span className="min-w-0 truncate">{t("chats_view.dictation_listening")}</span>
      <span className="ml-auto shrink-0 font-mono tabular-nums text-muted-foreground">
        {formatElapsed(elapsed)}
      </span>
      {onStop ? (
        <button
          type="button"
          onClick={onStop}
          className="shrink-0 rounded px-1.5 py-0.5 font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
        >
          {t("chats_view.dictation_stop")}
        </button>
      ) : null}
    </div>
  );
}

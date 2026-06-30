import { useEffect, useRef, useState } from "react";
import { Loader2, Mic } from "lucide-react";
import { useT } from "@/i18n";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { cn } from "@/lib/utils";

/**
 * Honest "can I speak yet?" indicator.
 *
 * The desktop window appears within ~1 s, but the wake word is not actually
 * ready until the wake model has loaded (a few seconds later — longer on a cold
 * cache). Backend made honest 2026-06-27: `voiceReady` now flips to true only
 * once the wake model can really hear (see jarvis/speech/pipeline.py). This
 * banner surfaces that gap PROMINENTLY (not just a sidebar dot) so users don't
 * speak too early and think the system is broken:
 *   - while warming  -> amber "Jarvis is starting up / getting ready to listen"
 *   - on becoming ready -> a brief green "Ready — you can speak now" confirmation
 */
export function VoiceWarmingBanner() {
  const t = useT();
  // Single source of truth shared with the Sidebar status line and the chat
  // empty-state, so all three readiness surfaces agree (no more "banner says
  // starting up while the centre says Ready for commands").
  const { warming, ready } = useVoiceReadiness();

  // Flash an explicit "you can speak now" confirmation on the warming -> ready
  // transition, so the go-ahead is a positive signal, not the banner silently
  // disappearing.
  const [justReady, setJustReady] = useState(false);
  const wasWarming = useRef(false);
  useEffect(() => {
    if (wasWarming.current && !warming && ready) {
      setJustReady(true);
      const timer = setTimeout(() => setJustReady(false), 4000);
      wasWarming.current = warming;
      return () => clearTimeout(timer);
    }
    wasWarming.current = warming;
    return undefined;
  }, [warming, ready]);

  if (!warming && !justReady) return null;

  return (
    <div
      data-testid="voice-warming-banner"
      data-state={warming ? "warming" : "ready"}
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-3 border-b px-4 py-2.5 text-sm transition-colors",
        warming
          ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
          : "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
      )}
    >
      {warming ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
      ) : (
        <Mic className="h-4 w-4 shrink-0" aria-hidden />
      )}
      <div className="flex min-w-0 flex-col">
        <span className="font-medium leading-tight">
          {warming
            ? t("voice_state.warming_title")
            : t("voice_state.ready_title")}
        </span>
        {warming && (
          <span className="text-xs leading-tight opacity-80">
            {t("voice_state.warming_hint")}
          </span>
        )}
      </div>
    </div>
  );
}

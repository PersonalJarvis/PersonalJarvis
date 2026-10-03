import { Loader2 } from "lucide-react";
import { useT } from "@/i18n";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";

/**
 * Show the startup status only while voice is unavailable. Readiness is
 * shared with the sidebar and chat; completing startup clears this strip.
 */
export function VoiceWarmingBanner() {
  const t = useT();
  const { warming } = useVoiceReadiness();
  if (!warming) return null;

  return (
    <div
      data-testid="voice-warming-banner"
      data-state="warming"
      role="status"
      aria-live="polite"
      // No wash. The banner spans the whole window, and a full-bleed region
      // never rises off the page it sits on — so the state is carried by the
      // GLYPH's colour and the hairline under it, the way a status is meant to
      // be, instead of by a tinted slab across the top of the app.
      className="flex items-center gap-3 border-b border-border px-4 py-2.5"
    >
      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-warning" aria-hidden />
      <span className="text-body font-medium text-foreground-strong">
        {t("voice_state.warming_title")}
      </span>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff } from "lucide-react";
import { useT } from "@/i18n";
import {
  useVoiceBlockedByPermission,
  useVoiceNotAskedByPermission,
} from "@/hooks/useVoiceBlockedByPermission";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";

/**
 * Honest "can I speak yet?" indicator.
 *
 * The desktop window appears within ~1 s, but the wake word is not actually
 * ready until the wake model has loaded (a few seconds later — longer on a cold
 * cache). Backend made honest 2026-06-27: `voiceReady` now flips to true only
 * once the wake model can really hear (see jarvis/speech/pipeline.py). This
 * banner surfaces that gap PROMINENTLY (not just a sidebar dot) so users don't
 * speak too early and think the system is broken:
 *   - while warming  -> amber "{name} is starting up / getting ready to listen"
 *     (the `{name}` i18n token resolves to the configured assistant name,
 *     falling back to the neutral "Assistant" — never a hardcoded brand)
 *   - on becoming ready -> a brief green "Ready — you can speak now" confirmation
 *   - on becoming ready with the microphone blocked by macOS -> the honest
 *     "Voice is ready, but the microphone is blocked" line instead, so the banner
 *     never says "you can speak now" while the sidebar header says "Microphone
 *     blocked" (both read `useVoiceBlockedByPermission`). macOS asking by itself
 *     is not a block there, so that case keeps the normal confirmation.
 */
export function VoiceWarmingBanner() {
  const t = useT();
  // Single source of truth shared with the Sidebar status line and the chat
  // empty-state, so all three readiness surfaces agree (no more "banner says
  // starting up while the centre says Ready for commands").
  const { warming, ready } = useVoiceReadiness();
  const micBlocked = useVoiceBlockedByPermission();
  const micNotAsked = useVoiceNotAskedByPermission();

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

  // Warming outranks it (the sidebar says "Voice starting…" then too); only the
  // go-ahead is withheld while macOS has the microphone blocked.
  const state = warming
    ? "warming"
    : micBlocked
      ? "blocked"
      : micNotAsked
        ? "not_asked"
        : "ready";

  return (
    <div
      data-testid="voice-warming-banner"
      data-state={state}
      role="status"
      aria-live="polite"
      // No wash. The banner spans the whole window, and a full-bleed region
      // never rises off the page it sits on — so the state is carried by the
      // GLYPH's colour and the hairline under it, the way a status is meant to
      // be, instead of by a tinted slab across the top of the app.
      className="flex items-center gap-3 border-b border-border px-4 py-2.5"
    >
      {state === "warming" ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-warning" aria-hidden />
      ) : state === "blocked" || state === "not_asked" ? (
        <MicOff className="h-4 w-4 shrink-0 text-warning" aria-hidden />
      ) : (
        <Mic className="h-4 w-4 shrink-0 text-success" aria-hidden />
      )}
      <div className="flex min-w-0 flex-col">
        <span className="text-body font-medium text-foreground-strong">
          {state === "warming"
            ? t("voice_state.warming_title")
            : state === "blocked"
              ? t("voice_state.ready_blocked_title")
              : state === "not_asked"
                ? t("voice_state.ready_not_asked_title")
                : t("voice_state.ready_title")}
        </span>
        {warming && (
          <span className="text-meta text-muted-foreground">
            {t("voice_state.warming_hint")}
          </span>
        )}
      </div>
    </div>
  );
}

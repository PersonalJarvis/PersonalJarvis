import { Radio } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";

/**
 * Realtime voice (browser) toggle inside the Settings view. Off by default —
 * flips ``mode`` between "pipeline" (the existing wake-word/STT/TTS pipeline)
 * and "realtime" (the browser-side full-duplex OpenAI Realtime engine). The
 * switch is disabled while loading/saving or when the backend reports the
 * realtime engine isn't available (e.g. no OpenAI key configured yet).
 *
 * Uses the in-house `useT()` i18n hook (see src/i18n/index.ts) rather than
 * react-i18next — this project doesn't depend on react-i18next.
 */
export function RealtimeVoiceGroup() {
  const t = useT();
  const { mode, realtimeAvailable, setMode, isLoading, isSaving } = useVoiceMode();
  const on = mode === "realtime";
  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Radio className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-medium">{t("settings_view.realtime_voice.title")}</h4>
            <Switch
              checked={on}
              disabled={isLoading || isSaving || !realtimeAvailable}
              onCheckedChange={(next) => setMode(next ? "realtime" : "pipeline")}
            />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {realtimeAvailable
              ? t("settings_view.realtime_voice.description")
              : t("settings_view.realtime_voice.unavailable")}
          </p>
        </div>
      </div>
    </div>
  );
}

import { Radio } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";

/**
 * Provider-neutral Realtime voice toggle inside the Settings view. It flips
 * ``mode`` between the classic wake-word/STT/TTS pipeline and full-duplex
 * Realtime voice. A missing Realtime key blocks turning the mode on, but never
 * traps a stale Realtime setting: users can always switch back to Pipeline.
 *
 * Uses the in-house `useT()` i18n hook (see src/i18n/index.ts) rather than
 * react-i18next — this project doesn't depend on react-i18next.
 */
export function RealtimeVoiceGroup() {
  const t = useT();
  const {
    mode,
    realtimeAvailable,
    sessionActive,
    activeSessionMode,
    activeSessionProvider,
    activeSessionModel,
    transitioning,
    setMode,
    isLoading,
    isSaving,
  } = useVoiceMode();
  const on = mode === "realtime";
  const runtimeDetail = [activeSessionProvider, activeSessionModel]
    .filter(Boolean)
    .join(" · ");
  const runtimeText = transitioning
    ? t("apikeys_view.runtime_switching")
    : sessionActive && activeSessionMode === "realtime"
      ? `${t("apikeys_view.runtime_realtime")}${runtimeDetail ? ` · ${runtimeDetail}` : ""}`
      : sessionActive && activeSessionMode === "pipeline" && on
        ? t("apikeys_view.runtime_fallback_pipeline")
        : sessionActive
          ? t("apikeys_view.runtime_pipeline")
          : t("apikeys_view.runtime_idle");
  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Radio className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-medium">{t("settings_view.realtime_voice.title")}</h4>
            <Switch
              checked={on}
              disabled={isLoading || isSaving || (!on && !realtimeAvailable)}
              onCheckedChange={(next) => setMode(next ? "realtime" : "pipeline")}
            />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {realtimeAvailable
              ? t("settings_view.realtime_voice.description")
              : t("settings_view.realtime_voice.unavailable")}
          </p>
          <p className="mt-1.5 text-[11px] text-muted-foreground" aria-live="polite">
            {runtimeText}
          </p>
        </div>
      </div>
    </div>
  );
}

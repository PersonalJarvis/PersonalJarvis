import { CuModelSelector } from "@/components/CuModelSelector";
import { useProviders } from "@/hooks/useProviders";
import { useT } from "@/i18n";

/**
 * Realtime mode's Computer-Use delegation panel (Feature B).
 *
 * Realtime speech-to-speech models (`openai-realtime`, `gemini-live`) are pure
 * audio — they have no vision/screen and no tool-call event
 * (`jarvis/realtime/protocol.py`), so they can never run Computer-Use
 * themselves. Computer-Use already runs on the ACTIVE Brain provider today
 * (`jarvis/brain/brain_call.py` → `_build_fallback_chain("fast")` leads with
 * `brain.primary`), so this panel just makes that delegation visible inside
 * the Realtime tab and lets the user pin a Computer-Use model for it — no new
 * "CU provider" concept, no backend change. It reuses the existing
 * `CuModelSelector`, which already talks to `GET/PUT
 * /api/providers/{id}/cu-model` and is vision-filtered; that endpoint 400s for
 * non-brain ids, so this always passes the resolved BRAIN provider id, never a
 * realtime id.
 *
 * Reads providers via its own `useProviders()` call (read-through, like
 * `SubagentCategory`'s `JarvisAgentSection` owns `/api/openclaw/status`)
 * rather than a prop, so it never depends on ApiKeysView passing the right
 * slice down.
 */
export function RealtimeComputerUsePanel() {
  const t = useT();
  const { providers } = useProviders();
  const activeBrain = providers.find((p) => p.tier === "brain" && p.active);

  return (
    <section
      data-testid="realtime-cu-panel"
      className="mt-6 border-t border-border pt-6"
    >
      <h3 className="font-display text-sm font-semibold tracking-tight">
        {t("apikeys_realtime_cu.title")}
      </h3>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {activeBrain
          ? t("apikeys_realtime_cu.description").replace("{0}", activeBrain.label)
          : t("apikeys_realtime_cu.description_generic")}
      </p>

      <div className="mt-3">
        {activeBrain ? (
          <CuModelSelector
            providerId={activeBrain.id}
            recommendedModel={activeBrain.recommended_model}
          />
        ) : (
          <p
            data-testid="realtime-cu-no-brain-hint"
            className="rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-700"
          >
            {t("apikeys_realtime_cu.no_active_brain_hint")}
          </p>
        )}
      </div>
    </section>
  );
}

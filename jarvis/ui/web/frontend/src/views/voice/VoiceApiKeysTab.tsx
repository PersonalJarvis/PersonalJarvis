import { useEffect, useRef, useState } from "react";
import { KeyRound } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import {
  EngineModeSwitch,
  makeProviderCategories,
  ProviderCategory,
  useTierHealth,
  type VoiceEngineMode,
} from "@/components/providers/ProviderTierSection";
import { useProviders } from "@/hooks/useProviders";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";

export interface VoiceApiKeysTabProps {
  /**
   * Suppress this view's own `ViewHeader`.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second bordered band right below it reads as a
   * rendering fault. Standalone rendering keeps its own header.
   */
  hideHeader?: boolean;
}

/**
 * "API Keys" tab of the merged voice section — the providers and keys that turn
 * speech into text.
 *
 * This is the SAME provider block the API-Keys view renders, scoped to the
 * `stt` tier: one shared `ProviderCategory`, not a second implementation. A key
 * saved here is therefore the same stored credential the API-Keys view shows,
 * and vice versa — the two screens are never mounted at the same time
 * (`MainView.SwitchOnActiveSection` renders exactly one section), and
 * `ApiKeyForm` announces every save/delete on the window event bus that
 * `useProviders` / `useSectionHealth` already listen to, so whichever screen is
 * opened next reads the current truth.
 *
 * The engine switch rides along because it decides whether this tier is used at
 * all: speech-to-text is a Pipeline-mode stage, and a realtime model replaces
 * it entirely. Hiding that would let someone add a key here that their voice
 * never touches.
 */
export function VoiceApiKeysTab({ hideHeader = false }: VoiceApiKeysTabProps = {}) {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  const health = useTierHealth(providers);
  const categories = makeProviderCategories(t);
  const {
    mode: liveMode,
    realtimeAvailable,
    setMode: setVoiceMode,
    isLoading: liveModeLoading,
  } = useVoiceMode();

  // Mirror the API-Keys view: open on the engine that is actually LIVE, once,
  // when the mode query resolves. Later live-mode changes never yank the view.
  const [engineMode, setEngineMode] = useState<VoiceEngineMode>("pipeline");
  const viewSyncedToLive = useRef(false);
  useEffect(() => {
    if (viewSyncedToLive.current || liveModeLoading) return;
    viewSyncedToLive.current = true;
    setEngineMode(liveMode === "realtime" ? "realtime" : "pipeline");
  }, [liveMode, liveModeLoading]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<KeyRound className="h-4 w-4 text-primary" />}
          title={t("voice.api_keys.title")}
          subtitle={t("voice.api_keys.description")}
        />
      )}
      <div
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-4 py-4"
        data-testid="voice-api-keys-tab"
      >
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-border bg-card/25 px-3 py-2">
            <p className="min-w-64 flex-1 text-[11px] leading-snug text-muted-foreground/80">
              {t("apikeys_view.mode_keys_hint")}
            </p>
            <EngineModeSwitch
              mode={engineMode}
              liveMode={liveMode}
              realtimeAvailable={realtimeAvailable}
              onSelect={setEngineMode}
              onSetVoiceMode={setVoiceMode}
            />
          </div>

          <ProviderCategory
            meta={categories.stt}
            tier="stt"
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
            health={health.stt}
          />
        </div>
      </div>
    </div>
  );
}

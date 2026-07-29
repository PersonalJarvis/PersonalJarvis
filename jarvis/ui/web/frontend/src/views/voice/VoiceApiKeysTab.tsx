import { KeyRound } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import {
  makeProviderCategories,
  ProviderCategory,
  useTierHealth,
} from "@/components/providers/ProviderTierSection";
import { useProviders } from "@/hooks/useProviders";
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
 * The Realtime|Pipeline engine switch deliberately does NOT ride along
 * (maintainer feedback 2026-07-29). This section is about turning speech into
 * text; picking the voice ENGINE is a different decision, and it already has
 * one home with the full explanatory context next to it — the API-Keys view's
 * `EngineModeSwitch` + `VoiceEngineContext`. A second switch here read as a
 * stray control in a section that never asked the question. Do not add it back.
 */
export function VoiceApiKeysTab({ hideHeader = false }: VoiceApiKeysTabProps = {}) {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  const health = useTierHealth(providers);
  const categories = makeProviderCategories(t);

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

          {/* The optional wording pass, right under the tier whose output it
              cleans up. It belongs here and not only on the API-Keys screen:
              this is the place someone configures dictation, and a key they
              never find is a feature they never get. */}
          <ProviderCategory
            meta={categories.dictation}
            tier="dictation"
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
            health={health.dictation}
          />
        </div>
      </div>
    </div>
  );
}

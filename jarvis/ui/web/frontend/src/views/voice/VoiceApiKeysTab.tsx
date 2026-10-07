import { AlertTriangle, KeyRound } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import { TierSection, useTierHealth } from "@/components/providers/ProviderTierSection";
import {
  PROVIDER_BACKEND_UNREACHABLE,
  useProviders,
  type ProviderDescriptor,
  type ProviderTier,
  type SectionHealth,
} from "@/hooks/useProviders";
import { VoiceGroup, VoiceNote, VoicePage, VoiceSection, VoiceTag } from "@/views/voice/voiceUi";
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

/** The two tiers this tab owns, in reading order. */
const TIERS: readonly {
  tier: Extract<ProviderTier, "stt" | "dictation">;
  titleKey: string;
  descriptionKey: string;
}[] = [
  {
    tier: "stt",
    titleKey: "voice.api_keys.stt_title",
    // Shared with the API-Keys view; it names the assistant by its {name} token.
    descriptionKey: "apikeys_view.cat_stt_desc",
  },
  {
    tier: "dictation",
    titleKey: "voice.api_keys.dictation_title",
    descriptionKey: "voice.api_keys.dictation_description",
  },
];

/**
 * "API Keys" tab of the merged voice section — the providers and keys that turn
 * speech into text.
 *
 * This is the SAME provider list the API-Keys view renders (`TierSection`, in
 * its voice presentation), scoped to the `stt` tier and the optional
 * `dictation` wording tier: not a second implementation. A key saved here is
 * therefore the same stored credential the API-Keys view shows, and vice versa
 * — the two screens are never mounted at the same time
 * (`MainView.SwitchOnActiveSection` renders exactly one section), and
 * `ApiKeyForm` announces every save/delete on the window event bus that
 * `useProviders` / `useSectionHealth` already listen to, so whichever screen is
 * opened next reads the current truth.
 *
 * Every state on screen comes from data already fetched (the provider list,
 * the cached section health, this session's own Test results). Nothing here
 * probes a provider on its own — a test only runs when the user presses Test.
 *
 * No voice-engine control rides along (maintainer feedback 2026-07-29): this
 * section is about turning speech into text. Realtime is the only engine the
 * settings offer, and the API Keys page's realtime tab is where it is set up.
 */
export function VoiceApiKeysTab({ hideHeader = false }: VoiceApiKeysTabProps = {}) {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  const health = useTierHealth(providers);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<KeyRound className="h-4 w-4 text-foreground" />}
          title={t("voice.api_keys.title")}
          subtitle={t("voice.api_keys.description")}
        />
      )}
      <div className="min-h-0 flex-1">
        <VoicePage testId="voice-api-keys-tab">
          {TIERS.map(({ tier, titleKey, descriptionKey }) => (
            <VoiceProviderTier
              key={tier}
              tier={tier}
              title={t(titleKey)}
              description={t(descriptionKey)}
              providers={providers}
              loading={loading}
              error={error}
              onChanged={refetch}
              onActivateOptimistic={setActiveOptimistic}
              health={health[tier]}
            />
          ))}
        </VoicePage>
      </div>
    </div>
  );
}

/**
 * One tier as a voice section: heading, one line, then the provider rows —
 * the one this tier runs on first and marked Active, every other provider a
 * compact row that opens in place for its key, model and test.
 */
function VoiceProviderTier({
  tier,
  title,
  description,
  providers,
  loading,
  error,
  onChanged,
  onActivateOptimistic,
  health,
}: {
  tier: ProviderTier;
  title: string;
  description: string;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
}) {
  const t = useT();
  const tierProviders = providers.filter(
    (p) => p.tier === tier && p.brain_switchable !== false,
  );
  const active = tierProviders.find((p) => p.active);
  // The wording tier ships pinned to "auto": no card is chosen and the
  // key-aware chain decides per call. Said as a state, not left as a list
  // with nothing marked.
  const automatic = tier === "dictation" && !active && tierProviders.length > 0;

  return (
    <VoiceSection
      title={title}
      description={description}
      testId={`voice-provider-tier-${tier}`}
      actions={
        !loading && !error && automatic ? (
          <span data-testid={`voice-provider-tier-${tier}-auto`}>
            <VoiceTag>{t("voice.api_keys.automatic")}</VoiceTag>
          </span>
        ) : undefined
      }
    >
      {loading && (
        <VoiceGroup>
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3.5 sm:px-5">
              <SkeletonBar className="h-9 w-9 rounded-md" />
              <div className="flex-1 space-y-1.5">
                <SkeletonBar className="h-3.5 w-40" />
                <SkeletonBar className="h-3 w-24" />
              </div>
            </div>
          ))}
          <span className="sr-only" role="status">
            {t("apikeys_view.loading_providers")}
          </span>
        </VoiceGroup>
      )}

      {!loading && error && (
        <VoiceNote tone="error" icon={<AlertTriangle />}>
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <span>
              {error === PROVIDER_BACKEND_UNREACHABLE
                ? t("apikeys_view.backend_unavailable")
                : `${t("apikeys_view.load_error")} (${error}).`}
            </span>
            <Button size="sm" variant="outline" onClick={() => onChanged()}>
              {t("common.retry")}
            </Button>
          </div>
        </VoiceNote>
      )}

      {!loading && !error && tierProviders.length === 0 && (
        <VoiceNote>{t("apikeys_view.no_providers_in_tier")}</VoiceNote>
      )}

      {!loading && !error && tierProviders.length > 0 && (
        <TierSection
          providers={tierProviders}
          onChanged={onChanged}
          onActivateOptimistic={onActivateOptimistic}
          health={health}
          variant="voice"
        />
      )}
    </VoiceSection>
  );
}

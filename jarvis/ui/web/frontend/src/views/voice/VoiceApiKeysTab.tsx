import { KeyRound } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
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
 * Placeholder shell: the shell, its section id and its i18n keys ship first so
 * the rest of the section can be built against a stable surface. The speech-to-
 * text provider block (the same subtree the API-Keys view renders, scoped to
 * the `stt` tier) lands here next.
 */
export function VoiceApiKeysTab({ hideHeader = false }: VoiceApiKeysTabProps = {}) {
  const t = useT();

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<KeyRound className="h-4 w-4 text-primary" />}
          title={t("voice.api_keys.title")}
          subtitle={t("voice.api_keys.description")}
        />
      )}
      <div
        className="flex-1 overflow-y-auto scrollbar-jarvis p-6"
        data-testid="voice-api-keys-tab"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4" />
      </div>
    </div>
  );
}

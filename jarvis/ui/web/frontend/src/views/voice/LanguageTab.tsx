import { Languages } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";

export interface LanguageTabProps {
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
 * "Language" tab of the merged voice section — which language dictation is
 * transcribed in.
 *
 * Placeholder shell: the shell, its section id and its i18n keys ship first so
 * the rest of the section can be built against a stable surface. The language
 * selector over `[dictation].language` lands here next.
 */
export function LanguageTab({ hideHeader = false }: LanguageTabProps = {}) {
  const t = useT();

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Languages className="h-4 w-4 text-primary" />}
          title={t("voice.language.title")}
          subtitle={t("voice.language.description")}
        />
      )}
      <div
        className="flex-1 overflow-y-auto scrollbar-jarvis p-6"
        data-testid="voice-language-tab"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4" />
      </div>
    </div>
  );
}

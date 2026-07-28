import { Info, Languages, Loader2 } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useDictation } from "@/hooks/useDictation";
import { useEventStore } from "@/store/events";
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
 * One control, and a deliberate recommendation attached to it: leave it on
 * automatic. Pinning a language is not a quality setting — it forces the
 * recognition model to decode every utterance as that language, which makes
 * results *worse* on a model that was never trained for it, and turns a
 * second-language sentence into nonsense instead of a best guess. The hint
 * says that in plain words rather than presenting four equal-looking options.
 *
 * This governs `[dictation].language` only. The wake word and the assistant's
 * reply language are separate settings on purpose — dictating in English while
 * being answered in German is a normal thing to want.
 */
export function LanguageTab({ hideHeader = false }: LanguageTabProps = {}) {
  const t = useT();
  const { settings, choices, loading, error, saveSettings } = useDictation();
  const pushToast = useEventStore((s) => s.pushToast);

  async function onPick(language: string) {
    try {
      await saveSettings({ language });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  const options = choices?.language ?? [];
  const value = settings?.language ?? "auto";

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
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {error && <p className="text-xs text-destructive">{error}</p>}

          <div className="rounded-lg border border-border bg-card/60 p-4">
            <h4 className="font-display text-sm font-semibold">
              {t("voice.language.title")}
            </h4>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("voice.language.description")}
            </p>

            {loading ? (
              <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("dictation.loading")}
              </div>
            ) : (
              <label className="mt-3 flex max-w-xs flex-col gap-1">
                <span className="sr-only">{t("voice.language.title")}</span>
                <select
                  value={value}
                  data-testid="dictation-language"
                  aria-label={t("voice.language.title")}
                  onChange={(e) => void onPick(e.target.value)}
                  className="rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  {options.map((option) => (
                    <option key={option} value={option}>
                      {languageLabel(t, option)}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <div className="mt-4 flex items-start gap-2 rounded-md border border-border/60 bg-background/40 p-3">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              <p
                className="text-[11px] text-muted-foreground"
                data-testid="dictation-language-hint"
              >
                {t("voice.language.auto_hint")}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const KNOWN_LANGUAGES: ReadonlySet<string> = new Set(["auto", "de", "en", "es"]);

/**
 * Human label for one language choice. A code this bundle does not have a
 * translation for is shown as-is rather than as a missing-key placeholder —
 * the backend's list is allowed to grow ahead of the frontend.
 */
function languageLabel(t: (key: string) => string, code: string): string {
  return KNOWN_LANGUAGES.has(code) ? t(`voice.language.${code}`) : code;
}

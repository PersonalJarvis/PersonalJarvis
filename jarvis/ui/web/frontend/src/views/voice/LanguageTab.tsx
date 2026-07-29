import { useState } from "react";
import { Info, Languages, Loader2, PlugZap, Wand2 } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  polishStatusLabel,
  testDictationPolish,
  useDictation,
  type DictationPolishTest,
} from "@/hooks/useDictation";
import { Combobox } from "@/components/ui/combobox";
import { LanguageSelect } from "@/components/ui/language-select";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * Display names for the polish families.
 *
 * Presentation only, and an unknown id falls through to the id itself — so a
 * family added on the backend shows up in the dropdown immediately (the LIST
 * comes over the wire, never from here) and merely reads as "cerebras" instead
 * of "Cerebras" until someone adds a line. Brand names are not translated, so
 * this does not belong in the locale files.
 */
const POLISH_PROVIDER_LABELS: Record<string, string> = {
  groq: "Groq",
  cerebras: "Cerebras",
  gemini: "Google Gemini",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  ollama: "Ollama (local)",
};

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
 *
 * The tab also owns the wording pass (`[dictation].polish`), because that is
 * the other half of the same question: the language decides what is recognized,
 * the wording pass decides how what was recognized is written down. Both are
 * text quality, and neither belongs on a screen about keys or shortcuts.
 */
export function LanguageTab({ hideHeader = false }: LanguageTabProps = {}) {
  const t = useT();
  const { settings, choices, loading, error, saveSettings } = useDictation();
  const pushToast = useEventStore((s) => s.pushToast);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<DictationPolishTest | null>(null);

  async function onPick(language: string) {
    try {
      await saveSettings({ language });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onTogglePolish(next: boolean) {
    // The previous run described a configuration that no longer applies, so it
    // goes rather than sitting there as a stale claim.
    setTestResult(null);
    try {
      await saveSettings({ polish: next });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onPickPolishProvider(provider: string) {
    setTestResult(null);
    try {
      await saveSettings({ polish_provider: provider });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function runPolishTest() {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await testDictationPolish());
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  const languageCodes = choices?.language ?? [];
  const value = settings?.language ?? "auto";

  // Default ON, which is safe on an install with no text-model key at all: the
  // chain comes back empty, the pass reports "unavailable" and the raw
  // transcript is delivered — byte-identical to a build without the feature.
  const polishOn = settings?.polish ?? true;
  const polishProvider = settings?.polish_provider ?? "auto";
  const served = choices?.polish_provider ?? ["auto"];
  // A pin the served list does not contain would otherwise render as the first
  // option, and the dropdown would quietly claim a provider the config does not
  // say. Showing the stored value is the honest fallback.
  const polishProviders = served.includes(polishProvider)
    ? served
    : [...served, polishProvider];

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
              <div className="mt-3 max-w-xs">
                <LanguageSelect
                  value={value}
                  codes={languageCodes}
                  onChange={(code) => void onPick(code)}
                  autoLabel={t("voice.language.auto")}
                  ariaLabel={t("voice.language.title")}
                  testId="dictation-language"
                />
              </div>
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

          <div
            className="rounded-lg border border-border bg-card/60 p-4"
            data-testid="dictation-polish-card"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h4 className="flex items-center gap-2 font-display text-sm font-semibold">
                  <Wand2 aria-hidden="true" className="h-3.5 w-3.5 text-primary" />
                  {t("voice.polish.title")}
                </h4>
                {/* The honest trade, in one line: what it changes, what it does
                    not, and where the untouched original stays. Anyone letting
                    a model rewrite their own words deserves to read that before
                    the switch, not after. */}
                <p
                  className="mt-1 text-xs text-muted-foreground"
                  data-testid="dictation-polish-description"
                >
                  {t("voice.polish.description")}
                </p>
              </div>
              <Switch
                checked={polishOn}
                disabled={loading}
                onCheckedChange={(next) => void onTogglePolish(next)}
                aria-label={t("voice.polish.title")}
                data-testid="dictation-polish-toggle"
              />
            </div>

            {polishOn && (
              <>
                {/* The same themed control as the language picker above it —
                    a native <select> sitting right beside one would put the
                    operating system's own grey list back on the card. No
                    search field: this list is six entries, not a hundred. */}
                <div className="mt-3 flex max-w-xs flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {t("voice.polish.provider_label")}
                  </span>
                  <Combobox
                    value={polishProvider}
                    ariaLabel={t("voice.polish.provider_label")}
                    onChange={(id) => void onPickPolishProvider(id)}
                    testId="dictation-polish-provider"
                    groups={[
                      {
                        id: "providers",
                        options: polishProviders.map((id) => ({
                          value: id,
                          label:
                            id === "auto"
                              ? t("voice.polish.provider_auto")
                              : POLISH_PROVIDER_LABELS[id] ?? id,
                        })),
                      },
                    ]}
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  {t("voice.polish.provider_hint")}
                </p>

                <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void runPolishTest()}
                    disabled={testing}
                    data-testid="dictation-polish-test"
                    className="h-7 gap-1.5 text-xs"
                  >
                    {testing ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <PlugZap className="h-3.5 w-3.5" />
                    )}
                    {testing ? t("voice.polish.testing") : t("voice.polish.test")}
                  </Button>
                  <span className="text-[11px] text-muted-foreground">
                    {t("voice.polish.test_hint")}
                  </span>
                </div>

                {testResult && (
                  <div
                    className="mt-3 space-y-2 rounded-md border border-border/60 bg-background/40 p-3"
                    data-testid="dictation-polish-test-result"
                  >
                    <p className="text-[11px] text-muted-foreground">
                      <span className="font-medium text-foreground">
                        {polishStatusLabel(t, testResult.status)}
                      </span>
                      {testResult.provider ? ` · ${testResult.provider}` : ""}
                      {testResult.model ? ` · ${testResult.model}` : ""}
                      {testResult.latency_ms
                        ? ` · ${Math.round(testResult.latency_ms)} ms`
                        : ""}
                      {testResult.reason ? ` · ${testResult.reason}` : ""}
                    </p>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t("voice.polish.sample_before")}
                      </p>
                      <p
                        className="break-words text-xs text-muted-foreground"
                        data-testid="dictation-polish-sample-in"
                      >
                        {testResult.sample_in}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t("voice.polish.sample_after")}
                      </p>
                      <p
                        className="break-words text-xs"
                        data-testid="dictation-polish-sample-out"
                      >
                        {testResult.sample_out}
                      </p>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


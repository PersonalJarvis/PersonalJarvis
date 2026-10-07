import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Info, Languages, Loader2, PlugZap } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  polishStatusLabel,
  testDictationPolish,
  useDictation,
  type DictationPolishTest,
  type DictationSettings,
} from "@/hooks/useDictation";
import { Combobox } from "@/components/ui/combobox";
import { LanguageSelect } from "@/components/ui/language-select";
import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { useProviders } from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import {
  StatusDot,
  VoiceGroup,
  VoiceNote,
  VoicePage,
  VoiceRow,
  VoiceSection,
  VoiceTag,
} from "@/views/voice/voiceUi";
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
 * second-language sentence into nonsense instead of a best guess. The row says
 * automatic suits almost everyone, and a note says what pinning costs once a
 * language is pinned.
 *
 * This governs `[dictation].language` only. The wake word and the assistant's
 * reply language are separate settings on purpose — dictating in English while
 * being answered in German is a normal thing to want.
 *
 * The tab also owns the wording pass (`[dictation].polish`) and the translation
 * (`[dictation].translate`), because those are the other two thirds of the same
 * question: the language decides what is recognized, the wording pass decides
 * how what was recognized is written down, and the translation decides which
 * language it is written down in. All three are text quality, and none belongs
 * on a screen about keys or shortcuts.
 *
 * Laid out in the voice section's grammar: a "Recognition language" section
 * with one row, then a "Writing" section whose one group holds a row per
 * switch, each switch's dependent controls nested under it, and the shared
 * text model with its key and dry run on a last row that exists while any
 * pass is on.
 */
export function LanguageTab({ hideHeader = false }: LanguageTabProps = {}) {
  const t = useT();
  const { settings, choices, wordingProvider, loading, error, saveSettings: persistSettings, refetch } =
    useDictation();
  // Only for the credential half of the translate card: the dashboard link and
  // the "which key is this" line live on the provider catalog, and copying
  // them into this view would be a second source of truth for both.
  const { providers, refetch: refetchProviders } = useProviders();
  const pushToast = useEventStore((s) => s.pushToast);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<DictationPolishTest | null>(null);
  const [saving, setSaving] = useState(false);
  const writing = useRef(false);
  const testVersion = useRef(0);
  const controlsBusy = loading || saving || testing;
  const providerHintId = useId();
  const testHintId = useId();

  useEffect(() => {
    testVersion.current += 1;
    setTestResult(null);
  }, [settings]);

  async function saveSettings(patch: Partial<DictationSettings>) {
    if (writing.current) return;
    writing.current = true;
    testVersion.current += 1;
    setTestResult(null);
    setSaving(true);
    try {
      await persistSettings(patch);
    } finally {
      writing.current = false;
      setSaving(false);
    }
  }

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
      await saveSettings({ polish_provider: provider, polish_model: "" });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onToggleConversation(next: boolean) {
    try {
      await saveSettings({ polish_conversation: next });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onTogglePrecision(next: boolean) {
    // Clearing matters more here than on the other switches: the dry run uses a
    // DIFFERENT sample in precision mode, so a stale result would sit next to
    // the switch showing a sentence the new setting would never produce.
    setTestResult(null);
    try {
      await saveSettings({ polish_precision: next });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onTogglePromptMode(next: boolean) {
    // The dry run switches to Prompt Mode's own sample while this is on, so a
    // result from the other mode would show a sentence this one never writes.
    setTestResult(null);
    try {
      await saveSettings({ prompt_mode: next });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onToggleTranslate(next: boolean) {
    setTestResult(null);
    try {
      await saveSettings({ translate: next });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function onPickTranslateTarget(translate_target: string) {
    setTestResult(null);
    try {
      await saveSettings({ translate_target });
      pushToast("success", t("dictation.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function runPolishTest() {
    if (controlsBusy) return;
    const version = ++testVersion.current;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testDictationPolish();
      if (version === testVersion.current) setTestResult(result);
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
  // Default OFF, unlike the switch above, and the card says why: this one
  // relaxes a guard rather than only costing a formatting pass.
  const precisionOn = settings?.polish_precision ?? false;
  const conversationOn = settings?.polish_conversation ?? false;
  const polishProvider = settings?.polish_provider ?? "auto";
  const served = choices?.polish_provider ?? ["auto"];
  // A pin the served list does not contain would otherwise render as the first
  // option, and the dropdown would quietly claim a provider the config does not
  // say. Showing the stored value is the honest fallback.
  const polishProviders = served.includes(polishProvider)
    ? served
    : [...served, polishProvider];

  // Ships OFF, unlike the wording pass: this changes WHICH WORDS come out, not
  // just how they are written, so it is never on until someone asks for it.
  const translateOn = settings?.translate ?? false;
  // Ships OFF too, and for a stronger reason than translation: it rewrites the
  // dictation into a brief for a coding agent, so WHAT the text says changes
  // by design. Chosen, never inherited.
  const promptModeOn = settings?.prompt_mode ?? false;
  const wordingOn = polishOn || precisionOn;
  const processingOn = wordingOn || promptModeOn || translateOn;
  const translateTarget = settings?.translate_target ?? "en";
  // No "auto" in this list, and none is added here — there is nothing to detect
  // on the output side, so an auto entry would be a choice that does nothing.
  const translateTargets = choices?.translate_target ?? [];
  // Pinning the dictation language to the same language the output is pinned to
  // means nothing will ever be translated. Neither setting is wrong on its own,
  // so this is said rather than prevented: silently ignoring one of two switches
  // the user set is the failure mode worth avoiding.
  const targetEqualsSource = value !== "auto" && value === translateTarget;

  // The credential half of the translate card.
  //
  // Which family answers is the BACKEND's answer (`wordingProvider`), never a
  // re-derivation from the list above: the `auto` order depends on the keys
  // this host holds and on the privacy rule that pins an on-device recognizer
  // to on-device models, and a second implementation of it here would let the
  // card name one provider while the dictation used another (AP-4).
  //
  // What the card still needs from the provider catalog is the human half —
  // the dashboard link and the "which key is this" sentence — so those are
  // looked up by the spec id the backend handed over rather than copied.
  const wordingCard = wordingProvider?.spec_id
    ? (providers.find((entry) => entry.id === wordingProvider.spec_id) ?? null)
    : null;
  // Asked whenever the resolved provider still needs a key. Deliberately NOT
  // gated on `polish`: a translation runs with the formatter switched off, so
  // hiding the only place to fix it there would leave the switch reading as on
  // with the feature dead and nothing on screen explaining it (AP-31).
  const wordingNeedsKey = Boolean(
    wordingProvider && !wordingProvider.ready && wordingProvider.secret_key,
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Languages className="h-4 w-4 text-foreground" />}
          title={t("voice.language.title")}
          subtitle={t("voice.language.description")}
        />
      )}
      <div className="min-h-0 flex-1">
        <VoicePage testId="voice-language-tab">
          {error && (
            <VoiceNote tone="error" icon={<AlertTriangle />}>
              {error}
            </VoiceNote>
          )}

          <VoiceSection
            title={t("voice.language.section_title")}
            description={t("voice.language.section_description")}
          >
            <VoiceGroup>
              <VoiceRow
                title={t("voice.language.row_title")}
                description={
                  <span data-testid="dictation-language-hint">
                    {t("voice.language.row_hint")}
                  </span>
                }
                control={
                  loading ? (
                    <SkeletonBar className="h-9 w-56" />
                  ) : (
                    <LanguageSelect
                      value={value}
                      codes={languageCodes}
                      onChange={(code) => void onPick(code)}
                      autoLabel={t("voice.language.auto")}
                      ariaLabel={t("voice.language.row_title")}
                      className="w-56"
                      testId="dictation-language"
                    />
                  )
                }
              >
                {/* The recommendation only speaks once it applies: a pinned
                    language forces every utterance through that language,
                    which makes results worse on a model never trained for
                    it and turns a second-language sentence into nonsense. */}
                {value !== "auto" && !loading && (
                  <VoiceNote icon={<Info />} testId="dictation-language-pinned">
                    {t("voice.language.pinned_note")}
                  </VoiceNote>
                )}
              </VoiceRow>
            </VoiceGroup>
          </VoiceSection>

          <VoiceSection
            title={t("voice.writing.title")}
            description={t("voice.writing.description")}
            actions={
              <span aria-live="polite" className="inline-flex min-h-5 items-center">
                {saving && (
                  <VoiceTag>
                    <Loader2
                      aria-hidden="true"
                      className="h-3 w-3 animate-spin motion-reduce:animate-none"
                    />
                    {t("common.saving")}
                  </VoiceTag>
                )}
              </span>
            }
          >
            <VoiceGroup testId="dictation-writing-group">
              <SwitchRow
                testId="dictation-polish-card"
                title={t("voice.polish.title")}
                description={
                  <span data-testid="dictation-polish-description">
                    {t("voice.polish.short_description")}
                  </span>
                }
                checked={polishOn}
                disabled={controlsBusy}
                onCheckedChange={(next) => void onTogglePolish(next)}
                toggleTestId="dictation-polish-toggle"
              />

              {/* Its own row and never nested under the switch above.
                  Precision also governs a TRANSLATED dictation, which runs
                  with the formatter switched off — hiding it there would leave
                  it silently in force with no way to see or reach it (AP-31). */}
              <SwitchRow
                testId="dictation-precision-row"
                title={t("voice.polish.precision_title")}
                description={
                  <>
                    <span data-testid="dictation-precision-description">
                      {t("voice.polish.precision_short")}
                    </span>{" "}
                    {/* The trade, said out loud: this relaxes the check that
                        rejects an answer in which an uncommon word vanished. */}
                    <span data-testid="dictation-precision-tradeoff">
                      {t("voice.polish.precision_tradeoff_short")}
                    </span>
                  </>
                }
                checked={precisionOn}
                disabled={controlsBusy}
                onCheckedChange={(next) => void onTogglePrecision(next)}
                toggleTestId="dictation-precision-toggle"
              />

              {/* Shown only while a wording pass runs: it switches the same
                  pass on for a second source rather than being a pass of its
                  own. Visible while the formatter is off, it would be a
                  switch that saves, reads as on, and does nothing (AP-31). */}
              {wordingOn && (
                <SwitchRow
                  testId="dictation-conversation-row"
                  title={t("voice.polish.conversation_title")}
                  description={
                    <span data-testid="dictation-conversation-description">
                      {t("voice.polish.conversation_short")}
                    </span>
                  }
                  checked={conversationOn}
                  disabled={controlsBusy}
                  onCheckedChange={(next) => void onToggleConversation(next)}
                  toggleTestId="dictation-conversation-toggle"
                  nested
                />
              )}

              {/* Prompt Mode outranks the passes around it: while it is on, the
                  dictation is rewritten in the spoken language by the shared
                  provider, and neither pass has anything left to do. */}
              <SwitchRow
                testId="dictation-prompt-mode-card"
                title={t("voice.prompt_mode.title")}
                description={
                  <span data-testid="dictation-prompt-mode-description">
                    {t("voice.prompt_mode.short_description")}
                  </span>
                }
                checked={promptModeOn}
                disabled={controlsBusy}
                onCheckedChange={(next) => void onTogglePromptMode(next)}
                toggleTestId="dictation-prompt-mode-toggle"
              >
                {promptModeOn && (
                  <VoiceNote icon={<Info />} testId="dictation-prompt-mode-outranks">
                    {t("voice.prompt_mode.outranks_short")}{" "}
                    <span data-testid="dictation-prompt-mode-writer-hint">
                      {t("voice.prompt_mode.writer_short")}
                    </span>
                  </VoiceNote>
                )}
              </SwitchRow>

              {/* Translation IS the wording pass pointed at a different
                  language: one model call does both, and the shared provider
                  below is the one that answers. */}
              <SwitchRow
                testId="dictation-translate-card"
                title={t("voice.translate.title")}
                description={
                  <span data-testid="dictation-translate-description">
                    {t("voice.translate.short_description")}
                  </span>
                }
                checked={translateOn}
                disabled={controlsBusy}
                onCheckedChange={(next) => void onToggleTranslate(next)}
                toggleTestId="dictation-translate-toggle"
              >
                {translateOn && (
                  <>
                    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                      <span aria-hidden="true" className="text-sm text-muted-foreground">
                        {t("voice.translate.target_label")}
                      </span>
                      <LanguageSelect
                        value={translateTarget}
                        codes={translateTargets}
                        onChange={(code) => void onPickTranslateTarget(code)}
                        autoLabel={t("voice.language.auto")}
                        ariaLabel={t("voice.translate.target_label")}
                        disabled={controlsBusy}
                        className="w-56"
                        testId="dictation-translate-target"
                      />
                    </div>

                    {/* Which provider will really answer, and — when none
                        can — a degraded note pointing at the one place that
                        fixes it. */}
                    <div
                      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
                      data-testid="dictation-translate-provider"
                    >
                      <span className="text-muted-foreground">
                        {t("voice.translate.answers_label")}
                      </span>
                      {wordingProvider?.ready && (
                        <span
                          className="inline-flex items-center gap-1.5 font-medium text-foreground"
                          data-testid="dictation-translate-provider-name"
                        >
                          <StatusDot tone="ready" />
                          {POLISH_PROVIDER_LABELS[wordingProvider.family] ??
                            wordingProvider.label ??
                            wordingProvider.family}
                        </span>
                      )}
                    </div>
                    {!wordingProvider?.ready && (
                      <VoiceNote
                        tone="warning"
                        icon={<AlertTriangle />}
                        testId="dictation-translate-no-provider"
                      >
                        {t("voice.translate.no_provider_short")}
                      </VoiceNote>
                    )}

                    {targetEqualsSource && (
                      <VoiceNote icon={<Info />} testId="dictation-translate-same-language">
                        {t("voice.translate.same_language_notice")}
                      </VoiceNote>
                    )}
                  </>
                )}
              </SwitchRow>

              {/* The one provider every pass above shares, its key when it
                  still needs one, and the dry run — shown once, whenever any
                  pass is on, so there is never a second picker reading as a
                  second setting. */}
              {processingOn && (
                <VoiceRow
                  testId="dictation-text-model-row"
                  title={t("voice.polish.provider_row_title")}
                  description={<span id={providerHintId}>{t("voice.polish.provider_row_hint")}</span>}
                  control={
                    <Combobox
                      value={polishProvider}
                      ariaLabel={t("voice.polish.provider_label")}
                      ariaDescribedBy={providerHintId}
                      onChange={(id) => void onPickPolishProvider(id)}
                      testId="dictation-polish-provider"
                      disabled={controlsBusy}
                      className="w-56"
                      groups={[
                        {
                          id: "providers",
                          options: polishProviders.map((id) => ({
                            value: id,
                            label:
                              id === "auto"
                                ? t("voice.polish.provider_auto")
                                : (POLISH_PROVIDER_LABELS[id] ?? id),
                          })),
                        },
                      ]}
                    />
                  }
                >
                  {wordingNeedsKey && wordingProvider && (
                    <div className="space-y-2" data-testid="dictation-wording-key">
                      <ApiKeyForm
                        secretKey={wordingProvider.secret_key}
                        dashboardUrl={wordingCard?.dashboard_url ?? null}
                        configured={Boolean(
                          wordingCard?.secrets_set?.[wordingProvider.secret_key],
                        )}
                        credentialHelp={wordingCard?.credential_help ?? null}
                        sharedWith={
                          wordingCard?.secret_shared_with?.[wordingProvider.secret_key]
                        }
                        onChanged={() => {
                          void refetchProviders();
                          void refetch();
                        }}
                      />
                      <p className="text-xs text-muted-foreground">
                        {t("voice.translate.key_saved_hint")}
                      </p>
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void runPolishTest()}
                      disabled={controlsBusy}
                      aria-describedby={testHintId}
                      data-testid="dictation-polish-test"
                      className="gap-2"
                    >
                      {testing ? (
                        <Loader2
                          aria-hidden="true"
                          className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                        />
                      ) : (
                        <PlugZap aria-hidden="true" className="h-3.5 w-3.5" />
                      )}
                      {testing ? t("voice.polish.testing") : t("voice.polish.test")}
                    </Button>
                    <span id={testHintId} className="text-xs text-muted-foreground">
                      {t("voice.polish.test_hint_short")}
                    </span>
                  </div>

                  {/* The dry run's own result: what answered, and the same
                      sentence before and after it — reading the two side by
                      side IS the point of the button. */}
                  <div aria-live="polite">
                    {testResult && (
                      <div
                        className="space-y-3 rounded-lg bg-secondary px-3 py-2.5"
                        data-testid="dictation-polish-test-result"
                      >
                        <p className="text-xs text-muted-foreground">
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
                          <p className="text-xs text-muted-foreground">
                            {t("voice.polish.sample_before")}
                          </p>
                          <p
                            className="mt-0.5 break-words text-sm text-muted-foreground"
                            data-testid="dictation-polish-sample-in"
                          >
                            {testResult.sample_in}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">
                            {t("voice.polish.sample_after")}
                          </p>
                          <p
                            className="mt-0.5 break-words text-sm text-foreground"
                            data-testid="dictation-polish-sample-out"
                          >
                            {testResult.sample_out}
                          </p>
                        </div>
                      </div>
                    )}
                  </div>
                </VoiceRow>
              )}
            </VoiceGroup>

            {/* Where the text GOES, for every switch above. Worded as the
                normal cloud feature it is — no banner, no warning colour — and
                shown whether the switches are on or off, because someone
                deciding to turn one ON is exactly who needs to read it. */}
            <p
              className="px-1 text-xs text-muted-foreground"
              data-testid="dictation-polish-sends-text"
            >
              {t("voice.polish.sends_text")}
            </p>
          </VoiceSection>
        </VoicePage>
      </div>
    </div>
  );
}

/**
 * One switch of the Writing group: name and one sentence on the left, the
 * switch on the right, and whatever depends on it nested underneath. The
 * switch is named by the row title and described by its sentence.
 */
function SwitchRow({
  testId,
  title,
  description,
  checked,
  disabled,
  onCheckedChange,
  toggleTestId,
  nested = false,
  children,
}: {
  testId: string;
  title: string;
  description: ReactNode;
  checked: boolean;
  disabled: boolean;
  onCheckedChange: (next: boolean) => void;
  toggleTestId: string;
  /** A row that only exists because the one above it is on: inset under it. */
  nested?: boolean;
  children?: ReactNode;
}) {
  const descriptionId = useId();
  return (
    <VoiceRow
      testId={testId}
      title={title}
      description={<span id={descriptionId}>{description}</span>}
      className={nested ? "sm:pl-10" : undefined}
      control={
        <Switch
          checked={checked}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
          aria-label={title}
          aria-describedby={descriptionId}
          data-testid={toggleTestId}
        />
      }
    >
      {children}
    </VoiceRow>
  );
}

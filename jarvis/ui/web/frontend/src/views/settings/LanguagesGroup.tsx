import { useEffect } from "react";
import {
  useT,
  useUiLanguage,
  useReplyLanguage,
  useSttLanguage,
  useSttLanguageOptions,
  setUiLanguage,
  setReplyLanguage,
  setSttLanguage,
  hydrateReplyLanguage,
  hydrateUiLanguage,
  hydrateSttLanguage,
  type UiLanguage,
  type ReplyLanguage,
} from "@/i18n";
import { LanguageSelect } from "@/components/ui/language-select";
import {
  SettingsCard,
  SettingsRow,
  SettingsSection,
  SettingsSelect,
  settingsSelectCls,
} from "@/views/settings/SettingsLayout";

const UI_OPTIONS: UiLanguage[] = ["en", "de", "es", "zh"];
const REPLY_OPTIONS: ReplyLanguage[] = ["auto", "en", "de", "es"];

/**
 * "Languages" section of the General settings page: interface language,
 * voice-recognition language and reply language, one row each with its
 * dropdown on the right (i18n keys ``languages_view.*``).
 */
export function LanguagesGroup() {
  const t = useT();
  const ui = useUiLanguage();
  const reply = useReplyLanguage();
  const stt = useSttLanguage();
  // The accepted set comes from the backend (hydrated below), never a copy kept
  // here — a second list would drift the first time a language is added (AP-4).
  const sttCodes = useSttLanguageOptions();
  // Whatever is persisted must be selectable even before the list arrives,
  // otherwise the picker would silently show something the config never said.
  const sttChoices = sttCodes.includes(stt) ? sttCodes : [...sttCodes, stt];

  // Reflect the backend's persisted languages on open (all are backend-backed
  // now, so a voice/Control-API change is shown and the choice survives restart).
  useEffect(() => {
    void hydrateReplyLanguage();
    void hydrateUiLanguage();
    void hydrateSttLanguage();
  }, []);

  return (
    <SettingsSection title={t("settings_view.languages_group_title")}>
      <SettingsCard>
        <SettingsRow
          title={t("languages_view.ui_section")}
          description={t("languages_view.ui_hint")}
          control={
            <SettingsSelect
              value={ui}
              onValueChange={(code) => setUiLanguage(code as UiLanguage)}
              ariaLabel={t("languages_view.ui_section")}
              testId="ui-language"
              options={UI_OPTIONS.map((code) => ({
                value: code,
                label: t(`languages_view.options.${code}.label`),
              }))}
            />
          }
        />
        {/*
          A searchable list: the recogniser understands ~100 languages, while
          the interface and reply languages have a handful each.
        */}
        <SettingsRow
          title={t("languages_view.stt_section")}
          description={t("languages_view.stt_hint")}
          control={
            <LanguageSelect
              value={stt}
              codes={sttChoices}
              onChange={(code) => setSttLanguage(code)}
              autoLabel={t("languages_view.options.auto.label")}
              ariaLabel={t("languages_view.stt_section")}
              testId="stt-language"
              className={settingsSelectCls}
            />
          }
        />
        <SettingsRow
          title={t("languages_view.reply_section")}
          description={t("languages_view.reply_hint")}
          control={
            <SettingsSelect
              value={reply}
              onValueChange={(code) => setReplyLanguage(code as ReplyLanguage)}
              ariaLabel={t("languages_view.reply_section")}
              testId="reply-language"
              options={REPLY_OPTIONS.map((code) => ({
                value: code,
                label: t(`languages_view.options.${code}.label`),
                description: t(`languages_view.reply_options.${code}`),
              }))}
            />
          }
        />
      </SettingsCard>
    </SettingsSection>
  );
}

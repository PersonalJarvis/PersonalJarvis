import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import {
  useT,
  useUiLanguage,
  setUiLanguage,
  useReplyLanguage,
  setReplyLanguage,
  type UiLanguage,
  type ReplyLanguage,
} from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function LanguageStep({ goNext }: StepProps) {
  const t = useT();
  const ui = useUiLanguage();
  const reply = useReplyLanguage();
  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.language.title")}</h2>
      <label className="text-sm">
        {t("onboarding.language.ui_label")}
        <BrandedSelect
          ariaLabel={t("onboarding.language.ui_label")}
          value={ui}
          onValueChange={(value) => setUiLanguage(value as UiLanguage)}
          options={[
            { value: "en", label: "English" },
            { value: "de", label: "Deutsch" },
            { value: "es", label: "Español" },
          ]}
          className="mt-1"
        />
      </label>
      <label className="text-sm">
        {t("onboarding.language.reply_label")}
        <BrandedSelect
          ariaLabel={t("onboarding.language.reply_label")}
          value={reply}
          onValueChange={(value) => setReplyLanguage(value as ReplyLanguage)}
          options={[
            { value: "auto", label: "Auto" },
            { value: "en", label: "English" },
            { value: "de", label: "Deutsch" },
            { value: "es", label: "Español" },
          ]}
          className="mt-1"
        />
      </label>
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
    </div>
  );
}

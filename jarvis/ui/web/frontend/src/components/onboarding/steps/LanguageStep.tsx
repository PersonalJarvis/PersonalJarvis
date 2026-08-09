import { Button } from "@/components/ui/button";
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
        <select
          aria-label={t("onboarding.language.ui_label")}
          value={ui}
          onChange={(e) => setUiLanguage(e.target.value as UiLanguage)}
          className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="en">English</option>
          <option value="de">Deutsch</option>
          <option value="es">Español</option>
        </select>
      </label>
      <label className="text-sm">
        {t("onboarding.language.reply_label")}
        <select
          aria-label={t("onboarding.language.reply_label")}
          value={reply}
          onChange={(e) => setReplyLanguage(e.target.value as ReplyLanguage)}
          className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="auto">Auto</option>
          <option value="en">English</option>
          <option value="de">Deutsch</option>
          <option value="es">Español</option>
        </select>
      </label>
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
    </div>
  );
}

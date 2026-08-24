import { useEffect } from "react";
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
import { StepFooter } from "../primitives";

const LANGUAGE_LABELS: Record<string, string> = {
  en: "English",
  de: "Deutsch",
  es: "Español",
};

/**
 * Two settings as two rows of a definition list: label and hint on the left,
 * the control on the right, a rule between them. The register summary reads
 * "Deutsch · replies Auto" as soon as either changes.
 */
export function LanguageStep({ goNext, goBack, setSummary }: StepProps) {
  const t = useT();
  const ui = useUiLanguage();
  const reply = useReplyLanguage();

  useEffect(() => {
    const replyLabel = reply === "auto" ? "Auto" : (LANGUAGE_LABELS[reply] ?? reply);
    setSummary(
      t("onboarding.language.summary")
        .replace("{0}", LANGUAGE_LABELS[ui] ?? ui)
        .replace("{1}", replyLabel),
    );
  }, [ui, reply, setSummary, t]);

  const rows = [
    {
      key: "ui",
      label: t("onboarding.language.ui_label"),
      hint: t("onboarding.language.ui_hint"),
      control: (
        <BrandedSelect
          ariaLabel={t("onboarding.language.ui_label")}
          value={ui}
          onValueChange={(value) => setUiLanguage(value as UiLanguage)}
          options={[
            { value: "en", label: "English" },
            { value: "de", label: "Deutsch" },
            { value: "es", label: "Español" },
          ]}
        />
      ),
    },
    {
      key: "reply",
      label: t("onboarding.language.reply_label"),
      hint: t("onboarding.language.reply_hint"),
      control: (
        <BrandedSelect
          ariaLabel={t("onboarding.language.reply_label")}
          value={reply}
          onValueChange={(value) => setReplyLanguage(value as ReplyLanguage)}
          options={[
            { value: "auto", label: t("onboarding.language.auto") },
            { value: "en", label: "English" },
            { value: "de", label: "Deutsch" },
            { value: "es", label: "Español" },
          ]}
        />
      ),
    },
  ];

  return (
    <div>
      <dl className="border-y border-border/70">
        {rows.map((row) => (
          <div
            key={row.key}
            className="grid gap-3 border-b border-border/50 py-4 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_240px] sm:items-center"
          >
            <dt>
              <span className="block text-sm font-medium text-foreground">{row.label}</span>
              <span className="mt-0.5 block text-[13px] leading-relaxed text-muted-foreground">
                {row.hint}
              </span>
            </dt>
            <dd className="min-w-0">{row.control}</dd>
          </div>
        ))}
      </dl>

      <StepFooter
        onBack={goBack}
        primary={{ label: t("onboarding.nav.next"), onClick: goNext }}
      />
    </div>
  );
}

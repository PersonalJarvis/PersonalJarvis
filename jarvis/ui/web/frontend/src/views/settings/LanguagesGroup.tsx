import { useEffect } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
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

const UI_OPTIONS: UiLanguage[] = ["en", "de", "es"];
const REPLY_OPTIONS: ReplyLanguage[] = ["auto", "en", "de", "es"];

/**
 * "Languages" group inside the Settings view — the interface-language and
 * reply-language selectors. Moved here from the former standalone Languages
 * section; the controls, i18n hooks, and i18n keys (``languages_view.*``) are
 * unchanged. The page-level ViewHeader is dropped because this group sits under
 * the Settings header, as the first panel of the view.
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
    <div className="space-y-5">
      <h3 className="text-lg font-semibold text-foreground-strong">
        {t("settings_view.languages_group_title")}
      </h3>

      <Section
        title={t("languages_view.ui_section")}
        hint={t("languages_view.ui_hint")}
      >
        {UI_OPTIONS.map((code) => (
          <LanguageRow
            key={`ui-${code}`}
            active={ui === code}
            label={t(`languages_view.options.${code}.label`)}
            description={t(`languages_view.options.${code}.description`)}
            onClick={() => setUiLanguage(code)}
          />
        ))}
      </Section>

      {/*
        A dropdown, not the row buttons the other two sections use: the
        recogniser understands ~100 languages, and rendering one card per
        language would bury the rest of the settings view. The reply and
        interface languages stay as rows — they have three options each.
      */}
      <Section
        title={t("languages_view.stt_section")}
        hint={t("languages_view.stt_hint")}
      >
        <li>
          <div className="max-w-xs">
            <LanguageSelect
              value={stt}
              codes={sttChoices}
              onChange={(code) => setSttLanguage(code)}
              autoLabel={t("languages_view.options.auto.label")}
              ariaLabel={t("languages_view.stt_section")}
              testId="stt-language"
            />
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            {t("languages_view.stt_options.auto")}
          </p>
        </li>
      </Section>

      <Section
        title={t("languages_view.reply_section")}
        hint={t("languages_view.reply_hint")}
      >
        {REPLY_OPTIONS.map((code) => (
          <LanguageRow
            key={`reply-${code}`}
            active={reply === code}
            label={t(`languages_view.options.${code}.label`)}
            description={t(`languages_view.reply_options.${code}`)}
            onClick={() => setReplyLanguage(code)}
          />
        ))}
      </Section>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="border-b border-border px-5 py-4">
        <div className="text-base font-medium text-foreground-strong">{title}</div>
        <div className="mt-1 text-sm text-muted-foreground">{hint}</div>
      </div>
      <ul className="divide-y divide-border px-2 py-1">{children}</ul>
    </div>
  );
}

function LanguageRow({
  active,
  label,
  description,
  onClick,
}: {
  active: boolean;
  label: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        aria-pressed={active}
        className={cn(
          "my-1 flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active ? "bg-secondary" : "hover:bg-secondary",
        )}
      >
        <div className="flex-1">
          <div className="text-base font-medium text-foreground">{label}</div>
          <div className="text-sm text-muted-foreground">{description}</div>
        </div>
        {active && <Check className="h-4 w-4 shrink-0 text-accent" />}
      </button>
    </li>
  );
}

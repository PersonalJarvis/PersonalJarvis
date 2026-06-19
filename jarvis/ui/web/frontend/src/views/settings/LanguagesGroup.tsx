import { useEffect } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useT,
  useUiLanguage,
  useReplyLanguage,
  setUiLanguage,
  setReplyLanguage,
  hydrateReplyLanguage,
  hydrateUiLanguage,
  type UiLanguage,
  type ReplyLanguage,
} from "@/i18n";

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

  // Reflect the backend's persisted languages on open (both are backend-backed
  // now, so a voice/Control-API change is shown and the choice survives restart).
  useEffect(() => {
    void hydrateReplyLanguage();
    void hydrateUiLanguage();
  }, []);

  return (
    <div className="mb-8 space-y-4">
      <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
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
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      <div className="mb-3 text-xs text-muted-foreground">{hint}</div>
      <ul className="space-y-2">{children}</ul>
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
          "flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left text-sm transition-colors",
          active
            ? "border-primary/40 bg-primary/5 shadow-[0_0_0_1px_hsl(var(--primary)/0.15)]"
            : "border-border bg-card/60 hover:border-primary/30 hover:bg-card/80",
        )}
      >
        <div className="flex-1">
          <div className="font-medium">{label}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">{description}</div>
        </div>
        {active && <Check className="h-4 w-4 shrink-0 text-primary" />}
      </button>
    </li>
  );
}

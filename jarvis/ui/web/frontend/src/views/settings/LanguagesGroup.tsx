import { useEffect } from "react";
import { Check, Mic2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useT,
  useUiLanguage,
  useReplyLanguage,
  setUiLanguage,
  setReplyLanguage,
  hydrateReplyLanguage,
  type UiLanguage,
  type ReplyLanguage,
} from "@/i18n";

const UI_OPTIONS: UiLanguage[] = ["en", "de", "es"];
const REPLY_OPTIONS: ReplyLanguage[] = ["auto", "en", "de", "es"];

/**
 * "Languages" group inside the Settings view — the interface-language and
 * reply-language selectors, plus the voice-recognition note and the per-session
 * override note. Moved here from the former standalone Languages section; the
 * controls, i18n hooks, and i18n keys (``languages_view.*``) are unchanged. The
 * page-level ViewHeader is dropped because this group sits under the Settings
 * header, as the first panel of the view.
 */
export function LanguagesGroup() {
  const t = useT();
  const ui = useUiLanguage();
  const reply = useReplyLanguage();

  // Reflect the backend's persisted reply language on open (survives restart).
  useEffect(() => {
    void hydrateReplyLanguage();
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

      <div className="flex items-start gap-3 rounded-lg border border-border bg-card/40 p-4 text-xs text-muted-foreground">
        <Mic2 className="mt-0.5 h-4 w-4 shrink-0 text-primary/70" />
        <div>
          <strong className="text-foreground">
            {t("languages_view.recognition_title")}
          </strong>
          <div className="mt-0.5">{t("languages_view.recognition_text")}</div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card/60 p-4 text-xs text-muted-foreground">
        <strong className="text-foreground">
          {t("languages_view.override_title")}:
        </strong>{" "}
        {t("languages_view.override_text")}
      </div>
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

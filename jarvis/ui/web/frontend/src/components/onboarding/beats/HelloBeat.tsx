import { AlertTriangle, Cloud, CreditCard, Monitor, Terminal } from "lucide-react";
import { useState } from "react";
import { useT } from "@/i18n";
import type { BeatProps } from "../WelcomeFlow";
import { CheckLine, PrimaryAction, QuietAction, Rise, Status } from "../ui";

/**
 * The welcome beat is the consent moment — the ONLY one: the installer asks
 * nothing. It says in five short lines what the assistant does on this
 * machine, then one checkbox and one button.
 *
 * Acceptance is awaited: the Terms record must exist before the guide goes
 * on. Declining is real and quits the app (the backend ends the process
 * moments after answering); nothing is saved, so the next start asks again.
 */
export function HelloBeat({ onb, next, cheer }: BeatProps) {
  const t = useT();
  const [accepted, setAccepted] = useState(Boolean(onb.state?.terms.accepted));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [declined, setDeclined] = useState(false);
  const [terms, setTerms] = useState<string | null>(null);
  const [showTerms, setShowTerms] = useState(false);

  const toggleTerms = async () => {
    setShowTerms((v) => !v);
    if (terms !== null) return;
    try {
      const res = await fetch("/api/onboarding/terms");
      setTerms(res.ok ? ((await res.json()) as { text: string }).text : "");
    } catch {
      setTerms("");
    }
  };

  const proceed = async () => {
    if (!accepted || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onb.acceptTerms();
      cheer("jump");
      next();
    } catch {
      setError(t("first_run.welcome.accept_failed"));
    } finally {
      setBusy(false);
    }
  };

  const decline = async () => {
    // The goodbye shows first: the backend ends the process right after it
    // answers, so this is the last thing the window paints.
    setDeclined(true);
    try {
      await fetch("/api/onboarding/decline-terms", { method: "POST" });
    } catch {
      // A warming backend cannot hold the goodbye back; the user closes the window either way.
    }
  };

  if (declined) {
    return (
      <div className="space-y-2 text-center" data-testid="onboarding-declined">
        <p className="text-base font-medium text-foreground">{t("first_run.welcome.declined_title")}</p>
        <p className="text-sm leading-relaxed text-muted-foreground">{t("first_run.welcome.declined_body")}</p>
      </div>
    );
  }

  const facts = [
    { key: "commands", Icon: Terminal },
    { key: "screen", Icon: Monitor },
    { key: "cloud", Icon: Cloud },
    { key: "costs", Icon: CreditCard },
    { key: "mistakes", Icon: AlertTriangle },
  ] as const;

  return (
    <div className="space-y-5">
      <Rise index={0}>
        <p className="mb-2 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {t("first_run.welcome.facts_label")}
        </p>
        <ul className="space-y-1.5 rounded-xl border border-border bg-background px-4 py-3" data-testid="onboarding-facts">
          {facts.map(({ key, Icon }) => (
            <li key={key} className="flex items-start gap-3 text-sm leading-snug text-foreground">
              <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <span>{t(`first_run.welcome.fact_${key}`)}</span>
            </li>
          ))}
        </ul>
      </Rise>

      <Rise index={1} className="space-y-2">
        <CheckLine checked={accepted} onChange={setAccepted} testId="onboarding-accept">
          {t("first_run.welcome.accept")}
        </CheckLine>
        <QuietAction onClick={() => void toggleTerms()} className="ml-7 underline underline-offset-4">
          {showTerms ? t("first_run.welcome.hide_terms") : t("first_run.welcome.read_terms")}
        </QuietAction>
        {showTerms && (
          <pre className="ml-7 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border bg-background p-3 font-sans text-xs leading-relaxed text-muted-foreground scrollbar-jarvis">
            {terms || t("first_run.welcome.terms_loading")}
          </pre>
        )}
      </Rise>

      {error && <Status tone="error">{error}</Status>}

      <Rise index={2} className="space-y-3 pt-1">
        <PrimaryAction onClick={() => void proceed()} disabled={!accepted} busy={busy}>
          {t("first_run.welcome.start")}
        </PrimaryAction>
        <div className="text-center">
          <QuietAction onClick={() => void decline()} testId="onboarding-decline">
            {t("first_run.welcome.decline")}
          </QuietAction>
        </div>
      </Rise>
    </div>
  );
}

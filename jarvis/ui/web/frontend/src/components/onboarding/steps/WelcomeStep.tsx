import { useEffect, useState } from "react";
import { AlertTriangle, Cloud, CreditCard, Monitor, Terminal } from "lucide-react";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";
import { IntroSequence } from "../IntroSequence";
import { ConsentLine, Register, StatusLine, StepFooter, StepSection } from "../primitives";

/**
 * The first step is the consent moment. It replaced a separate "I understand
 * the risks" card (a checkbox and a warning emoji) with the plain list of
 * what the assistant actually does on this machine, then the one line of
 * acceptance the legal posture needs.
 *
 * Acceptance is AWAITED: the Terms record must exist before the guide goes
 * on. A warming backend answers this route from the fast-boot path, so the
 * wait is normally invisible; a real failure shows in place and the step
 * stays. Declining is a real, equal-weight choice: it asks the backend to
 * quit the whole app and renders a terminal goodbye state (the desktop
 * window disappears with the process). Nothing is persisted on decline —
 * the next start shows this step again.
 */
export function WelcomeStep({ onb, goNext, setSummary }: StepProps) {
  const t = useT();
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [declined, setDeclined] = useState(false);
  const [terms, setTerms] = useState<string | null>(null);
  const [showTerms, setShowTerms] = useState(false);

  useEffect(() => {
    setSummary(accepted ? t("onboarding.welcome.summary_accepted") : null);
  }, [accepted, setSummary, t]);

  const toggleTerms = async () => {
    setShowTerms((v) => !v);
    if (terms === null) {
      try {
        const res = await fetch("/api/onboarding/terms");
        setTerms(res.ok ? ((await res.json()) as { text: string }).text : "");
      } catch {
        setTerms("");
      }
    }
  };

  const proceed = async () => {
    if (!accepted || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onb.acceptTerms();
      goNext();
    } catch {
      setError(t("onboarding.welcome.accept_failed"));
    } finally {
      setBusy(false);
    }
  };

  const decline = async () => {
    // Show the goodbye state first — the backend kills the process moments
    // after answering, so this screen is the last thing the browser renders.
    setDeclined(true);
    try {
      await fetch("/api/onboarding/decline-terms", { method: "POST" });
    } catch {
      // Best-effort: a warming/erroring backend cannot block the goodbye
      // screen; the user closes the window either way.
    }
  };

  if (declined) {
    return (
      <div className="max-w-xl space-y-3 pt-6" data-testid="onboarding-declined">
        <h3 className="text-lg font-semibold tracking-tight">
          {t("onboarding.welcome.declined_title")}
        </h3>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t("onboarding.welcome.declined_body")}
        </p>
      </div>
    );
  }

  const capabilities = [
    { key: "commands", icon: <Terminal className="h-4 w-4" />, text: t("onboarding.welcome.cap_commands") },
    { key: "screen", icon: <Monitor className="h-4 w-4" />, text: t("onboarding.welcome.cap_screen") },
    { key: "cloud", icon: <Cloud className="h-4 w-4" />, text: t("onboarding.welcome.cap_cloud") },
    { key: "costs", icon: <CreditCard className="h-4 w-4" />, text: t("onboarding.welcome.cap_costs") },
    { key: "mistakes", icon: <AlertTriangle className="h-4 w-4" />, text: t("onboarding.welcome.cap_mistakes") },
  ];

  return (
    <div className="space-y-8">
      <IntroSequence />

      <StepSection label={t("onboarding.welcome.capabilities_label")}>
        <Register
          items={capabilities.map((c) => ({ key: c.key, icon: c.icon, children: c.text }))}
        />
      </StepSection>

      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t("onboarding.welcome.liability")}
        </p>
        <button
          type="button"
          className="text-[13px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
          onClick={() => void toggleTerms()}
        >
          {showTerms ? t("onboarding.welcome.hide_terms") : t("onboarding.welcome.view_terms")}
        </button>
        {showTerms && (
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-border py-1 pl-3 font-sans text-xs leading-relaxed text-muted-foreground scrollbar-jarvis">
            {terms ?? t("onboarding.welcome.terms_loading")}
          </pre>
        )}
      </div>

      <ConsentLine checked={accepted} onChange={setAccepted} testId="onboarding-accept">
        {t("onboarding.welcome.accept_label")}
      </ConsentLine>

      {error && <StatusLine tone="error">{error}</StatusLine>}

      <StepFooter
        primary={{
          label: t("onboarding.welcome.proceed"),
          onClick: () => void proceed(),
          disabled: !accepted,
          busy,
        }}
        secondary={{
          label: t("onboarding.welcome.decline"),
          onClick: () => void decline(),
          testId: "onboarding-decline",
        }}
      />
    </div>
  );
}

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

/**
 * A blocking "use at your own risk" acknowledgement shown once, before the
 * onboarding flow itself (mirrors the trust prompt CLIs like Claude Code show on
 * first run). Frontend-only: it gates the flow via local state and never mutates
 * onboarding/completed state, so it cannot reintroduce the "onboarding reappears
 * every restart" bug. The user must tick the checkbox before continuing.
 */
export function RiskGate({ onAccept }: { onAccept: () => void }) {
  const t = useT();
  const [accepted, setAccepted] = useState(false);
  // Full Terms text, fetched lazily on first expand — proceeding through this
  // gate records the acceptance (see OnboardingGate), so the text must be
  // reachable right here.
  const [terms, setTerms] = useState<string | null>(null);
  const [showTerms, setShowTerms] = useState(false);

  const toggleTerms = async () => {
    setShowTerms((v) => !v);
    if (terms === null) {
      try {
        const res = await fetch("/api/onboarding/terms");
        if (res.ok) setTerms(((await res.json()) as { text: string }).text);
        else setTerms("");
      } catch {
        setTerms("");
      }
    }
  };

  return (
    <div className="flex w-full max-w-lg flex-col gap-5 rounded-2xl border border-primary/40 bg-card p-8 shadow-2xl">
      <div className="flex items-center gap-3">
        <span className="text-2xl" aria-hidden>
          ⚠️
        </span>
        <h1 className="font-display text-xl font-semibold">{t("onboarding.risk.title")}</h1>
      </div>
      <p className="text-sm font-semibold text-foreground">{t("onboarding.risk.lead")}</p>
      <p className="text-sm leading-relaxed text-muted-foreground">{t("onboarding.risk.body")}</p>
      <p className="text-sm leading-relaxed text-muted-foreground">{t("onboarding.risk.liability")}</p>
      <button
        type="button"
        className="self-start text-xs text-muted-foreground underline"
        onClick={() => void toggleTerms()}
      >
        {t("onboarding.risk.view_terms")}
      </button>
      {showTerms && (
        <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
          {terms ?? t("onboarding.risk.terms_loading")}
        </pre>
      )}
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-1 shrink-0"
          checked={accepted}
          onChange={(e) => setAccepted(e.target.checked)}
        />
        <span>{t("onboarding.risk.accept_label")}</span>
      </label>
      <Button className="w-full" disabled={!accepted} onClick={() => accepted && onAccept()}>
        {t("onboarding.risk.proceed")}
      </Button>
    </div>
  );
}

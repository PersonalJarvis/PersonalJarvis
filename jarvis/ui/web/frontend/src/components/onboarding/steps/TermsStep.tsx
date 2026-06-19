import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function TermsStep({ onb, goNext }: StepProps) {
  const t = useT();
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [body, setBody] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/onboarding/terms")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { text?: string }) => {
        if (!cancelled) setBody(d.text ?? "");
      })
      .catch(() => {
        if (!cancelled) setBody("");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onContinue() {
    if (!accepted || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await onb.acceptTerms();
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.terms.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.terms.intro")}</p>
      <div className="max-h-56 min-h-0 overflow-y-auto scrollbar-jarvis whitespace-pre-line rounded-md border border-border bg-background p-3 text-xs text-muted-foreground">
        {body}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
        {t("onboarding.terms.accept_label")}
      </label>
      {err && <p className="text-xs text-amber-500">{err}</p>}
      <Button className="w-full" disabled={!accepted || busy} onClick={onContinue}>
        {t("onboarding.terms.continue")}
      </Button>
    </div>
  );
}

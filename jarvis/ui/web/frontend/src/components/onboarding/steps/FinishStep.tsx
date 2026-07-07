import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

interface AutostartState {
  enabled: boolean;
  supported: boolean;
}

export function FinishStep({ onb, goNext }: StepProps) {
  const t = useT();
  const skipped = onb.state?.skipped_steps ?? [];

  // "Start Jarvis at login" toggle (formerly a terminal-wizard question).
  // Capability-gated: hidden on hosts where autostart is unsupported
  // (headless Linux) or when the probe fails — Settings stays the recovery
  // path either way.
  const [autostart, setAutostart] = useState<AutostartState | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch("/api/settings/autostart");
        if (res.ok) setAutostart((await res.json()) as AutostartState);
      } catch {
        // capability probe is best-effort — hide the toggle on failure
      }
    })();
  }, []);

  const toggleAutostart = async (enabled: boolean) => {
    setAutostart((s) => (s ? { ...s, enabled } : s));
    try {
      await fetch("/api/settings/autostart", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
    } catch {
      // keep the optimistic value; the Settings view remains the recovery path
    }
  };

  return (
    <div className="flex flex-col gap-4 text-center">
      <h2 className="font-display text-xl font-semibold">{t("onboarding.finish.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.finish.body")}</p>
      {skipped.length > 0 && (
        <div className="text-xs text-muted-foreground">
          <div className="font-medium">{t("onboarding.finish.skipped_title")}</div>
          <ul className="mt-1">
            {skipped.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {autostart?.supported && (
        <label className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-left text-sm">
          <span>{t("onboarding.finish.autostart_label")}</span>
          <input
            type="checkbox"
            checked={autostart.enabled}
            onChange={(e) => void toggleAutostart(e.target.checked)}
          />
        </label>
      )}
      <div className="flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-left text-xs text-muted-foreground">
        <span aria-hidden className="mt-px text-primary">⏱</span>
        <span>{t("onboarding.finish.boot_notice")}</span>
      </div>
      <Button className="w-full" onClick={goNext}>{t("onboarding.finish.start_cta")}</Button>
    </div>
  );
}

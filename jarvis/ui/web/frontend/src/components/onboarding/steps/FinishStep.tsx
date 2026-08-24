import { useEffect, useState } from "react";
import { ExternalLink, Play } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";
import { StatusLine, StepFooter, StepSection } from "../primitives";

interface AutostartState {
  enabled: boolean;
  supported: boolean;
}

// The public onboarding walkthrough on YouTube. It used to be a full screen
// of its own between the risk gate and the wizard; now it is one line on the
// last step that opens in the user's browser, so nobody is asked to sit
// through a video before they may type a key.
const TOUR_URL = "https://www.youtube.com/watch?v=FXz1HclXL1g";

/** The steps whose summary the review reads back, in register order. */
const REVIEW_STEPS = ["language", "api-keys", "permissions", "wake-word"] as const;

/**
 * The review: what was set up, as a definition list of the register's own
 * summaries, plus the autostart switch and the one gold action that starts
 * the assistant. Completion is awaited — the backend restarts the app once
 * the marker is written — and a failure shows in place instead of leaving
 * the button dead.
 */
export function FinishStep({ onb, goBack, summaries }: StepProps) {
  const t = useT();
  const skipped = new Set(onb.state?.skipped_steps ?? []);
  const steps = onb.state?.steps ?? [];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // "Start at login" toggle (formerly a terminal-wizard question).
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

  const start = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // Awaited through the hook (which dispatches jarvis:onboarding-changed
      // on success) so a failed POST is visible here instead of a dead button.
      await onb.complete();
    } catch {
      setError(t("onboarding.finish.start_failed"));
      setBusy(false);
    }
  };

  const review = REVIEW_STEPS.filter((key) => steps.includes(key)).map((key) => ({
    key,
    label: t(`onboarding.steps.${key}.label`),
    value: summaries[key] ?? (skipped.has(key) ? t("onboarding.finish.skipped") : "—"),
    muted: !summaries[key],
  }));

  return (
    <div className="space-y-8">
      <StepSection label={t("onboarding.finish.summary_label")}>
        <dl className="border-y border-border/70" data-testid="onboarding-review">
          {review.map((row) => (
            <div
              key={row.key}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-4 border-b border-border/50 py-3 text-sm last:border-b-0"
            >
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd
                className={
                  row.muted
                    ? "text-right text-[13px] text-muted-foreground/80"
                    : "text-right font-medium text-foreground"
                }
              >
                {row.value}
              </dd>
            </div>
          ))}
          {autostart?.supported && (
            <div className="flex items-center justify-between gap-4 border-b border-border/50 py-3 last:border-b-0">
              <div className="min-w-0">
                <span className="block text-sm font-medium text-foreground">
                  {t("onboarding.finish.autostart_label")}
                </span>
                <span className="mt-0.5 block text-[13px] text-muted-foreground">
                  {t("onboarding.finish.autostart_hint")}
                </span>
              </div>
              <Switch
                checked={autostart.enabled}
                onCheckedChange={(v) => void toggleAutostart(v)}
                aria-label={t("onboarding.finish.autostart_label")}
                data-testid="onboarding-autostart"
              />
            </div>
          )}
        </dl>
      </StepSection>

      <StepSection label={t("onboarding.finish.tour_label")}>
        <a
          href={TOUR_URL}
          target="_blank"
          rel="noreferrer"
          className="group flex items-center gap-3 border-y border-border/70 py-3 text-sm"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control border border-border bg-card/60 text-foreground transition-colors group-hover:border-primary/60 group-hover:text-primary">
            <Play className="h-3.5 w-3.5 translate-x-px fill-current" />
          </span>
          <span className="min-w-0">
            <span className="block font-medium text-foreground">
              {t("onboarding.finish.tour_title")}
            </span>
            <span className="block text-[13px] text-muted-foreground">
              {t("onboarding.finish.tour_body")}
            </span>
          </span>
          <ExternalLink className="ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        </a>
      </StepSection>

      <p className="text-[13px] leading-relaxed text-muted-foreground">
        {t("onboarding.finish.boot_notice")}
      </p>

      {error && <StatusLine tone="error">{error}</StatusLine>}

      <StepFooter
        onBack={goBack}
        primary={{
          label: busy ? t("onboarding.finish.starting") : t("onboarding.finish.start_cta"),
          onClick: () => void start(),
          busy,
          testId: "onboarding-start",
        }}
        hidePrimaryArrow
      />
    </div>
  );
}

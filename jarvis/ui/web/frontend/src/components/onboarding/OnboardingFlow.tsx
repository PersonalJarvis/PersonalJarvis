import { useCallback, useEffect, useMemo, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { WelcomeStep } from "./steps/WelcomeStep";
import { LanguageStep } from "./steps/LanguageStep";
import { PermissionsStep } from "./steps/PermissionsStep";
import { WakeWordStep } from "./steps/WakeWordStep";
import { ApiKeysStep } from "./steps/ApiKeysStep";
import { FinishStep } from "./steps/FinishStep";

export interface StepProps {
  onb: ReturnType<typeof useOnboarding>;
  goNext: () => void;
  goBack: () => void;
  skip: () => void;
  isFirst: boolean;
  isLast: boolean;
  /**
   * The one line the register shows under this step's name once it is done —
   * "Deutsch · replies Auto", "OpenRouter", "Hey Nova". Steps report it as
   * soon as they know it; the register is the progress indicator.
   */
  setSummary: (text: string | null) => void;
  /** Every step's current summary, keyed by step id (the finish step reads it). */
  summaries: Record<string, string | null>;
  /**
   * What this step leaves UNDONE, in one plain sentence — "no key yet, chat
   * stays off", "wake word off until the local model is installed". The
   * finish step lists every gap and asks for one acknowledgement before the
   * assistant starts; a step with nothing missing reports null.
   */
  setGap: (text: string | null) => void;
  gaps: Record<string, string | null>;
}

// Restart batching (2026-07-18): permissions + wake-word sit LAST before
// finish — both only fully apply after a relaunch, and onboarding already
// ends with ONE unconditional fresh restart. Never demand a second one.
const REGISTRY: Record<string, (p: StepProps) => JSX.Element> = {
  welcome: WelcomeStep,
  language: LanguageStep,
  "api-keys": ApiKeysStep,
  permissions: PermissionsStep,
  "wake-word": WakeWordStep,
  finish: FinishStep,
};

// Exported for the cross-layer parity test: these must equal the backend's
// ONBOARDING_STEPS (jarvis/setup/onboarding_meta.py). A typo here would render
// the silent fallback div instead of a real step.
export const STEP_KEYS = Object.keys(REGISTRY);

/**
 * Which backend steps this machine actually needs. The permissions step is
 * macOS TCC only — on Windows and Linux it used to render a "nothing to do
 * here" card the user had to click through. Now it is not in the register at
 * all. Unknown platform (probe failed, still warming) keeps the step: showing
 * an unnecessary screen is a small cost, hiding a necessary one is not.
 */
export function visibleSteps(steps: string[], platform: string | null): string[] {
  if (platform === "win32" || platform === "linux") {
    return steps.filter((s) => s !== "permissions");
  }
  return steps;
}

function usePlatform(): string | null {
  const [platform, setPlatform] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/permissions/status");
        if (!res.ok) return;
        const data = (await res.json()) as { platform?: string };
        if (!cancelled && typeof data?.platform === "string") setPlatform(data.platform);
      } catch {
        // Best-effort: an unreachable probe keeps every step visible.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return platform;
}

/**
 * The first-run guide as a full-window stage in the workspace launcher's
 * editorial language: a header with an eyebrow step counter, a numbered
 * register on the left that doubles as the progress indicator (each entry
 * carries a live one-line summary of what was chosen), and the step's own
 * content on the right, separated by rules rather than nested cards.
 */
export function OnboardingFlow({
  onb,
}: {
  onb: ReturnType<typeof useOnboarding>;
}) {
  const t = useT();
  const platform = usePlatform();
  const allSteps = onb.state?.steps ?? ["welcome", "finish"];
  const steps = useMemo(() => visibleSteps(allSteps, platform), [allSteps, platform]);
  // Always begin at the first step so every run walks each step in order. We do
  // NOT resume to a saved current_step: a user who already finished once would
  // otherwise be auto-jumped to the last step, which feels like the flow skipped
  // itself.
  const [idx, setIdx] = useState(0);
  const [maxVisited, setMaxVisited] = useState(0);
  const [skipped, setSkipped] = useState<string[]>(onb.state?.skipped_steps ?? []);
  const [summaries, setSummaries] = useState<Record<string, string | null>>({});
  const [gaps, setGaps] = useState<Record<string, string | null>>({});

  const safeIdx = Math.min(idx, steps.length - 1);
  const stepKey = steps[safeIdx];
  const StepComp = REGISTRY[stepKey] ?? ((_: StepProps) => <div>{stepKey}</div>);

  const advance = (next: number, nextSkipped = skipped) => {
    if (next >= steps.length) {
      void onb.complete();
      return;
    }
    setSkipped(nextSkipped);
    setIdx(next);
    setMaxVisited((m) => Math.max(m, next));
    void onb.saveStep(steps[next], nextSkipped);
  };

  const setSummary = useCallback(
    (text: string | null) => {
      setSummaries((s) => (s[stepKey] === text ? s : { ...s, [stepKey]: text }));
    },
    [stepKey],
  );

  const setGap = useCallback(
    (text: string | null) => {
      setGaps((g) => (g[stepKey] === text ? g : { ...g, [stepKey]: text }));
    },
    [stepKey],
  );

  const props: StepProps = {
    onb,
    goNext: () => advance(safeIdx + 1),
    goBack: () => setIdx((i) => Math.max(0, i - 1)),
    skip: () => advance(safeIdx + 1, [...new Set([...skipped, stepKey])]),
    isFirst: safeIdx === 0,
    isLast: safeIdx === steps.length - 1,
    setSummary,
    summaries,
    setGap,
    gaps,
  };

  const title = t(`onboarding.steps.${stepKey}.title`);
  const hint = t(`onboarding.steps.${stepKey}.hint`);

  return (
    <div
      data-testid="onboarding-flow"
      className="flex h-full min-h-0 flex-col font-display text-[15px]"
    >
      <header className="shrink-0 border-b border-border/70 px-6 py-6 sm:px-12 xl:py-8">
        <div className="mx-auto flex w-full max-w-[1400px] items-start justify-between gap-6">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary/80">
              {t("onboarding.eyebrow")}
              <span className="px-2 text-muted-foreground/50">/</span>
              <span
                role="progressbar"
                aria-label={t("onboarding.progress_label")}
                aria-valuemin={1}
                aria-valuemax={steps.length}
                aria-valuenow={safeIdx + 1}
                className="font-mono tabular-nums"
              >
                {t("onboarding.step_progress")
                  .replace("{0}", String(safeIdx + 1).padStart(2, "0"))
                  .replace("{1}", String(steps.length).padStart(2, "0"))}
              </span>
            </p>
            <h1 className="mt-2 text-2xl tracking-tight text-foreground [text-wrap:balance] sm:text-3xl xl:text-4xl">
              {title}
            </h1>
            <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-muted-foreground xl:text-base">
              {hint}
            </p>
          </div>
          <div className="shrink-0 pt-0.5">
            <MascotGigi size={56} reactToVoice={false} enableComments={false} />
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto grid w-full max-w-[1400px] gap-x-14 gap-y-4 px-6 pb-12 pt-6 sm:px-12 lg:grid-cols-[260px_minmax(0,1fr)] xl:pt-8">
          <nav aria-label={t("onboarding.progress_label")} className="min-w-0">
            <ol
              className="grid border-b border-border/70 lg:flex lg:flex-col lg:border-b-0 lg:border-r lg:pr-6"
              style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}
            >
              {steps.map((key, index) => {
                const selected = index === safeIdx;
                const enabled = index <= maxVisited;
                const summary = summaries[key];
                return (
                  <li key={key}>
                    <button
                      type="button"
                      data-testid={`onboarding-step-${key}`}
                      aria-current={selected ? "step" : undefined}
                      disabled={!enabled}
                      onClick={() => setIdx(index)}
                      className={cn(
                        "group relative w-full min-w-0 px-2 py-3 text-left transition-colors lg:px-0 lg:py-4",
                        "disabled:cursor-not-allowed disabled:opacity-35",
                        selected ? "text-foreground" : "text-muted-foreground",
                      )}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "absolute bottom-[-1px] left-0 right-0 h-0.5 lg:bottom-0 lg:left-auto lg:right-[-25px] lg:top-0 lg:h-auto lg:w-0.5",
                          selected ? "bg-primary" : "bg-transparent",
                        )}
                      />
                      <span className="block font-mono text-[11px] tabular-nums text-muted-foreground/70">
                        {(index + 1).toString().padStart(2, "0")}
                      </span>
                      <span className="mt-1 block truncate text-[15px] font-medium">
                        {t(`onboarding.steps.${key}.label`)}
                      </span>
                      <span className="mt-0.5 hidden truncate text-xs text-muted-foreground lg:block">
                        {summary ?? " "}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>

          <section
            key={stepKey}
            className="profile-rise min-w-0 max-w-[920px] pt-2"
            aria-labelledby="onboarding-step-title"
          >
            <h2 id="onboarding-step-title" className="sr-only">
              {title}
            </h2>
            <StepComp {...props} />
          </section>
        </div>
      </div>
    </div>
  );
}

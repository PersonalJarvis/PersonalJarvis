/**
 * The React side of "Set up everything": one hook that runs `runLocalSetup`
 * once at a time, keeps the current step in state for the progress line,
 * survives a tab switch (nothing is written to state after unmount) and
 * repaints the section when the run ends — however it ends.
 *
 * `useSilentTune` is the tune step on its own, shared with the per-row
 * "Use recommended" button: the suggested options go in, one readback
 * sentence comes out, and a failed tune never fails the flow — the role is
 * already assigned, only the sentence is lost.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getSuggestedOptions,
  saveModelOptions,
  useInvalidateLocalModels,
  type SuggestedOptionsResponse,
} from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";

import { formatContext } from "./localModelsFormat";
import { runLocalSetup, type SetupStep } from "./localSetup";

/** The one-sentence readback after a silent tune. */
export function readbackFor(
  s: SuggestedOptionsResponse,
  t: (key: string) => string,
): string {
  const ctx = s.options.num_ctx;
  if (typeof ctx === "number") {
    const key =
      s.options.num_gpu != null
        ? "local_models.roles.readback_gpu"
        : "local_models.roles.readback_ctx";
    return fill(t(key), { ctx: formatContext(ctx) });
  }
  return s.reasons[0] ?? t("local_models.roles.readback_defaults");
}

/** Suggested options in, one sentence out. Never throws. */
export function useSilentTune(providerId: string) {
  const t = useT();
  return useCallback(
    async (model: string): Promise<string> => {
      try {
        const suggested = await getSuggestedOptions(providerId, model);
        await saveModelOptions(providerId, model, suggested.options);
        return readbackFor(suggested, t);
      } catch (err) {
        console.warn(
          "[local-models] suggested options not applied",
          model,
          err,
        );
        return t("local_models.roles.tune_skipped");
      }
    },
    [providerId, t],
  );
}

export interface LocalSetupState {
  /** Starts a run; a second call while one runs is ignored. */
  run: () => void;
  /** The current step, `null` before the first run. */
  step: SetupStep | null;
  /** A run is underway. */
  busy: boolean;
  /** Clears a finished run's line. */
  reset: () => void;
}

export function useLocalSetup(providerId: string): LocalSetupState {
  const [step, setStep] = useState<SetupStep | null>(null);
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const alive = useCallback(() => mounted.current, []);
  const invalidate = useInvalidateLocalModels(providerId);
  const tune = useSilentTune(providerId);

  const run = useCallback(() => {
    if (running.current) return;
    running.current = true;
    const report = (next: SetupStep) => {
      if (mounted.current) setStep(next);
    };
    report({ phase: "planning" });
    void (async () => {
      try {
        await runLocalSetup({ providerId, report, alive, tune });
      } catch (err) {
        report({
          phase: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      } finally {
        running.current = false;
        invalidate();
      }
    })();
  }, [alive, invalidate, providerId, tune]);

  const reset = useCallback(() => {
    if (!running.current) setStep(null);
  }, []);

  const busy =
    step !== null && step.phase !== "done" && step.phase !== "error";
  return { run, step, busy, reset };
}

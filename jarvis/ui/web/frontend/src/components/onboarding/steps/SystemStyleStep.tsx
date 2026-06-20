import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useOverlayStyle, type OverlayStyle } from "@/hooks/useOverlayStyle";
import { StylePreview } from "@/components/overlay/OverlayStylePreviews";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { StepProps } from "../OnboardingFlow";

// The Jarvis Bar is the default + recommended on-screen surface.
const RECOMMENDED: OverlayStyle = "whisper_bar";

/**
 * Onboarding step: choose the on-screen "system style" (the Jarvis Bar vs. the
 * Orb vs. no overlay). Reuses the existing overlay-style axis
 * (`/api/settings/overlay-style` via `useOverlayStyle`) and the shared preview
 * graphics, with the Jarvis Bar pre-selected and labelled "Recommended".
 *
 * The pick is persisted immediately and live-applied when Tk can swap the
 * surface in place. When it can't (a `bar <-> mascot` first-time swap needs a
 * brand-new Tk root — BUG-031 — which would crash the process), the backend
 * reports `restart_required`; this step then offers a one-click self-restart
 * (the same `request_restart` relauncher the Settings panel uses) so the choice
 * actually takes effect instead of leaving the user to relaunch by hand. We
 * never restart on the *pick* itself — only when the user clicks "Restart now".
 */
export function SystemStyleStep({ goNext, skip }: StepProps) {
  const t = useT();
  const { config, loading, saveStyle } = useOverlayStyle();
  const [style, setStyle] = useState<OverlayStyle>(RECOMMENDED);
  const [saving, setSaving] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [showSaveError, setShowSaveError] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // Armed after the backend refuses a restart (HTTP 409) because missions are
  // running; the next click resends with force=true. Mirrors the Settings panel.
  const [forceArmed, setForceArmed] = useState(false);

  useEffect(() => {
    if (config) setStyle(config.style);
  }, [config]);

  const options = config?.options ?? (["whisper_bar", "mascot", "none"] as OverlayStyle[]);

  async function onPick(opt: OverlayStyle) {
    if (saving || restarting) return;
    setStyle(opt); // optimistic — reverted below if the save fails
    setSaving(true);
    setNeedsRestart(false);
    setForceArmed(false);
    setShowSaveError(false);
    try {
      const res = await saveStyle(opt);
      // Live-applied → the surface already changed on screen. Otherwise the
      // choice is persisted and a restart is needed to deliver it.
      if (!res.applied_live) setNeedsRestart(true);
    } catch {
      // Honest failure: revert the optimistic selection to the last persisted
      // value so the highlighted card never claims a pick that wasn't saved, and
      // surface a non-blocking error. The default still stands; the user can
      // retry or continue.
      setStyle(config?.style ?? RECOMMENDED);
      setShowSaveError(true);
    } finally {
      setSaving(false);
    }
  }

  async function onRestartNow() {
    if (restarting) return;
    setRestarting(true);
    try {
      const url = forceArmed
        ? "/api/settings/restart-app?force=true"
        : "/api/settings/restart-app";
      const res = await fetch(url, { method: "POST" });
      if (res.status === 409) {
        // Live missions would be killed — arm a force restart instead of
        // killing them silently. The next click resends with force=true.
        setRestarting(false);
        setForceArmed(true);
        return;
      }
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      // On success the window goes away as the app relaunches, so we keep
      // `restarting` true (never cleared here).
    } catch {
      // Re-enable the button so the user can retry; the choice is already
      // persisted, so it still applies on the next manual start either way.
      setRestarting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-display text-lg font-semibold">
          {t("onboarding.system_style.title")}
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("onboarding.system_style.body")}
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            aria-label={t(`onboarding.system_style.options.${opt}`)}
            aria-pressed={opt === style}
            disabled={saving || loading}
            onClick={() => onPick(opt)}
            className={cn(
              "relative flex flex-col items-center gap-2 rounded-lg border p-3 transition-all disabled:opacity-60",
              opt === style
                ? "border-primary bg-primary/5 ring-1 ring-primary/50"
                : "border-border bg-background/40 hover:border-primary/50",
            )}
          >
            {opt === RECOMMENDED && (
              <span className="absolute -top-2 right-1 rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground">
                {t("onboarding.system_style.recommended")}
              </span>
            )}
            <div className="flex h-16 w-full items-center justify-center overflow-hidden rounded-md bg-card/80">
              <StylePreview style={opt} />
            </div>
            <span
              className={cn(
                "text-xs font-medium",
                opt === style ? "text-primary" : "text-muted-foreground",
              )}
            >
              {t(`onboarding.system_style.options.${opt}`)}
            </span>
          </button>
        ))}
      </div>

      <p className="text-xs text-muted-foreground">
        {t(`onboarding.system_style.captions.${style}`)}
      </p>

      {needsRestart && (
        <div className="flex flex-col gap-2 rounded-md border border-primary/40 bg-primary/5 p-3">
          <p className="text-xs text-muted-foreground">
            {t("onboarding.system_style.needs_restart")}
          </p>
          <button
            type="button"
            onClick={onRestartNow}
            disabled={restarting}
            className="self-start rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-60"
          >
            {restarting
              ? t("onboarding.system_style.restarting")
              : forceArmed
                ? t("topbar.restart_force")
                : t("onboarding.system_style.restart_now")}
          </button>
          {forceArmed && !restarting && (
            <p className="text-xs text-amber-500">{t("topbar.restart_missions_running")}</p>
          )}
        </div>
      )}

      {showSaveError && (
        <p className="text-xs text-destructive">
          {t("onboarding.system_style.save_error")}
        </p>
      )}

      <Button className="w-full" onClick={goNext}>
        {t("onboarding.nav.next")}
      </Button>
      <button
        type="button"
        className="text-xs text-muted-foreground underline"
        onClick={skip}
      >
        {t("onboarding.system_style.skip")}
      </button>
    </div>
  );
}

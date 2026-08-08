import { useEffect, useState } from "react";
import { CheckCircle2, HardDrive, KeyRound, Radio, Waypoints } from "lucide-react";
import { Button } from "@/components/ui/button";
import { switchBrainProvider } from "@/hooks/useProviders";
import { setLocalMode } from "@/lib/localMode";
import { putVoiceMode } from "@/lib/voiceEngineMode";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

const GUIDE_SCREENSHOT = "/onboarding/api-keys-realtime-guide.png";

/**
 * First-sixty-seconds local path (maintainer amendment 2026-07-25): a
 * zero-key user with Ollama running must SEE the local option during
 * onboarding, not discover it later in settings. The probe goes through the
 * backend catalog route (never a browser-direct localhost:11434 call, which
 * CORS would block anyway) and runs once on step mount only (AP-26).
 */
function LocalPathCard() {
  const t = useT();
  const [probe, setProbe] = useState<"checking" | "reachable" | "empty" | "unreachable">(
    "checking",
  );
  const [activating, setActivating] = useState(false);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/providers/ollama/models");
        const data = res.ok ? await res.json() : null;
        const live = data?.source === "live" || data?.source === "cache";
        const models = Array.isArray(data?.models) ? data.models.length : 0;
        if (!cancelled) {
          setProbe(live ? (models > 0 ? "reachable" : "empty") : "unreachable");
        }
      } catch {
        if (!cancelled) setProbe("unreachable");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function useLocal() {
    setActivating(true);
    setError(null);
    try {
      await switchBrainProvider("ollama");
      // Picking the local brain has to pin the PIPELINE engine as well.
      // Realtime replaces STT + Brain + TTS with one full-duplex cloud model
      // and never consults `[brain].primary`, and `[voice].mode` defaults to
      // realtime — so activating the local brain alone left the user on the
      // realtime cards with their choice having changed nothing audible.
      // Persisted, because onboarding ends in a restart.
      await putVoiceMode("pipeline");
      // Somebody who picks the local path here lands in a provider console
      // whose cards are almost entirely hosted accounts they just declined.
      // Local Mode opens it on the handful that need no key. A view preference,
      // reversible with one click on the switch in that view's header — never a
      // state the user has to hunt for a way out of.
      setLocalMode(true);
      setActive(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivating(false);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-muted/30 p-4">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <HardDrive aria-hidden="true" className="h-4 w-4 text-primary" />
        {t("onboarding.api_keys.local_title")}
      </div>
      {probe === "checking" && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("onboarding.api_keys.local_checking")}
        </p>
      )}
      {probe === "reachable" && (
        <div className="space-y-2">
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("onboarding.api_keys.local_detected")}
          </p>
          {active ? (
            <p className="flex items-center gap-2 text-xs font-medium text-emerald-600">
              <CheckCircle2 aria-hidden="true" className="h-4 w-4 shrink-0" />
              {t("onboarding.api_keys.local_active")}
            </p>
          ) : (
            <Button size="sm" variant="secondary" onClick={useLocal} disabled={activating}>
              {t("onboarding.api_keys.local_use_button")}
            </Button>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
      )}
      {probe === "empty" && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("onboarding.api_keys.local_detected_empty")}
        </p>
      )}
      {probe === "unreachable" && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("onboarding.api_keys.local_missing")}{" "}
          <a
            href="https://ollama.com/download"
            target="_blank"
            rel="noreferrer"
            className="font-medium text-primary underline-offset-2 hover:underline"
          >
            {t("onboarding.api_keys.local_missing_link")}
          </a>
        </p>
      )}
    </section>
  );
}

/**
 * Introduces the credential path without embedding the full provider console in
 * a first-run modal. The screenshot is captured from the real API Keys view;
 * percentage-based markers stay aligned when the image scales down.
 */
export function ApiKeysStep({ goNext }: StepProps) {
  const t = useT();

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <KeyRound aria-hidden="true" className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h2 className="mb-1 font-display text-xl font-semibold text-balance">
            {t("onboarding.api_keys.title")}
          </h2>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t("onboarding.api_keys.body")}
          </p>
        </div>
      </div>

      <figure className="overflow-hidden rounded-xl border border-border bg-background shadow-sm">
        <div className="relative aspect-[1.96/1] overflow-hidden bg-muted">
          <img
            src={GUIDE_SCREENSHOT}
            alt={t("onboarding.api_keys.screenshot_alt")}
            width={2560}
            height={1305}
            className="h-full w-full object-cover"
            decoding="async"
            draggable={false}
          />

          <div
            data-testid="api-keys-marker"
            aria-hidden="true"
            className="pointer-events-none absolute left-[0.3%] top-[49.5%] h-[3.1%] w-[10.5%] rounded-md border-2 border-primary bg-primary/10 shadow-[0_0_0_3px_rgba(231,196,110,0.18)]"
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[10.1%] top-[48.5%] flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground shadow-lg"
          >
            1
          </span>

          <div
            data-testid="voice-mode-marker"
            aria-hidden="true"
            className="pointer-events-none absolute left-[84.8%] top-[4.3%] h-[3.2%] w-[14.4%] rounded-md border-2 border-primary bg-primary/10 shadow-[0_0_0_3px_rgba(231,196,110,0.18)]"
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-[84%] top-[3.5%] flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground shadow-lg"
          >
            2
          </span>
        </div>
      </figure>

      <div className="grid gap-3 sm:grid-cols-2">
        <section className="rounded-xl border border-border bg-muted/30 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              1
            </span>
            {t("onboarding.api_keys.api_location_title")}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("onboarding.api_keys.api_location_body")}
          </p>
        </section>

        <section className="rounded-xl border border-primary/40 bg-primary/5 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              2
            </span>
            {t("onboarding.api_keys.voice_mode_title")}
          </div>
          <div className="space-y-2 text-xs leading-relaxed text-muted-foreground">
            <p className="flex items-start gap-2">
              <Waypoints aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              <span>
                <strong className="font-semibold text-foreground">
                  {t("onboarding.api_keys.pipeline_label")}
                </strong>{" "}
                {t("onboarding.api_keys.pipeline_body")}
              </span>
            </p>
            <p className="flex items-start gap-2">
              <Radio aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              <span>
                <strong className="font-semibold text-foreground">
                  {t("onboarding.api_keys.realtime_label")}
                </strong>{" "}
                {t("onboarding.api_keys.realtime_body")}
              </span>
            </p>
          </div>
        </section>
      </div>

      <LocalPathCard />

      <p className="flex items-start gap-2 rounded-lg bg-emerald-500/10 px-3 py-2.5 text-xs leading-relaxed text-emerald-600">
        <CheckCircle2 aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        {t("onboarding.api_keys.security_note")}
      </p>

      <Button className="w-full" onClick={goNext}>
        {t("onboarding.api_keys.continue")}
      </Button>
    </div>
  );
}

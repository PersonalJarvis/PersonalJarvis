import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

type MicState = "checking" | "ok" | "no-mic";

export function MicTestStep({ goNext, skip }: StepProps) {
  const t = useT();
  const [mic, setMic] = useState<MicState>("checking");

  useEffect(() => {
    let cancelled = false;
    const md = (navigator as Navigator).mediaDevices;
    if (!md || typeof md.getUserMedia !== "function") {
      setMic("no-mic");
      return;
    }
    md.getUserMedia({ audio: true })
      .then((stream) => {
        if (cancelled) return;
        stream.getTracks().forEach((tr) => tr.stop());
        setMic("ok");
      })
      .catch(() => {
        if (!cancelled) setMic("no-mic");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.mic_test.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.mic_test.body")}</p>
      {mic === "no-mic" && <p className="text-xs text-amber-500">{t("onboarding.mic_test.no_mic")}</p>}
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>
        {t("onboarding.mic_test.skip")}
      </button>
    </div>
  );
}

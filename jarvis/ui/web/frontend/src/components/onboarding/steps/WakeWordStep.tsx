import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useWakeWord } from "@/hooks/useWakeWord";
import { useT } from "@/i18n";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import type { StepProps } from "../OnboardingFlow";

export function WakeWordStep({ onb, goNext }: StepProps) {
  const t = useT();
  const { saveWakeWord } = useWakeWord();
  const [word, setWord] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const trimmed = word.trim();
  const canSave = trimmed.length >= 2 && ack && !busy;
  const derivedName = deriveAssistantName(`Hey ${trimmed}`);
  const refs = onb.state?.legal_references ?? [];

  async function onSave() {
    if (!canSave) return;
    setBusy(true);
    setErr(null);
    try {
      await onb.acknowledgeWakeWord();
      await saveWakeWord({ phrase: `Hey ${trimmed}`, engine: "auto", persist: true });
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.wake_word.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.wake_word.body")}</p>

      <div className="flex items-center gap-2">
        <span className="rounded-md bg-muted px-3 py-2 text-sm font-medium">
          {t("onboarding.wake_word.prefix")}
        </span>
        <input
          aria-label={t("onboarding.wake_word.input_label")}
          type="text"
          value={word}
          maxLength={56}
          autoFocus
          onChange={(e) => setWord(e.target.value)}
          placeholder={t("onboarding.wake_word.placeholder")}
          className="w-full rounded-md border border-muted-foreground/25 bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </div>

      {trimmed.length >= 2 && derivedName ? (
        <p className="text-xs text-muted-foreground">
          {t("onboarding.wake_word.derived_name").replace("{0}", derivedName)}
        </p>
      ) : null}

      <p className="text-xs text-muted-foreground">{t("onboarding.wake_word.notice")}</p>
      <div className="text-xs">
        <div className="font-medium">{t("onboarding.wake_word.references_title")}</div>
        <ul className="mt-1 list-disc pl-4">
          {refs.map((r) => (
            <li key={r.url}>
              <a href={r.url} target="_blank" rel="noreferrer" className="text-primary underline">
                {r.label}
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-1 text-muted-foreground">{t("onboarding.wake_word.references_caveat")}</p>
      </div>

      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />
        {t("onboarding.wake_word.ack_label")}
      </label>

      {err && <p className="text-xs text-amber-500">{err}</p>}

      <Button className="w-full" disabled={!canSave} onClick={onSave}>
        {busy ? t("onboarding.wake_word.saving") : t("onboarding.wake_word.cta")}
      </Button>
    </div>
  );
}

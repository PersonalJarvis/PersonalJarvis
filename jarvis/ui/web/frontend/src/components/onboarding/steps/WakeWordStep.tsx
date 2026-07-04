import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useWakeWord, useLocalSpeechInstall } from "@/hooks/useWakeWord";
import { useT } from "@/i18n";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import type { StepProps } from "../OnboardingFlow";

// Phrases that run on the always-on neural wake (openWakeWord) with zero
// download and no GPU — they ship as pretrained models in every install
// (KNOWN_OWW_MODELS in jarvis/speech/wake_constants.py). Any OTHER phrase needs
// the local-Whisper pack; without it the backend degrades to a built-in phrase.
// Kept in lockstep with KNOWN_OWW_MODELS; a miss here only means we skip the
// green "instant" hint — the authoritative signal is the save's `degraded` flag.
const INSTANT_CORE_WORDS = new Set(["jarvis", "rhasspy"]);

export function WakeWordStep({ onb, goNext }: StepProps) {
  const t = useT();
  const { saveWakeWord } = useWakeWord();
  const [word, setWord] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showRefs, setShowRefs] = useState(false);
  // Set when a save resolved to a degraded engine (the chosen phrase has no
  // pretrained model AND local Whisper is absent). We DON'T advance silently in
  // that case — we tell the user and offer the one-click local-speech install.
  const [degraded, setDegraded] = useState(false);
  const { status: install, install: startInstall } = useLocalSpeechInstall();

  const trimmed = word.trim();
  const canSave = trimmed.length >= 2 && ack && !busy;
  const derivedName = deriveAssistantName(`Hey ${trimmed}`);
  const refs = onb.state?.legal_references ?? [];
  const isInstantWord = INSTANT_CORE_WORDS.has(trimmed.toLowerCase());

  function setWordReset(next: string) {
    // Any edit invalidates a previous degraded verdict — back to the normal CTA.
    setWord(next);
    if (degraded) setDegraded(false);
    if (err) setErr(null);
  }

  async function onSave() {
    if (!canSave) return;
    setBusy(true);
    setErr(null);
    try {
      await onb.acknowledgeWakeWord();
      const result = await saveWakeWord({ phrase: `Hey ${trimmed}`, engine: "auto", persist: true });
      // Honesty gate: only advance when the phrase will actually be heard. A
      // degraded result means the app would listen for a built-in fallback word,
      // not the user's — surface it instead of pretending it worked.
      if (result.degraded) {
        setDegraded(true);
        return;
      }
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
          onChange={(e) => setWordReset(e.target.value)}
          placeholder={t("onboarding.wake_word.placeholder")}
          className="w-full rounded-md border border-muted-foreground/25 bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </div>

      {/* Recommended shortcut: "Hey Jarvis" runs out-of-the-box on any machine. */}
      {!isInstantWord && (
        <button
          type="button"
          onClick={() => setWordReset("Jarvis")}
          className="self-start rounded-md border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10"
        >
          {t("onboarding.wake_word.recommended_cta")}
        </button>
      )}

      {trimmed.length >= 2 && derivedName ? (
        <p className="text-xs text-muted-foreground">
          {t("onboarding.wake_word.derived_name").replace("{0}", derivedName)}
        </p>
      ) : null}

      {isInstantWord && (
        <p className="text-xs text-emerald-500">{t("onboarding.wake_word.instant_ok")}</p>
      )}

      <p className="text-xs text-muted-foreground">
        {t("onboarding.wake_word.notice")}{" "}
        <button
          type="button"
          onClick={() => setShowRefs((v) => !v)}
          className="text-primary underline"
        >
          {t("onboarding.wake_word.learn_more")}
        </button>
      </p>

      {showRefs && refs.length > 0 && (
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
      )}

      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />
        {t("onboarding.wake_word.ack_label")}
      </label>

      {err && <p className="text-xs text-amber-500">{err}</p>}

      {degraded ? (
        // The chosen phrase has no pretrained model and local Whisper is absent.
        // Offer the one-click local-speech install, or continue with the honest
        // knowledge that the word takes effect after the pack is installed.
        <div className="flex flex-col gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
          <p className="text-xs text-amber-500">
            {t("settings_view.wake_word.needs_whisper_hint")}
          </p>
          {install.state === "running" ? (
            <p className="text-xs text-muted-foreground">
              {t("settings_view.wake_word.enable_local_installing")}
            </p>
          ) : install.state === "done" ? (
            <p className="text-xs text-emerald-500">
              {t("settings_view.wake_word.enable_local_done")}
            </p>
          ) : install.state === "error" ? (
            <p className="text-xs text-amber-500">
              {t("settings_view.wake_word.enable_local_error")}
            </p>
          ) : null}
          <div className="flex gap-2">
            {install.state !== "done" && (
              <Button
                variant="outline"
                className="flex-1"
                disabled={install.state === "running"}
                onClick={() => void startInstall()}
              >
                {install.state === "error"
                  ? t("settings_view.wake_word.enable_local_retry")
                  : t("settings_view.wake_word.enable_local_button")}
              </Button>
            )}
            <Button className="flex-1" onClick={goNext}>
              {t("onboarding.wake_word.continue_anyway")}
            </Button>
          </div>
        </div>
      ) : (
        <Button className="w-full" disabled={!canSave} onClick={onSave}>
          {busy ? t("onboarding.wake_word.saving") : t("onboarding.wake_word.cta")}
        </Button>
      )}
    </div>
  );
}

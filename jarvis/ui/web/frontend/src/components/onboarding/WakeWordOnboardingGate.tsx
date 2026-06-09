import { useState } from "react";
import { Mic } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWakeWord, isConfigured } from "@/hooks/useWakeWord";
import { useT } from "@/i18n";

// ---------------------------------------------------------------------------
// Step machine — extend this tuple to add future onboarding steps.
// ---------------------------------------------------------------------------
const STEPS = ["wake-word"] as const;
type Step = (typeof STEPS)[number];

// ---------------------------------------------------------------------------
// WakeWordStep — the single onboarding step shipped today.
// ---------------------------------------------------------------------------
function WakeWordStep() {
  const t = useT();
  const { saveWakeWord } = useWakeWord();

  const [phrase, setPhrase] = useState("");
  const [saving, setSaving] = useState(false);
  const [degradedNote, setDegradedNote] = useState<string | null>(null);

  const trimmed = phrase.trim();
  const canSubmit = trimmed.length > 0 && !saving;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setDegradedNote(null);
    try {
      const result = await saveWakeWord({
        phrase: trimmed,
        engine: "auto",
        persist: true,
      });
      if (result.degraded) {
        setDegradedNote(result.message || t("settings_view.onboarding.wake_word.degraded_note"));
      }
      // On success the jarvis:wake-word-changed event is dispatched by
      // saveWakeWord → the useWakeWord hook refetches → isConfigured becomes
      // true → this gate unmounts automatically.
    } catch {
      // Save failed — keep the gate visible so the user can retry.
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="w-full max-w-md rounded-xl border border-border bg-card p-8 shadow-2xl">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10">
          <Mic className="h-5 w-5 text-primary" />
        </div>
        <h2 className="font-display text-lg font-semibold">
          {t("settings_view.onboarding.wake_word.title")}
        </h2>
      </div>

      <p className="mt-4 text-sm text-muted-foreground">
        {t("settings_view.onboarding.wake_word.body")}
      </p>

      <form onSubmit={onSubmit} className="mt-6 space-y-4">
        <div>
          <label
            htmlFor="onboarding-wake-phrase"
            className="block text-xs font-medium text-muted-foreground"
          >
            {t("settings_view.onboarding.wake_word.input_label")}
          </label>
          <input
            id="onboarding-wake-phrase"
            type="text"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            maxLength={64}
            placeholder={t("settings_view.onboarding.wake_word.placeholder")}
            autoFocus
            className="mt-1.5 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>

        {degradedNote && (
          <p className="text-xs text-amber-500">{degradedNote}</p>
        )}

        <Button type="submit" className="w-full" disabled={!canSubmit}>
          {saving
            ? t("settings_view.onboarding.wake_word.saving")
            : t("settings_view.onboarding.wake_word.cta")}
        </Button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WakeWordOnboardingGate — blocking full-screen overlay until configured.
// ---------------------------------------------------------------------------
export function WakeWordOnboardingGate() {
  const { config, loading, error } = useWakeWord();

  // While loading: render nothing (avoid flash).
  if (loading) return null;

  // On GET error: fail open — do not block the user.
  if (error) return null;

  // Already configured: gate is transparent.
  if (isConfigured(config)) return null;

  // Resolve the active step (always index 0 for now).
  const step: Step = STEPS[0];

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
    >
      {step === "wake-word" && <WakeWordStep />}
    </div>
  );
}

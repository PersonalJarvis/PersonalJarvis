import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useWakeWord, useLocalSpeechInstall } from "@/hooks/useWakeWord";
import { useT } from "@/i18n";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import type { StepProps } from "../OnboardingFlow";

type Mode = "choice" | "wake" | "shortcut";

/** GET /api/settings/wake-word/mic-level response shape. */
interface MicLevelResult {
  max_dbfs: number;
  no_device: boolean;
  too_quiet: boolean;
  permission_required?: boolean;
}

type MicCheckState = "idle" | "checking" | "done";

/**
 * Two honest activation paths — no branded default (Marvel owns "Jarvis" as a
 * trademark, so recommending "Hey Jarvis" out of the box is off the table):
 *
 *  - "wake": the user picks their OWN word. It only actually fires once a local
 *    model exists for that exact word — if the save comes back `degraded` we do
 *    NOT silently advance, we offer the one-click local-speech install or an
 *    honest "continue anyway" (wake word off until the pack lands).
 *  - "shortcut": no wake word at all — the Call keyboard shortcut starts a
 *    normal voice session and remains editable later in Settings.
 */
export function WakeWordStep({ onb, goNext }: StepProps) {
  const t = useT();
  const { saveWakeWord, setWakeActivation } = useWakeWord();
  const [mode, setMode] = useState<Mode>("choice");
  const [word, setWord] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showRefs, setShowRefs] = useState(false);
  // Set when a save resolved to a degraded engine (no local model matches the
  // user's own word). We DON'T advance silently in that case — we tell the
  // user and offer the one-click local-speech install, or an honest opt-out.
  const [degraded, setDegraded] = useState(false);
  const { status: install, install: startInstall } = useLocalSpeechInstall(() => {
    // The first save already persisted the phrase. Once the recovery installer
    // has both the engine and its model, discard that stale degraded verdict and
    // let the normal CTA re-check + activate it. This prevents the contradictory
    // "model installed" plus "continue anyway" state from lingering on screen.
    setDegraded(false);
    setErr(null);
  });
  // Mic verification (Task 7): a live dBFS read from the desktop app's own
  // capture path (the same one the wake-word detector listens on) — never
  // blocks the save/acknowledge below, it just surfaces an honest signal so a
  // quiet mic or a headless/no-mic host is visible before the user commits.
  const [micCheck, setMicCheck] = useState<{
    state: MicCheckState;
    result: MicLevelResult | null;
    error: string | null;
  }>({ state: "idle", result: null, error: null });

  const trimmed = word.trim();
  const canSave = trimmed.length >= 2 && ack && !busy;
  const derivedName = deriveAssistantName(`Hey ${trimmed}`);
  const refs = onb.state?.legal_references ?? [];

  function setWordReset(next: string) {
    // Any edit invalidates a previous degraded verdict — back to the normal CTA.
    setWord(next);
    if (degraded) setDegraded(false);
    if (err) setErr(null);
  }

  // Shared by both the "Test your microphone" and "Say your wake word once"
  // affordances — both hit the same live dBFS read; the second just prompts
  // the user to say their word during the ~3s window instead of just any
  // sound. Never throws: a fetch failure is shown as its own honest state,
  // never blocks the save/acknowledge CTA below.
  async function runMicCheck() {
    setMicCheck({ state: "checking", result: null, error: null });
    try {
      const res = await fetch("/api/settings/wake-word/mic-level");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: MicLevelResult = await res.json();
      setMicCheck({ state: "done", result: data, error: null });
    } catch (e) {
      setMicCheck({ state: "done", result: null, error: (e as Error).message });
    }
  }

  async function onSaveWake() {
    if (!canSave) return;
    setBusy(true);
    setErr(null);
    try {
      await onb.acknowledgeWakeWord();
      const result = await saveWakeWord({ phrase: `Hey ${trimmed}`, engine: "auto", persist: true });
      // Honesty gate: only advance when the phrase will actually be heard. A
      // degraded result means no local model matches the user's own word — the
      // wake word would effectively be off — so surface that instead of
      // pretending it worked.
      if (result.degraded) {
        setDegraded(true);
        return;
      }
      await setWakeActivation(true);
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onContinueDegraded() {
    setBusy(true);
    setErr(null);
    try {
      await setWakeActivation(true);
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onChooseShortcut() {
    setBusy(true);
    setErr(null);
    try {
      await setWakeActivation(false);
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (mode === "choice") {
    return (
      <div className="flex flex-col gap-4">
        <h2 className="font-display text-lg font-semibold">{t("onboarding.wake_word.choice_title")}</h2>
        <p className="text-sm text-muted-foreground">{t("onboarding.wake_word.choice_body")}</p>
        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={() => setMode("wake")}
            className="flex flex-col gap-1 rounded-lg border border-muted-foreground/25 p-4 text-left hover:border-primary hover:bg-primary/5"
          >
            <span className="font-medium">{t("onboarding.wake_word.mode_wake_title")}</span>
            <span className="text-xs text-muted-foreground">{t("onboarding.wake_word.mode_wake_body")}</span>
          </button>
          <button
            type="button"
            onClick={() => setMode("shortcut")}
            className="flex flex-col gap-1 rounded-lg border border-muted-foreground/25 p-4 text-left hover:border-primary hover:bg-primary/5"
          >
            <span className="font-medium">{t("onboarding.wake_word.mode_shortcut_title")}</span>
            <span className="text-xs text-muted-foreground">{t("onboarding.wake_word.mode_shortcut_body")}</span>
          </button>
        </div>
      </div>
    );
  }

  if (mode === "shortcut") {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">{t("onboarding.wake_word.mode_shortcut_title")}</h2>
          <button
            type="button"
            onClick={() => setMode("choice")}
            className="text-xs text-muted-foreground underline"
          >
            {t("onboarding.wake_word.back_to_choice")}
          </button>
        </div>
        <p className="text-sm text-muted-foreground">{t("onboarding.wake_word.shortcut_note")}</p>
        {err && <p className="text-xs text-amber-500">{err}</p>}
        <Button className="w-full" disabled={busy} onClick={onChooseShortcut}>
          {busy ? t("onboarding.wake_word.saving") : t("onboarding.wake_word.shortcut_cta")}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg font-semibold">{t("onboarding.wake_word.title")}</h2>
        <button
          type="button"
          onClick={() => setMode("choice")}
          className="text-xs text-muted-foreground underline"
        >
          {t("onboarding.wake_word.back_to_choice")}
        </button>
      </div>
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

      {trimmed.length >= 2 && derivedName ? (
        <p className="text-xs text-muted-foreground">
          {t("onboarding.wake_word.derived_name").replace("{0}", derivedName)}
        </p>
      ) : null}

      <div className="flex flex-col gap-2 rounded-md border border-muted-foreground/25 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-medium">{t("onboarding.wake_word.mic_check.title")}</span>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={micCheck.state === "checking"}
              onClick={() => void runMicCheck()}
            >
              {micCheck.state === "checking"
                ? t("onboarding.wake_word.mic_check.checking")
                : t("onboarding.wake_word.mic_check.test_button")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={micCheck.state === "checking"}
              onClick={() => void runMicCheck()}
            >
              {t("onboarding.wake_word.mic_check.say_once_button")}
            </Button>
          </div>
        </div>
        {micCheck.state === "checking" && (
          <p className="text-xs text-muted-foreground">{t("onboarding.wake_word.mic_check.listening")}</p>
        )}
        {micCheck.state === "done" && micCheck.error && (
          <p className="text-xs text-amber-500">{t("onboarding.wake_word.mic_check.error")}</p>
        )}
        {micCheck.state === "done" && micCheck.result && (
          micCheck.result.permission_required ? (
            <p className="text-xs text-amber-500">{t("onboarding.wake_word.mic_check.permission_required")}</p>
          ) : micCheck.result.no_device ? (
            <p className="text-xs text-muted-foreground">{t("onboarding.wake_word.mic_check.no_device")}</p>
          ) : micCheck.result.too_quiet ? (
            <p className="text-xs text-amber-500">{t("onboarding.wake_word.mic_check.too_quiet")}</p>
          ) : (
            <p className="text-xs text-emerald-500">{t("onboarding.wake_word.mic_check.good")}</p>
          )
        )}
      </div>

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
        // The chosen word has no pretrained model and local Whisper is absent.
        // Offer the one-click local-speech install, or continue with the
        // honest knowledge that the wake word stays off until the pack lands.
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
            <Button className="flex-1" disabled={busy} onClick={onContinueDegraded}>
              {t("onboarding.wake_word.continue_anyway")}
            </Button>
          </div>
        </div>
      ) : (
        <Button className="w-full" disabled={!canSave} onClick={onSaveWake}>
          {busy ? t("onboarding.wake_word.saving") : t("onboarding.wake_word.cta")}
        </Button>
      )}
    </div>
  );
}

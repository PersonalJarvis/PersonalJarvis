import { useEffect, useState } from "react";
import { Mic } from "lucide-react";
import { Button, Field } from "@/components/agentic/controls";
import { useWakeWord, useLocalSpeechInstall } from "@/hooks/useWakeWord";
import { useT } from "@/i18n";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import type { StepProps } from "../OnboardingFlow";
import {
  ChoiceRow,
  ConsentLine,
  StatusLine,
  StepFooter,
  StepSection,
} from "../primitives";

type Mode = "wake" | "shortcut";

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
 *
 * Both paths sit on one screen as two selectable rows; the wake-word row
 * unfolds its input in place. One microphone check (the old step had two
 * buttons wired to the same probe).
 */
export function WakeWordStep({ onb, goNext, goBack, setSummary, setGap }: StepProps) {
  const t = useT();
  const { saveWakeWord, setWakeActivation } = useWakeWord();
  const [mode, setMode] = useState<Mode>("wake");
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
  // Mic verification: a live dBFS read from the desktop app's own capture
  // path (the same one the wake-word detector listens on) — never blocks the
  // save/acknowledge below, it just surfaces an honest signal so a quiet mic
  // or a headless/no-mic host is visible before the user commits.
  const [micCheck, setMicCheck] = useState<{
    state: MicCheckState;
    result: MicLevelResult | null;
    error: string | null;
  }>({ state: "idle", result: null, error: null });

  const trimmed = word.trim();
  const canSave = trimmed.length >= 2 && ack && !busy;
  const derivedName = deriveAssistantName(`Hey ${trimmed}`);
  const refs = onb.state?.legal_references ?? [];

  useEffect(() => {
    if (mode === "shortcut") setSummary(t("onboarding.wake_word.summary_shortcut"));
    else setSummary(trimmed.length >= 2 ? `Hey ${trimmed}` : null);
  }, [mode, trimmed, setSummary, t]);

  function setWordReset(next: string) {
    // Any edit invalidates a previous degraded verdict — back to the normal CTA.
    setWord(next);
    if (degraded) setDegraded(false);
    if (err) setErr(null);
  }

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
      setGap(null);
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
      // The phrase is saved but nothing can hear it yet — the finish step
      // must say so before the user expects "Hey X" to work.
      setGap(t("onboarding.wake_word.gap_degraded").replace("{0}", `Hey ${trimmed}`));
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
      setGap(null);
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const micLine = (() => {
    if (micCheck.state === "checking") {
      return <StatusLine tone="muted">{t("onboarding.wake_word.mic_check.listening")}</StatusLine>;
    }
    if (micCheck.state !== "done") return null;
    if (micCheck.error) {
      return <StatusLine tone="warning">{t("onboarding.wake_word.mic_check.error")}</StatusLine>;
    }
    const r = micCheck.result;
    if (!r) return null;
    if (r.permission_required) {
      return (
        <StatusLine tone="warning">
          {t("onboarding.wake_word.mic_check.permission_required")}
        </StatusLine>
      );
    }
    if (r.no_device) {
      return <StatusLine tone="muted">{t("onboarding.wake_word.mic_check.no_device")}</StatusLine>;
    }
    if (r.too_quiet) {
      return <StatusLine tone="warning">{t("onboarding.wake_word.mic_check.too_quiet")}</StatusLine>;
    }
    return <StatusLine tone="ok">{t("onboarding.wake_word.mic_check.good")}</StatusLine>;
  })();

  return (
    <div className="space-y-8">
      <StepSection label={t("onboarding.wake_word.mode_label")}>
        <div role="radiogroup" aria-label={t("onboarding.wake_word.mode_label")} className="space-y-2">
          <ChoiceRow
            selected={mode === "wake"}
            onSelect={() => setMode("wake")}
            title={t("onboarding.wake_word.mode_wake_title")}
            body={t("onboarding.wake_word.mode_wake_body")}
            testId="wake-mode-wake"
          />
          <ChoiceRow
            selected={mode === "shortcut"}
            onSelect={() => setMode("shortcut")}
            title={t("onboarding.wake_word.mode_shortcut_title")}
            body={t("onboarding.wake_word.mode_shortcut_body")}
            testId="wake-mode-shortcut"
          />
        </div>
      </StepSection>

      {mode === "shortcut" ? (
        <>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t("onboarding.wake_word.shortcut_note")}
          </p>
          {err && <StatusLine tone="error">{err}</StatusLine>}
          <StepFooter
            onBack={goBack}
            primary={{
              label: busy ? t("onboarding.wake_word.saving") : t("onboarding.nav.next"),
              onClick: () => void onChooseShortcut(),
              busy,
            }}
          />
        </>
      ) : (
        <>
          <StepSection label={t("onboarding.wake_word.word_label")}>
            <div className="space-y-3 border-y border-border/70 py-4">
              <p className="text-[13px] text-muted-foreground">{t("onboarding.wake_word.body")}</p>
              <div className="flex items-center gap-2">
                <span className="inline-flex h-8 items-center rounded-control bg-secondary px-3 text-sm font-medium text-foreground">
                  {t("onboarding.wake_word.prefix")}
                </span>
                <Field
                  aria-label={t("onboarding.wake_word.input_label")}
                  type="text"
                  value={word}
                  maxLength={56}
                  autoFocus
                  onChange={(e) => setWordReset(e.target.value)}
                  placeholder={t("onboarding.wake_word.placeholder")}
                  className="max-w-xs"
                />
              </div>
              {trimmed.length >= 2 && derivedName ? (
                <p className="text-[13px] text-muted-foreground">
                  {t("onboarding.wake_word.derived_name").replace("{0}", derivedName)}
                </p>
              ) : null}
              <p className="text-[13px] text-muted-foreground">
                {t("onboarding.wake_word.notice")}{" "}
                <button
                  type="button"
                  onClick={() => setShowRefs((v) => !v)}
                  className="text-primary underline-offset-4 hover:underline"
                >
                  {t("onboarding.wake_word.learn_more")}
                </button>
              </p>
              {showRefs && refs.length > 0 && (
                <div className="border-l-2 border-border pl-3 text-[13px]">
                  <div className="font-medium">{t("onboarding.wake_word.references_title")}</div>
                  <ul className="mt-1 space-y-0.5">
                    {refs.map((r) => (
                      <li key={r.url}>
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary underline-offset-4 hover:underline"
                        >
                          {r.label}
                        </a>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-muted-foreground">
                    {t("onboarding.wake_word.references_caveat")}
                  </p>
                </div>
              )}
            </div>
          </StepSection>

          <StepSection label={t("onboarding.wake_word.mic_check.title")}>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="quiet"
                disabled={micCheck.state === "checking"}
                onClick={() => void runMicCheck()}
                data-testid="wake-mic-test"
              >
                <Mic className="h-3.5 w-3.5" />
                {micCheck.state === "checking"
                  ? t("onboarding.wake_word.mic_check.checking")
                  : t("onboarding.wake_word.mic_check.test_button")}
              </Button>
              <div className="min-w-0 flex-1">{micLine}</div>
            </div>
          </StepSection>

          <ConsentLine checked={ack} onChange={setAck} testId="wake-ack">
            {t("onboarding.wake_word.ack_label")}
          </ConsentLine>

          {err && <StatusLine tone="error">{err}</StatusLine>}

          {degraded ? (
            // The chosen word has no pretrained model and local Whisper is
            // absent. Offer the one-click local-speech install, or continue
            // with the honest knowledge that the wake word stays off until
            // the pack lands.
            <div className="space-y-3">
              <StatusLine tone="warning">{t("settings_view.wake_word.needs_whisper_hint")}</StatusLine>
              {install.state === "running" && (
                <StatusLine tone="muted">
                  {t("settings_view.wake_word.enable_local_installing")}
                </StatusLine>
              )}
              {install.state === "done" && (
                <StatusLine tone="ok">{t("settings_view.wake_word.enable_local_done")}</StatusLine>
              )}
              {install.state === "error" && (
                <StatusLine tone="warning">
                  {t("settings_view.wake_word.enable_local_error")}
                </StatusLine>
              )}
              <StepFooter
                onBack={goBack}
                primary={{
                  label: t("onboarding.wake_word.continue_anyway"),
                  onClick: () => void onContinueDegraded(),
                  busy,
                }}
                secondary={
                  install.state !== "done"
                    ? {
                        label:
                          install.state === "error"
                            ? t("settings_view.wake_word.enable_local_retry")
                            : t("settings_view.wake_word.enable_local_button"),
                        onClick: () => void startInstall(),
                        busy: install.state === "running",
                      }
                    : null
                }
              />
            </div>
          ) : (
            <StepFooter
              onBack={goBack}
              primary={{
                label: busy ? t("onboarding.wake_word.saving") : t("onboarding.wake_word.cta"),
                onClick: () => void onSaveWake(),
                disabled: !canSave,
                busy,
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

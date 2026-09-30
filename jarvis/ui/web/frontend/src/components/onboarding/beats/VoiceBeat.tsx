import { Mic } from "lucide-react";
import { useEffect, useState } from "react";
import { FOCUS_RING } from "@/components/agentic/controls";
import { useLocalSpeechInstall, useWakeWord, type WakeActivationResult } from "@/hooks/useWakeWord";
import { fill, useT } from "@/i18n";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import { cn } from "@/lib/utils";
import type { BeatProps } from "../WelcomeFlow";
import { CheckLine, Keycaps, Option, PrimaryAction, QuietAction, Rise, Status } from "../ui";

type Mode = "wake" | "shortcut";

/** GET /api/settings/wake-word/mic-level. */
interface MicLevel {
  max_dbfs: number;
  no_device: boolean;
  too_quiet: boolean;
  permission_required?: boolean;
}

type MicCheck =
  | { state: "idle" }
  | { state: "listening" }
  | { state: "done"; level: MicLevel }
  | { state: "failed" };

/** -60 dBFS reads as silence, 0 dBFS as the loudest a microphone gets. */
export function meterFill(maxDbfs: number): number {
  if (!Number.isFinite(maxDbfs)) return 0;
  return Math.min(1, Math.max(0, (maxDbfs + 60) / 60));
}

const METER_SEGMENTS = 16;

/**
 * How the user calls the assistant — and, through the wake word, what it is
 * called: "Hey Nova" makes an assistant named Nova. That is the guide's one
 * moment of making it your own, so the name answers live under the field.
 *
 * Two honest paths, no branded default (the wake word is always the user's
 * own word, never a trademark):
 *  - wake word: only counts as working once a local model can hear that exact
 *    word; a `degraded` save says so and offers the one-click local speech
 *    install, or going on with the shortcut meanwhile;
 *  - shortcut: nothing listens until the Call keys are pressed.
 * A failed call always leaves a way on (BUG-209).
 */
export function VoiceBeat({ onb, next, skip, report, cheer, setChosenName }: BeatProps) {
  const t = useT();
  const { saveWakeWord, setWakeActivation } = useWakeWord();
  const [mode, setMode] = useState<Mode>("wake");
  const [word, setWord] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState(false);
  const [showRefs, setShowRefs] = useState(false);
  const [callCombo, setCallCombo] = useState<string | null>(null);
  const [mic, setMic] = useState<MicCheck>({ state: "idle" });
  const { status: install, install: startInstall } = useLocalSpeechInstall(() => {
    // The pack is in: the stale degraded verdict goes, and the normal action
    // re-checks and switches the word on.
    setDegraded(false);
    setError(null);
  });

  const trimmed = word.trim();
  const phrase = `Hey ${trimmed}`;
  const derived = trimmed.length >= 2 ? deriveAssistantName(phrase) : "";
  const refs = onb.state?.legal_references ?? [];

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/settings/keybinds");
        if (!res.ok) return;
        const data = (await res.json()) as {
          keybinds?: Record<string, string>;
          defaults?: Record<string, string>;
        };
        const combo = data.keybinds?.call || data.defaults?.call || null;
        if (!cancelled) setCallCombo(combo);
      } catch {
        // Without the binding the copy names "the Call shortcut" instead of its keys.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // A live-applied switch the config writer could not persist works now but
  // is gone after the restart — the ready beat must say so.
  function persistGap(result: WakeActivationResult, fallback: string | null): string | null {
    if (result.persisted) return fallback;
    const lost = t("first_run.voice.gap_not_persisted");
    return fallback ? `${fallback} ${lost}` : lost;
  }

  function edit(value: string) {
    setWord(value);
    if (degraded) setDegraded(false);
    if (error) setError(null);
  }

  async function checkMic() {
    setMic({ state: "listening" });
    try {
      const res = await fetch("/api/settings/wake-word/mic-level");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const level = (await res.json()) as MicLevel;
      setMic({ state: "done", level });
      if (!level.no_device && !level.too_quiet && !level.permission_required) cheer("jump");
    } catch {
      setMic({ state: "failed" });
    }
  }

  async function saveWake() {
    if (trimmed.length < 2 || !ack || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onb.acknowledgeWakeWord();
      const result = await saveWakeWord({ phrase, engine: "auto", persist: true });
      if (result.degraded) {
        // Nothing can hear this word yet — say so instead of pretending.
        setDegraded(true);
        return;
      }
      const activation = await setWakeActivation(true);
      setChosenName(derived || null);
      report({ summary: phrase, gap: persistGap(activation, null) });
      cheer("jump");
      next();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function continueDegraded() {
    setBusy(true);
    setError(null);
    try {
      const activation = await setWakeActivation(true);
      setChosenName(derived || null);
      report({
        summary: phrase,
        gap: persistGap(activation, fill(t("first_run.voice.gap_degraded"), { phrase })),
      });
      next();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function chooseShortcut() {
    setBusy(true);
    setError(null);
    try {
      const activation = await setWakeActivation(false);
      setChosenName(null);
      report({
        summary: callCombo
          ? fill(t("first_run.voice.summary_shortcut_keys"), { keys: callCombo.toUpperCase() })
          : t("first_run.voice.summary_shortcut"),
        gap: persistGap(activation, null),
      });
      next();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function skipAfterError() {
    report({ summary: null, gap: t("first_run.voice.gap_skipped") });
    skip();
  }

  const micStatus = (() => {
    if (mic.state === "listening") return <Status tone="muted">{t("first_run.voice.mic_listening")}</Status>;
    if (mic.state === "failed") return <Status tone="warning">{t("first_run.voice.mic_failed")}</Status>;
    if (mic.state !== "done") return null;
    const l = mic.level;
    if (l.permission_required) return <Status tone="warning">{t("first_run.voice.mic_permission")}</Status>;
    if (l.no_device) return <Status tone="muted">{t("first_run.voice.mic_no_device")}</Status>;
    if (l.too_quiet) return <Status tone="warning">{t("first_run.voice.mic_quiet")}</Status>;
    return <Status tone="ok">{t("first_run.voice.mic_good")}</Status>;
  })();

  const level = mic.state === "done" ? meterFill(mic.level.max_dbfs) : 0;
  const lit = Math.round(level * METER_SEGMENTS);

  return (
    <div className="space-y-5">
      <Rise index={0}>
        <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={t("first_run.voice.title")}>
          <Option
            selected={mode === "wake"}
            onSelect={() => setMode("wake")}
            title={t("first_run.voice.wake_title")}
            body={t("first_run.voice.wake_body")}
            testId="wake-mode-wake"
          />
          <Option
            selected={mode === "shortcut"}
            onSelect={() => setMode("shortcut")}
            title={t("first_run.voice.shortcut_title")}
            body={
              callCombo ? (
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  {t("first_run.voice.shortcut_body_keys")} <Keycaps combo={callCombo} />
                </span>
              ) : (
                t("first_run.voice.shortcut_body")
              )
            }
            testId="wake-mode-shortcut"
          />
        </div>
      </Rise>

      {mode === "wake" && (
        <Rise index={1} className="space-y-3">
          <div className="flex items-center gap-2 rounded-xl border border-border bg-background p-2 focus-within:border-accent">
            <span className="rounded-lg bg-secondary px-3 py-1.5 text-base font-medium text-foreground">
              {t("first_run.voice.prefix")}
            </span>
            <input
              type="text"
              value={word}
              maxLength={56}
              onChange={(e) => edit(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void saveWake();
              }}
              aria-label={t("first_run.voice.word_label")}
              placeholder={t("first_run.voice.placeholder")}
              className="min-w-0 flex-1 bg-transparent px-1 text-lg text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
              data-testid="wake-word-input"
            />
          </div>
          <p className="min-h-5 text-sm text-muted-foreground" aria-live="polite" data-testid="wake-derived-name">
            {derived ? (
              <>
                {t("first_run.voice.name_intro")}{" "}
                <span className="rounded-md bg-accent-soft px-1.5 py-0.5 font-medium text-accent">{derived}</span>
              </>
            ) : (
              t("first_run.voice.name_hint")
            )}
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("first_run.voice.trademark")}{" "}
            <button
              type="button"
              onClick={() => setShowRefs((v) => !v)}
              className={cn("rounded-sm text-accent underline-offset-4 hover:underline", FOCUS_RING)}
            >
              {t("first_run.voice.how_to_check")}
            </button>
          </p>
          {showRefs && refs.length > 0 && (
            <ul className="space-y-0.5 rounded-lg border border-border bg-background px-3 py-2 text-xs">
              {refs.map((r) => (
                <li key={r.url}>
                  <a href={r.url} target="_blank" rel="noreferrer" className="text-accent underline-offset-4 hover:underline">
                    {r.label}
                  </a>
                </li>
              ))}
              <li className="pt-1 text-muted-foreground">{t("first_run.voice.refs_caveat")}</li>
            </ul>
          )}
          <CheckLine checked={ack} onChange={setAck} testId="wake-ack">
            {t("first_run.voice.ack")}
          </CheckLine>
        </Rise>
      )}

      <Rise index={2}>
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-background px-4 py-3">
          <button
            type="button"
            onClick={() => void checkMic()}
            disabled={mic.state === "listening"}
            data-testid="wake-mic-test"
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-lg border border-border-strong bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-60",
              FOCUS_RING,
            )}
          >
            <Mic aria-hidden className={cn("h-3.5 w-3.5", mic.state === "listening" && "animate-pulse text-accent")} />
            {t("first_run.voice.mic_test")}
          </button>
          <div className="flex h-3 flex-1 items-end gap-[3px]" aria-hidden data-testid="wake-mic-meter">
            {Array.from({ length: METER_SEGMENTS }, (_, i) => (
              <span
                key={i}
                className={cn(
                  "flex-1 rounded-[2px] transition-colors duration-200",
                  mic.state === "listening"
                    ? "animate-pulse bg-border-strong"
                    : i < lit
                      ? "bg-accent"
                      : "bg-border",
                )}
                style={{ height: `${40 + (i / METER_SEGMENTS) * 60}%`, animationDelay: `${i * 45}ms` }}
              />
            ))}
          </div>
          <div className="w-full">{micStatus}</div>
        </div>
      </Rise>

      {error && (
        <div className="space-y-2">
          <Status tone="error">{error}</Status>
          <QuietAction onClick={skipAfterError} testId="wake-skip-after-error">
            {t("first_run.voice.skip_after_error")}
          </QuietAction>
        </div>
      )}

      {degraded && (
        <div className="space-y-2" data-testid="wake-degraded">
          <Status tone="warning">{t("settings_view.wake_word.needs_whisper_hint")}</Status>
          {install.state === "running" && <Status tone="muted">{t("settings_view.wake_word.enable_local_installing")}</Status>}
          {install.state === "done" && <Status tone="ok">{t("settings_view.wake_word.enable_local_done")}</Status>}
          {install.state === "error" && <Status tone="warning">{t("settings_view.wake_word.enable_local_error")}</Status>}
        </div>
      )}

      <Rise index={3} className="space-y-3">
        {mode === "shortcut" ? (
          <PrimaryAction onClick={() => void chooseShortcut()} busy={busy}>
            {t("first_run.continue")}
          </PrimaryAction>
        ) : degraded ? (
          <>
            {install.state !== "done" && (
              <PrimaryAction onClick={() => void startInstall()} busy={install.state === "running"} testId="wake-install-local">
                {install.state === "error"
                  ? t("settings_view.wake_word.enable_local_retry")
                  : t("settings_view.wake_word.enable_local_button")}
              </PrimaryAction>
            )}
            {install.state === "done" ? (
              <PrimaryAction onClick={() => void saveWake()} busy={busy}>
                {t("first_run.voice.save")}
              </PrimaryAction>
            ) : (
              <div className="text-center">
                <QuietAction onClick={() => void continueDegraded()} disabled={busy} testId="wake-continue-anyway">
                  {t("first_run.voice.continue_anyway")}
                </QuietAction>
              </div>
            )}
          </>
        ) : (
          <PrimaryAction onClick={() => void saveWake()} disabled={trimmed.length < 2 || !ack} busy={busy}>
            {t("first_run.voice.save")}
          </PrimaryAction>
        )}
      </Rise>
    </div>
  );
}

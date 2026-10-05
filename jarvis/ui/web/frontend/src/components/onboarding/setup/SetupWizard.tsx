/**
 * The setup window: one centred dialog over the dimmed app with three steps
 * — the assistant's name, connecting an AI, and how the user talks to it.
 *
 * A row of numbered step pills sits under the header; finished steps can be
 * clicked to go back. Each step is a large title, one plain sentence and the
 * step's own rows, with the way forward at the bottom right. Nothing in the
 * window blocks: every step past the name can be left for later.
 */
import { ArrowLeft, ArrowRight, Check, Loader2 } from "lucide-react";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useAutostart } from "@/hooks/useAutostart";
import { useKeybinds } from "@/hooks/useHotkey";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, setUiLanguage, useT, useUiLanguage, type UiLanguage } from "@/i18n";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { GuidePetFigure } from "../pet/GuidePet";
import { TOUR_LAYER_ATTR } from "../tourEvents";
import { QuietAction } from "../ui";
import { ConnectStep } from "./ConnectStep";
import { WIZARD_STEP_IDS, type WizardStepId } from "./setupSteps";

const LANGS: UiLanguage[] = ["en", "de", "es"];

export function SetupWizard({
  step,
  preview,
  onStep,
  onSkip,
  onFinish,
}: {
  step: WizardStepId;
  /** A replay from Settings: nothing restarts at the end. */
  preview: boolean;
  /** Move to another step of the window (Back, a finished pill, Continue). */
  onStep: (target: WizardStepId) => void;
  /** Continue with nothing done on this step — recorded as skipped. */
  onSkip: (target: WizardStepId) => void;
  /** The last step's button: on to the walk through the app. */
  onFinish: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const index = WIZARD_STEP_IDS.indexOf(step);
  const next = WIZARD_STEP_IDS[index + 1];
  const prev = index > 0 ? WIZARD_STEP_IDS[index - 1] : null;

  return createPortal(
    <div
      {...{ [TOUR_LAYER_ATTR]: "" }}
      className="fixed inset-0 z-[110] grid place-items-center overflow-y-auto bg-black/50 p-4 backdrop-blur-[2px]"
      data-testid="setup-window-backdrop"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="setup-card"
        data-step={step}
        className="flex max-h-[calc(100vh-32px)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-float"
      >
        <header className="shrink-0 space-y-4 px-6 pb-4 pt-5">
          <div className="flex items-center gap-2.5">
            <GuidePetFigure state="success" px={34} />
            <h1 id={titleId} className="text-lg font-semibold tracking-tight text-foreground">
              {t("first_run.window.title")}
            </h1>
            <LanguageSwitch className="ml-auto" />
          </div>
          <StepPills current={index} onPick={(i) => onStep(WIZARD_STEP_IDS[i])} />
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto border-t border-border bg-background/60 px-6 py-6">
          {step === "name" && <NameStep onContinue={() => onStep("connect")} />}
          {step === "connect" && (
            <ConnectStepFrame
              onBack={() => prev && onStep(prev)}
              onContinue={(anything) => (anything ? onStep(next ?? "voice") : onSkip(next ?? "voice"))}
            />
          )}
          {step === "voice" && <VoiceStep preview={preview} onBack={() => prev && onStep(prev)} onFinish={onFinish} />}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ---------------------------------------------------------------- framing */

function StepPills({ current, onPick }: { current: number; onPick: (index: number) => void }) {
  const t = useT();
  return (
    <ol
      className="grid auto-cols-fr grid-flow-col gap-1 rounded-xl bg-secondary p-1 ring-1 ring-border"
      aria-label={t("first_run.window.progress")}
      data-testid="setup-steps"
    >
      {WIZARD_STEP_IDS.map((id, i) => {
        const done = i < current;
        const here = i === current;
        return (
          <li key={id} className="min-w-0">
            <button
              type="button"
              disabled={!done}
              onClick={() => onPick(i)}
              aria-current={here ? "step" : undefined}
              data-testid={`setup-pill-${id}`}
              className={cn(
                "flex w-full min-w-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring max-sm:justify-center",
                done && "cursor-pointer hover:bg-popover",
                !done && "cursor-default",
                here && "bg-popover shadow-sm ring-1 ring-border",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "grid h-5 w-5 shrink-0 place-items-center rounded-full text-xs font-medium ring-1",
                  done
                    ? "bg-accent text-accent-foreground ring-accent"
                    : here
                      ? "bg-accent-soft text-accent ring-accent"
                      : "bg-popover text-muted-foreground ring-border",
                )}
              >
                {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span
                className={cn(
                  "min-w-0 truncate text-sm font-medium max-sm:hidden",
                  here ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {t(`first_run.${id}.pill`)}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function LanguageSwitch({ className }: { className?: string }) {
  const t = useT();
  const lang = useUiLanguage();
  return (
    <div
      className={cn("flex items-center gap-0.5 rounded-full border border-border p-0.5", className)}
      role="radiogroup"
      aria-label={t("first_run.window.language")}
    >
      {LANGS.map((code) => (
        <button
          key={code}
          type="button"
          role="radio"
          aria-checked={lang === code}
          onClick={() => setUiLanguage(code)}
          data-testid={`onboarding-lang-${code}`}
          className={cn(
            "rounded-full px-2.5 py-0.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            lang === code ? "bg-secondary font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {code.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

function StepHead({ title, lede }: { title: string; lede: string }) {
  return (
    <>
      <h2 className="text-2xl font-semibold tracking-tight text-foreground" data-testid="setup-step-title">
        {title}
      </h2>
      <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{lede}</p>
    </>
  );
}

function StepFooter({ left, children }: { left?: ReactNode; children: ReactNode }) {
  return (
    <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
      <div>{left}</div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

function BackAction({ onClick }: { onClick: () => void }) {
  const t = useT();
  return (
    <QuietAction onClick={onClick} className="inline-flex items-center gap-1 text-sm" testId="setup-back">
      <ArrowLeft aria-hidden className="h-3.5 w-3.5" />
      {t("first_run.back")}
    </QuietAction>
  );
}

/* ------------------------------------------------------------------ steps */

/**
 * "What would you like to call me?" The name is the wake word: saving it
 * renames the assistant everywhere (bylines, the voice, the sidebar).
 */
function NameStep({ onContinue }: { onContinue: () => void }) {
  const t = useT();
  const { config, saveWakeWord } = useWakeWord();
  const saved = deriveAssistantName(config?.phrase ?? "");
  const [typed, setTyped] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const value = typed ?? saved;
  const name = deriveAssistantName(value);

  async function submit() {
    if (!name) return;
    if (name !== saved) {
      setSaving(true);
      setError(null);
      try {
        // A custom wake model belongs to the old word; a new word starts on auto.
        const engine = config?.engine && config.engine !== "custom_onnx" ? config.engine : "auto";
        await saveWakeWord({ phrase: value.trim(), engine });
      } catch (e) {
        setError((e as Error).message);
        setSaving(false);
        return;
      }
      setSaving(false);
    }
    onContinue();
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <StepHead title={t("first_run.name.title")} lede={t("first_run.name.lede")} />
      <input
        autoFocus
        value={value}
        onChange={(event) => setTyped(event.target.value)}
        placeholder={t("first_run.name.placeholder")}
        aria-label={t("first_run.name.label")}
        maxLength={40}
        spellCheck={false}
        autoComplete="off"
        data-testid="setup-name-input"
        className="mt-5 h-12 w-full rounded-lg border border-border-strong bg-input px-4 text-lg text-foreground placeholder:text-foreground-faint focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
      <StepFooter>
        <Button type="submit" disabled={!name || saving} data-testid="onboarding-primary">
          {saving && <Loader2 aria-hidden className="animate-spin" />}
          {t("first_run.continue")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </form>
  );
}

function ConnectStepFrame({ onBack, onContinue }: { onBack: () => void; onContinue: (anything: boolean) => void }) {
  const t = useT();
  const [anything, setAnything] = useState(false);
  const onConnectedChange = useCallback((any: boolean) => setAnything(any), []);
  return (
    <>
      <StepHead title={t("first_run.connect.title")} lede={t("first_run.connect.lede")} />
      <div className="mt-5">
        <ConnectStep onConnectedChange={onConnectedChange} />
      </div>
      {!anything && (
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground" data-testid="setup-connect-nothing">
          {t("first_run.connect.nothing_yet")}
        </p>
      )}
      <StepFooter left={<BackAction onClick={onBack} />}>
        <Button
          variant={anything ? "default" : "outline"}
          onClick={() => onContinue(anything)}
          data-testid="onboarding-primary"
        >
          {anything ? t("first_run.continue") : t("first_run.connect.later")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </>
  );
}

/** One line of the voice step's list: a label, one plain sentence, a control. */
function SettingRow({
  title,
  detail,
  control,
  testId,
}: {
  title: string;
  detail: string;
  control: ReactNode;
  testId?: string;
}) {
  return (
    <div className="flex items-center gap-4 px-4 py-3.5" data-testid={testId}>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{detail}</p>
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

/** A key combination as keycaps: "ctrl+alt+j" → Ctrl + Alt + J. */
function Keycaps({ combo }: { combo: string }) {
  const keys = combo.split("+").map((k) => k.trim()).filter(Boolean);
  return (
    <span className="inline-flex items-center gap-1" data-testid="setup-voice-keys">
      {keys.map((key, i) => (
        <kbd
          key={`${key}-${i}`}
          className="min-w-6 rounded-md border border-border-strong bg-secondary px-1.5 py-0.5 text-center font-mono text-xs text-foreground shadow-[inset_0_-1px_0_hsl(var(--border-strong))]"
        >
          {key.length <= 2 ? key.toUpperCase() : key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ")}
        </kbd>
      ))}
    </span>
  );
}

/**
 * How the user talks to the assistant: the wake word heard all the time (or
 * the Call shortcut), and the app starting at login so it is there to hear.
 */
function VoiceStep({ preview, onBack, onFinish }: { preview: boolean; onBack: () => void; onFinish: () => void }) {
  const t = useT();
  const { config, setWakeActivation } = useWakeWord();
  const autostart = useAutostart();
  const { config: keybinds } = useKeybinds();
  const callCombo = keybinds?.keybinds?.call?.trim() ?? "";
  const assistantName = useEventStore((s) => s.assistantName);
  const [listening, setListening] = useState<boolean | null>(null);
  const [wakeNote, setWakeNote] = useState<string | null>(null);
  const [startAtLogin, setStartAtLogin] = useState<boolean | null>(null);
  const phrase = config?.phrase.trim() ?? "";
  const name = deriveAssistantName(phrase) || assistantName;
  const on = listening ?? Boolean(config?.enabled && phrase);
  const loginOn = startAtLogin ?? Boolean(autostart.config?.enabled);

  useEffect(() => {
    if (config) setListening(null);
  }, [config]);

  async function toggleListening(next: boolean) {
    setListening(next);
    setWakeNote(null);
    try {
      const result = await setWakeActivation(next);
      if (!result.persisted && result.message) setWakeNote(result.message);
      else if (result.restart_required) setWakeNote(t("first_run.voice.after_restart"));
    } catch (e) {
      setListening(!next);
      setWakeNote((e as Error).message);
    }
  }

  async function toggleLogin(next: boolean) {
    setStartAtLogin(next);
    try {
      await autostart.setEnabled(next);
    } catch {
      setStartAtLogin(!next);
    }
  }

  return (
    <>
      <StepHead title={t("first_run.voice.title")} lede={t("first_run.voice.lede")} />
      <div className="mt-5 divide-y divide-border overflow-hidden rounded-lg border border-border bg-popover">
        <SettingRow
          testId="setup-voice-wake"
          title={phrase ? fill(t("first_run.voice.wake_title"), { phrase: `Hey ${name}` }) : t("first_run.voice.no_word")}
          detail={on ? t("first_run.voice.wake_on") : t("first_run.voice.wake_off")}
          control={
            <Switch
              checked={on}
              disabled={!phrase}
              onCheckedChange={(v) => void toggleListening(v)}
              aria-label={t("first_run.voice.wake_switch")}
              data-testid="setup-voice-wake-switch"
            />
          }
        />
        {callCombo && (
          <SettingRow
            testId="setup-voice-call"
            title={t("first_run.voice.call_title")}
            detail={t("first_run.voice.call_detail")}
            control={<Keycaps combo={callCombo} />}
          />
        )}
        {autostart.config?.supported && (
          <SettingRow
            testId="setup-voice-login"
            title={t("first_run.voice.login_title")}
            detail={t("first_run.voice.login_detail")}
            control={
              <Switch
                checked={loginOn}
                onCheckedChange={(v) => void toggleLogin(v)}
                aria-label={t("first_run.voice.login_title")}
                data-testid="onboarding-autostart"
              />
            }
          />
        )}
      </div>
      {wakeNote && <p className="mt-2 px-1 text-xs text-muted-foreground">{wakeNote}</p>}
      <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
        {preview ? t("first_run.voice.preview_note") : t("first_run.voice.restart_note")}
      </p>
      <StepFooter left={<BackAction onClick={onBack} />}>
        <Button onClick={onFinish} data-testid="onboarding-start">
          {t("first_run.voice.start")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </>
  );
}

import { useCallback, type KeyboardEvent, type MouseEvent } from "react";
import { Loader2, PhoneOff, Sparkles } from "lucide-react";

import { useEventStore, type VoiceState } from "@/store/events";
import type { WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { StageWaveform } from "@/components/home/StageWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { usePromptMode } from "@/hooks/usePromptMode";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The Jarvis bar — the front page's one voice control, drawn as a card in
 * the chat composer's language (components/ChatInput.tsx: rounded-2xl,
 * bg-card, a hairline border, the same soft shadow) so the two surfaces
 * read as siblings.
 *
 * Top row: the waveform, filling the card's width (components/home/
 * StageWaveform — breathing at rest, the microphone while listening).
 * Bottom row: the state (dot + word) and, in the same breath, what to do
 * or what is happening ("Say “Hey George” or tap to start", "Listening…")
 * on the left; on the right the Prompt Mode pill — lit while every dictation
 * comes out as a finished prompt for a coding agent, one click flips it
 * (maintainer, 2026-08-27) — then the engine pill — provider and model,
 * click to change it — and, only while a conversation is running, an End
 * button. The whole card is the start/stop control (maintainer, 2026-08-23:
 * no microphone button — you tap the bar or say the wake word); the pills
 * stop the click from reaching the card so changing the model or flipping
 * Prompt Mode never starts a call by accident.
 *
 * A `div` with the button role rather than a `<button>`: a button may not
 * contain the two inner buttons, and nesting them is how a screen reader
 * would announce one control three times.
 */
export function JarvisBar({ phase, hint }: { phase: WaveformPhase; hint: string }) {
  const t = useT();
  const voiceState = useEventStore((s) => s.voiceState);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const pushToast = useEventStore((s) => s.pushToast);
  const { connected } = useVoiceReadiness();
  const engine = useVoiceEngineDisplay();
  const promptMode = usePromptMode();
  const { active: callActive, busy: callBusy, connecting, toggleCall } = useVoiceCall();

  const disabled = callBusy || connecting || !connected;
  const label = callActive ? t("home.bar_stop") : t("home.bar_start");
  const stateWord = t(`voice_state.${stateKey(voiceState, connecting, connected)}`);

  const onCardClick = useCallback(() => {
    if (disabled) return;
    void toggleCall();
  }, [disabled, toggleCall]);

  const onCardKey = useCallback(
    (ev: KeyboardEvent<HTMLDivElement>) => {
      if (ev.target !== ev.currentTarget) return; // a pill has its own keys
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        onCardClick();
      }
    },
    [onCardClick],
  );

  const onEngineClick = useCallback(
    (ev: MouseEvent<HTMLButtonElement>) => {
      ev.stopPropagation();
      setActiveSection("apikeys");
    },
    [setActiveSection],
  );

  const onEndClick = useCallback(
    (ev: MouseEvent<HTMLButtonElement>) => {
      ev.stopPropagation();
      if (callBusy) return;
      void toggleCall();
    },
    [callBusy, toggleCall],
  );

  const onPromptModeClick = useCallback(
    (ev: MouseEvent<HTMLButtonElement>) => {
      ev.stopPropagation();
      promptMode.toggle().catch((err: unknown) => {
        // The backend's own sentence names the blocker (a config file that
        // could not be written); ours would send the user to the wrong place.
        const detail = err instanceof Error && err.message ? err.message : "";
        pushToast("error", detail || t("home.prompt_mode_failed"));
      });
    },
    [promptMode, pushToast, t],
  );

  const promptModeTitle =
    promptMode.enabled === true ? t("home.prompt_mode_on") : t("home.prompt_mode_off");

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled || undefined}
      aria-label={label}
      title={label}
      onClick={onCardClick}
      onKeyDown={onCardKey}
      data-testid="jarvis-bar"
      data-phase={phase}
      data-active={callActive || undefined}
      className={cn(
        "group flex w-full cursor-pointer select-none flex-col gap-2 rounded-2xl border border-border bg-card px-4 pb-2.5 pt-4 text-left",
        "shadow-[0_1px_2px_rgb(var(--scrim-rgb)/0.05),0_8px_24px_rgb(var(--scrim-rgb)/0.06)] transition-[border-color,box-shadow]",
        "hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "aria-disabled:cursor-default aria-disabled:opacity-80 aria-disabled:hover:border-border",
        callActive && "border-primary/50",
      )}
    >
      <div className="h-14 w-full">
        <StageWaveform levelRef={voiceInputLevelRef} phase={phase} />
      </div>
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "flex min-w-0 items-center gap-2 px-1 font-mono text-[10px] uppercase tracking-[0.16em]",
            callActive ? "text-primary" : "text-muted-foreground",
          )}
          data-testid="jarvis-bar-state"
        >
          <span
            aria-hidden
            className={cn(
              "h-2 w-2 shrink-0 rounded-full",
              voiceState === "error"
                ? "bg-destructive"
                : callActive
                  ? "bg-primary animate-jarvis-pulse"
                  : "bg-muted-foreground/40",
            )}
          />
          <span className="shrink-0">{stateWord}</span>
        </span>
        <span
          className="min-w-0 flex-1 truncate text-xs text-muted-foreground"
          data-testid="voice-hint"
        >
          {hint}
        </span>
        {promptMode.enabled !== null && (
          <button
            type="button"
            onClick={onPromptModeClick}
            disabled={promptMode.busy}
            aria-pressed={promptMode.enabled}
            aria-label={promptModeTitle}
            title={promptModeTitle}
            data-testid="jarvis-bar-prompt-mode"
            data-on={promptMode.enabled || undefined}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
              promptMode.enabled
                ? "border-primary/40 bg-primary/10 text-primary hover:bg-primary/15"
                : "border-transparent text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {promptMode.busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="h-3.5 w-3.5" aria-hidden />
            )}
            <span className="hidden font-medium sm:inline">{t("home.prompt_mode_pill")}</span>
            {promptMode.enabled && (
              <span
                aria-hidden
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary animate-jarvis-pulse"
              />
            )}
          </button>
        )}
        <button
          type="button"
          onClick={onEngineClick}
          title={t("home.model_hint")}
          data-testid="jarvis-bar-engine"
          className="inline-flex h-8 max-w-[280px] items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="truncate font-medium text-foreground">{engine.providerLabel}</span>
          {engine.model && (
            <span className="hidden truncate font-mono text-[10px] text-muted-foreground sm:inline">
              {engine.model}
            </span>
          )}
        </button>
        {callActive && (
          <button
            type="button"
            onClick={onEndClick}
            disabled={callBusy}
            aria-label={t("home.bar_end")}
            title={t("home.bar_end")}
            data-testid="jarvis-bar-end"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/20 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <PhoneOff className="h-3.5 w-3.5" aria-hidden />
            {t("home.bar_end")}
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Which `voice_state.*` word the bar shows. Offline wins over whatever the
 * store last heard; the call being negotiated wins over the stale state
 * before it.
 */
export function stateKey(state: VoiceState, connecting: boolean, connected: boolean): string {
  if (!connected) return "offline";
  if (connecting) return "connecting";
  return state;
}

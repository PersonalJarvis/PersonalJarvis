import { useCallback } from "react";
import { Keyboard, Loader2, Mic, Sparkles } from "lucide-react";

import { useEventStore } from "@/store/events";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { usePromptMode } from "@/hooks/usePromptMode";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Voice mode's composer — the chat composer's card, spoken instead of typed
 * (2026-10-01).
 *
 * Same shape as the front page's typed composer (components/agentchat/
 * AgentComposer, minimal): one rounded row. Where the text would be, the
 * line says what is happening ("Listening…", "Speaking…") or what to do; on
 * the right sit Prompt Mode and the one round control — Start while no call
 * runs, a lit "Stop" with three breathing dots while one does. The way back
 * to the keyboard takes the "+" slot on the left, and only where the page
 * that hosts the stage has a typed half to go back to. Under the card, the
 * voice engine in small print, the way the chat names its model; a click
 * opens the provider settings.
 *
 * Start and Stop go through the one start/stop path every voice surface
 * shares (useVoiceCall), so there is still exactly one way a call begins.
 */
export function VoiceComposer({ hint, onExit }: { hint: string; onExit?: () => void }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const pushToast = useEventStore((s) => s.pushToast);
  const { connected } = useVoiceReadiness();
  const engine = useVoiceEngineDisplay();
  const promptMode = usePromptMode();
  const { active, busy, connecting, toggleCall } = useVoiceCall();

  const onCall = useCallback(() => {
    if (busy || connecting) return;
    void toggleCall();
  }, [busy, connecting, toggleCall]);

  const onPromptMode = useCallback(() => {
    promptMode.toggle().catch((err: unknown) => {
      // The backend's own sentence names the blocker (a config file that
      // could not be written); ours would send the user to the wrong place.
      const detail = err instanceof Error && err.message ? err.message : "";
      pushToast("error", detail || t("home.prompt_mode_failed"));
    });
  }, [promptMode, pushToast, t]);

  const promptModeTitle =
    promptMode.enabled === true ? t("home.prompt_mode_on") : t("home.prompt_mode_off");
  const live = active || connecting;

  return (
    <div className="flex flex-col gap-1.5" data-testid="voice-composer" data-active={active || undefined}>
      <div
        data-tour="voice-bar"
        className="flex items-center gap-1 rounded-[22px] border border-border bg-card px-2.5 py-2.5 shadow-rim">
        {onExit && (
          <button
            type="button"
            onClick={onExit}
            data-testid="voice-mode-exit"
            aria-label={t("assistant_chat.voice_exit")}
            title={fill(t("assistant_chat.voice_exit_hint"), { name: assistantName })}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Keyboard className="h-4 w-4" aria-hidden />
          </button>
        )}
        <span
          className="min-w-0 flex-1 truncate px-1.5 text-reading text-muted-foreground"
          data-testid="voice-hint"
          aria-live="polite"
        >
          {hint}
        </span>
        {promptMode.enabled !== null && (
          <button
            type="button"
            onClick={onPromptMode}
            disabled={promptMode.busy}
            aria-pressed={promptMode.enabled}
            aria-label={promptModeTitle}
            title={promptModeTitle}
            data-testid="voice-prompt-mode"
            data-on={promptMode.enabled || undefined}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full text-xs font-medium transition-colors disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              promptMode.enabled
                ? "bg-accent-soft px-3 text-accent"
                : "w-8 justify-center text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {promptMode.busy ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="h-4 w-4" aria-hidden />
            )}
            {promptMode.enabled && t("home.prompt_mode_pill")}
          </button>
        )}
        {live ? (
          <button
            type="button"
            onClick={onCall}
            disabled={busy || connecting}
            aria-label={t("home.bar_stop")}
            title={t("home.bar_stop")}
            data-testid="voice-call-stop"
            className="ml-0.5 inline-flex h-8 shrink-0 items-center gap-2 rounded-full bg-accent-soft px-3 text-sm font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <BreathingDots />
            {t("assistant_chat.voice_stop")}
          </button>
        ) : (
          <button
            type="button"
            onClick={onCall}
            disabled={busy || !connected}
            aria-label={t("home.bar_start")}
            title={t("home.bar_start")}
            data-testid="voice-call-start"
            className="ml-0.5 inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-foreground px-3 text-sm font-medium text-background transition-colors hover:bg-foreground/85 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Mic className="h-4 w-4" aria-hidden />
            )}
            {t("assistant_chat.voice_start")}
          </button>
        )}
      </div>
      <div className="flex justify-end px-3">
        <button
          type="button"
          onClick={() => setActiveSection("apikeys")}
          title={t("home.model_hint")}
          data-testid="voice-engine"
          className="inline-flex max-w-[320px] items-center gap-1.5 rounded-md px-1 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="truncate font-medium text-foreground/80">{engine.providerLabel}</span>
          {engine.model && <span className="truncate">{engine.model}</span>}
        </button>
      </div>
    </div>
  );
}

/** Three dots breathing one after another — a call is open. */
function BreathingDots() {
  return (
    <span className="inline-flex items-center gap-[3px]" aria-hidden>
      {[0, 200, 400].map((delay) => (
        <span
          key={delay}
          className="h-1.5 w-1.5 rounded-full bg-current animate-pulse motion-reduce:animate-none"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}

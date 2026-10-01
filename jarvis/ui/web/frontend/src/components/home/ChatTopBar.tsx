import { AudioLines, Keyboard, Loader2, PanelRightClose, PanelRightOpen } from "lucide-react";

import { GigiMark } from "@/components/GigiMark";
import { TONE_DOT, useAssistantStatus, useVoiceModeSwitch } from "@/components/home/assistantStatus";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * The front page's top bar: who you are talking to, what they are doing, and
 * the one button that turns the chat into a spoken conversation.
 *
 * Left, the assistant as a pill — face, name, status dot; clicking it shows
 * or hides the assistant card on the right. Centre, a status pill that only
 * appears when there is news (working, listening, speaking, offline): "Ready"
 * is the normal state and earns no space. Right, the voice-mode button —
 * outlined while you type, filled in the signal hue while voice mode is on,
 * with a live ping while the microphone is actually open.
 */
export function ChatTopBar({
  panelOpen,
  onTogglePanel,
}: {
  panelOpen: boolean;
  onTogglePanel: () => void;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const status = useAssistantStatus();
  const voice = useVoiceModeSwitch();
  const news = status.tone !== "ready";

  return (
    <header className="relative flex h-14 shrink-0 items-center gap-2 px-4" data-testid="chat-top-bar">
      <button
        type="button"
        onClick={onTogglePanel}
        title={fill(t(panelOpen ? "assistant_chat.panel_close" : "assistant_chat.panel_open"), { name: assistantName })}
        data-testid="chat-top-assistant"
        className="flex h-9 min-w-0 items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-3.5 text-sm font-semibold text-foreground-strong transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="relative shrink-0">
          <GigiMark size={28} className="rounded-full" />
          <span
            aria-hidden
            className={cn(
              "absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-card",
              TONE_DOT[status.tone],
              status.tone === "live" && "animate-jarvis-pulse",
            )}
          />
        </span>
        <span className="truncate">{assistantName}</span>
      </button>

      {news && (
        <div
          role="status"
          data-testid="chat-top-status"
          data-tone={status.tone}
          className="pointer-events-none absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full bg-secondary px-3 py-1.5 text-xs font-medium text-foreground"
        >
          {status.tone === "busy" || status.tone === "live" ? (
            <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin text-accent motion-reduce:animate-none" />
          ) : (
            <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", TONE_DOT[status.tone])} />
          )}
          <span>{status.label}</span>
        </div>
      )}

      <div className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={voice.toggle}
          aria-pressed={voice.on}
          disabled={voice.busy}
          data-testid="chat-voice-mode"
          title={fill(t(voice.on ? "assistant_chat.voice_exit_hint" : "assistant_chat.voice_enter_hint"), { name: assistantName })}
          className={cn(
            "relative flex h-9 items-center gap-2 rounded-full px-3.5 text-sm font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
            voice.on
              ? "bg-accent text-accent-foreground hover:bg-accent/90"
              : "border border-border bg-card text-foreground hover:bg-secondary",
          )}
        >
          {voice.on ? <Keyboard aria-hidden className="h-4 w-4" /> : <AudioLines aria-hidden className="h-4 w-4" />}
          <span>{t(voice.on ? "assistant_chat.voice_exit" : "assistant_chat.voice_enter")}</span>
          {voice.on && voice.live && (
            <span aria-hidden className="relative ml-0.5 flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-foreground opacity-70 motion-reduce:animate-none" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-foreground" />
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={onTogglePanel}
          aria-pressed={panelOpen}
          data-testid="chat-panel-toggle"
          aria-label={fill(t(panelOpen ? "assistant_chat.panel_close" : "assistant_chat.panel_open"), { name: assistantName })}
          title={fill(t(panelOpen ? "assistant_chat.panel_close" : "assistant_chat.panel_open"), { name: assistantName })}
          className="hidden h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring xl:flex"
        >
          {panelOpen ? <PanelRightClose aria-hidden className="h-4 w-4" /> : <PanelRightOpen aria-hidden className="h-4 w-4" />}
        </button>
      </div>
    </header>
  );
}

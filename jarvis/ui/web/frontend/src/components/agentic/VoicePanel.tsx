/**
 * The voice column of the Agentic IDE — talk to the assistant while the
 * agents work, without reaching for the wake word.
 *
 * One orb, one status line, the live transcript, and a hint. Clicking the orb
 * TOGGLES the conversation: idle it arms a wake-style session (the same
 * `request_voice_session` path the call hotkey and the chats "Speak" button
 * take, so a click can never behave differently from the wake word), active it
 * hangs up. The wake word keeps working in parallel — the orb is the faster
 * hand, not a replacement.
 *
 * The panel reads everything it shows from the event store the voice pipeline
 * already feeds over the WebSocket (`voiceState`, the live transcription), so
 * it costs no polling and is exactly as current as the sidebar's status dot.
 *
 * Collapsible to a slim strip rather than removable: the strip keeps the
 * reopen affordance and a minimal state dot on screen, the same reasoning as
 * the prompt bar's collapsed strip.
 */
import { useCallback, useState } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { useEventStore, type VoiceState } from "@/store/events";
import { requestVoiceCall, requestVoiceHangup } from "@/lib/voiceApi";
import { VoiceOrb } from "./VoiceOrb";

const OPEN_KEY = "jarvis.agenticIde.voicePanelOpen";

function storedOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_KEY) !== "closed";
  } catch {
    return true;
  }
}

function storeOpen(open: boolean): void {
  try {
    window.localStorage.setItem(OPEN_KEY, open ? "open" : "closed");
  } catch {
    /* private mode — the preference just will not survive this session */
  }
}

/** What the line under the orb says, per state. */
const STATUS: Record<VoiceState, string> = {
  idle: "Ready",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking",
  error: "Voice trouble",
};

/** Is a conversation running — the half of the toggle a click would end? */
function isActive(state: VoiceState): boolean {
  return state === "listening" || state === "thinking" || state === "speaking";
}

export function VoicePanel() {
  const voiceState = (useEventStore((s) => s.voiceState) ?? "idle") as VoiceState;
  const transcription = useEventStore((s) => s.transcription) ?? "";
  const assistantName =
    (useEventStore((s) => s.assistantName) ?? "").trim() || "your assistant";
  const pushToast = useEventStore((s) => s.pushToast);

  const [open, setOpen] = useState(storedOpen);
  const [busy, setBusy] = useState(false);

  const active = isActive(voiceState);

  const toggleOpen = useCallback(() => {
    setOpen((current) => {
      storeOpen(!current);
      return !current;
    });
  }, []);

  const toggleCall = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (active) {
        await requestVoiceHangup();
      } else {
        const { armed } = await requestVoiceCall();
        // A refusal is an answer: a session already runs, or activation is
        // blocked (the app window is hidden). Say so instead of a dead click.
        if (!armed) {
          pushToast(
            "warning",
            "Could not start listening — a conversation may already be running.",
          );
        }
      }
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setBusy(false);
    }
  }, [busy, active, pushToast]);

  if (!open) {
    return (
      <aside
        data-testid="voice-panel-collapsed"
        className="flex h-full w-10 shrink-0 flex-col items-center gap-3 border-l border-border py-2"
      >
        <button
          type="button"
          data-testid="voice-panel-open"
          onClick={toggleOpen}
          title={`Open the voice panel — talk to ${assistantName} here`}
          aria-label="Open the voice panel"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        >
          <PanelRightOpen className="h-4 w-4" />
        </button>
        {/* The one-glance state the strip still owes: gold and pulsing while a
            conversation runs, quiet otherwise. */}
        <span
          aria-hidden="true"
          className={cn(
            "h-2.5 w-2.5 rounded-full",
            active ? "animate-pulse bg-primary" : "bg-muted-foreground/40",
          )}
        />
      </aside>
    );
  }

  return (
    <aside
      data-testid="voice-panel"
      className="flex h-full w-72 shrink-0 flex-col border-l border-border"
    >
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Voice
        </span>
        <button
          type="button"
          data-testid="voice-panel-close"
          onClick={toggleOpen}
          title="Collapse the voice panel"
          aria-label="Collapse the voice panel"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        >
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-5 px-5 pb-6">
        <button
          type="button"
          data-testid="voice-orb-button"
          onClick={() => void toggleCall()}
          disabled={busy}
          aria-pressed={active}
          title={
            active
              ? "Hang up the conversation"
              : `Talk to ${assistantName} — or just say the wake word`
          }
          aria-label={active ? "Hang up" : `Talk to ${assistantName}`}
          className={cn(
            "rounded-full outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-ring/60",
            // The glow is the orb's aura, swelling with the conversation.
            active
              ? "shadow-[0_0_80px_-12px_hsl(50_100%_52%/0.55)]"
              : "shadow-[0_0_48px_-16px_hsl(50_100%_52%/0.3)]",
            busy && "opacity-70",
          )}
        >
          <VoiceOrb state={voiceState} size={176} />
        </button>

        <div className="flex flex-col items-center gap-1.5 text-center">
          <span
            data-testid="voice-panel-status"
            className="font-display text-base font-semibold"
          >
            {STATUS[voiceState] ?? STATUS.idle}
          </span>
          <span className="text-xs text-muted-foreground">
            {active
              ? "Click the orb to hang up."
              : `Click the orb — or say the wake word — to talk to ${assistantName}.`}
          </span>
        </div>

        {/* What the microphone is hearing, as it hears it. Shown only while a
            conversation runs — an idle panel repeating the last sentence of a
            finished one would read as a stuck microphone. */}
        {active && transcription && (
          <p
            data-testid="voice-panel-transcript"
            className="line-clamp-3 max-w-full text-center text-sm italic text-muted-foreground"
          >
            “{transcription}”
          </p>
        )}
      </div>
    </aside>
  );
}

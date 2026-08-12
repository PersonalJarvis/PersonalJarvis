/**
 * The floating voice bubble of the Agentic IDE — talk to the assistant while
 * the agents work, without giving up a column of the screen for it.
 *
 * It replaces the old fixed voice side panel: the terminals now own the whole
 * width, and the conversation surface is a bubble the user summons from the
 * toolbar and drags wherever it is least in the way. The position and the
 * open/closed choice are remembered per browser profile.
 *
 * One orb, a control row, the live transcript. Clicking the orb TOGGLES the
 * conversation: idle it arms a wake-style session (the same
 * `request_voice_session` path the call hotkey takes, so a click can never
 * behave differently from the wake word), active it hangs up. The wake word
 * keeps working in parallel — the bubble is the faster hand, not a
 * replacement. The mic button is the same toggle said explicitly; X hangs up
 * and puts the bubble away; the speaker mutes the assistant's voice for this
 * session only (never persisted — a forgotten mute must not survive a
 * restart).
 *
 * ## Why the bubble has no panel around it
 *
 * It used to be a rounded card: a 224 px slab of opaque background sitting on
 * top of the terminal wall for as long as the voice was open. The orb is the
 * only part of it that has to be seen, and the card was covering agents to
 * frame it. So there is no card — the orb, the status, the controls and the
 * update notice each carry their own small surface, and the space between them
 * is the workspace, visible and clickable.
 *
 * That last part is a rule, not an aesthetic: the wrapper is
 * `pointer-events-none` and every real control turns them back on. A
 * transparent-but-solid rectangle would eat clicks meant for the terminal
 * behind it, which is exactly the thing the card was already doing visibly.
 * Anything added here needs `pointer-events-auto` to work, and needs to deserve
 * the clicks it takes away from the pane underneath.
 *
 * Dragging still works anywhere on the orb, the status pill or the transcript —
 * they carry `data-drag-surface`, which is also where the pointer capture goes,
 * because a capture on the `pointer-events-none` wrapper is not delivered.
 *
 * Everything shown is read from the event store the voice pipeline already
 * feeds over the WebSocket (`voiceState`, the live transcription), so the
 * bubble costs no polling of its own and is exactly as current as the
 * sidebar's status dot. Files dropped on the orb are staged for the voice
 * conversation, same contract as the old panel and the native Jarvis Bar.
 */
import {
  type DragEvent,
  Fragment,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Check,
  FilePlus2,
  Mic,
  MicOff,
  Paperclip,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useEventStore, type VoiceState } from "@/store/events";
import {
  fetchTtsVolume,
  requestVoiceHangup,
  setTtsVolume,
} from "@/lib/voiceApi";
import {
  attachToTerminal,
  fetchAllVoiceAttachments,
  removeVoiceAttachment,
  type PaneNotification,
} from "@/lib/agenticIdeApi";
import { playDropConfirm } from "@/lib/sound";
import { useT } from "@/i18n";
import {
  dragCarriesFiles,
  extractPaneDrop,
  isEmptyPayload,
  type PaneDropPayload,
} from "./paneDrop";
import { VoiceOrb } from "./VoiceOrb";
import { VoiceBubbleNotice } from "./VoiceBubbleNotice";
import { useVoiceCall } from "./useVoiceCall";

const OPEN_KEY = "jarvis.agenticIde.voiceBubbleOpen";
const POS_KEY = "jarvis.agenticIde.voiceBubblePos";

/** The bubble's fixed width; the drag clamp keeps this much reachable. */
const BUBBLE_WIDTH_PX = 256;
/** How much of the bubble must stay inside the window after a drag/resize. */
const MIN_VISIBLE_PX = 88;

export function storedVoiceBubbleOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_KEY) === "open";
  } catch {
    return false;
  }
}

export function storeVoiceBubbleOpen(open: boolean): void {
  try {
    window.localStorage.setItem(OPEN_KEY, open ? "open" : "closed");
  } catch {
    /* private mode — the preference just will not survive this session */
  }
}

interface BubblePos {
  x: number;
  y: number;
}

/** Under the toolbar, hugging the right edge — where the old panel lived. */
function defaultPos(): BubblePos {
  return {
    x: Math.max(8, window.innerWidth - BUBBLE_WIDTH_PX - 24),
    y: 88,
  };
}

/** Keep enough of the bubble inside the window to grab it back. */
function clampPos(pos: BubblePos): BubblePos {
  return {
    x: Math.min(
      Math.max(pos.x, MIN_VISIBLE_PX - BUBBLE_WIDTH_PX),
      window.innerWidth - MIN_VISIBLE_PX,
    ),
    y: Math.min(Math.max(pos.y, 0), window.innerHeight - MIN_VISIBLE_PX),
  };
}

function storedPos(): BubblePos | null {
  try {
    const raw = window.localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BubblePos>;
    if (typeof parsed.x !== "number" || typeof parsed.y !== "number") return null;
    return clampPos({ x: parsed.x, y: parsed.y });
  } catch {
    return null;
  }
}

function storePos(pos: BubblePos): void {
  try {
    window.localStorage.setItem(POS_KEY, JSON.stringify(pos));
  } catch {
    /* private mode — the bubble simply opens at the default spot next time */
  }
}

/** Locale keys for the status line, per state. */
const STATUS_KEY: Record<VoiceState, string> = {
  idle: "agentic_grid.voice_bubble.ready",
  // The bubble has no line of its own for a negotiating transport, and the
  // sidebar already spells it out. Borrow the voice-state wording rather than
  // invent a second phrasing for the same moment.
  connecting: "voice_state.connecting",
  listening: "agentic_grid.voice_bubble.listening",
  thinking: "agentic_grid.voice_bubble.thinking",
  speaking: "agentic_grid.voice_bubble.speaking",
  paused: "agentic_grid.voice_bubble.paused",
  error: "agentic_grid.voice_bubble.error",
};

type DropPhase = "idle" | "over" | "reading";

interface DropReceipt {
  batchId: string;
  target: string;
  files: string[];
  reserved: boolean;
}

function format(text: string, ...values: Array<string | number>): string {
  return values.reduce<string>(
    (result, value, index) => result.replace(`{${index}}`, String(value)),
    text,
  );
}

/**
 * One round control of the bubble's action row.
 *
 * Each button is its own small disc with its own background: with no panel
 * behind them, a row of bare glyphs would sit directly on whatever the terminal
 * underneath happens to be printing, and half of them would be unreadable half
 * the time. `pointer-events-auto` is per button, not on the row — the gaps
 * between them belong to the pane below.
 */
const BUBBLE_BTN =
  "pointer-events-auto flex h-8 w-8 items-center justify-center rounded-full " +
  "border border-border/50 bg-background/85 text-muted-foreground shadow-lg " +
  "backdrop-blur transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-primary/50 disabled:cursor-not-allowed disabled:opacity-40";

/** The small floating surfaces (status, transcript, receipts) share this look. */
const BUBBLE_SURFACE =
  "pointer-events-auto border border-border/50 bg-background/85 shadow-lg backdrop-blur";

export function VoiceBubble({
  open,
  onClose,
  promptTarget = "",
  onScreen = true,
  onJumpToPane,
}: {
  open: boolean;
  onClose: () => void;
  promptTarget?: string;
  onScreen?: boolean;
  /**
   * Take the user to the pane an update card names. Optional: without it the
   * card still reports what finished, it just is not a link.
   */
  onJumpToPane?: (workspaceId: string, pane: string) => void;
}) {
  const t = useT();
  const { active, busy, connecting, toggleCall, voiceState } = useVoiceCall();
  const transcription = useEventStore((s) => s.transcription) ?? "";
  // Whether the recognizer has committed to the sentence. Partial text is
  // provisional — the STT may still rewrite it — and the transcript styles the
  // two honestly instead of pretending every intermediate guess is settled.
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal !== false);
  const assistantName =
    (useEventStore((s) => s.assistantName) ?? "").trim() ||
    t("agentic_grid.voice_bubble.assistant_fallback");
  const pushToast = useEventStore((s) => s.pushToast);

  const [dropPhase, setDropPhase] = useState<DropPhase>("idle");
  const [dropTarget, setDropTarget] = useState("");
  const [receipts, setReceipts] = useState<DropReceipt[]>([]);
  const [muted, setMuted] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const prevVolumeRef = useRef(1);

  const mounted = open && onScreen;

  /*
   * ------------------------------------------------------------------- drag
   *
   * The whole bubble is the handle, except the controls (`data-no-drag`): a
   * button that sometimes moves the bubble instead of doing its job is a
   * button nobody trusts. A press only becomes a drag after 4 px of travel,
   * and a drag that actually moved suppresses the click that follows it —
   * otherwise every reposition would also toggle the conversation.
   */
  const [pos, setPos] = useState<BubblePos | null>(null);
  const posRef = useRef<BubblePos | null>(null);
  posRef.current = pos;
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);
  const suppressClickRef = useRef(false);

  // Position is resolved when the bubble appears, not at module load: the
  // stored point may be off screen after a window resize, and the default
  // depends on the window size at the moment of opening.
  useEffect(() => {
    if (!mounted) return;
    setPos((current) => current ?? storedPos() ?? defaultPos());
  }, [mounted]);

  useEffect(() => {
    if (!mounted) return;
    const onResize = () => setPos((p) => (p ? clampPos(p) : p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [mounted]);

  const onDragPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    suppressClickRef.current = false;
    if ((event.target as HTMLElement).closest("[data-no-drag]")) return;
    const origin = posRef.current;
    if (!origin) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: origin.x,
      originY: origin.y,
      moved: false,
    };
    /*
     * Capture on the surface that was actually grabbed, not on the wrapper:
     * the wrapper is `pointer-events-none` so the terminals behind the gaps
     * stay clickable, and a capture target that takes no pointer events never
     * receives the move/up it was captured for. Captured events still bubble
     * up to the wrapper's handlers, so the drag reads exactly as before.
     *
     * Optional-chained: jsdom has no pointer capture, and losing it only means
     * a very fast drag can slip off the bubble mid-gesture.
     */
    const handle =
      (event.target as HTMLElement).closest<HTMLElement>("[data-drag-surface]") ??
      event.currentTarget;
    handle.setPointerCapture?.(event.pointerId);
  }, []);

  const onDragPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || event.pointerId !== drag.pointerId) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    setPos(clampPos({ x: drag.originX + dx, y: drag.originY + dy }));
  }, []);

  const onDragPointerEnd = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || event.pointerId !== drag.pointerId) return;
    dragRef.current = null;
    if (drag.moved) {
      suppressClickRef.current = true;
      const settled = posRef.current;
      if (settled) storePos(settled);
    }
  }, []);

  // ------------------------------------------------------------ attachments
  // The native Jarvis Bar lives in another process, so it cannot update this
  // component's local state when a file lands. Reconcile the authoritative
  // backend queue while the bubble is visible: this both discovers external
  // drops and removes a receipt only after the spoken delivery consumed it.
  useEffect(() => {
    if (!mounted) return;
    let cancelled = false;
    let inFlight = false;
    let refreshQueued = false;
    let timer: number | undefined;
    const reconcile = () => {
      if (cancelled) return;
      if (inFlight) {
        refreshQueued = true;
        return;
      }
      inFlight = true;
      void fetchAllVoiceAttachments()
        .then((response) => {
          if (cancelled) return;
          setReceipts(
            response.batches.map((batch) => ({
              batchId: batch.batch_id,
              target: batch.terminal,
              files: batch.files,
              reserved: batch.reserved,
            })),
          );
        })
        .catch(() => {
          // A transient reconnect must not invent an empty queue. The next
          // interval/focus reconciliation retries without erasing the receipt.
        })
        .finally(() => {
          inFlight = false;
          if (refreshQueued) {
            refreshQueued = false;
            reconcile();
          } else if (!cancelled) {
            timer = window.setTimeout(reconcile, active ? 750 : 1_500);
          }
        });
    };
    reconcile();
    const refreshNow = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      reconcile();
    };
    window.addEventListener("focus", refreshNow);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("focus", refreshNow);
    };
  }, [active, mounted]);

  // A drag may leave the app without sending the orb a final dragleave.
  // Clear only the hover state; reading/ready are real work and must remain.
  useEffect(() => {
    if (dropPhase !== "over") return;
    const clear = () => setDropPhase("idle");
    window.addEventListener("drop", clear);
    window.addEventListener("dragend", clear);
    return () => {
      window.removeEventListener("drop", clear);
      window.removeEventListener("dragend", clear);
    };
  }, [dropPhase]);

  // ------------------------------------------------------------------ voice
  const closeBubble = useCallback(() => {
    // X is the "put the voice away" gesture: a running conversation ends with
    // it. Fired without awaiting — the bubble should leave the screen on the
    // click, and a failed hangup still lands as a toolbar pulse the user can
    // see and act on.
    if (active) {
      requestVoiceHangup().catch((error) =>
        pushToast("error", (error as Error).message),
      );
    }
    onClose();
  }, [active, onClose, pushToast]);

  // The speaker toggle mirrors the master TTS volume so it agrees with the
  // settings slider, but its own writes are session-only (persist=false).
  useEffect(() => {
    if (!mounted) return;
    let cancelled = false;
    fetchTtsVolume()
      .then((state) => {
        if (cancelled) return;
        if (state.volume > 0) prevVolumeRef.current = state.volume;
        setMuted(state.volume === 0);
      })
      .catch(() => {
        // Unknown volume — treat as audible. The first toggle then simply
        // mutes, and any real failure surfaces through the toggle's own toast.
      });
    return () => {
      cancelled = true;
    };
  }, [mounted]);

  // Only a parent that can actually move the grid makes the card a link —
  // otherwise it stays a plain report, because a link that goes nowhere is
  // worse than no link.
  const jumpToNotice = useMemo(
    () =>
      onJumpToPane
        ? (entry: PaneNotification) => onJumpToPane(entry.workspace_id, entry.pane)
        : undefined,
    [onJumpToPane],
  );

  const toggleMute = useCallback(async () => {
    const next = !muted;
    try {
      await setTtsVolume(next ? 0 : prevVolumeRef.current || 1, false);
      setMuted(next);
    } catch (error) {
      pushToast("error", (error as Error).message);
    }
  }, [muted, pushToast]);

  // ------------------------------------------------------------------ files
  const stageFiles = useCallback(
    async (payload: PaneDropPayload, target: string) => {
      if (isEmptyPayload(payload)) {
        pushToast("warning", t("agentic_grid.voice_drop.empty"));
        return;
      }
      if (!target) {
        pushToast("warning", t("agentic_grid.voice_drop.no_target"));
        return;
      }

      setDropTarget(target);
      setDropPhase("reading");
      try {
        const result = await attachToTerminal(target, {
          ...payload,
          analyze: true,
          deliver: false,
          stageForVoice: true,
        });
        if (!result.staged_for_voice || !result.voice_batch_id) {
          pushToast("warning", t("agentic_grid.voice_drop.empty"));
          return;
        }
        setReceipts((current) => [
          ...current,
          {
            batchId: result.voice_batch_id as string,
            target,
            files: result.files,
            reserved: false,
          },
        ]);
        playDropConfirm();
      } catch (error) {
        pushToast("error", (error as Error).message);
      } finally {
        setDropPhase("idle");
        setDropTarget("");
      }
    },
    [pushToast, t],
  );

  const handleDrop = useCallback(
    (event: DragEvent<HTMLButtonElement>) => {
      event.preventDefault();
      event.stopPropagation();
      // Read synchronously: DataTransfer is emptied as soon as this handler
      // yields, so extracting after the first await would produce a ghost drop.
      const payload = extractPaneDrop(event.dataTransfer);
      if (!dragCarriesFiles(event.dataTransfer)) {
        setDropPhase("idle");
        pushToast("warning", t("agentic_grid.voice_drop.empty"));
        return;
      }
      void stageFiles(payload, promptTarget);
    },
    [promptTarget, pushToast, stageFiles, t],
  );

  const removeReceipt = useCallback(
    async (receipt: DropReceipt) => {
      try {
        await removeVoiceAttachment(receipt.target, receipt.batchId);
        setReceipts((current) =>
          current.filter((item) => item.batchId !== receipt.batchId),
        );
      } catch (error) {
        pushToast("error", (error as Error).message);
      }
    },
    [pushToast],
  );

  // ------------------------------------------------------------- transcript
  // The transcript is shown only while a conversation runs and there is text —
  // an idle bubble repeating the last sentence of a finished one would read as
  // a stuck microphone. But a surface that BLINKS off the moment that stops
  // being true reads as a glitch, so the last text is held as a ghost for one
  // short fade-out and only then leaves the DOM. The ghost is a transition,
  // not a memory: after ~250 ms the element is really gone.
  const showTranscript = active && transcription.trim().length > 0;
  const [transcriptGhost, setTranscriptGhost] = useState("");
  useEffect(() => {
    if (showTranscript) {
      // Mirror while visible (a bail-out when unchanged), so the exact frame
      // the transcript is dismissed already has the text to fade out with.
      setTranscriptGhost(transcription);
      return;
    }
    if (!transcriptGhost) return;
    const timer = window.setTimeout(() => setTranscriptGhost(""), 260);
    return () => window.clearTimeout(timer);
  }, [showTranscript, transcription, transcriptGhost]);

  const transcriptText = showTranscript ? transcription : transcriptGhost;
  // Words carry their own spans so a NEW word can fade in while the already
  // printed ones hold perfectly still — the word index keys them, and a word
  // the recognizer revises re-mounts and visibly re-settles.
  const transcriptWords = useMemo(
    () => transcriptText.split(/\s+/).filter(Boolean),
    [transcriptText],
  );

  // The window shows the TAIL of a long sentence, not the head: in a live
  // transcript the newest words are the ones being listened for. An
  // overflow-hidden block is still programmatically scrollable, so pinning
  // scrollTop to the bottom is the whole mechanism — and the soft fade over
  // the cropped top line exists only while something was actually cropped,
  // measured here rather than guessed at in CSS.
  const transcriptWindowRef = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    const el = transcriptWindowRef.current;
    if (!el) return;
    el.dataset.overflowing = el.scrollHeight > el.clientHeight + 1 ? "true" : "false";
    el.scrollTop = el.scrollHeight;
  }, [transcriptText]);

  // After every hook: hooks must run in the same order on every render, so
  // the bubble decides to be invisible down here, never above a hook.
  if (!mounted || !pos) return null;

  /** Nothing is happening and nothing is being dropped — say nothing. */
  const quiet = voiceState === "idle" && dropPhase === "idle";

  return (
    <div
      data-testid="voice-bubble"
      role="dialog"
      aria-label={t("agentic_grid.voice_bubble.button_label")}
      style={{ left: pos.x, top: pos.y }}
      onPointerDown={onDragPointerDown}
      onPointerMove={onDragPointerMove}
      onPointerUp={onDragPointerEnd}
      onPointerCancel={onDragPointerEnd}
      onClickCapture={(event) => {
        // A drag that moved must not also click whatever it started on.
        if (suppressClickRef.current) {
          suppressClickRef.current = false;
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      className={cn(
        // No card: the wrapper is only a column of floating pieces, and it lets
        // every click it does not need through to the terminals underneath.
        "pointer-events-none fixed z-50 flex w-64 cursor-grab select-none",
        "flex-col items-center gap-2",
        "active:cursor-grabbing",
      )}
    >
      <div
        data-testid="voice-orb-stage"
        data-drag-surface
        data-state={voiceState}
        data-drop-state={
          dropPhase === "idle" && receipts.some((receipt) => receipt.reserved)
            ? "using"
            : dropPhase === "idle" && receipts.length > 0
              ? "ready"
              : dropPhase
        }
        className="agentic-voice-orb-stage pointer-events-auto relative grid h-40 w-40 shrink-0 place-items-center"
      >
        <span aria-hidden="true" className="agentic-voice-orb-ring agentic-voice-orb-ring-a" />
        <span aria-hidden="true" className="agentic-voice-orb-ring agentic-voice-orb-ring-b" />
        <button
          type="button"
          data-testid="voice-orb-button"
          onClick={() => void toggleCall()}
          onDragEnter={(event) => {
            event.preventDefault();
            if (dragCarriesFiles(event.dataTransfer)) setDropPhase("over");
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = dragCarriesFiles(event.dataTransfer)
              ? "copy"
              : "none";
          }}
          onDragLeave={(event) => {
            const next = event.relatedTarget as Node | null;
            if (next && event.currentTarget.contains(next)) return;
            if (dropPhase === "over") setDropPhase("idle");
          }}
          onDrop={(event) => void handleDrop(event)}
          disabled={busy || connecting || dropPhase === "reading"}
          aria-pressed={active}
          title={
            active
              ? t("agentic_grid.voice_bubble.hang_up_title")
              : format(t("agentic_grid.voice_bubble.talk_title"), assistantName)
          }
          aria-label={
            active
              ? t("agentic_grid.voice_bubble.hang_up")
              : format(t("agentic_grid.voice_bubble.talk"), assistantName)
          }
          className={cn(
            /*
             * The idle orb is DESATURATED and dimmed, and only a running
             * conversation brings its colour up. The renderer paints the same
             * ivory-to-amber weather either way; what changes is how loudly
             * it is allowed to say so — an indicator that looks identical
             * whether or not anything is happening is decoration.
             */
            "relative z-10 rounded-full outline-none transition-[transform,filter,opacity] duration-700 ease-out",
            "hover:scale-[1.02] active:scale-[0.98] focus-visible:ring-2 focus-visible:ring-primary/50 motion-reduce:transform-none",
            // With no card behind it, the sphere needs its own separation from
            // whatever the pane below is drawing — a cast shadow, not a frame.
            "drop-shadow-[0_10px_28px_rgba(0,0,0,0.5)]",
            active
              ? "[filter:saturate(1)_brightness(1)]"
              : "[filter:saturate(0.45)_brightness(0.72)] hover:[filter:saturate(0.7)_brightness(0.85)]",
            dropPhase === "over" && "scale-[1.04]",
            (busy || connecting || dropPhase === "reading") && "cursor-wait opacity-80",
          )}
        >
          <VoiceOrb state={voiceState} size={132} />
        </button>
      </div>

      {/*
        The status line says something only when there is something to say. An
        idle bubble printing "READY" over a terminal is a label for a state the
        desaturated orb already reads as — so at rest it stays in the DOM for
        screen readers and leaves the screen alone. It is still a drag surface,
        which is why it keeps its element either way.
      */}
      <span
        data-drag-surface
        className={cn(
          quiet
            ? "sr-only"
            : cn(BUBBLE_SURFACE, "flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1"),
        )}
      >
        <span
          aria-hidden="true"
          data-state={voiceState}
          className="agentic-voice-state-dot"
        />
        <span
          data-testid="voice-bubble-status"
          aria-live="polite"
          className={cn(
            "text-[11px] font-medium uppercase tracking-[0.1em] transition-colors",
            active ? "text-primary" : "text-muted-foreground",
          )}
        >
          {dropPhase === "over"
            ? format(
                t("agentic_grid.voice_drop.hover"),
                promptTarget || t("agentic_grid.voice_drop.target_fallback"),
              )
            : dropPhase === "reading"
              ? format(t("agentic_grid.voice_drop.reading"), dropTarget)
              : t(STATUS_KEY[voiceState] ?? STATUS_KEY.idle)}
        </span>
      </span>

      {/* What the microphone is hearing, as it hears it — set like a subtitle
          under the orb. The pill spans the full column so the FRAME holds
          still while words stream into it; only the text moves. Muted while
          the recognizer is still guessing (plus a caret that says "still
          writing"), brightening to foreground the moment the sentence is
          final. Presence is animated both ways: see the ghost hooks above. */}
      {transcriptText && (
        <div
          data-testid="voice-bubble-transcript"
          data-drag-surface
          data-leaving={showTranscript ? undefined : "true"}
          className={cn(
            BUBBLE_SURFACE,
            "agentic-voice-transcript w-full shrink-0 rounded-2xl px-3.5 py-2.5",
          )}
        >
          <p
            ref={transcriptWindowRef}
            className={cn(
              "agentic-voice-transcript-window break-words text-center text-[13px]",
              "font-medium leading-relaxed transition-colors duration-300",
              transcriptionFinal ? "text-foreground/90" : "text-muted-foreground",
            )}
          >
            {transcriptWords.map((word, index) => (
              <Fragment key={`${index}:${word}`}>
                {index > 0 ? " " : null}
                <span className="agentic-voice-transcript-word">{word}</span>
              </Fragment>
            ))}
            {showTranscript && !transcriptionFinal && (
              <span aria-hidden="true" className="agentic-voice-transcript-caret" />
            )}
          </p>
        </div>
      )}

      {receipts.map((receipt) => (
        <div
          key={receipt.batchId}
          data-testid="voice-orb-drop-context"
          aria-live="polite"
          className={cn(
            BUBBLE_SURFACE,
            "flex w-full max-w-full shrink-0 items-start gap-2 rounded-2xl px-2.5 py-2 text-left",
          )}
        >
          <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-primary/15 text-primary">
            <Check className="h-2.5 w-2.5" aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1 text-[11px] leading-relaxed text-muted-foreground">
            <span className="block truncate font-medium text-foreground">
              <FilePlus2
                className="mr-1 inline h-3.5 w-3.5 text-primary"
                aria-hidden="true"
              />
              {receipt.files.join(", ")}
            </span>
            {receipt.reserved
              ? format(
                  t("agentic_grid.voice_drop.using"),
                  receipt.files.length,
                  receipt.target,
                )
              : format(
                  t(
                    receipt.files.length === 1
                      ? "agentic_grid.voice_drop.ready_one"
                      : "agentic_grid.voice_drop.ready_many",
                  ),
                  receipt.files.length,
                  receipt.target,
                )}
          </span>
          <button
            type="button"
            data-no-drag
            disabled={receipt.reserved}
            onClick={() => void removeReceipt(receipt)}
            title={t("agentic_grid.voice_drop.remove")}
            aria-label={t("agentic_grid.voice_drop.remove")}
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-35"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
      ))}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        tabIndex={-1}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          event.currentTarget.value = "";
          void stageFiles({ paths: [], files }, promptTarget);
        }}
      />

      {/* The action row: attach, talk, put away, mute — four equal circles so
          none of them reads as the "main" one; the orb above is the main one.
          The row itself takes no pointer events, so the gaps between the discs
          belong to the terminal behind them. */}
      <div className="pointer-events-none flex shrink-0 items-center gap-2">
        <button
          type="button"
          data-no-drag
          data-testid="voice-bubble-attach"
          disabled={!promptTarget || dropPhase === "reading"}
          onClick={() => fileInputRef.current?.click()}
          title={t("agentic_grid.voice_drop.choose")}
          aria-label={t("agentic_grid.voice_drop.choose")}
          className={BUBBLE_BTN}
        >
          <Paperclip className="h-4 w-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          data-no-drag
          data-testid="voice-bubble-mic"
          disabled={busy || connecting}
          aria-pressed={active}
          onClick={() => void toggleCall()}
          title={
            active
              ? t("agentic_grid.voice_bubble.mic_stop")
              : format(t("agentic_grid.voice_bubble.mic_start"), assistantName)
          }
          aria-label={
            active
              ? t("agentic_grid.voice_bubble.mic_stop")
              : format(t("agentic_grid.voice_bubble.mic_start"), assistantName)
          }
          className={cn(
            BUBBLE_BTN,
            active && "border-primary/50 bg-primary/15 text-primary hover:bg-primary/20 hover:text-primary",
          )}
        >
          {active ? (
            <Mic className="h-4 w-4" aria-hidden="true" />
          ) : (
            <MicOff className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
        <button
          type="button"
          data-no-drag
          data-testid="voice-bubble-close"
          onClick={closeBubble}
          title={t("agentic_grid.voice_bubble.close")}
          aria-label={t("agentic_grid.voice_bubble.close")}
          className={cn(BUBBLE_BTN, "hover:bg-destructive/15 hover:text-destructive")}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          data-no-drag
          data-testid="voice-bubble-speaker"
          aria-pressed={muted}
          onClick={() => void toggleMute()}
          title={
            muted
              ? format(t("agentic_grid.voice_bubble.unmute"), assistantName)
              : format(t("agentic_grid.voice_bubble.mute"), assistantName)
          }
          aria-label={
            muted
              ? format(t("agentic_grid.voice_bubble.unmute"), assistantName)
              : format(t("agentic_grid.voice_bubble.mute"), assistantName)
          }
          className={cn(
            BUBBLE_BTN,
            muted && "border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/15 hover:text-destructive",
          )}
        >
          {muted ? (
            <VolumeX className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Volume2 className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </div>

      {/* What finished while the user was talking — one card, newest unread
          only. It sits below the controls so it can grow without ever pushing
          the orb or the buttons somewhere else. */}
      <VoiceBubbleNotice onJump={jumpToNotice} />
    </div>
  );
}

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
import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  FilePlus2,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useEventStore, type VoiceState } from "@/store/events";
import { requestVoiceCall, requestVoiceHangup } from "@/lib/voiceApi";
import {
  attachToTerminal,
  fetchAllVoiceAttachments,
  removeVoiceAttachment,
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

/** Locale keys for the line under the orb, per state. */
const STATUS_KEY: Record<VoiceState, string> = {
  idle: "agentic_grid.voice_panel.ready",
  listening: "agentic_grid.voice_panel.listening",
  thinking: "agentic_grid.voice_panel.thinking",
  speaking: "agentic_grid.voice_panel.speaking",
  error: "agentic_grid.voice_panel.error",
};

/** Is a conversation running — the half of the toggle a click would end? */
function isActive(state: VoiceState): boolean {
  return state === "listening" || state === "thinking" || state === "speaking";
}

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

export function VoicePanel({
  promptTarget = "",
  onScreen = true,
}: {
  promptTarget?: string;
  onScreen?: boolean;
}) {
  const t = useT();
  const voiceState = (useEventStore((s) => s.voiceState) ?? "idle") as VoiceState;
  const transcription = useEventStore((s) => s.transcription) ?? "";
  const assistantName =
    (useEventStore((s) => s.assistantName) ?? "").trim() ||
    t("agentic_grid.voice_panel.assistant_fallback");
  const pushToast = useEventStore((s) => s.pushToast);

  const [open, setOpen] = useState(storedOpen);
  const [busy, setBusy] = useState(false);
  const [dropPhase, setDropPhase] = useState<DropPhase>("idle");
  const [dropTarget, setDropTarget] = useState("");
  const [receipts, setReceipts] = useState<DropReceipt[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const active = isActive(voiceState);

  // The native Jarvis Bar lives in another process, so it cannot update this
  // component's local state when a file lands. Reconcile the authoritative
  // backend queue while the panel is mounted: this both discovers external
  // drops and removes a receipt only after the spoken delivery consumed it.
  useEffect(() => {
    if (!onScreen) return;
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
  }, [active, onScreen]);

  // A drag may leave the app without sending this button a final dragleave.
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
            t("agentic_grid.voice_panel.start_failed"),
          );
        }
      }
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setBusy(false);
    }
  }, [busy, active, pushToast, t]);

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
          title={format(
            t("agentic_grid.voice_panel.open_title"),
            assistantName,
          )}
          aria-label={t("agentic_grid.voice_panel.open")}
          className="flex h-7 w-7 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        >
          <PanelRightOpen className="h-4 w-4" />
        </button>
        {/* The one-glance state the strip still owes: gold and pulsing while a
            conversation runs, quiet otherwise. */}
        <span
          aria-hidden="true"
          className={cn(
            "h-2.5 w-2.5 rounded-full",
            active
              ? "animate-pulse bg-[#e7c46e] motion-reduce:animate-none"
              : "bg-muted-foreground/40",
          )}
        />
      </aside>
    );
  }

  return (
    <aside
      data-testid="voice-panel"
      /*
       * 240 px rather than 288. This column is a presence indicator and a
       * transcript, not a reading surface — and it is the third vertical band
       * on a screen whose actual content is the terminals. Every pixel it does
       * not need belongs to them.
       */
      className="flex h-full w-60 shrink-0 flex-col border-l border-border"
    >
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/60 px-3">
        <span className="truncate text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          {t("agentic_grid.voice_panel.label")}
        </span>
        <button
          type="button"
          data-testid="voice-panel-close"
          onClick={toggleOpen}
          title={t("agentic_grid.voice_panel.close")}
          aria-label={t("agentic_grid.voice_panel.close")}
          className="flex h-7 w-7 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        >
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>

      {/*
        Aligned to the TOP of the column, not centred in it.
        A 176 px orb parked in the middle of a full-height column reads as a
        splash screen — the largest, brightest, most animated object on a
        screen whose subject is a wall of terminals. Sat at the top at 96 px it
        is what it actually is: a status light you can click, with the
        transcript under it and the column's own controls at its foot.
      */}
      <div className="flex min-h-0 flex-1 flex-col items-center gap-3 px-4 pb-3 pt-6">
        <div
          data-testid="voice-orb-stage"
          data-state={voiceState}
          data-drop-state={
            dropPhase === "idle" && receipts.some((receipt) => receipt.reserved)
              ? "using"
              : dropPhase === "idle" && receipts.length > 0
                ? "ready"
                : dropPhase
          }
          className="agentic-voice-orb-stage relative grid h-28 w-28 shrink-0 place-items-center"
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
            disabled={busy || dropPhase === "reading"}
            aria-pressed={active}
            title={
              active
                ? t("agentic_grid.voice_panel.hang_up_title")
                : format(
                    t("agentic_grid.voice_panel.talk_title"),
                    assistantName,
                  )
            }
            aria-label={
              active
                ? t("agentic_grid.voice_panel.hang_up")
                : format(t("agentic_grid.voice_panel.talk"), assistantName)
            }
            className={cn(
              /*
               * The idle orb is DESATURATED and dimmed, and only a running
               * conversation brings its colour up. The renderer paints the same
               * ivory-to-amber weather either way; what changes is how loudly
               * it is allowed to say so. An indicator that looks identical
               * whether or not anything is happening is decoration, and a
               * decoration this bright is what made the column read as a
               * screensaver bolted to an IDE.
               */
              "relative z-10 rounded-full outline-none transition-[transform,filter,opacity] duration-700 ease-out",
              "hover:scale-[1.02] active:scale-[0.98] focus-visible:ring-2 focus-visible:ring-primary/50 motion-reduce:transform-none",
              active
                ? "[filter:saturate(1)_brightness(1)]"
                : "[filter:saturate(0.45)_brightness(0.72)] hover:[filter:saturate(0.7)_brightness(0.85)]",
              dropPhase === "over" && "scale-[1.04]",
              (busy || dropPhase === "reading") && "cursor-wait opacity-80",
            )}
          >
            <VoiceOrb state={voiceState} size={96} />
          </button>
        </div>

        {/*
          One line, at label weight. It used to be a 16 px display-font
          headline with a second explanatory sentence under it — "Ready" set
          larger than anything in the terminals beside it, followed by a
          standing instruction telling the reader how to click the thing they
          were looking at. The instruction is now the button's own tooltip,
          which is where an instruction about a control belongs.
        */}
        <span className="flex shrink-0 items-center gap-1.5">
          <span
            aria-hidden="true"
            data-state={voiceState}
            className="agentic-voice-state-dot"
          />
          <span
            data-testid="voice-panel-status"
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

        {receipts.map((receipt) => (
          <div
            key={receipt.batchId}
            data-testid="voice-orb-drop-context"
            aria-live="polite"
            className="flex w-full max-w-full shrink-0 items-start gap-2 rounded-control border border-border/70 bg-card/50 px-2.5 py-2 text-left"
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
        {/* What the microphone is hearing, as it hears it. Shown only while a
            conversation runs — an idle panel repeating the last sentence of a
            finished one would read as a stuck microphone. */}
        {active && transcription && (
          <p
            data-testid="voice-panel-transcript"
            className="line-clamp-4 max-w-full shrink-0 text-center text-[13px] leading-relaxed text-muted-foreground"
          >
            {transcription}
          </p>
        )}

        {/* Last in the column, pushed to its foot: the fallback for people who
            do not drag files onto the orb. `mt-auto` is what keeps it there
            whether or not a transcript and a receipt are above it — a control
            that drifts up and down the column as state arrives is one nobody
            can learn the position of. */}
        <button
          type="button"
          disabled={!promptTarget || dropPhase === "reading"}
          onClick={() => fileInputRef.current?.click()}
          className="mt-auto flex shrink-0 items-center gap-1.5 rounded-control px-2 py-1 text-[11px] text-muted-foreground/80 transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
          {t("agentic_grid.voice_drop.choose")}
        </button>
      </div>
    </aside>
  );
}

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
  fetchVoiceAttachments,
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

export function VoicePanel({ promptTarget = "" }: { promptTarget?: string }) {
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
  const receiptKey = receipts
    .map((receipt) => `${receipt.target}:${receipt.batchId}`)
    .join("|");

  // The panel can remount while the backend queue survives (navigation, an
  // error boundary, frontend reload). Hydrate every terminal, not just the one
  // currently selected, so no pending context can become invisible.
  useEffect(() => {
    let cancelled = false;
    void fetchAllVoiceAttachments()
      .then((response) => {
        if (cancelled) return;
        if (response.batches.length === 0) return;
        setReceipts((current) => {
          const merged = new Map(current.map((item) => [item.batchId, item]));
          for (const batch of response.batches) {
            merged.set(batch.batch_id, {
              batchId: batch.batch_id,
              target: batch.terminal,
              files: batch.files,
              reserved: batch.reserved,
            });
          }
          return Array.from(merged.values());
        });
      })
      .catch(() => {
        // A transient reconnect must not invent an empty queue. Per-state
        // reconciliation retries once the voice pipeline moves again.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Voice state is global, but attachment ownership is per pane. Ask the
  // backend which batch IDs still exist instead of guessing that any global
  // listening/thinking transition consumed the receipt currently on screen.
  useEffect(() => {
    if (receipts.length === 0) return;
    let cancelled = false;
    const targets = [...new Set(receipts.map((receipt) => receipt.target))];
    let timer: number | undefined;
    const reconcile = () => {
      void Promise.allSettled(
        targets.map(async (target) => {
          const response = await fetchVoiceAttachments(target);
          return { target, batches: response.batches };
        }),
      )
        .then((results) => {
          if (cancelled) return;
          setReceipts((current) => {
            let next = current;
            for (const result of results) {
              if (result.status !== "fulfilled") continue;
              const byId = new Map(
                result.value.batches.map((batch) => [batch.batch_id, batch]),
              );
              next = next
                .filter(
                  (receipt) =>
                    receipt.target !== result.value.target ||
                    byId.has(receipt.batchId),
                )
                .map((receipt) => {
                  if (receipt.target !== result.value.target) return receipt;
                  const batch = byId.get(receipt.batchId);
                  return batch
                    ? { ...receipt, files: batch.files, reserved: batch.reserved }
                    : receipt;
                });
            }
            return next;
          });
        })
        .finally(() => {
          if (active && !cancelled) timer = window.setTimeout(reconcile, 750);
        });
    };
    reconcile();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active, receiptKey, voiceState]);

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
      className="flex h-full w-72 shrink-0 flex-col border-l border-border"
    >
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("agentic_grid.voice_panel.label")}
        </span>
        <button
          type="button"
          data-testid="voice-panel-close"
          onClick={toggleOpen}
          title={t("agentic_grid.voice_panel.close")}
          aria-label={t("agentic_grid.voice_panel.close")}
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
        >
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-5 pb-6">
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
          className="agentic-voice-orb-stage relative grid h-48 w-48 shrink-0 place-items-center"
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
              "relative z-10 rounded-full outline-none transition-[transform,box-shadow,opacity] duration-500 ease-out hover:scale-[1.015] active:scale-[0.98] focus-visible:ring-2 focus-visible:ring-[#e7c46e]/55 motion-reduce:transform-none",
              active
                ? "shadow-[0_20px_56px_-38px_rgba(231,196,110,0.48)]"
                : "shadow-[0_18px_48px_-40px_rgba(231,196,110,0.3)]",
              dropPhase === "over" && "scale-[1.045]",
              (busy || dropPhase === "reading") && "cursor-wait opacity-80",
            )}
          >
            <VoiceOrb state={voiceState} size={176} />
          </button>
        </div>

        <div className="flex flex-col items-center gap-1.5 text-center">
          <span className="flex items-center gap-2">
            <span
              aria-hidden="true"
              data-state={voiceState}
              className="agentic-voice-state-dot"
            />
            <span
              data-testid="voice-panel-status"
              aria-live="polite"
              className={cn(
                "font-display text-base font-semibold transition-colors",
                active && "text-[#e7c46e]",
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
          <span className="text-xs text-muted-foreground">
            {active
              ? t("agentic_grid.voice_panel.active_hint")
              : format(
                  t("agentic_grid.voice_panel.idle_hint"),
                  assistantName,
                )}
          </span>
        </div>

        {receipts.map((receipt) => (
          <div
            key={receipt.batchId}
            data-testid="voice-orb-drop-context"
            aria-live="polite"
            className="flex max-w-full items-start gap-2 rounded-xl border border-[#e7c46e]/30 bg-[#e7c46e]/[0.07] px-3 py-2 text-left"
          >
            <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#e7c46e]/15 text-[#e7c46e]">
              <Check className="h-3 w-3" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1 text-[11px] leading-relaxed text-muted-foreground">
              <span className="block truncate font-medium text-foreground">
                <FilePlus2
                  className="mr-1 inline h-3.5 w-3.5 text-[#e7c46e]"
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
        <button
          type="button"
          disabled={!promptTarget || dropPhase === "reading"}
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
          {t("agentic_grid.voice_drop.choose")}
        </button>

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

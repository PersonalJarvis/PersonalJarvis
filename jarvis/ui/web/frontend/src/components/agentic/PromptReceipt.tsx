import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, HelpCircle, X } from "lucide-react";
import { fetchLastPrompt } from "@/lib/agenticIdeApi";

/**
 * Visible proof that a pane was handed a prompt — drawn by the app, never by
 * the agent's screen.
 *
 * ## Why this exists at all
 *
 * Jarvis composes a brief, types it into a pane and says so out loud. Until
 * this component, the ONLY evidence the user had was the pane echoing the text
 * back, and that evidence is missing in exactly the situations where somebody
 * would want it:
 *
 * - the pane had parked its output while it was off screen, and un-parking
 *   depends on an `IntersectionObserver` reporting its way out of states it
 *   does not report (see ./offscreenBuffer);
 * - the pane's socket was reconnecting, and a hidden window's timers are
 *   clamped, so "reconnecting" lasted minutes rather than half a second;
 * - the emulator had not painted yet, so the pane was a black rectangle;
 * - or the CLI simply redrew its input box somewhere the user was not looking.
 *
 * Every one of those has happened in production, and from the user's chair
 * they are one single experience: **Jarvis claimed it sent the brief and the
 * screen shows nothing.** The reasonable conclusion is that it did not, and no
 * amount of "but the agent really did get it" repairs the trust that costs.
 *
 * So the receipt is deliberately built from a DIFFERENT source than the screen:
 * the pane's own delivery record (`last_prompt_at`), pushed instantly over the
 * socket and read again from the state on every mount, reconnect and poll. If
 * the terminal is black, the socket is down and the observer is confused, the
 * receipt still appears.
 *
 * ## Why it does not simply fade away
 *
 * A toast that vanishes after four seconds only reassures a user who was
 * already watching — the one who never doubted it. The doubt belongs to
 * whoever looked away, and it appears minutes later. So this stays: prominent
 * while it is fresh, then shrinking to a quiet line that remains until the
 * next prompt replaces it or the user dismisses it. Proof that expires is not
 * proof.
 */

/** How long the receipt stays in its full, hard-to-miss form. */
export const RECEIPT_PROMINENT_MS = 9_000;

export interface PromptReceiptProps {
  /** Call-sign of the pane this receipt belongs to. */
  terminal: string;
  /** Which workspace holds it — several can be open on the same call-signs. */
  workspaceId?: string | null;
  /** When the prompt was delivered (epoch SECONDS, as the backend stamps it). */
  at: number;
  /** Opening of the delivered text; the full brief is fetched on demand. */
  preview: string;
  /** Full length of the prompt, which `preview` is only the start of. */
  chars: number;
  /**
   * Did the agent accept it and start?
   *
   * Three states, never rounded to two: `false` means the text is sitting in
   * the pane's input box unsent, which looks exactly like a working agent and
   * is the one case where the user has to act.
   */
  submitted: boolean | null;
  onDismiss: () => void;
}

/** "just now" / "3 min ago" — short enough for a one-line receipt. */
export function agoLabel(deliveredAt: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - deliveredAt * 1000) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
}

/** The clock time it landed — the detail that can be checked against a memory. */
export function clockLabel(deliveredAt: number): string {
  try {
    return new Date(deliveredAt * 1000).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

export function PromptReceipt({
  terminal,
  workspaceId,
  at,
  preview,
  chars,
  submitted,
  onDismiss,
}: PromptReceiptProps) {
  const [prominent, setProminent] = useState(true);
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // A fresh delivery is prominent again: this component is keyed by `at` in the
  // pane, but a re-render with a new timestamp must not leave a two-minute-old
  // receipt in its quiet form while the user is being told something arrived.
  useEffect(() => {
    setProminent(true);
    setOpen(false);
    setFull(null);
    setLoadError("");
    const timer = window.setTimeout(() => setProminent(false), RECEIPT_PROMINENT_MS);
    return () => window.clearTimeout(timer);
  }, [at]);

  // The relative label has to keep up on its own — nothing else re-renders this
  // pane once the agent settles, and a receipt frozen at "just now" half an
  // hour later is a small lie in a component whose entire job is not lying.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  const loadFull = useCallback(async () => {
    if (full !== null) return;
    try {
      const answer = await fetchLastPrompt(terminal, workspaceId ?? undefined);
      if (!mounted.current) return;
      setFull(answer.text);
      setLoadError("");
    } catch (err) {
      if (!mounted.current) return;
      // The excerpt is still on screen, so this degrades to "you can see the
      // opening but not the rest" rather than to an empty box.
      setLoadError(err instanceof Error ? err.message : "The full prompt could not be read.");
    }
  }, [full, terminal, workspaceId]);

  const toggle = useCallback(() => {
    setOpen((wasOpen) => {
      if (!wasOpen) void loadFull();
      return !wasOpen;
    });
  }, [loadFull]);

  const verdict =
    submitted === true
      ? {
          icon: <Check className="h-3.5 w-3.5 shrink-0 text-emerald-400" />,
          label: "Prompt sent",
          note: "the agent took it and started",
        }
      : submitted === false
        ? {
            icon: <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-400" />,
            label: "Prompt typed, not started",
            note: "it is sitting in this pane's input box — press Enter here to run it",
          }
        : {
            icon: <HelpCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />,
            label: "Prompt sent",
            note: "it went to the pane; the agent's start could not be confirmed",
          };

  const clock = clockLabel(at);

  return (
    <div
      className={`pointer-events-auto absolute inset-x-2 bottom-2 z-20 rounded-md border
                  backdrop-blur transition-all duration-500 ${
                    prominent
                      ? "border-primary/50 bg-card/95 shadow-lg"
                      : "border-border/60 bg-card/80"
                  }`}
      // Announced rather than only drawn: the whole point is that the user
      // finds out, and someone using a screen reader gets no help from a ring
      // around a rectangle.
      role="status"
      aria-live="polite"
      data-testid="prompt-receipt"
      data-prominent={prominent ? "true" : "false"}
    >
      <div className="flex items-center gap-2 px-2 py-1.5">
        {verdict.icon}
        <button
          type="button"
          onClick={toggle}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open}
          data-testid="prompt-receipt-toggle"
          title={`Read exactly what was sent to ${terminal}`}
        >
          <span className="shrink-0 text-[11px] font-medium text-foreground">
            {verdict.label}
          </span>
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {agoLabel(at, now)}
            {clock ? ` · ${clock}` : ""}
          </span>
          {/* The opening of the brief, on the line itself. Even closed, the
              receipt shows a piece of the real text — a bare "sent" is another
              claim, and claims are what the user already had. */}
          <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground/80">
            {preview}
          </span>
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${
              open ? "rotate-180" : ""
            }`}
          />
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="btn-ghost h-6 w-6 shrink-0 p-0"
          aria-label={`Dismiss the delivery receipt for ${terminal}`}
          data-testid="prompt-receipt-dismiss"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {prominent && !open && (
        <p className="px-2 pb-1.5 text-[10px] leading-snug text-muted-foreground">
          {verdict.note} · click to read what was sent
        </p>
      )}

      {open && (
        <div className="border-t border-border/60 px-2 py-2" data-testid="prompt-receipt-body">
          <pre
            className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words rounded
                       bg-background/60 px-2 py-1.5 font-mono text-[11px] leading-relaxed
                       text-foreground"
          >
            {full ?? preview}
          </pre>
          <p className="mt-1.5 text-[10px] text-muted-foreground">
            {full === null && !loadError
              ? `Opening of ${chars.toLocaleString()} characters — reading the rest…`
              : loadError
                ? `Showing the opening only — ${loadError}`
                : `${chars.toLocaleString()} characters, delivered to ${terminal}${
                    clock ? ` at ${clock}` : ""
                  }.`}
          </p>
        </div>
      )}
    </div>
  );
}

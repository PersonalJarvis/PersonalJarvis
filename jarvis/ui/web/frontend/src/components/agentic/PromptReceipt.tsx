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
 * It shows for five seconds and goes (maintainer decision 2026-07-28, after
 * seeing it live). A receipt that lingers sits on top of the agent's output —
 * the pane is a terminal, and the bottom rows are where a CLI does its
 * talking, so a permanent bar buys certainty by covering the thing it is
 * certifying. Five seconds is enough to see WHICH pane was addressed and that
 * something real was sent, which is what was missing.
 *
 * Nothing is lost by leaving: the delivery stays in the pane's state and the
 * full text stays readable at `GET /terminals/{name}/prompt` (and through the
 * CLI), so a doubt that surfaces ten minutes later still has an answer. What
 * expires is the interruption, not the proof.
 *
 * Two deliberate exceptions, because both would turn "brief" into "broken":
 *
 * - a receipt the user is READING or pointing at stays until they are done —
 *   a panel that closes under the cursor is worse than one that never opened;
 * - a prompt that was typed but never STARTED stays, full stop. That is not a
 *   confirmation, it is a warning: the pane looks exactly like a working one
 *   and only the user can push it over the line. Hiding it after five seconds
 *   would recreate the original bug in a new place.
 */

/** How long an ordinary receipt stays on screen before it withdraws. */
export const RECEIPT_VISIBLE_MS = 5_000;

/**
 * Grace period after the pointer leaves, before a hovered receipt withdraws.
 *
 * Short, but not zero: the receipt must not evaporate in the instant between
 * "I am moving the mouse towards it" and arriving.
 */
export const RECEIPT_LEAVE_GRACE_MS = 1_200;

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
  const [gone, setGone] = useState(false);
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
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

  // A new delivery is a new receipt: this component is keyed by `at` in the
  // pane, but a withdrawn one must never stay withdrawn while something fresh
  // is being announced.
  useEffect(() => {
    setGone(false);
    setOpen(false);
    setHovered(false);
    setFull(null);
    setLoadError("");
  }, [at]);

  /**
   * Withdraw after five seconds — unless withdrawing would be the wrong thing.
   *
   * A prompt that never STARTED keeps its receipt: that is a warning the user
   * has to act on, not a confirmation they can be spared. And a receipt being
   * read or pointed at is not in anyone's way; it leaves shortly after the
   * pointer does.
   */
  const holdOpen = submitted === false || open || hovered;
  /** Has this receipt ever been held back? Then it leaves on the short clock. */
  const wasHeld = useRef(false);
  useEffect(() => {
    wasHeld.current = false;
  }, [at]);
  useEffect(() => {
    if (holdOpen) {
      wasHeld.current = true;
      return;
    }
    if (gone) return;
    // Someone who has just finished reading does not need the full five
    // seconds again — but they do need more than zero, or the receipt
    // disappears in the gap between leaving it and deciding to come back.
    const delay = wasHeld.current ? RECEIPT_LEAVE_GRACE_MS : RECEIPT_VISIBLE_MS;
    const timer = window.setTimeout(() => setGone(true), delay);
    return () => window.clearTimeout(timer);
  }, [at, gone, holdOpen]);

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
            // The qualifier rides in the LABEL rather than the line below it,
            // which only the warning case still renders. A five-second bar
            // reading a bare "Prompt sent" would quietly upgrade "could not
            // confirm it started" into "it started" — the exact overstatement
            // this component exists to stop.
            label: "Prompt sent — start could not be confirmed",
            note: "it went to the pane; the agent's start could not be confirmed",
          };

  const clock = clockLabel(at);

  // Withdrawn: the pane belongs to its agent again. Unmounting rather than
  // hiding, so nothing of this sits over the terminal's bottom rows — which
  // are exactly the rows a CLI writes its answers into.
  if (gone) return null;

  return (
    <div
      className="pointer-events-auto absolute inset-x-2 bottom-2 z-20 rounded-md border
                 border-primary/50 bg-card/95 shadow-lg backdrop-blur
                 transition-opacity duration-300"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      // Announced rather than only drawn: the whole point is that the user
      // finds out, and someone using a screen reader gets no help from a ring
      // around a rectangle. `polite` matters more now that it is brief — it
      // must not cut off whatever the reader is in the middle of saying.
      role="status"
      aria-live="polite"
      data-testid="prompt-receipt"
      // Whether this one will withdraw on its own. False is the warning case,
      // which stays until dealt with.
      data-transient={submitted === false ? "false" : "true"}
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
          <span className="shrink-0 truncate text-[11px] font-medium text-foreground">
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

      {/* The second line is for the case that needs explaining and acting on.
          On the ordinary "sent and running" receipt it would be one more row
          covering the terminal for five seconds to say what the first row
          already said. */}
      {submitted === false && !open && (
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

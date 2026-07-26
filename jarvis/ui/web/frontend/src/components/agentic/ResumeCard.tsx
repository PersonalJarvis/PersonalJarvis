/**
 * "Resume all sessions" — the offer to reopen the workspace that was last open.
 *
 * Shown at the top of the wizard whenever a previous workspace can be brought
 * back: after the browser was closed, after the app restarted, after a reboot.
 *
 * ## Why a card and not a dialog
 *
 * A dialog would sit in front of the wizard on every single visit, including
 * the many where the user came here to open something else entirely. That is
 * how people learn to dismiss a thing without reading it — and this one has
 * something worth reading. A card is just as visible, stays out of the way, and
 * costs nothing to ignore.
 *
 * ## Why every pane is listed individually
 *
 * Because "resumed" is not one answer. A pane can come back with its whole
 * conversation, or with nothing but its call-sign — and the two look identical
 * on screen until you ask the agent a follow-up question and get a blank stare.
 * So each pane says which of the two it will be BEFORE the click, and a pane
 * whose coding CLI is no longer installed says that too, instead of failing
 * quietly a second after the workspace opens.
 */
import { AlertCircle, RotateCcw, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ResumeOffer } from "@/lib/agenticIdeApi";

interface ResumeCardProps {
  offer: ResumeOffer;
  busy: boolean;
  onResume: () => void;
  onDismiss: () => void;
}

/**
 * "2 hours ago" for a POSIX timestamp.
 *
 * Deliberately coarse. The exact minute a workspace was saved is not a decision
 * input; "yesterday" versus "just now" is.
 */
export function savedAgo(seconds: number, now: number = Date.now()): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const minutes = Math.floor((now - seconds * 1000) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** One line of plain language for what the button will actually do. */
export function resumeSummary(offer: ResumeOffer): string {
  const total = offer.terminals.length;
  const panes = `${total} terminal${total === 1 ? "" : "s"}`;
  if (!offer.folder_exists) return "That folder is no longer on this machine.";
  if (!offer.available) {
    return "None of these terminals can run here — their coding CLIs are not installed.";
  }
  const kept = offer.resumable_count;
  if (kept === 0) {
    return `${panes} come back with their names and places; none of their conversations could be kept.`;
  }
  if (kept === total) {
    return `${panes}, each continuing the conversation it was having.`;
  }
  return `${panes} — ${kept} continue their conversation, the rest start fresh.`;
}

export function ResumeCard({ offer, busy, onResume, onDismiss }: ResumeCardProps) {
  const when = savedAgo(offer.saved_at);
  return (
    <section
      data-testid="resume-card"
      aria-labelledby="resume-card-title"
      className="rounded-xl border border-primary/30 bg-primary/5 p-5"
    >
      <div className="flex flex-wrap items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/15">
          <RotateCcw className="h-4 w-4 text-primary" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 id="resume-card-title" className="font-display text-base font-semibold">
            Resume all sessions
          </h3>
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{offer.folder_name}</span>
            {when && <span> · last open {when}</span>}
          </p>
          <code className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
            {offer.folder}
          </code>
        </div>
      </div>

      <ul className="mt-4 flex flex-wrap gap-2">
        {offer.terminals.map((pane) => (
          <li
            key={pane.key}
            data-testid={`resume-pane-${pane.key}`}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs",
              pane.available
                ? "border-border bg-card/60"
                : "border-amber-500/40 bg-amber-500/10",
            )}
          >
            <Terminal className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span className="font-medium text-foreground">{pane.name}</span>
            <span className="text-muted-foreground">{pane.display_name}</span>
            {!pane.available ? (
              <span className="text-amber-200">not installed here</span>
            ) : (
              !pane.resumable && (
                <span className="text-muted-foreground">· starts fresh</span>
              )
            )}
          </li>
        ))}
      </ul>

      <p className="mt-3 text-xs text-muted-foreground">{resumeSummary(offer)}</p>

      {!offer.folder_exists && (
        <p className="mt-2 flex items-start gap-2 text-xs text-amber-200">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          The folder was moved or deleted, so there is nothing left to reopen.
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          data-testid="resume-dismiss"
          className="btn-ghost"
          disabled={busy}
          onClick={onDismiss}
          title="Forget this workspace and start from the wizard"
        >
          Start fresh
        </button>
        <button
          type="button"
          data-testid="resume-all"
          className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
          disabled={busy || !offer.available}
          onClick={onResume}
        >
          <RotateCcw className="h-4 w-4" />
          Resume all sessions
        </button>
      </div>
    </section>
  );
}

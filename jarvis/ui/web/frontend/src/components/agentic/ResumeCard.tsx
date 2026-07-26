/**
 * "Resume all sessions" — the offer to reopen everything that was open before.
 *
 * Shown at the top of the wizard whenever previous workspaces can be brought
 * back: after the browser was closed, after the workspaces were closed for the
 * day, after the app restarted, after a reboot.
 *
 * ## Why a card and not a dialog
 *
 * A dialog would sit in front of the wizard on every single visit, including
 * the many where the user came here to open something else entirely. That is
 * how people learn to dismiss a thing without reading it — and this one has
 * something worth reading. A card is just as visible, stays out of the way, and
 * costs nothing to ignore.
 *
 * ## Why every workspace and every pane is listed
 *
 * Because "resumed" is not one answer. A pane can come back with its whole
 * conversation, or with nothing but its call-sign — and the two look identical
 * on screen until you ask the agent a follow-up question and get a blank stare.
 * A whole workspace can be unreachable because its folder was moved. So each
 * one says which of those it is BEFORE the click, rather than after.
 *
 * With many panes the list is summarised instead of printed in full: a hundred
 * chips is not information, it is wallpaper. The call-signs of the first few are
 * what someone recognises their workspace by; the rest are a count.
 */
import { AlertCircle, FolderGit2, RotateCcw, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ResumeOffer, ResumeWorkspaceOffer } from "@/lib/agenticIdeApi";

interface ResumeCardProps {
  offer: ResumeOffer;
  busy: boolean;
  onResume: () => void;
  onDismiss: () => void;
}

/** Call-signs printed in full before the rest becomes "+N more". */
const NAMES_SHOWN = 8;

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
  const spaces = offer.workspace_count;
  const panes = offer.terminal_count;
  const where = spaces === 1 ? "1 folder" : `${spaces} folders`;
  const what = `${panes} terminal${panes === 1 ? "" : "s"}`;
  if (!offer.available) {
    const anyFolder = offer.workspaces.some((w) => w.folder_exists);
    return anyFolder
      ? "None of these terminals can run here — their coding CLIs are not installed."
      : "Those folders are no longer on this machine.";
  }
  const kept = offer.resumable_count;
  if (kept === 0) {
    return `${where}, ${what} — names and places come back; none of their conversations could be kept.`;
  }
  if (kept === panes) {
    return `${where}, ${what}, each continuing the conversation it was having.`;
  }
  return `${where}, ${what} — ${kept} continue their conversation, the rest start fresh.`;
}

function WorkspaceRow({ space }: { space: ResumeWorkspaceOffer }) {
  const shown = space.terminals.slice(0, NAMES_SHOWN);
  const hidden = space.terminals.length - shown.length;
  const fresh = space.terminals.length - space.resumable_count;
  return (
    <li
      data-testid={`resume-workspace-${space.folder_name}`}
      className={cn(
        "rounded-lg border p-3",
        space.available
          ? "border-border bg-card/60"
          : "border-amber-500/40 bg-amber-500/10",
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <FolderGit2 className="h-3.5 w-3.5 shrink-0 translate-y-0.5 text-primary" />
        <span className="font-medium text-foreground">
          {space.name || space.folder_name}
        </span>
        <span className="text-xs text-muted-foreground">
          {space.terminals.length} terminal{space.terminals.length === 1 ? "" : "s"}
          {space.resumable_count > 0 && ` · ${space.resumable_count} continuing`}
          {fresh > 0 && ` · ${fresh} starting fresh`}
        </span>
      </div>
      <code className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
        {space.folder}
      </code>
      {!space.folder_exists && (
        <p className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          That folder was moved or deleted, so this one cannot come back.
        </p>
      )}
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {shown.map((pane) => (
          <li
            key={pane.key}
            data-testid={`resume-pane-${pane.key}`}
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
              pane.available
                ? "border-border/70 text-muted-foreground"
                : "border-amber-500/40 text-amber-200",
            )}
          >
            <Terminal className="h-3 w-3 shrink-0 text-primary" />
            <span className="font-medium text-foreground">{pane.name}</span>
            {!pane.available ? (
              <span>not installed here</span>
            ) : (
              !pane.resumable && <span>· starts fresh</span>
            )}
          </li>
        ))}
        {hidden > 0 && (
          <li
            data-testid={`resume-more-${space.folder_name}`}
            className="flex items-center rounded-md px-2 py-1 text-[11px] text-muted-foreground"
          >
            +{hidden} more
          </li>
        )}
      </ul>
    </li>
  );
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
            Pick up where you left off{when && <span> · last open {when}</span>}
          </p>
        </div>
      </div>

      <ul className="mt-4 space-y-2">
        {offer.workspaces.map((space) => (
          <WorkspaceRow key={space.folder} space={space} />
        ))}
      </ul>

      <p className="mt-3 text-xs text-muted-foreground">{resumeSummary(offer)}</p>

      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          data-testid="resume-dismiss"
          className="btn-ghost"
          disabled={busy}
          onClick={onDismiss}
          title="Forget these workspaces and start from the wizard"
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

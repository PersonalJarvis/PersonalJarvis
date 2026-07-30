/**
 * The offer to continue the last Agentic IDE session.
 *
 * This is deliberately a flat section, not a card containing workspace cards
 * containing terminal chips. Folder rows and terminal rows are separated by
 * rules, so the hierarchy comes from alignment and type rather than outlines.
 */
import { cn } from "@/lib/utils";
import { Button, SectionLabel } from "./controls";
import type { ResumeOffer, ResumeWorkspaceOffer } from "@/lib/agenticIdeApi";

interface ResumeCardProps {
  offer: ResumeOffer;
  busy: boolean;
  onResume: () => void;
  onDismiss: () => void;
}

/** Call-signs printed in full before the rest becomes "+N more". */
const NAMES_SHOWN = 8;

/** Coarse relative time: enough to identify the session, never false precision. */
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
    const anyFolder = offer.workspaces.some(
      (workspace) => workspace.folder_exists,
    );
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
  const when = savedAgo(space.saved_at ?? 0);

  return (
    <li
      data-testid={`resume-workspace-${space.folder_name}`}
      className={cn(
        "grid gap-x-7 gap-y-3 border-t border-border/60 py-4 lg:grid-cols-[minmax(0,1fr)_auto]",
        !space.available && "text-amber-200",
        space.in_last_session === false && "opacity-70",
      )}
    >
      <div className="min-w-0">
        <h4 className="truncate text-sm font-semibold text-foreground">
          {space.name || space.folder_name}
        </h4>
        <code className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
          {space.folder}
        </code>
        {!space.folder_exists && (
          <p className="mt-2 text-xs leading-relaxed text-amber-200">
            This folder was moved or deleted, so it cannot come back.
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-baseline gap-x-3 text-[11px] text-muted-foreground lg:justify-end">
        <span className="font-mono tabular-nums text-foreground">
          {space.terminals.length.toString().padStart(2, "0")} terminal
          {space.terminals.length === 1 ? "" : "s"}
        </span>
        {space.resumable_count > 0 && (
          <span>{space.resumable_count} continuing</span>
        )}
        {fresh > 0 && <span>{fresh} starting fresh</span>}
        {when && <span>{when}</span>}
      </div>

      <ul className="lg:col-span-2 grid gap-x-6 gap-y-1 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((pane) => (
          <li
            key={pane.key}
            data-testid={`resume-pane-${pane.key}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-3 border-t border-border/40 py-2 text-xs"
          >
            <span className="truncate font-mono text-foreground">
              {pane.name}
            </span>
            <span
              className={cn(
                "text-[10px]",
                pane.available && pane.resumable
                  ? "text-primary/80"
                  : "text-amber-200",
              )}
            >
              {!pane.available
                ? "not installed here"
                : pane.resumable
                  ? "continues"
                  : "starts fresh"}
            </span>
          </li>
        ))}
        {hidden > 0 && (
          <li
            data-testid={`resume-more-${space.folder_name}`}
            className="border-t border-border/40 py-2 font-mono text-[10px] text-muted-foreground"
          >
            +{hidden} more
          </li>
        )}
      </ul>
    </li>
  );
}

export function ResumeCard({
  offer,
  busy,
  onResume,
  onDismiss,
}: ResumeCardProps) {
  const when = savedAgo(offer.saved_at);
  const coming = offer.workspaces.filter(
    (workspace) => workspace.in_last_session !== false,
  );
  const earlier = offer.workspaces.filter(
    (workspace) => workspace.in_last_session === false,
  );

  return (
    <section
      data-testid="resume-card"
      aria-labelledby="resume-card-title"
      className="border-y border-primary/25 bg-primary/[0.025] px-4 py-5 sm:px-5"
    >
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <SectionLabel className="text-primary/80">
            Previous session
          </SectionLabel>
          <h3
            id="resume-card-title"
            className="mt-2 text-lg font-semibold tracking-tight text-foreground"
          >
            Continue where you left off
          </h3>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted-foreground">
            {resumeSummary(offer)}
            {when && <span> Last open {when}.</span>}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
          <Button
            variant="subtle"
            data-testid="resume-dismiss"
            disabled={busy}
            onClick={onDismiss}
            title="Forget these workspaces and configure a new one"
          >
            Start fresh
          </Button>
          <Button
            variant="primary"
            data-testid="resume-all"
            disabled={busy || !offer.available}
            onClick={onResume}
            className="px-4"
          >
            {busy ? "Resuming…" : "Resume all sessions"}
          </Button>
        </div>
      </div>

      <ul className="mt-4 border-b border-border/60">
        {coming.map((space, index) => (
          <WorkspaceRow
            key={space.session_id || `${space.folder}#${index}`}
            space={space}
          />
        ))}
      </ul>

      {earlier.length > 0 && (
        <details className="mt-4" data-testid="resume-earlier">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
            Also remembered from earlier sessions ({earlier.length})
          </summary>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            Resuming does not reopen these. Open one from the folder picker when
            you want it back.
          </p>
          <ul className="mt-2 border-b border-border/60">
            {earlier.map((space, index) => (
              <WorkspaceRow
                key={space.session_id || `${space.folder}#${index}`}
                space={space}
              />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
